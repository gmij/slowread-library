# Slowread 内容仓库

独立于应用保存书目、原文、章节和中文译文。当前只包含《傲慢与偏见》第一章完整试读，不冒充全书。

- `catalog.json`：书目、来源与可读章节。
- `books/<id>/original.txt`：原作正文。
- `books/<id>/chapters/<chapter>.json`：带稳定段落ID的原文。
- `books/<id>/translations/zh-CN/<chapter>.json`：对应译文。

原文和译文必须使用相同sourceHash、相同段落顺序及ID。内容更新先审核，再发布；书目中未准备的章节必须标记不可读。

来源：https://www.gutenberg.org/ebooks/1342 。仅提取Jane Austen原作第一章，排除现代序言与插画。中文是本次AI辅助新译，尚未专业校订。美国公有领域标记不能替代所有目标地区的权利核对。

不要提交用户隐私文件、访问凭据或无权分享的现代译本。仓库默认私有，Worker通过仅此仓库的只读凭据访问。
