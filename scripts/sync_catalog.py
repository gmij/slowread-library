"""Build immutable provider/catalog indexes. No application database or HTML scraping."""
import argparse, csv, hashlib, io, json, os, re, tempfile, unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

CSV_URL = 'https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv'
RSS_URL = 'https://www.gutenberg.org/cache/epub/feeds/today.rss'
NS = {'pg': 'http://www.gutenberg.org/2009/pgterms/', 'dc': 'http://purl.org/dc/terms/', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'}

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def key(value):
    return str(int(digest(value)[:10], 16))

def tokens(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]{2,}|[\u3400-\u9fff]', text))

def get(url):
    with urlopen(Request(url, headers={'User-Agent': 'Slowread-Catalog/1.0 (metadata sync)'}), timeout=120) as r:
        data = r.read(80_000_001)
        if len(data) > 80_000_000:
            raise ValueError('Upstream catalog exceeds size limit')
        return data

def split(value):
    return [v.strip() for v in (value or '').split(';') if v.strip()]

def from_csv(data):
    result = {}
    rows = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    if not {'Text#', 'Type', 'Title', 'Language', 'Authors', 'Bookshelves'} <= set(rows.fieldnames or []):
        raise ValueError('CSV schema changed')
    for row in rows:
        if row['Type'] != 'Text':
            continue
        number = int(row['Text#'])
        if number <= 0 or not row['Title'].strip():
            raise ValueError('Invalid provider record')
        result[number] = {'externalId': str(number), 'origin': 'gutenberg', 'providerId': 'gutenberg-catalog',
            'title': re.sub(r'\s+', ' ', row['Title']).strip(), 'authors': split(row['Authors']),
            'languages': split(row['Language']), 'subjects': split(row['Subjects']),
            'bookshelves': split(row['Bookshelves']), 'classification': split(row['LoCC']),
            'releaseDate': row['Issued'], 'rights': None, 'assets': {}}
    return result

def from_rdf(data):
    root = ET.fromstring(data)
    book = root.find('pg:ebook', NS)
    if book is None:
        raise ValueError('Missing RDF ebook')
    kind = book.find('dc:type/rdf:Description/rdf:value', NS)
    if kind is not None and kind.text != 'Text':
        return None, None
    number = int(book.attrib['{' + NS['rdf'] + '}about'].rsplit('/', 1)[1])
    def values(path):
        return [x.text.strip() for x in book.findall(path, NS) if x.text and x.text.strip()]
    return number, {'externalId': str(number), 'origin': 'gutenberg', 'providerId': 'gutenberg-catalog',
        'title': ' '.join(values('dc:title')), 'authors': values('dc:creator/pg:agent/pg:name'),
        'languages': values('dc:language/rdf:Description/rdf:value'),
        'subjects': values('dc:subject/rdf:Description/rdf:value'),
        'bookshelves': values('pg:bookshelf/rdf:Description/rdf:value'), 'classification': [],
        'releaseDate': next(iter(values('dc:issued')), ''), 'rights': next(iter(values('dc:rights')), None),
        'assets': {'rdf': f'https://www.gutenberg.org/cache/epub/{number}/pg{number}.rdf'}}

def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

def build(records, output, aliases=None):
    aliases = aliases or {}
    config_path = Path(__file__).with_name('taxonomy.json')
    taxonomy = json.loads(config_path.read_text('utf-8')) if config_path.exists() else {'categories': {}, 'legacyShelves': {}}
    native_shelves = {name: identifier for identifier, name in taxonomy['legacyShelves'].items()}
    details, canonical, postings, indexes = defaultdict(dict), defaultdict(dict), defaultdict(lambda: defaultdict(list)), defaultdict(list)
    authors, shelves = {}, {}
    for number, source in sorted(records.items()):
        legacy = f'gutenberg-{number}'
        # Never infer work equality from a title; curated mappings may join editions/providers.
        book_id = aliases.get(legacy, 'bk_' + digest('gutenberg:' + str(number))[:24])
        if not re.fullmatch(r'bk_[a-f0-9]{24}', book_id):
            raise ValueError('Invalid canonical mapping')
        record = dict(source, id=legacy, canonicalId=book_id, sourceEditionId='gutenberg:' + str(number),
                      metadataHash=digest(json.dumps(source, sort_keys=True, ensure_ascii=False)))
        details[str(number // 1000)][str(number)] = record
        work = canonical[book_id[3:5]].setdefault(book_id, {'id': book_id, 'title': source['title'], 'authors': source['authors'], 'sources': []})
        work['sources'].append(record['sourceEditionId'])
        indexes['all'].append(number)
        for language in source['languages']:
            indexes['language-' + language].append(number)
        for author in source['authors']:
            aid = key('author:' + author)
            if aid in authors and authors[aid]['title'] != author:
                raise ValueError('Author identifier collision')
            authors[aid] = {'id': aid, 'title': author}
            indexes['author-' + aid].append(number)
        for shelf in source['bookshelves']:
            sid = native_shelves.get(shelf.removeprefix('Category: '), key('shelf:' + shelf))
            shelves[sid] = {'id': sid, 'title': shelf.removeprefix('Category: '), 'titleEn': shelf.removeprefix('Category: '), 'category': shelf.startswith('Category: '), 'group': taxonomy['categories'].get(shelf.removeprefix('Category: '), 'Other')}
            indexes['shelf-' + sid].append(number)
        for token in tokens(source['title'] + ' ' + ' '.join(source['authors'] + source['subjects'] + source['bookshelves'])):
            bucket = digest(token)[:2]
            postings[bucket][token].append(number)
    for group, value in details.items():
        write(output, 'editions/' + group + '.json', value)
    for group, value in canonical.items():
        write(output, 'books/' + group + '.json', value)
    # Most recent editions first. Posting lists remain complete; never truncate common words.
    grouped = defaultdict(dict)
    for name, ids in indexes.items():
        ids = sorted(set(ids), reverse=True)
        if name.startswith(('author-', 'shelf-')):
            kind, identifier = name.split('-', 1)
            grouped[f'{kind}/{int(identifier) % 256}'][identifier] = ids
        else:
            write(output, 'browse/' + name + '.json', ids)
    for name, value in grouped.items():
        write(output, 'browse/' + name + '.json', value)
    for bucket, terms in postings.items():
        write(output, 'search/' + bucket + '.json', terms)
    for letter in 'abcdefghijklmnopqrstuvwxyz':
        write(output, 'authors/' + letter + '.json', sorted([a for a in authors.values() if a['title'].lower().startswith(letter)], key=lambda a: a['title']))
    write(output, 'directory.json', list(shelves.values()))
    return {'totalBooks': len(records), 'canonicalBooks': sum(len(v) for v in canonical.values()), 'authors': len(authors), 'shelves': len(shelves)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--csv')
    parser.add_argument('--mode', choices=['full', 'incremental'], default='full')
    args = parser.parse_args()
    root = Path(args.root).resolve() / 'metadata'
    previous_path = root / 'manifest.json'
    previous = json.loads(previous_path.read_text('utf-8')) if previous_path.exists() else None
    if args.mode == 'incremental' and previous:
        records = {}
        for path in (root / previous['version'] / 'editions').glob('*.json'):
            for number, record in json.loads(path.read_text('utf-8')).items():
                records[int(number)] = {k: v for k, v in record.items() if k not in ['id', 'canonicalId', 'sourceEditionId', 'metadataHash']}
        feed = ET.fromstring(get(RSS_URL))
        ids = sorted({int(m) for item in feed.iter() if item.text for m in re.findall(r'/ebooks/(\d+)', item.text)})
        if len(ids) > 1000:
            raise ValueError('Unexpected incremental feed size')
        import time
        for number in ids:
            _, record = from_rdf(get(f'https://www.gutenberg.org/cache/epub/{number}/pg{number}.rdf'))
            if record is not None:
                if not record['title'] or not record['languages']:
                    raise ValueError('Incomplete RDF metadata')
                records[number] = record
            time.sleep(2)
    else:
        records = from_csv(Path(args.csv).read_bytes() if args.csv else get(CSV_URL))
        # Preserve richer RDF fields when the CSV record itself did not change.
        if previous:
            for path in (root / previous['version'] / 'editions').glob('*.json'):
                for number, old in json.loads(path.read_text('utf-8')).items():
                    new = records.get(int(number))
                    if new and all(new[k] == old.get(k) for k in ['title', 'authors', 'languages']):
                        new['rights'], new['assets'] = old.get('rights'), old.get('assets', {})
    if len(records) < 1000 or (previous and len(records) < previous['totalBooks'] * .95):
        raise ValueError('Incomplete catalog; keep last good snapshot')
    aliases_path = root / 'canonical-mappings.json'
    aliases = json.loads(aliases_path.read_text('utf-8')) if aliases_path.exists() else {}
    version = 'v-' + digest(json.dumps([3, records, aliases], sort_keys=True, ensure_ascii=False))[:20]
    target = root / version
    target.mkdir(parents=True, exist_ok=True)
    totals = build(records, target, aliases)
    manifest = {k: totals[k] for k in ['totalBooks', 'canonicalBooks', 'authors', 'shelves']}
    manifest.update(schemaVersion=1, version=version, syncedAt=datetime.now(timezone.utc).isoformat(), mode=args.mode,
                    providers=[{'id': 'gutenberg-catalog', 'origin': 'gutenberg', 'enabled': True, 'priority': 100, 'metadata': 'csv+rdf', 'fullSync': 'weekly', 'incrementalSync': 'daily-rss-rdf'},
                               {'id': 'gutendex', 'origin': 'gutenberg', 'enabled': False, 'priority': 50, 'metadata': 'json', 'reason': 'optional adapter; not required for catalog availability'}])
    # Files land first; manifest is published in the same Git commit. Failed builds never switch readers.
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / 'manifest.tmp'
    write(root, 'manifest.tmp', manifest)
    os.replace(tmp, previous_path)
    # Keep active and previous versions; Git retains historical snapshots for recovery.
    import shutil
    keep = {version, previous['version'] if previous else version}
    for path in root.iterdir():
        if path.is_dir() and re.fullmatch(r'v-[a-f0-9]{20}', path.name) and path.name not in keep:
            if path.resolve().parent != root.resolve():
                raise ValueError('Unsafe cleanup path')
            shutil.rmtree(path)
    print(json.dumps(manifest, ensure_ascii=False))

if __name__ == '__main__':
    main()
