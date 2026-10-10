"""SQLite 持久化层。

用临时库跑，不碰真实数据。

重点覆盖 `zotero_links.attached_pdf` 的合并语义 —— 这一处出过一次真实故障：
MAX 的高水位语义让「附件已在 Zotero 侧被删」的条目永远回退不到 0，而
attach-pdf 又靠这个标记决定跳过，导致那些论文再也补不到 PDF。
（实测 5 篇测试文献里有 3 篇受影响。）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from litwarden.models import Paper
from litwarden.store import Store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class TestPaperRoundTrip(StoreTestCase):
    def test_upsert_and_read_back(self):
        p = Paper(title="A paper", doi="10.1038/s41586-021-01358-2", year=2021,
                  cited_by=10, cited_by_source="crossref",
                  cited_by_fetched_at="2026-01-01T00:00:00+00:00",
                  authors=["Smith J"], sources=["crossref"], is_oa=True)
        self.assertEqual(self.store.upsert_papers([p]), 1)
        back = self.store.get_papers([p.key])[0]
        self.assertEqual(back.doi, p.doi)
        self.assertEqual(back.authors, ["Smith J"])
        self.assertEqual(back.cited_by, 10)
        self.assertEqual(back.cited_by_source, "crossref")
        self.assertEqual(back.cited_by_fetched_at, "2026-01-01T00:00:00+00:00")
        self.assertTrue(back.is_oa)
        self.assertEqual(back.sources, ["crossref"])

    def test_upsert_is_idempotent(self):
        p = Paper(title="A paper", doi="10.1038/abc")
        self.store.upsert_papers([p])
        self.store.upsert_papers([p])
        self.assertEqual(self.store.count_papers(), 1)

    def test_empty_input(self):
        self.assertEqual(self.store.upsert_papers([]), 0)


class TestRunPapers(StoreTestCase):
    def _mk_run(self):
        p = Paper(title="A paper", doi="10.1038/abc")
        self.store.upsert_papers([p])
        rid = self.store.create_run("test", {"query": {"text": "x"}})
        self.store.attach_run_papers(rid, [
            {"run_id": rid, "paper_key": p.key, "keyword_score": 0.42,
             "rank": 1, "decision": "include", "note": None},
        ])
        return rid, p

    def test_attach_writes_keyword_score(self):
        """回归：纯 search 的 run 也必须把分数写进去（曾经恒为 NULL）。"""
        rid, _ = self._mk_run()
        rows = self.store.get_run_papers(rid)
        self.assertEqual(rows[0]["keyword_score"], 0.42)

    def test_attach_is_upsert(self):
        rid, p = self._mk_run()
        self.store.attach_run_papers(rid, [
            {"run_id": rid, "paper_key": p.key, "keyword_score": 0.9,
             "rank": 1, "decision": "exclude", "note": "n"},
        ])
        rows = self.store.get_run_papers(rid)
        self.assertEqual(len(rows), 1, "同一 run 内同一论文不该出现两行")
        self.assertEqual(rows[0]["decision"], "exclude")
        self.assertEqual(rows[0]["keyword_score"], 0.9)

    def test_get_run_parses_json_columns(self):
        rid, _ = self._mk_run()
        run = self.store.get_run(rid)
        self.assertIsInstance(run["recipe"], dict)
        self.assertEqual(run["recipe"]["query"]["text"], "x")


class TestAttachedPdfSemantics(StoreTestCase):
    """attached_pdf 的合并规则 —— 这一处出过真实故障。"""

    def test_same_item_keeps_high_water_mark(self):
        """同一个 Zotero 条目：导入路径传 0 不该抹掉已知的 1。"""
        self.store.link_zotero("doi:x", "ITEM1", attached_pdf=1)
        self.store.link_zotero("doi:x", "ITEM1", attached_pdf=0)
        self.assertEqual(self.store.get_zotero_link("doi:x")["attached_pdf"], 1)

    def test_new_item_resets_the_flag(self):
        """zotero_key 变了说明是重新导入到新条目，旧标记属于另一个条目，必须重置。"""
        self.store.link_zotero("doi:x", "ITEM1", attached_pdf=1)
        self.store.link_zotero("doi:x", "ITEM2", attached_pdf=0)
        link = self.store.get_zotero_link("doi:x")
        self.assertEqual(link["zotero_key"], "ITEM2")
        self.assertEqual(link["attached_pdf"], 0,
                         "换了条目还沿用旧条目的「已附 PDF」就是脏数据")

    def test_set_attached_pdf_forces_value(self):
        """体检修正走这个入口：必须能覆盖 MAX 的结果，否则漂移永远修不回来。"""
        self.store.link_zotero("doi:x", "ITEM1", attached_pdf=1)
        self.store.set_attached_pdf("doi:x", False)
        self.assertEqual(self.store.get_zotero_link("doi:x")["attached_pdf"], 0)

    def test_set_attached_pdf_can_set_true(self):
        self.store.link_zotero("doi:x", "ITEM1", attached_pdf=0)
        self.store.set_attached_pdf("doi:x", True)
        self.assertEqual(self.store.get_zotero_link("doi:x")["attached_pdf"], 1)

    def test_status_and_collection_are_preserved_when_omitted(self):
        self.store.link_zotero("doi:x", "ITEM1", collection="C1", status="未读")
        self.store.link_zotero("doi:x", "ITEM1", attached_pdf=1)
        link = self.store.get_zotero_link("doi:x")
        self.assertEqual(link["collection"], "C1")
        self.assertEqual(link["status"], "未读")

    def test_missing_link_returns_none(self):
        self.assertIsNone(self.store.get_zotero_link("doi:nope"))


class TestJournalRank(StoreTestCase):
    def _row(self, key, mtype="title"):
        return {"system": "sjr", "match_key": key, "match_type": mtype,
                "quartile": "Q1", "metric": "sjr", "value": "5.4",
                "journal": "Genome Biology"}

    def test_replace_and_query(self):
        self.assertEqual(self.store.replace_journal_rank(
            "sjr", [self._row("genome biology"), self._row("1474-760X", "issn")]), 2)
        self.assertEqual(self.store.journal_rank_stats().get("sjr"), 2)

    def test_replace_is_not_append(self):
        self.store.replace_journal_rank("sjr", [self._row("a"), self._row("b")])
        self.store.replace_journal_rank("sjr", [self._row("c")])
        self.assertEqual(self.store.journal_rank_stats().get("sjr"), 1,
                         "重新导入应替换而不是累加")


if __name__ == "__main__":
    unittest.main()
