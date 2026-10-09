"""期刊等级表解析与查表的测试。

这块的特殊性：数据来自用户自行下载的 CSV，格式五花八门
（Scimago 用分号 + 表头拆两行，中科院分区表列名是中文），
所以解析必须足够宽容，而「同一期刊多行取更优分区」的规则必须正确。
"""

from __future__ import annotations

import unittest

from litflow.journalrank import (
    RANK_ORDER,
    _map_rows,
    _pick,
    _sniff_rows,
    normalize_rank,
    pick_best_rows,
    rank_index,
    rank_ok,
)


class TestNormalizeRank(unittest.TestCase):
    def test_sjr_variants(self):
        for raw in ("Q1", "q1", " Q 1 ", "Q1 "):
            self.assertEqual(normalize_rank("sjr", raw), "Q1", raw)

    def test_sjr_rejects_out_of_range(self):
        self.assertIsNone(normalize_rank("sjr", "Q5"))
        self.assertIsNone(normalize_rank("sjr", "A"))
        self.assertIsNone(normalize_rank("sjr", None))

    def test_ccf_variants(self):
        for raw in ("A", "a", "CCF-A", "A类"):
            self.assertEqual(normalize_rank("ccf", raw), "A", raw)

    def test_cas_variants(self):
        for raw in ("1区", "一区", "大类1区", "1"):
            self.assertEqual(normalize_rank("cas", raw), "1区", raw)

    def test_cas_chinese_digits(self):
        self.assertEqual(normalize_rank("cas", "二区"), "2区")
        self.assertEqual(normalize_rank("cas", "三区"), "3区")


class TestRankIndex(unittest.TestCase):
    def test_order_is_best_first(self):
        self.assertEqual(rank_index("sjr", "Q1"), 0)
        self.assertEqual(rank_index("sjr", "Q4"), 3)
        self.assertEqual(rank_index("ccf", "A"), 0)
        self.assertEqual(rank_index("cas", "1区"), 0)

    def test_unknown_system_or_value(self):
        self.assertIsNone(rank_index("nope", "Q1"))
        self.assertIsNone(rank_index("sjr", None))
        self.assertIsNone(rank_index("sjr", "garbage"))

    def test_rank_order_table_is_consistent(self):
        for system, order in RANK_ORDER.items():
            for i, label in enumerate(order):
                self.assertEqual(rank_index(system, label), i)


class TestRankOk(unittest.TestCase):
    def test_no_minimum_always_passes(self):
        self.assertTrue(rank_ok("sjr", None, None))
        self.assertTrue(rank_ok("sjr", "Q4", None))

    def test_meets_or_beats_threshold(self):
        self.assertTrue(rank_ok("sjr", "Q1", "Q2"))
        self.assertTrue(rank_ok("sjr", "Q2", "Q2"))
        self.assertFalse(rank_ok("sjr", "Q3", "Q2"))

    def test_unknown_value_fails_when_threshold_set(self):
        self.assertFalse(rank_ok("sjr", None, "Q2"))


class TestPickColumn(unittest.TestCase):
    def test_prefers_exact_match_over_substring(self):
        row = {"best quartile": "Q1", "q": "should-not-win"}
        self.assertEqual(_pick(row, "q", "best quartile"), "should-not-win")

    def test_falls_back_to_substring_case_insensitively(self):
        row = {"Best Quartile": "Q2"}
        self.assertEqual(_pick(row, "quartile"), "Q2")

    def test_skips_empty_values(self):
        row = {"quartile": "", "sjr": "1.5"}
        self.assertEqual(_pick(row, "quartile", "sjr"), "1.5")

    def test_returns_none_when_absent(self):
        self.assertIsNone(_pick({"a": "1"}, "zzz"))


SJR_SAMPLE = """\
;Scimago Journal Rank 2025
;Source: https://www.scimagojr.com
Rank;Title;Type;Issn;SJR;Best Quartile;H index
1;Genome Biology;journal;1474-760X;5.400;Q1;200
2;Some Obscure Journal;journal;1234-5678;0.100;Q4;3
3;No Issn Journal;journal;;0.900;Q2;10
"""


class TestSniffRows(unittest.TestCase):
    def setUp(self):
        self.rows = _sniff_rows(SJR_SAMPLE)

    def test_skips_preamble_and_finds_real_header(self):
        self.assertEqual(len(self.rows), 3, "前两行注释不该被当表头")

    def test_detects_semicolon_delimiter(self):
        self.assertEqual(self.rows[0]["Title"], "Genome Biology")

    def test_strips_whitespace_in_keys_and_values(self):
        for k in self.rows[0]:
            self.assertEqual(k, k.strip())


class TestMapRows(unittest.TestCase):
    def test_writes_both_issn_and_title_keys(self):
        """预印本常常没有 ISSN，只写 ISSN 会漏查大量论文。"""
        rows = _map_rows(
            "sjr",
            {"Title": "Genome Biology", "Issn": "1474-760X", "Best Quartile": "Q1"},
        )
        kinds = {r["match_type"] for r in rows}
        self.assertEqual(kinds, {"issn", "title"})
        by_type = {r["match_type"]: r for r in rows}
        self.assertEqual(by_type["issn"]["match_key"], "1474-760X")
        self.assertEqual(by_type["title"]["match_key"], "genome biology")
        for r in rows:
            self.assertEqual(r["quartile"], "Q1")
            self.assertEqual(r["system"], "sjr")

    def test_no_issn_still_yields_title_row(self):
        rows = _map_rows("sjr", {"Title": "Only Title Journal", "Best Quartile": "Q2"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["match_type"], "title")

    def test_drops_row_without_quartile(self):
        self.assertEqual(_map_rows("sjr", {"Title": "J", "Best Quartile": ""}), [])

    def test_drops_row_without_title(self):
        self.assertEqual(_map_rows("sjr", {"Issn": "1234-5678", "Best Quartile": "Q1"}), [])


class TestPickBestRows(unittest.TestCase):
    def _row(self, key, mtype, q):
        return {
            "system": "sjr",
            "match_key": key,
            "match_type": mtype,
            "quartile": q,
            "metric": "sjr",
            "value": "",
            "journal": "J",
        }

    def test_keeps_best_quartile_when_journal_appears_multiple_times(self):
        """Scimago 里同一期刊会出现在多个学科分类下，分区可能不同。

        必须保留**更优**的那个，否则按 Q1 筛选会漏掉期刊。
        """
        rows = [
            self._row("j", "title", "Q2"),
            self._row("j", "title", "Q1"),
        ]
        best = pick_best_rows(rows, "sjr")
        self.assertEqual(len(best), 1)
        self.assertEqual(best[0]["quartile"], "Q1")

    def test_q1_first_then_worse_must_not_downgrade(self):
        """Q1 的 rank_index 是 0，用 `or 99` 兜底会把它误判成最差。"""
        rows = [
            self._row("j", "title", "Q1"),
            self._row("j", "title", "Q4"),
        ]
        best = pick_best_rows(rows, "sjr")
        self.assertEqual(best[0]["quartile"], "Q1")

    def test_keeps_separate_keys_independent(self):
        rows = [
            self._row("issn:1", "issn", "Q3"),
            self._row("title:j", "title", "Q1"),
        ]
        self.assertEqual(len(pick_best_rows(rows, "sjr")), 2)

    def test_cas_uses_its_own_ordering(self):
        rows = [
            self._row("j", "title", "3区"),
            self._row("j", "title", "1区"),
        ]
        self.assertEqual(pick_best_rows(rows, "cas")[0]["quartile"], "1区")

    def test_empty_input(self):
        self.assertEqual(pick_best_rows([], "sjr"), [])


if __name__ == "__main__":
    unittest.main()
