"""筛选、打分与排序的测试。

这是用户每天真正在调的旋钮，行为错了会直接影响选出来的文献质量。
"""

from __future__ import annotations

import unittest

from litflow.filterer import (
    Bm25,
    Filters,
    apply,
    is_correction,
    is_preprint,
    passes,
    score_keywords,
    sort_papers,
    tokenize,
)
from litflow.models import Paper


class TestTokenize(unittest.TestCase):
    def test_splits_on_non_alnum_and_lowercases(self):
        self.assertEqual(
            tokenize("Single-cell RNA sequencing"),
            ["single", "cell", "rna", "sequencing"],
        )

    def test_drops_stopwords_and_single_chars(self):
        self.assertEqual(tokenize("The a of in"), [])

    def test_handles_none_and_empty(self):
        self.assertEqual(tokenize(None), [])
        self.assertEqual(tokenize(""), [])


class TestBm25(unittest.TestCase):
    def setUp(self):
        self.docs = [
            "spatial transcriptomics deconvolution methods",
            "unrelated topic about protein folding",
        ]

    def test_relevant_doc_scores_higher(self):
        s = Bm25(self.docs).scores("deconvolution")
        self.assertGreater(s[0], s[1])

    def test_non_matching_query_scores_zero(self):
        s = Bm25(self.docs).scores("zzzz")
        self.assertEqual(s, [0.0, 0.0])

    def test_empty_query_scores_zero(self):
        s = Bm25(self.docs).scores("")
        self.assertEqual(s, [0.0, 0.0])

    def test_empty_corpus_does_not_crash(self):
        self.assertEqual(Bm25([]).scores("anything"), [])

    def test_length_normalisation_prefers_shorter_doc(self):
        """同样命中一次，短文档应得分更高（b 参数的作用）。"""
        docs = ["deconvolution", "deconvolution " + "filler " * 200]
        s = Bm25(docs).scores("deconvolution")
        self.assertGreater(s[0], s[1])


class TestScoreKeywords(unittest.TestCase):
    def test_normalises_top_score_to_one(self):
        ps = [
            Paper(title="Spatial transcriptomics deconvolution"),
            Paper(title="Something else entirely"),
        ]
        scores = score_keywords(ps, "spatial transcriptomics deconvolution")
        self.assertEqual(scores[0], 1.0)
        self.assertLess(scores[1], 1.0)

    def test_all_zero_when_nothing_matches(self):
        ps = [Paper(title="Alpha beta"), Paper(title="Gamma delta")]
        self.assertEqual(score_keywords(ps, "zzzzz"), [0.0, 0.0])

    def test_title_weighted_above_abstract(self):
        """标题命中应比摘要命中得分高（实现里整篇标题计入 3 次）。

        构造两篇**等长**文档来隔离变量：只有「命中出现在标题还是摘要」不同。
        """
        ps = [
            Paper(title="deconvolution x", abstract="y y y y"),
            Paper(title="x y", abstract="deconvolution y y y y"),
        ]
        scores = score_keywords(ps, "deconvolution")
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(scores[0], 1.0)


class TestCorrectionAndPreprint(unittest.TestCase):
    def test_detects_correction_records(self):
        for t in (
            "Correction to: STCGAN: a novel cycle-consistent GAN",
            "Author Correction: Deep learning",
            "Erratum: something",
            "Corrigendum to something",
            "Withdrawn: a paper",
        ):
            self.assertTrue(is_correction(Paper(title=t)), t)

    def test_normal_paper_is_not_correction(self):
        self.assertFalse(is_correction(Paper(title="A normal paper about cells")))
        self.assertFalse(is_correction(Paper(title=None)))

    def test_preprint_detection_sources(self):
        self.assertTrue(is_preprint(Paper(title="T", arxiv_id="2211.1")))
        self.assertTrue(is_preprint(Paper(title="T", extra={"is_preprint": True})))
        self.assertTrue(is_preprint(Paper(title="T", type="preprint")))
        self.assertTrue(is_preprint(Paper(title="T", journal="bioRxiv")))
        self.assertTrue(is_preprint(Paper(title="T", journal="medRxiv")))
        self.assertFalse(is_preprint(Paper(title="T", journal="Nature")))


class TestPasses(unittest.TestCase):
    def _p(self, **kw):
        base = dict(title="Spatial deconvolution method", year=2024, cited_by=10)
        base.update(kw)
        return Paper(**base)

    def test_year_bounds(self):
        self.assertFalse(passes(self._p(year=2019), Filters(year_from=2020))[0])
        self.assertFalse(passes(self._p(year=2025), Filters(year_to=2024))[0])
        self.assertTrue(passes(self._p(year=2024), Filters(year_from=2020))[0])

    def test_min_cites_treats_missing_as_zero(self):
        ok, why = passes(self._p(cited_by=None), Filters(min_cites=1))
        self.assertFalse(ok)
        self.assertIn("引用", why)

    def test_min_cites_per_year_rejects_unknown_citation_count(self):
        """引用量缺失是 None 而不是 0，不能当成「0 引用」悄悄放过。"""
        ok, _ = passes(
            self._p(cited_by=None), Filters(min_cites_per_year=1.0)
        )
        self.assertFalse(ok)

    def test_title_must_only_looks_at_title(self):
        f = Filters(title_must=["deconvolution"])
        self.assertTrue(passes(self._p(title="Spatial deconvolution method"), f)[0])
        # 词只在摘要里，title_must 不该放行
        self.assertFalse(
            passes(self._p(title="A study", abstract="deconvolution"), f)[0]
        )

    def test_must_include_looks_at_title_or_abstract(self):
        f = Filters(must_include=["deconvolution"])
        self.assertTrue(passes(self._p(title="A study", abstract="deconvolution"), f)[0])

    def test_exclude_terms(self):
        f = Filters(exclude_terms=["review"])
        self.assertFalse(passes(self._p(title="A review of methods"), f)[0])

    def test_exclude_corrections_switch(self):
        p = self._p(title="Correction to: something")
        self.assertTrue(passes(p, Filters())[0], "默认不剔除勘误")
        self.assertFalse(passes(p, Filters(exclude_corrections=True))[0])

    def test_from_sources_requires_intersection(self):
        p = self._p(sources=["crossref"])
        self.assertTrue(passes(p, Filters(from_sources=["crossref"]))[0])
        self.assertFalse(passes(p, Filters(from_sources=["arxiv"]))[0])

    def test_min_keyword_requires_score_first(self):
        p = self._p()
        ok, why = passes(p, Filters(min_keyword=0.5))
        self.assertFalse(ok)
        self.assertIn("关键词匹配度", why)
        p.keyword_score = 0.9
        self.assertTrue(passes(p, Filters(min_keyword=0.5))[0])

    def test_oa_and_doi_requirements(self):
        self.assertFalse(passes(self._p(is_oa=False), Filters(oa_only=True))[0])
        self.assertTrue(passes(self._p(is_oa=True), Filters(oa_only=True))[0])
        self.assertFalse(passes(self._p(doi=None), Filters(require_doi=True))[0])
        self.assertTrue(passes(self._p(doi="10.1/a"), Filters(require_doi=True))[0])

    def test_rank_gate_and_allow_unknown(self):
        p = self._p(journal_rank={"sjr": "Q2"})
        self.assertTrue(passes(p, Filters(rank_min={"sjr": "Q2"}))[0])
        self.assertFalse(passes(p, Filters(rank_min={"sjr": "Q1"}))[0])

        unknown = self._p(journal_rank={})
        self.assertFalse(passes(unknown, Filters(rank_min={"sjr": "Q2"}))[0])
        self.assertTrue(
            passes(unknown, Filters(rank_min={"sjr": "Q2"}, rank_allow_unknown=True))[0]
        )


class TestApply(unittest.TestCase):
    def setUp(self):
        self.papers = [
            Paper(title="Alpha deconvolution", doi="10.1/a", year=2024, cited_by=100),
            Paper(title="Beta analysis", doi="10.1/b", year=2018, cited_by=5),
            Paper(title="Gamma deconvolution", doi="10.1/c", year=2024, cited_by=50),
        ]

    def test_keeps_and_drops_are_reported(self):
        res = apply(self.papers, Filters(year_from=2020))
        self.assertEqual(len(res.kept), 2)
        self.assertEqual(len(res.dropped), 1)
        self.assertTrue(res.dropped[0][1], "剔除项必须带原因，界面要展示")

    def test_sort_by_cites_descending(self):
        res = apply(self.papers, Filters(), sort_by="cites")
        self.assertEqual([p.cited_by for p in res.kept], [100, 50, 5])

    def test_top_limits_result(self):
        res = apply(self.papers, Filters(), sort_by="cites", top=2)
        self.assertEqual(len(res.kept), 2)
        self.assertEqual(res.kept[0].cited_by, 100)

    def test_keyword_scores_are_attached(self):
        res = apply(
            self.papers,
            Filters(),
            query_text="deconvolution",
            sort_by="keyword",
        )
        self.assertEqual(res.kept[0].keyword_score, 1.0)
        self.assertEqual(len(res.kept), 3)

    def test_invalid_sort_key_is_rejected(self):
        with self.assertRaises(ValueError):
            apply(self.papers, Filters(), sort_by="nonsense")


class TestSortPapers(unittest.TestCase):
    def test_year_sort_breaks_ties_by_citations(self):
        ps = [
            Paper(title="A", year=2024, cited_by=1),
            Paper(title="B", year=2024, cited_by=9),
            Paper(title="C", year=2020, cited_by=99),
        ]
        out = sort_papers(ps, "year", [])
        self.assertEqual([p.title for p in out], ["B", "A", "C"])

    def test_title_sort_is_case_insensitive(self):
        ps = [Paper(title="beta"), Paper(title="Alpha")]
        self.assertEqual([p.title for p in sort_papers(ps, "title", [])], ["Alpha", "beta"])

    def test_missing_citations_sort_last(self):
        ps = [Paper(title="A", cited_by=None), Paper(title="B", cited_by=3)]
        out = sort_papers(ps, "cites", [])
        self.assertEqual(out[0].title, "B")


if __name__ == "__main__":
    unittest.main()
