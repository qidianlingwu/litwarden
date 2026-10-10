"""初读提炼解析器的回归测试。

重点覆盖两种真实笔记形态：
  · 长文总结 —— HTML 里的 <h2> 转成 `#` 标题后按小节切分
  · 文献表格 —— 两列 Markdown 表格，**没有任何标题**，必须单独解析

以及同一篇文献存在多篇笔记时的合并规则。
"""

import unittest

from litwarden.digester import (
    ParsedNote,
    classify,
    parse_note,
    parsed_for,
    strip_butler_meta,
    table_fields,
)


# 合成样例：形态照着真实笔记写，内容全是编的。
TABLE_NOTE = """
<h2>📊 文献表格 - A Synthetic Paper on Widget Calibration.</h2>
<p>AI 来源：供应商：FakeVendor · 模型：fake-model · 生成时间：2020/1/1 00:00:00</p>
<p>维度</p><p>内容</p>
<p>研究问题</p><p>How should widgets be calibrated under noise?</p>
<p>👇 以下为系统缓存的原始 Markdown 数据（用于追加填表，请勿修改）：</p>
| 维度 | 内容 |
|------|------|
| 论文标题 | A Synthetic Paper on Widget Calibration |
| 作者 | A. Author, B. Author |
| 研究问题 | How should widgets be calibrated under noise? |
| 研究方法 | We sweep the gain parameter on a synthetic grid. |
| 主要发现 | Gains above 2.0 destabilise the loop. |
| 创新点 | First systematic sweep of the gain parameter. |
| 局限性 | Only synthetic data; no hardware validation. |
"""

LONG_NOTE = """
<h2>AI 总结 - A Synthetic Paper on Widget Calibration.</h2>
<p>AI 来源：供应商：FakeVendor · 模型：fake-model · 生成时间：2020/1/1 00:05:00</p>
<p>This synthetic paper studies widget calibration and reports a stability boundary.</p>
<h2>一、论文要解决的问题</h2>
<p>Widgets drift. Existing calibrations ignore process noise entirely, which matters at high gain.</p>
<h2>二、建模思路</h2>
<p>A very long and detailed derivation follows here, with equations and several pages of prose.</p>
"""


class TestButlerMeta(unittest.TestCase):
    """笔记开头的模型信息行必须被剥掉。"""

    def test_strip_meta_line(self):
        raw = "AI 来源：供应商：X · 模型：y · 生成时间：2020/1/1 00:00:00\n正文第一段。"
        out = strip_butler_meta(raw)
        self.assertNotIn("AI 来源", out)
        self.assertIn("正文第一段", out)

    def test_summary_has_no_meta(self):
        """summary 里不该出现「AI 来源」——它曾经是提炼表第一列的常客。"""
        note = parse_note(LONG_NOTE)
        self.assertTrue(note.field("summary"))
        self.assertNotIn("AI 来源", note.field("summary"))
        self.assertNotIn("供应商", note.field("summary"))
        self.assertIn("synthetic paper", note.field("summary"))


class TestTableNote(unittest.TestCase):
    """「文献表格」形态：没有 # 标题，字段全在两列表格里。"""

    def test_fields_extracted(self):
        note = parse_note(TABLE_NOTE)
        self.assertEqual(
            note.field("problem"), "How should widgets be calibrated under noise?"
        )
        self.assertIn("gain parameter", note.field("method"))
        self.assertIn("Gains above 2.0", note.field("results"))
        self.assertIn("First systematic sweep", note.field("contribution"))
        self.assertIn("no hardware validation", note.field("limitation"))

    def test_table_origin_recorded(self):
        note = parse_note(TABLE_NOTE)
        for f in ("problem", "method", "results", "contribution", "limitation"):
            self.assertIn(f, note.tabular, f)

    def test_not_swallowed_by_unclassified_heading(self):
        """只有无意义标题时必须能兜底，而不是一个字段都抽不出来。

        这是最初的 bug：`## 📊 文献表格 - …` 归类失败进了 other，
        而兜底条件要求 other 为空，于是整篇笔记被静默丢弃。
        """
        note = parse_note(TABLE_NOTE)
        self.assertTrue(note.has_content)
        self.assertGreaterEqual(len(note.sections), 5)

    def test_table_fields_helper(self):
        fields = table_fields(TABLE_NOTE)
        self.assertEqual(fields.get("limitation"), "Only synthetic data; no hardware validation.")
        # 表头与分隔行不能混进来
        self.assertNotIn("内容", fields.values())
        self.assertNotIn("------", " ".join(fields.values()))
        # 「论文标题」「作者」不在字段表里，应被忽略
        self.assertNotIn("title", fields)
        self.assertNotIn("author", fields)


class TestHeadingNote(unittest.TestCase):
    """长文总结形态：`#` 小节，原有能力不能回退。"""

    def test_sections_split(self):
        note = parse_note(LONG_NOTE)
        self.assertIn("synthetic paper", note.field("summary"))
        self.assertIn("Widgets drift", note.field("problem"))
        self.assertIn("detailed derivation", note.field("method"))

    def test_field_order_beats_substring(self):
        """「研究方法」应归到 method，不能被更短的「方法」抢先，也不能被当成 summary。"""
        self.assertEqual(classify("研究方法"), "method")
        self.assertEqual(classify("一句话总结"), "summary")
        self.assertEqual(classify("局限性"), "limitation")
        self.assertIsNone(classify("论文标题"))

    def test_pure_prose_falls_back_to_summary(self):
        note = parse_note("<p>Just one paragraph with no headings at all.</p>")
        self.assertIn("Just one paragraph", note.field("summary"))


class _FakeStore:
    """只实现 parsed_for 用到的那一个方法，避免为测试拉起 SQLite。"""

    def __init__(self, notes):
        self._notes = notes

    def get_notes(self, key):  # noqa: ARG002
        return self._notes


class TestMergeNotes(unittest.TestCase):
    """一篇文献两篇笔记时，字段级择优合并。"""

    def _merged(self):
        notes = [
            {"title": "AI 总结", "content": LONG_NOTE},
            {"title": "文献表格", "content": TABLE_NOTE},
        ]
        return parsed_for(_FakeStore(notes), "doi:10.0000/synthetic")

    def test_table_wins_for_shared_field(self):
        m = self._merged()
        # 两篇都提到了研究问题，表格的一行式结论更适合作汇总表
        self.assertEqual(
            m.field("problem"), "How should widgets be calibrated under noise?"
        )
        self.assertIn("problem", m.tabular)

    def test_fields_only_in_long_note_survive(self):
        """合并不能把长文独有的字段丢掉——这是旧「取最长一篇」策略的另一个问题。"""
        m = self._merged()
        self.assertIn("synthetic paper", m.field("summary"))

    def test_union_of_fields(self):
        m = self._merged()
        expected = {"summary", "problem", "method", "results", "contribution", "limitation"}
        self.assertTrue(expected.issubset(set(m.sections)), m.sections.keys())

    def test_no_notes_returns_empty(self):
        m = parsed_for(_FakeStore([]), "doi:none")
        self.assertFalse(m.has_content)
        self.assertEqual(m.sections, {})

    def test_single_note_still_works(self):
        m = parsed_for(_FakeStore([{"title": "表格", "content": TABLE_NOTE}]), "k")
        self.assertIn("gain parameter", m.field("method"))

    def test_result_is_parsednote(self):
        self.assertIsInstance(self._merged(), ParsedNote)


if __name__ == "__main__":
    unittest.main()
