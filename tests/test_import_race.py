"""导入流程「先上传、后认亲」的回归测试。

背景（详见 docs/butler-integration.md）：
Zotero-AI-Butler 在父条目入库后 2 秒判定附件可否分析，而它的判定**只看附件
条目在不在、不查磁盘上是否真有文件**；真正提取 PDF 时才查文件路径。
于是「条目已存在、文件还没落盘」的窗口必然失败，且失败带 suppressTaskRetry，
Butler 永不重试，论文就永久没有笔记。

所以导入必须先传附件（传成没有父条目的独立条目）、再建父条目、最后认亲。
这几个测试把**调用顺序**钉死，防止将来有人图省事改回直觉顺序
（建条目 → 传附件），那样又会静默地丢掉一批笔记。
"""

import unittest
from pathlib import Path

from litwarden import librarian as L
from litwarden.config import Config
from litwarden.models import Paper


class _FakeClient:
    """只记录调用，不发请求。"""

    def __init__(self, fail_titles=()):
        self.calls: list[tuple] = []
        self._fail = set(fail_titles)
        self._n = 0

    def ensure_collection(self, name):
        self.calls.append(("ensure_collection", name))
        return "COLL0000"

    def collection_items(self, key):  # noqa: ARG002
        return []

    def list_items(self, limit=3000, **kw):  # noqa: ARG002
        return []

    def create_items(self, payload):
        out, fails = [], []
        for it in payload:
            title = it.get("title")
            if title in self._fail:
                fails.append((it, "模拟：条目类型不被接受"))
                continue
            self._n += 1
            k = f"PARENT{self._n:02d}"
            self.calls.append(("create_item", title, k))
            out.append((0, {"key": k, "data": {"key": k}}))
        return out, fails

    def upload_attachment(self, parent_key, filename, content, mime=None):  # noqa: ARG002
        self._n += 1
        ak = f"ATT{self._n:02d}"
        self.calls.append(("upload_orphan", ak, parent_key))
        return ak

    def set_parent(self, att_key, parent_key):
        self.calls.append(("set_parent", att_key, parent_key))

    def delete_items(self, keys):
        self.calls.append(("delete_items", tuple(keys)))
        return len(keys)


class _FakeStore:
    def __init__(self):
        self.link_calls: list[tuple] = []

    def zotero_links(self):
        return {}

    def link_zotero(self, *a, **kw):
        self.link_calls.append((a, kw))


def _cfg():
    return Config(root=Path("/tmp"), data_dir=Path("/tmp"))


class TestOrphanFirstOrder(unittest.TestCase):
    def setUp(self):
        # 预下载替换成假字节，测试不碰网络
        self._orig = L.prefetch_pdfs
        L.prefetch_pdfs = lambda store, papers, http, email, verbose=True: {
            p.key: ("fake.pdf", b"%PDF-1.4 fake") for p in papers
        }

    def tearDown(self):
        L.prefetch_pdfs = self._orig

    def _run(self, papers, client=None, attach=True):
        client = client or _FakeClient()
        store = _FakeStore()
        res = L.import_papers(
            _cfg(), store, client, papers, L.ImportOptions(attach_pdf=attach)
        )
        return client, store, res

    @staticmethod
    def _names(calls):
        return [c[0] for c in calls]

    def test_order_is_upload_then_create_then_reparent(self):
        p = Paper(title="Paper A", doi="10.1/a", oa_url="https://x/a.pdf")
        c, _, _ = self._run([p])
        names = self._names(c.calls)
        self.assertIn("upload_orphan", names)
        self.assertLess(names.index("upload_orphan"), names.index("create_item"))
        self.assertLess(names.index("create_item"), names.index("set_parent"))

    def test_orphan_is_uploaded_without_parent(self):
        """附件必须先建成**独立**条目：独立附件的 add 事件 Butler 不处理，
        所以这个中间态不会误触发扫描。"""
        p = Paper(title="Paper A", doi="10.1/a", oa_url="https://x/a.pdf")
        c, _, _ = self._run([p])
        _, _att, parent_arg = next(x for x in c.calls if x[0] == "upload_orphan")
        self.assertIsNone(parent_arg)

    def test_reparent_uses_the_created_parent(self):
        p = Paper(title="Paper A", doi="10.1/a", oa_url="https://x/a.pdf")
        c, store, res = self._run([p])
        up = next(x for x in c.calls if x[0] == "upload_orphan")
        cr = next(x for x in c.calls if x[0] == "create_item")
        _, att, parent = next(x for x in c.calls if x[0] == "set_parent")
        self.assertEqual(att, up[1])
        self.assertEqual(parent, cr[2])
        self.assertEqual(res.attached, 1)
        self.assertEqual(store.link_calls[0][1].get("attached_pdf"), 1)

    def test_failed_parent_cleans_up_orphan(self):
        """父条目没建成时不能在库里留下孤儿附件。"""
        p = Paper(title="Paper B", doi="10.1/b", oa_url="https://x/b.pdf")
        c, _, res = self._run([p], client=_FakeClient(fail_titles={"Paper B"}))
        self.assertIn("delete_items", self._names(c.calls))
        self.assertEqual(res.attached, 0)
        self.assertEqual(len(res.failed), 1)

    def test_no_oa_url_means_no_attachment(self):
        p = Paper(title="Paper C", doi="10.1/c")
        c, _, res = self._run([p])
        self.assertNotIn("upload_orphan", self._names(c.calls))
        self.assertEqual(res.attached, 0)
        self.assertEqual(len(res.created), 1)

    def test_attach_pdf_off_means_no_attachment(self):
        p = Paper(title="Paper D", doi="10.1/d", oa_url="https://x/d.pdf")
        c, _, _ = self._run([p], attach=False)
        self.assertNotIn("upload_orphan", self._names(c.calls))


if __name__ == "__main__":
    unittest.main()
