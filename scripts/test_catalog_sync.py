import importlib.util, json, tempfile, unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('sync_catalog', Path(__file__).with_name('sync_catalog.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

class CatalogSyncTests(unittest.TestCase):
    def test_editions_do_not_merge_on_title_and_curated_mappings_are_stable(self):
        raw = b'Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n1,Text,2000-01-01,Same title,en,Author,Novel,P,Category: Novels\n2,Text,2000-01-01,Same title,fr,Author,Novel,P,Category: Novels\n3,Sound,2000-01-01,Audio,en,Author,,,\n'
        records = sync.from_csv(raw)
        self.assertEqual(len(records), 2)
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            totals = sync.build(records, output)
            rows = json.loads((output / 'editions/0.json').read_text('utf-8'))
            self.assertNotEqual(rows['1']['canonicalId'], rows['2']['canonicalId'])
            stable_id = rows['1']['canonicalId']
            records[1]['title'] = 'Corrected title'
            sync.build(records, output)
            rows = json.loads((output / 'editions/0.json').read_text('utf-8'))
            self.assertEqual(rows['1']['canonicalId'], stable_id)
            self.assertNotEqual(rows['1']['metadataHash'], rows['2']['metadataHash'])
            totals = sync.build(records, output, {'gutenberg-2': stable_id})
            self.assertEqual(totals['canonicalBooks'], 1)
            self.assertEqual(totals['totalBooks'], 2)
    def test_failed_build_keeps_active_manifest(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            metadata = Path(root) / 'metadata'
            metadata.mkdir()
            active = {'schemaVersion': 1, 'version': 'v-' + 'a' * 20, 'totalBooks': 1000}
            manifest = metadata / 'manifest.json'
            manifest.write_text(json.dumps(active), encoding='utf-8')
            csv_path = Path(root) / 'catalog.csv'
            csv_path.write_text('Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n' + ''.join(f'{i},Text,2000-01-01,Book {i},en,Author,,,\n' for i in range(1, 1001)))
            with patch('sys.argv', ['sync', '--root', root, '--csv', str(csv_path)]), patch.object(sync, 'build', side_effect=RuntimeError('interrupted')):
                with self.assertRaises(RuntimeError):
                    sync.main()
            self.assertEqual(json.loads(manifest.read_text()), active)

    def test_rejects_changed_schema(self):
        with self.assertRaises(ValueError):
            sync.from_csv(b'id,name\n1,test\n')
    def test_rdf_preserves_languages_rights_and_creator(self):
        xml = '''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:pgterms="http://www.gutenberg.org/2009/pgterms/" xmlns:dcterms="http://purl.org/dc/terms/"><pgterms:ebook rdf:about="ebooks/1342"><dcterms:title>Pride and Prejudice</dcterms:title><dcterms:creator><pgterms:agent><pgterms:name>Austen, Jane</pgterms:name></pgterms:agent></dcterms:creator><dcterms:language><rdf:Description><rdf:value>en</rdf:value></rdf:Description></dcterms:language><dcterms:rights>Public domain in the USA.</dcterms:rights></pgterms:ebook></rdf:RDF>'''
        number, record = sync.from_rdf(xml.encode())
        self.assertEqual(number, 1342)
        self.assertEqual(record['languages'], ['en'])
        self.assertEqual(record['authors'], ['Austen, Jane'])
        self.assertIn('Public domain', record['rights'])

if __name__ == '__main__':
    unittest.main()
