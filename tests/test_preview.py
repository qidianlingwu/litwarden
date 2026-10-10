"""摘要速览（abstract_preview）的回归测试。

背景：用户要在「导入之前」就能判断一篇文献值不值得读。原先摘要只出现在导出表的
最后一列（中位 1293 字符），在 Web 论文表里根本没有——所以真正读文献只能等导入
Zotero 之后。本模块提供「首句 + 末两句」的速览，让摘要可扫。

这里守的是三类容易错的地方：
  1. 句切分不能把常见缩写、小数、单字母缩写当成句末；
  2. 长度必须有硬上限（速览要塞进表格单元格）；
  3. 空摘要必须稳定返回空串，由界面层决定怎么标注。
"""

import unittest

from litwarden.models import Paper
from litwarden.preview import abstract_preview, split_sentences

EN = (
    "Reconstructing population dynamics from unlabeled samples is hard. "
    "We introduce VeloTree, a method based on varifold distances. "
    "It was compared against prior work by Chen et al. in 2023. "
    "VeloTree outperformed baselines by 3.5 points on all benchmarks. "
    "Our results suggest a robust route to trajectory inference."
)
ZH = (
    "从无标注样本重建群体动力学是一项难题。"
    "本文提出 VeloTree，一种基于变分流形距离的方法。"
    "我们在多个基准上与既有方法做了对比。"
    "VeloTree 在所有基准上平均提升 3.5 个点。"
    "结果表明这是一条稳健的轨迹推断路径。"
)


class TestSplitSentences(unittest.TestCase):
    def test_splits_english_on_period(self):
        self.assertEqual(len(split_sentences(EN)), 5)

    def test_splits_chinese_on_ideographic_marks(self):
        self.assertEqual(len(split_sentences(ZH)), 5)

    def test_splits_on_question_and_exclamation(self):
        self.assertEqual(len(split_sentences("第一句？第二句！第三句。")), 3)

    def test_abbreviation_et_al_is_not_a_boundary(self):
        """"Chen et al. in 2023" 里的句点不是句末。"""
        s = split_sentences("It was compared by Chen et al. in 2023.")
        self.assertEqual(len(s), 1)

    def test_latin_abbreviations_are_not_boundaries(self):
        for text in ("See e.g. the survey.", "That is i.e. the same thing.",
                     "A vs. B comparison."):
            with self.subTest(text=text):
                self.assertEqual(len(split_sentences(text)), 1)

    def test_decimal_number_is_not_a_boundary(self):
        s = split_sentences("The gain was 3.5 points overall.")
        self.assertEqual(len(s), 1)

    def test_single_letter_initial_is_not_a_boundary(self):
        s = split_sentences("Written by J. Smith and A. Doe today.")
        self.assertEqual(len(s), 1)

    def test_dotted_acronym_is_not_a_boundary(self):
        s = split_sentences("A study in the U.S. found the same effect.")
        self.assertEqual(len(s), 1)

    def test_consecutive_terminators_do_not_make_empty_sentences(self):
        s = split_sentences("Really?! Yes!!! Fine.")
        self.assertEqual(len(s), 3)
        self.assertTrue(all(x.strip() for x in s))

    def test_empty_and_none_return_no_sentences(self):
        for text in (None, "", "   ", "\n\t"):
            with self.subTest(text=repr(text)):
                self.assertEqual(split_sentences(text), [])

    def test_whitespace_is_collapsed(self):
        s = split_sentences("First  part.\n\nSecond part.")
        self.assertEqual(s, ["First part.", "Second part."])


class TestAbstractPreview(unittest.TestCase):
    def test_keeps_first_sentence_and_last_two(self):
        p = abstract_preview(EN)
        self.assertTrue(p.startswith("Reconstructing population dynamics"))
        self.assertIn("VeloTree outperformed baselines", p)
        self.assertIn("Our results suggest", p)
        # 中间那句（方法/对比）应当被略去，否则就不叫速览了
        self.assertNotIn("varifold distances", p)

    def test_works_for_chinese(self):
        p = abstract_preview(ZH)
        self.assertTrue(p.startswith("从无标注样本重建群体动力学"))
        self.assertIn("结果表明", p)

    def test_three_or_fewer_sentences_are_kept_whole(self):
        text = "One. Two. Three."
        self.assertEqual(abstract_preview(text), "One. Two. Three.")

    def test_single_sentence_is_returned_as_is(self):
        self.assertEqual(abstract_preview("Only one sentence here."),
                         "Only one sentence here.")

    def test_empty_input_returns_empty_string(self):
        """空摘要返回空串 —— 由界面层决定显示「无摘要」。"""
        for text in (None, "", "   "):
            with self.subTest(text=repr(text)):
                self.assertEqual(abstract_preview(text), "")

    def test_result_never_exceeds_limit(self):
        long_text = " ".join(f"Sentence number {i} is here." for i in range(60))
        for limit in (40, 80, 160, 220):
            with self.subTest(limit=limit):
                self.assertLessEqual(len(abstract_preview(long_text, limit)), limit)

    def test_long_conclusion_wins_the_budget(self):
        """末两句几乎吃掉整个上限时，丢掉背景句、保住结论。

        这是拿真实摘要跑出来的教训：原先把余量给尾部的写法，会把结论从
        中间截断（实测截成 "...within the peripheral im…"），
        而结论恰恰是筛选时最该看的部分。
        """
        text = ("A background sentence sits here first of all. "
                "Middle sentence number one. Middle sentence number two. "
                "The proposed method improves every reported metric. "
                "We therefore conclude that the effect is robust and reproducible.")
        out = abstract_preview(text, 140)
        self.assertLessEqual(len(out), 140)
        self.assertTrue(out.endswith("robust and reproducible."))
        self.assertNotIn("background sentence", out)

    def test_conclusion_survives_when_it_fits_the_limit(self):
        """结论本身没超上限时，任何情况下都不许截断它。"""
        for total in (60, 100, 140, 180):
            with self.subTest(tail_len=total):
                pad = "pad " * max(1, (total - 40) // 4)
                text = ("Background first sentence is here. M one. M two. "
                        "Result sentence with numbers. " + pad +
                        "Final conclusion is intact.")
                out = abstract_preview(text, 240)
                self.assertLessEqual(len(out), 240)
                self.assertTrue(out.endswith("Final conclusion is intact."))

    def test_very_long_single_sentence_is_clipped_with_ellipsis(self):
        text = "A" * 500
        out = abstract_preview(text, 60)
        self.assertLessEqual(len(out), 60)
        self.assertTrue(out.endswith("…"))

    def test_short_input_is_not_padded_or_altered(self):
        self.assertEqual(abstract_preview("Short abstract."), "Short abstract.")

    def test_is_deterministic(self):
        self.assertEqual(abstract_preview(EN), abstract_preview(EN))

    def test_does_not_mutate_input(self):
        original = EN
        abstract_preview(EN)
        self.assertEqual(EN, original)


BMC = (
    "Background Single-cell RNA sequencing has become routine. "
    "Results We benchmarked seven integration methods on 12 datasets. "
    "Conclusions COBRA removes batch effects without over-correcting. "
    "Availability COBRA is freely available at https://github.com/wonlab/COBRA. "
    "The code to reproduce the analyses is archived at Zenodo."
)


class TestStructuredAbstracts(unittest.TestCase):
    """结构化摘要（BMC 那类）惯以 Availability 段收尾。

    这时「末两句」会选中「代码托管在 GitHub 某处 + 归档在 Zenodo」这种对筛选
    毫无用处的内容，反而挤掉真正该看的结论。
    """

    def test_prefers_the_conclusions_section(self):
        p = abstract_preview(BMC, 260)
        self.assertIn("removes batch effects", p)
        self.assertNotIn("github.com", p)
        self.assertNotIn("Zenodo", p)

    def test_drops_trailing_availability_without_a_conclusions_section(self):
        text = ("Background Something is hard. Methods We built a tool. "
                "Results It improves accuracy by 3.5 points. "
                "Availability The software is freely available at "
                "https://example.org/tool and archived on Zenodo.")
        p = abstract_preview(text, 260)
        self.assertIn("improves accuracy", p)
        self.assertNotIn("example.org", p)

    def test_falls_back_when_both_trailing_sentences_are_boilerplate(self):
        """结尾连着两句样板时，要退回真正的结论，而不是留下其中一句样板。

        真实摘要（COBRA）上踩到的：末两句恰好都是可用性声明。若只砍最后一句、
        再受「至少留一句」的保护收手，剩下那句样板反而被当成结论，
        而真正的结论（Results）根本没进入候选。
        """
        text = ("Abstract Something is hard. Methods We built a tool. "
                "Results It improves accuracy by 3.5 points. "
                "Availability The software is freely available at "
                "https://github.com/example/tool. "
                "The code to reproduce the analyses is archived at Zenodo.")
        p = abstract_preview(text, 300)
        self.assertIn("improves accuracy", p)
        self.assertNotIn("github.com", p)
        self.assertNotIn("Zenodo", p)

    def test_unstructured_abstract_still_uses_last_two_sentences(self):
        """没有分节标记、也没有可用性声明时，原有行为不能被改坏。"""
        p = abstract_preview(EN)
        self.assertIn("VeloTree outperformed baselines", p)
        self.assertIn("Our results suggest", p)

    def test_a_plain_url_does_not_make_a_sentence_boilerplate(self):
        """只有链接还不够，必须同时有「可获取/下载/归档」这类动词才判样板。

        否则会把「结果见 https://… 的基准测试」这种正常结论误删。
        """
        text = ("Background Something is hard. Methods We built a tool. "
                "Results It improves accuracy. Our benchmark details live at "
                "https://example.org/bench for reference.")
        p = abstract_preview(text, 300)
        self.assertIn("benchmark details", p)

    def test_never_drops_every_sentence(self):
        """整篇就是一句可用性声明时，也不能返回空串。"""
        p = abstract_preview("Availability The code is at https://example.org/x.")
        self.assertTrue(p)
        self.assertIn("https://example.org/x", p)

    def test_chinese_conclusion_marker(self):
        """中文结构化摘要同样要认「结论」这个分节标记。"""
        text = ("背景 单细胞测序已成为常规手段。"
                "方法 我们在 12 个数据集上评测了七种方法。"
                "结果 COBRA 在不矫枉过正的前提下消除了批次效应。"
                "讨论 该方法在多种组织类型上表现稳定。"
                "结论 COBRA 可作为默认的批次校正方案。")
        p = abstract_preview(text, 260)
        self.assertIn("默认的批次校正方案", p)
        # 紧挨结论前的那句会被「末两句」规则选中，有分节标记时不该出现
        self.assertNotIn("表现稳定", p)


class TestWebIntegration(unittest.TestCase):
    """论文表要用的字段必须真的被序列化出去，否则前端加列也拿不到值。"""

    def test_paper_json_exposes_preview(self):
        from litwarden.web import paper_json

        # 注意 Paper.key 是只读属性（由 DOI > PMID > arXiv > 标题+年份 派生），
        # 不能当构造参数传 —— 这里踩过一次
        p = Paper(title="VeloTree", doi="10.1000/xyz", abstract=EN)
        d = paper_json(p)
        self.assertIn("abstract_preview", d)
        self.assertTrue(d["abstract_preview"].startswith("Reconstructing"))

    def test_paper_json_preview_is_empty_without_abstract(self):
        from litwarden.web import paper_json

        d = paper_json(Paper(title="No abstract here"))
        self.assertEqual(d.get("abstract_preview"), "")

    def test_full_abstract_is_not_sent_to_the_table(self):
        """列表接口只带速览。完整摘要中位 1293 字符，几百篇一起传会把页面拖垮。"""
        from litwarden.web import paper_json

        d = paper_json(Paper(title="Long one", abstract=EN))
        self.assertLess(len(d["abstract_preview"]), len(EN))

    def test_frontend_column_is_declared(self):
        """COLS.papers 里必须有这一列，否则后端给了字段也显示不出来。"""
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "litwarden" / "webui.py"
        text = src.read_text(encoding="utf-8")
        m = re.search(r"papers:\s*\[(.*?)\n  \]", text, re.S)
        self.assertIsNotNone(m, "没能定位 COLS.papers")
        self.assertIn("abstract_preview", m.group(1))


if __name__ == "__main__":
    unittest.main()
