"""导出：CSV / TSV / Markdown / BibTeX / JSON。全部标准库实现。"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from .models import Paper

# CSV 列顺序：把「筛选时最常看的字段」放前面
COLUMNS = [
    "rank_idx",
    "title",
    "authors",
    "journal",
    "year",
    "cited_by",
    "cites_per_year",
    "keyword_score",
    "rank",
    "is_oa",
    "oa_url",
    "doi",
    "pmid",
    "arxiv_id",
    "type",
    "sources",
    "url",
    "abstract",
]

_CN_MAP = str.maketrans({"&": " and ", "%": " ", "#": " ", "_": " ", "$": " "})


def _row(p: Paper, idx: int) -> dict[str, str]:
    return {
        "rank_idx": str(idx),
        "title": p.title or "",
        "authors": p.author_str,
        "journal": p.journal or "",
        "year": "" if p.year is None else str(p.year),
        "cited_by": "" if p.cited_by is None else str(p.cited_by),
        "cites_per_year": "" if p.cites_per_year is None else f"{p.cites_per_year:.2f}",
        "keyword_score": "" if p.keyword_score is None else f"{p.keyword_score:.3f}",
        "rank": "; ".join(f"{k}={v}" for k, v in (p.journal_rank or {}).items()),
        "is_oa": "" if p.is_oa is None else ("Y" if p.is_oa else "N"),
        "oa_url": p.oa_url or "",
        "doi": p.doi or "",
        "pmid": p.pmid or "",
        "arxiv_id": p.arxiv_id or "",
        "type": p.type or "",
        "sources": ",".join(p.sources),
        "url": p.url or "",
        "abstract": (p.abstract or "").replace("\n", " "),
    }


# CSV 公式注入的触发前缀。Excel / WPS / Numbers 见到这些开头的单元格
# 会当公式求值——`=HYPERLINK(...)`、`+cmd|...`、`@SUM(...)` 等。
# 论文标题与摘要来自第三方数据源，属于不可信输入，必须转义。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: str) -> str:
    """给可能被表格软件当公式的单元格加前缀。

    标准做法是前置一个单引号，Excel 会按纯文本处理。
    只在命中前缀时才加，避免污染绝大多数正常单元格。
    """
    if value and value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def to_csv(papers: Iterable[Paper], path: Path, delimiter: str = ",") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=delimiter, extrasaction="ignore")
        w.writeheader()
        for i, p in enumerate(papers, 1):
            # xlsx 走 inlineStr（字符串单元格）天然安全，CSV 没有类型信息，只能转义
            w.writerow({k: csv_safe(v) for k, v in _row(p, i).items()})
    return path


def to_markdown(papers: list[Paper], path: Path, query: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if query:
        lines.append(f"# 文献候选表\n\n检索式：`{query}`　　共 {len(papers)} 篇\n")
    else:
        lines.append(f"# 文献候选表\n\n共 {len(papers)} 篇\n")

    lines.append("| # | 标题 | 期刊 | 年 | 引用 | 年均 | 关键词 | 等级 | OA | DOI |")
    lines.append("|--:|---|---|--:|--:|--:|--:|---|:--:|---|")
    for i, p in enumerate(papers, 1):
        title = (p.title or "").replace("|", "\\|")
        if len(title) > 80:
            title = title[:77] + "..."
        journal = (p.journal or "").replace("|", "\\|")
        if len(journal) > 30:
            journal = journal[:27] + "..."
        rank = ", ".join(f"{k}:{v}" for k, v in (p.journal_rank or {}).items())
        oa = "✓" if p.is_oa else ("" if p.is_oa is None else "✗")
        cpy = f"{p.cites_per_year:.1f}" if p.cites_per_year is not None else ""
        ks = f"{p.keyword_score:.2f}" if p.keyword_score is not None else ""
        doi = p.doi or ""
        lines.append(
            f"| {i} | {title} | {journal} | {p.year or ''} | {p.cited_by if p.cited_by is not None else ''} "
            f"| {cpy} | {ks} | {rank} | {oa} | {doi} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def to_json(papers: list[Paper], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for i, p in enumerate(papers, 1):
        d = _row(p, i)
        d["abstract"] = p.abstract or ""
        d["journal_rank"] = p.journal_rank
        d["extra"] = p.extra
        data.append(d)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- BibTeX


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


_INITIALS_RE = re.compile(r"^[A-Z]{1,3}$")


def bibkey(p: Paper, used: set[str]) -> str:
    """生成 BibTeX key。

    要处理两种作者写法：
        "Satterstrom, Frank"  -> 姓在前，逗号分隔
        "Satterstrom FK"      -> Europe PMC 的「姓 + 首字母缩写」格式
    后者的最后一个 token 是缩写，不能当成姓，否则 key 会变成 fk2020xxx。
    """
    last = ""
    if p.authors:
        raw = p.authors[0].strip()
        if "," in raw:
            last = raw.split(",", 1)[0].strip()
        else:
            parts = [x for x in raw.split() if x]
            if len(parts) >= 2 and _INITIALS_RE.match(parts[-1]):
                last = parts[-2]
            elif len(parts) >= 2 and re.fullmatch(r"[A-Z]\.", parts[-1]):
                last = parts[-2]
            else:
                last = parts[-1] if parts else raw
    last = re.sub(r"[^A-Za-z]", "", _ascii(last)).lower() or "anon"
    year = p.year or "nd"
    word = ""
    for w in re.split(r"[^A-Za-z]+", _ascii(p.title or "")):
        if len(w) > 3:
            word = w.lower()
            break
    base = f"{last}{year}{word}" or "ref"
    key, n = base, 0
    while key in used:
        n += 1
        key = f"{base}{chr(ord('a') + n - 1)}"
    used.add(key)
    return key


def _bib_escape(s: str) -> str:
    return s.translate(_CN_MAP).replace("\\", " ")


def to_bibtex(papers: Iterable[Paper], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    entries: list[str] = []
    for p in papers:
        fields: list[tuple[str, str]] = [("title", _bib_escape(p.title or ""))]
        if p.authors:
            fields.append(("author", _bib_escape(" and ".join(p.authors))))
        if p.year:
            fields.append(("year", str(p.year)))
        if p.journal:
            j = p.journal or ""
            is_pre = "preprint" in j.lower() or "arxiv" in j.lower()
            fields.append(("journal" if not is_pre else "howpublished", _bib_escape(j)))
        if p.doi:
            fields.append(("doi", p.doi))
        if p.url:
            fields.append(("url", p.url))
        if p.abstract:
            fields.append(("abstract", _bib_escape(p.abstract[:1500])))
        if p.keyword_score is not None:
            fields.append(("keywords", f"litflow_score={p.keyword_score}"))
        if p.journal_rank:
            fields.append(
                ("note", _bib_escape("rank: " + "; ".join(f"{k}={v}" for k, v in p.journal_rank.items())))
            )

        etype = "article"
        t = (p.type or "").lower()
        if "preprint" in t or p.arxiv_id:
            etype = "misc"
        elif "proceedings" in t or "conference" in t:
            etype = "inproceedings"

        body = ",\n".join(f"  {k:<10} = {{{v}}}" for k, v in fields)
        entries.append(f"@{etype}{{{bibkey(p, used)},\n{body}\n}}")
    path.write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- XLSX
#
# 用标准库手写 xlsx（本质是个 zip + 若干 XML）。
# 这么做是为了守住「零第三方依赖」——不引入 openpyxl。
# 特性：表头加粗填充、冻结首行、自动筛选、列宽、数字列右对齐。

_XLSX_COLUMNS: list[tuple[str, str, int]] = [
    # (表头, 取值方式, 列宽)
    ("序号", "idx", 5),
    ("标题", "title", 62),
    ("作者", "authors", 26),
    ("期刊", "journal", 28),
    ("年份", "year", 6),
    ("引用数", "cited_by", 8),
    ("年均引用", "cites_per_year", 9),
    ("期刊分区", "rank", 12),
    ("开放获取", "oa", 9),
    ("DOI", "doi", 30),
    ("链接", "url", 34),
    ("匹配度", "keyword_score", 8),
    ("来源", "sources", 20),
    ("摘要", "abstract", 70),
]

_XLSX_NUMERIC = {"idx", "year", "cited_by", "cites_per_year", "keyword_score"}


def _xml_escape(s: str) -> str:
    """XML 转义，并剔除 XML 1.0 不允许的控制字符（摘要里偶尔会有）。"""
    s = "".join(c for c in s if c >= " " or c in "\t\n")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _col_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA"""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _xlsx_cell(ref: str, value: str | float | int | None, numeric: bool, style: int) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if numeric and isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return (
        f'<c r="{ref}" s="{style}" t="inlineStr">'
        f"<is><t xml:space=\"preserve\">{_xml_escape(str(value))}</t></is></c>"
    )


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FF1F3864"/><name val="Calibri"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F1"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def to_xlsx(papers: list[Paper], path: Path, sheet_name: str = "文献表") -> Path:
    """文献候选表的 xlsx 导出（14 列，表头加粗 + 冻结首行 + 自动筛选）。"""
    rows = [
        {field: _xlsx_value(p, i, field) for _, field, _ in _XLSX_COLUMNS}
        for i, p in enumerate(papers, 1)
    ]
    return to_xlsx_rows(rows, path, _XLSX_COLUMNS, _XLSX_NUMERIC, sheet_name)


def _xlsx_value(p: Paper, idx: int, field: str) -> str | float | int | None:
    if field == "idx":
        return idx
    if field == "title":
        return p.title or ""
    if field == "authors":
        return p.author_str
    if field == "journal":
        return p.journal or ""
    if field == "year":
        return p.year
    if field == "cited_by":
        return p.cited_by
    if field == "cites_per_year":
        return p.cites_per_year
    if field == "rank":
        return "; ".join(f"{k}={v}" for k, v in (p.journal_rank or {}).items())
    if field == "oa":
        # 用「未知」而不是留空：Excel 里空白单元格容易被读成「否」，
        # 而这里 is_oa=None 的含义是「数据源没提供这个信息」
        return "未知" if p.is_oa is None else ("是" if p.is_oa else "否")
    if field == "doi":
        return p.doi or ""
    if field == "url":
        return p.url or (f"https://doi.org/{p.doi}" if p.doi else "")
    if field == "keyword_score":
        return p.keyword_score
    if field == "sources":
        return ",".join(p.sources)
    if field == "abstract":
        return p.abstract or ""
    return ""


def to_xlsx_rows(
    rows: list[dict[str, Any]],
    path: Path,
    columns: list[tuple[str, str, int]],
    numeric_fields: set[str] | None = None,
    sheet_name: str = "Sheet1",
) -> Path:
    """通用 xlsx 写出。

    columns = [(表头, 取值 key, 列宽), ...]
    rows    = [ {key: value}, ... ]

    拆成通用版本是为了让「文献候选表」和「初读提炼汇总表」共用同一套
    表格样式（加粗表头 / 冻结首行 / 自动筛选 / 列宽），不必写两遍。
    """
    import zipfile

    numeric_fields = numeric_fields or set()
    path.parent.mkdir(parents=True, exist_ok=True)
    n_rows = len(rows) + 1
    n_cols = len(columns)
    dim = f"A1:{_col_letter(n_cols)}{n_rows}"

    head = "".join(
        _xlsx_cell(f"{_col_letter(i)}1", h, False, 1)
        for i, (h, _, _) in enumerate(columns, 1)
    )

    body_rows = []
    for ri, row in enumerate(rows, 2):
        cells = []
        for ci, (_, key, _) in enumerate(columns, 1):
            ref = f"{_col_letter(ci)}{ri}"
            cells.append(
                _xlsx_cell(ref, row.get(key), key in numeric_fields, 0)
            )
        body_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')

    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, (_, _, w) in enumerate(columns, 1)
    )

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dim}"/>'
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols}</cols>"
        f'<sheetData><row r="1">{head}</row>{"".join(body_rows)}</sheetData>'
        f'<autoFilter ref="{dim}"/>'
        "</worksheet>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{_xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


# ---------------------------------------------------------------- 终端表格


def render_table(papers: list[Paper], query: str = "", limit: int = 30) -> str:
    """等宽对齐的终端预览表。中文按 2 列宽计算。"""
    if not papers:
        return "（没有匹配的文献）"

    def width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

    def cut(s: str, w: int) -> str:
        out, cur = "", 0
        for c in s:
            cw = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            if cur + cw > w:
                return out + "…"
            out += c
            cur += cw
        return out

    def pad(s: str, w: int) -> str:
        return s + " " * max(0, w - width(s))

    headers = ["#", "标题", "期刊", "年", "引用", "年均", "关键", "等级", "OA"]
    widths = [4, 62, 26, 5, 6, 6, 5, 10, 3]

    lines = []
    if query:
        lines.append(f"检索式：{query}")
    lines.append(f"命中 {len(papers)} 篇" + (f"，显示前 {limit} 篇" if len(papers) > limit else ""))
    lines.append("")
    lines.append("  ".join(pad(h, w) for h, w in zip(headers, widths)))
    lines.append("  ".join("-" * w for w in widths))

    for i, p in enumerate(papers[:limit], 1):
        oa = "✓" if p.is_oa else ("·" if p.is_oa is None else "✗")
        rank = ",".join(f"{k}:{v}" for k, v in (p.journal_rank or {}).items())
        cells = [
            str(i),
            cut(p.title or "", widths[1]),
            cut(p.journal or "", widths[2]),
            str(p.year or ""),
            str(p.cited_by) if p.cited_by is not None else "-",
            f"{p.cites_per_year:.1f}" if p.cites_per_year is not None else "-",
            f"{p.keyword_score:.2f}" if p.keyword_score is not None else "-",
            cut(rank, widths[7]),
            oa,
        ]
        lines.append("  ".join(pad(c, w) for c, w in zip(cells, widths)))

    if len(papers) > limit:
        lines.append(f"… 另有 {len(papers) - limit} 篇，用 --limit 调整或导出查看")

    lines.append("")
    lines.append(
        "列说明：年均=年均引用（总引用/发表年数）  关键=关键词匹配度(0-1)  "
        "等级=期刊分区  OA=开放获取(✓有/✗无/·未知)"
    )
    lines.append("提示：想按等级或引用排序用 `litflow filter`，检索结果这里只按关键词匹配度排。")
    return "\n".join(lines)
