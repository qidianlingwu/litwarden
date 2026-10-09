"""标识归一化与跨源去重的测试。

这一层是整个候选池的地基：
  · 归一化错了 -> 同一篇论文被当成两篇（重复条目）
  · key 不稳定 -> run 记录与 Zotero 关联全部失配
"""

from __future__ import annotations

import unittest
from datetime import datetime

from litflow.models import (
    Paper,
    dedupe,
    normalize_arxiv,
    normalize_doi,
    normalize_issn,
    normalize_journal,
    normalize_pmid,
    normalize_title,
)


class TestNormalizeDoi(unittest.TestCase):
    def test_strips_url_prefix_and_lowercases(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.1038/Nature12373"),
            "10.1038/nature12373",
        )

    def test_strips_doi_prefix_and_trailing_punctuation(self):
        # 从参考文献字符串里抠 DOI 时，尾部常带着句号
        self.assertEqual(normalize_doi("doi:10.1000/xyz."), "10.1000/xyz")
        self.assertEqual(normalize_doi("10.1000/xyz,"), "10.1000/xyz")

    def test_keeps_balanced_characters_inside_doi(self):
        # DOI 里合法出现的括号/分号不能被尾标点清理误伤
        self.assertEqual(
            normalize_doi("10.1016/S0140-6736(20)30183-5"),
            "10.1016/s0140-6736(20)30183-5",
        )

    def test_rejects_non_doi(self):
        for bad in ("", None, "not a doi", "10.1/x", "PMID: 12345"):
            self.assertIsNone(normalize_doi(bad), f"{bad!r} 不该被当成 DOI")


class TestNormalizeArxiv(unittest.TestCase):
    def test_drops_prefix_and_version(self):
        self.assertEqual(normalize_arxiv("arXiv:2211.06292v2"), "2211.06292")
        self.assertEqual(normalize_arxiv("2211.06292"), "2211.06292")

    def test_accepts_abs_url(self):
        self.assertEqual(
            normalize_arxiv("https://arxiv.org/abs/2310.09561"), "2310.09561"
        )

    def test_empty(self):
        for bad in ("", None):
            self.assertIsNone(normalize_arxiv(bad))


class TestNormalizePmid(unittest.TestCase):
    def test_keeps_digits_only(self):
        self.assertEqual(normalize_pmid("PMID: 12345678"), "12345678")
        self.assertEqual(normalize_pmid(12345678), "12345678")

    def test_empty(self):
        for bad in ("", None, "abc"):
            self.assertIsNone(normalize_pmid(bad))


class TestNormalizeIssn(unittest.TestCase):
    def test_formats_with_hyphen(self):
        self.assertEqual(normalize_issn("1474760x"), "1474-760X")
        self.assertEqual(normalize_issn("1474-760X"), "1474-760X")

    def test_rejects_wrong_length(self):
        self.assertIsNone(normalize_issn("123"))
        self.assertIsNone(normalize_issn("1474-760X1"))
        self.assertIsNone(normalize_issn(None))


class TestNormalizeTitleAndJournal(unittest.TestCase):
    def test_title_keeps_alnum_only(self):
        self.assertEqual(normalize_title("Hello, World! (2024)"), "hello world 2024")

    def test_journal_drops_common_noise_words(self):
        # 等级表里的刊名是 "Biological Chemistry" 这类短名，
        # 论文元数据常带 "The Journal of" 前缀，不清理就查不上
        self.assertEqual(
            normalize_journal("The Journal of Biological Chemistry"),
            "biological chemistry",
        )

    def test_journal_is_idempotent(self):
        once = normalize_journal("The Journal of Biological Chemistry")
        self.assertEqual(normalize_journal(once), once)


class TestPaperKey(unittest.TestCase):
    def test_priority_is_doi_pmid_arxiv_title(self):
        p = Paper(title="T", doi="10.1/a", pmid="1", arxiv_id="2211.1")
        self.assertEqual(p.key, "doi:10.1/a")
        p.doi = None
        self.assertEqual(p.key, "pmid:1")
        p.pmid = None
        self.assertEqual(p.key, "arxiv:2211.1")

    def test_title_fallback_is_stable_and_year_sensitive(self):
        a = Paper(title="Same Title Here", year=2024)
        b = Paper(title="same   title HERE", year=2024)  # 归一化后相同
        c = Paper(title="Same Title Here", year=2025)
        self.assertEqual(a.key, b.key, "标题归一化后应得到同一个 key")
        self.assertNotEqual(a.key, c.key, "年份不同应区分开")


class TestCitesPerYear(unittest.TestCase):
    def test_none_when_missing_input(self):
        self.assertIsNone(Paper(title="T", year=None, cited_by=10).cites_per_year)
        self.assertIsNone(Paper(title="T", year=2020, cited_by=None).cites_per_year)

    def test_divides_by_age_with_floor_of_one(self):
        now = datetime.now().year
        p = Paper(title="T", year=now, cited_by=7)
        # 当年发表的论文 age 为 0，必须被 max(1, ...) 兜住，否则除零
        self.assertEqual(p.cites_per_year, 7.0)

    def test_future_year_does_not_go_negative(self):
        p = Paper(title="T", year=datetime.now().year + 3, cited_by=5)
        self.assertEqual(p.cites_per_year, 5.0)


class TestMerge(unittest.TestCase):
    def _pair(self):
        a = Paper(
            title="Short title",
            doi="10.1/a",
            sources=["crossref"],
            cited_by=5,
            cited_by_source="crossref",
        )
        b = Paper(
            title="A noticeably longer title from another source",
            pmid="999",
            sources=["pubmed"],
            cited_by=9,
            cited_by_source="pubmed",
            abstract="abs",
        )
        return a, b

    def test_unions_sources_and_fills_identifiers(self):
        a, b = self._pair()
        a.merge(b)
        self.assertEqual(a.sources, ["crossref", "pubmed"])
        self.assertEqual(a.pmid, "999")
        self.assertEqual(a.doi, "10.1/a", "已有的 DOI 不该被覆盖")

    def test_keeps_larger_citation_count_and_longer_text(self):
        a, b = self._pair()
        a.merge(b)
        self.assertEqual(a.cited_by, 9)
        self.assertEqual(a.cited_by_source, "pubmed")
        self.assertEqual(a.title, "A noticeably longer title from another source")
        self.assertEqual(a.abstract, "abs")

    def test_does_not_downgrade_citations(self):
        a, b = self._pair()
        b.cited_by = 1
        a.merge(b)
        self.assertEqual(a.cited_by, 5)
        self.assertEqual(a.cited_by_source, "crossref")

    def test_oa_is_sticky_true(self):
        a = Paper(title="T", is_oa=None)
        b = Paper(title="T", is_oa=True)
        a.merge(b)
        self.assertTrue(a.is_oa)
        # 反向：已确认为 False 的一方不该被未知值覆盖
        c = Paper(title="T", is_oa=False)
        d = Paper(title="T", is_oa=None)
        c.merge(d)
        self.assertFalse(c.is_oa)


class TestDedupe(unittest.TestCase):
    def test_same_doi_collapses(self):
        ps = [
            Paper(title="Paper A", doi="10.1/x", sources=["crossref"]),
            Paper(title="Paper A", doi="10.1/x", sources=["openalex"]),
        ]
        out = dedupe(ps)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].sources, ["crossref", "openalex"])

    def test_cross_source_pairing_without_shared_identifier(self):
        """同一篇论文在两个源里各只有一种标识，第一轮抓不住，靠第二轮标题配。"""
        title = "A sufficiently long paper title for matching"
        ps = [
            Paper(title=title, doi="10.1/y", sources=["crossref"], year=2023),
            Paper(title=title, pmid="555", sources=["pubmed"], year=2023),
        ]
        out = dedupe(ps)
        self.assertEqual(len(out), 1, "标题+年份应能跨源配对")
        self.assertEqual(out[0].doi, "10.1/y")
        self.assertEqual(out[0].pmid, "555")

    def test_versioned_doi_same_title_merges(self):
        """eLife 这类版本化 DOI：字符串不同，但确实是同一篇。"""
        title = "Versioned DOI paper title long enough"
        ps = [
            Paper(title=title, doi="10.7554/elife.88431", year=2023),
            Paper(title=title, doi="10.7554/elife.88431.3", year=2023),
        ]
        self.assertEqual(len(dedupe(ps)), 1)

    def test_short_titles_are_not_merged(self):
        """标题太短不足以作为身份依据，宁可保留重复也不能误合并。"""
        ps = [
            Paper(title="Editorial", doi="10.1/a"),
            Paper(title="Editorial", doi="10.1/b"),
        ]
        self.assertEqual(len(dedupe(ps)), 2)

    def test_same_title_different_year_stays_separate(self):
        title = "Annual report on something quite specific"
        ps = [
            Paper(title=title, doi="10.1/2023", year=2023),
            Paper(title=title, doi="10.1/2019", year=2019),
        ]
        self.assertEqual(len(dedupe(ps)), 2)

    def test_preserves_input_order(self):
        ps = [
            Paper(title="First paper title here", doi="10.1/1"),
            Paper(title="Second paper title here", doi="10.1/2"),
            Paper(title="Third paper title here", doi="10.1/3"),
        ]
        self.assertEqual([p.doi for p in dedupe(ps)], ["10.1/1", "10.1/2", "10.1/3"])


if __name__ == "__main__":
    unittest.main()
