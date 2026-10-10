"""导出格式的测试。

导出的 xlsx/csv/bib 是用户真正拿去用的东西：
· BibTeX 的 key 重复会让 Zotero 导入时条目被吞掉
· xlsx 是手写 XML，写坏了 Excel 会直接报「文件已损坏」
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from litwarden.exporters import (
    bibkey,
    render_table,
    to_bibtex,
    to_csv,
    to_json,
    to_markdown,
    to_xlsx,
)
from litwarden.models import Paper


def sample_papers() -> list[Paper]:
    return [
        Paper(
            title="Benchmarking atlas-level data integration in single-cell genomics",
            authors=["Luecken MD", "Buttner M"],
            journal="Nature Methods",
            year=2022,
            cited_by=1039,
            doi="10.1038/s41592-021-01358-2",
            abstract="Abstract text",
            is_oa=True,
            keyword_score=0.87,
            journal_rank={"sjr": "Q1"},
        ),
        Paper(
            title="A second paper about deconvolution methods",
            authors=["Satterstrom, Frank"],
            journal="arXiv",
            year=2024,
            cited_by=3,
            arxiv_id="2401.00001",
        ),
    ]


class TestBibkey(unittest.TestCase):
    def test_handles_europepmc_initials_format(self):
        """Europe PMC 写作 "Satterstrom FK"，末位是缩写，不能被当成姓。"""
        p = Paper(title="Large scale study of cells", authors=["Satterstrom FK"], year=2020)
        key = bibkey(p, set())
        self.assertTrue(key.startswith("satterstrom2020"), key)

    def test_handles_comma_format(self):
        p = Paper(title="Large scale study of cells", authors=["Satterstrom, Frank"], year=2020)
        self.assertTrue(bibkey(p, set()).startswith("satterstrom2020"))

    def test_no_authors_falls_back_to_anon(self):
        p = Paper(title="Some paper title here", authors=[], year=2021)
        self.assertTrue(bibkey(p, set()).startswith("anon2021"))

    def test_missing_year_uses_nd(self):
        p = Paper(title="Some paper title here", authors=["Smith J"], year=None)
        self.assertIn("nd", bibkey(p, set()))

    def test_duplicate_keys_get_suffix(self):
        p = Paper(title="Same title repeated here", authors=["Smith J"], year=2020)
        used: set[str] = set()
        first = bibkey(p, used)
        second = bibkey(p, used)
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith(first))

    def test_non_ascii_author_is_transliterated(self):
        p = Paper(title="A study", authors=["王小明"], year=2020)
        key = bibkey(p, set())
        self.assertTrue(key[0].isalnum(), key)


class TestExportFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.papers = sample_papers()

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_has_bom_and_header(self):
        path = to_csv(self.papers, self.dir / "out.csv")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "缺 BOM 会让 Excel 打开中文乱码")
        text = raw.decode("utf-8-sig")
        self.assertIn("title", text.splitlines()[0])
        self.assertIn("Benchmarking", text)

    def test_tsv_delimiter(self):
        path = to_csv(self.papers, self.dir / "out.tsv", delimiter="\t")
        self.assertIn("\t", path.read_text(encoding="utf-8-sig").splitlines()[0])

    def test_markdown_structure(self):
        path = to_markdown(self.papers, self.dir / "out.md", query="deconvolution")
        text = path.read_text(encoding="utf-8")
        self.assertIn("#", text)
        self.assertIn("Benchmarking", text)
        self.assertIn("deconvolution", text, "检索式应写进文件便于复现")

    def test_json_is_valid_and_complete(self):
        import json

        path = to_json(self.papers, self.dir / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["doi"], "10.1038/s41592-021-01358-2")

    def test_bibtex_is_parseable_and_keys_unique(self):
        path = to_bibtex(self.papers, self.dir / "out.bib")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("@"), 2)
        keys = [ln.split("{", 1)[1].split(",")[0] for ln in text.splitlines() if ln.startswith("@")]
        self.assertEqual(len(keys), len(set(keys)), "BibTeX key 必须唯一")

    def test_bibtex_uses_howpublished_for_preprints(self):
        path = to_bibtex(self.papers, self.dir / "out.bib")
        text = path.read_text(encoding="utf-8")
        self.assertIn("howpublished", text)
        self.assertIn("@misc", text, "arXiv 预印本应为 @misc")

    def test_bibtex_escapes_latex_specials(self):
        p = Paper(title="A 100% safe & sound study", authors=["Smith J"], year=2020)
        path = to_bibtex([p], self.dir / "esc.bib")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("100% safe", text, "% 在 LaTeX 里是注释符")
        self.assertIn("100", text)

    def test_xlsx_is_valid_zip_with_required_parts(self):
        path = to_xlsx(self.papers, self.dir / "out.xlsx", sheet_name="文献表")
        self.assertTrue(zipfile.is_zipfile(path), "xlsx 本质是 zip，非法结构 Excel 会报损坏")
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for required in (
                "[Content_Types].xml",
                "_rels/.rels",
                "xl/workbook.xml",
                "xl/worksheets/sheet1.xml",
            ):
                self.assertIn(required, names)
            sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("Benchmarking", sheet)

    def test_xlsx_survives_control_characters_in_abstract(self):
        """摘要里偶有 XML 1.0 不允许的控制字符，不清理会让 Excel 报错。"""
        p = Paper(title="Title", abstract="bad\x0cchar\x01here", year=2024)
        path = to_xlsx([p], self.dir / "ctrl.xlsx")
        with zipfile.ZipFile(path) as z:
            xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for ch in ("\x0c", "\x01"):
            self.assertNotIn(ch, xml)

    def test_xlsx_escapes_xml_metacharacters(self):
        p = Paper(title="A <b>bold</b> & risky title", year=2024)
        path = to_xlsx([p], self.dir / "escape.xlsx")
        with zipfile.ZipFile(path) as z:
            xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("&lt;b&gt;", xml)
        self.assertIn("&amp;", xml)

    def test_empty_paper_list_still_writes_file(self):
        for fn, name in ((to_csv, "e.csv"), (to_markdown, "e.md"),
                         (to_json, "e.json"), (to_bibtex, "e.bib")):
            with self.subTest(fn=fn.__name__):
                path = fn([], self.dir / name)
                self.assertTrue(path.exists())


class TestRenderTable(unittest.TestCase):
    def test_renders_rows_with_header(self):
        out = render_table(sample_papers(), query="deconvolution", limit=10)
        self.assertIn("Benchmarking", out)
        self.assertIn("deconvolution", out)
        self.assertIn("列说明", out, "缩写列名需要图例")

    def test_empty_list_shows_hint(self):
        self.assertIn("没有", render_table([], query="x"))

    def test_truncates_and_reports_remainder(self):
        papers = sample_papers() * 5
        out = render_table(papers, query="x", limit=3)
        self.assertIn("另有", out)

    def test_handles_cjk_width(self):
        """中文按 2 列宽计算，否则表格会错位。"""
        p = Paper(title="中文标题测试", journal="中文期刊", year=2024)
        out = render_table([p], limit=5)
        self.assertIn("中文标题测试", out)


if __name__ == "__main__":
    unittest.main()
