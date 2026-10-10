"""Digester —— 初读提炼。

把 Zotero-AI-Butler 生成的精读笔记回读、解析、汇总成一张
「每篇讲了什么方法 / 用了什么数据 / 结论是什么」的对照表。

为什么要这么绕（而不是直接调 LLM）：
    Butler 是一个 Zotero GUI 插件，**没有任何对外 API / CLI / hook**，
    唯一可行的自动化路径是「投喂 PDF → 靠它自己的『自动扫描新文献』出笔记
    → 我们用 Zotero 本地 API 把笔记读回来」。
    好处是复用你已经配好的模型和提示词模板，不重复造轮子。

笔记存储的现实情况：
    Zotero 的笔记字段本质是 HTML。Butler 生成的是 Markdown，
    但它可能以原样 Markdown 存在 HTML 里，也可能被转成了 HTML 标签。
    所以下面的解析器对两种形态都要容忍。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .models import Paper
from .store import Store
from .zotero import ZoteroClient, ZoteroError

# ---------------------------------------------------------------- 字段归一
#
# 把各种标题写法归并到统一的规范字段。
# 顺序有意义：匹配时按关键词长度从长到短，避免「研究方法」被「方法」抢先截短。
FIELD_PATTERNS: list[tuple[str, list[str]]] = [
    ("summary", ["一句话总结", "一句话概括", "核心总结", "总结", "摘要", "概述", "tldr", "summary"]),
    ("background", ["研究背景", "背景介绍", "背景", "引言", "background", "introduction"]),
    ("problem", ["研究问题", "待解决问题", "问题", "research question", "problem"]),
    ("method", [
        "研究方法", "方法学", "方法论", "技术路线", "技术方案", "模型方法", "实现细节",
        "算法", "建模", "方法", "方案", "模型",
        "method", "methodology", "approach", "algorithm",
    ]),
    # 注意顺序：把更长的标题放前面，「实验数据」不应被「数据」抢先
    ("data", ["数据集", "数据来源", "数据与设置", "实验数据", "数据", "样本", "dataset", "data"]),
    ("experiment", ["实验设计", "实验设置", "实验", "评测", "experiment", "evaluation", "setup"]),
    ("results", ["主要结果", "实验结果", "结果", "发现", "result", "finding"]),
    ("conclusion", ["结论与展望", "结论", "讨论", "展望", "conclusion", "discussion", "outlook"]),
    ("contribution", ["创新点", "主要贡献", "贡献", "亮点", "novelty", "contribution", "highlight"]),
    ("limitation", ["局限性", "不足", "局限", "limitation", "weakness", "drawback"]),
]

FIELD_LABELS = {
    "summary": "一句话总结",
    "background": "背景",
    "problem": "问题",
    "method": "方法",
    "data": "数据",
    "experiment": "实验",
    "results": "结果",
    "conclusion": "结论",
    "contribution": "创新点",
    "limitation": "局限",
}

# 按关键词长度降序，保证更具体的标题优先匹配
_SORTED_PATTERNS = sorted(
    [(f, kw) for f, kws in FIELD_PATTERNS for kw in kws],
    key=lambda x: -len(x[1]),
)

_HTML_HEAD_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S | re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_HEAD_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
# Markdown 表格行：| 维度 | 内容 |
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|(.+?)\|\s*$", re.M)
# Butler 写在每篇笔记开头的模型信息行，对提炼表是纯噪声
_BUTLER_META_RE = re.compile(r"^\s*AI\s*来源\s*[:：].*$", re.M)


@dataclass
class ParsedNote:
    """一篇论文的笔记解析结果。"""

    raw: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    # 未能归类的原始小节，保留下来避免信息丢失
    other: dict[str, str] = field(default_factory=dict)
    # 值来自 Markdown 表格的字段名。合并多篇笔记时它们优先——
    # 表格是 Butler 专门为填表生成的一行式结论，比长文小节更适合汇总表。
    tabular: set[str] = field(default_factory=set)

    @property
    def has_content(self) -> bool:
        return bool(self.sections or self.other)

    def field(self, name: str, limit: int = 0) -> str:
        v = self.sections.get(name, "") or ""
        v = re.sub(r"\s+", " ", v).strip()
        if limit and len(v) > limit:
            return v[: limit - 1] + "…"
        return v


def html_to_text(raw: str) -> str:
    """把 Zotero 笔记（HTML）转成便于按行解析的文本。

    关键：先把 <h1>..<h6> 转成 Markdown 的 `#` 标题，
    这样后面一套解析逻辑就能同时处理「HTML 笔记」和「原样 Markdown」两种形态。
    """

    def head_sub(m: re.Match) -> str:
        level = int(m.group(1))
        inner = _HTML_TAG_RE.sub("", m.group(2))
        return f"\n\n{'#' * level} {html.unescape(inner).strip()}\n\n"

    s = _HTML_HEAD_RE.sub(head_sub, raw or "")
    # <br> / </p> / </div> / </li> 视作换行
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|div|li|tr|h[1-6])\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<\s*li[^>]*>", "- ", s, flags=re.I)
    s = _HTML_TAG_RE.sub("", s)
    s = html.unescape(s)
    return s


def strip_butler_meta(text: str) -> str:
    """去掉 Butler 写在笔记开头的模型信息行。

    原始形态：`AI 来源：供应商：DeepSeek · 模型：deepseek-flash · 生成时间：2026/10/9 00:27:12`

    这行紧跟在正文第一段之前，不剥掉会被当成 summary 的首句写进提炼表，
    变成「AI 来源：供应商…」这种对研究毫无价值的单元格。
    """
    return _BUTLER_META_RE.sub("", text or "")


def classify(heading: str) -> str | None:
    """把一个标题归到规范字段。"""
    h = re.sub(r"[\s:：\-—_*]+", "", (heading or "").lower())
    if not h:
        return None
    for field_name, kw in _SORTED_PATTERNS:
        if kw in h:
            return field_name
    return None


def table_fields(text: str) -> dict[str, str]:
    """从 Markdown 表格里抽字段。

    为什么必须有这个：Butler 的「文献表格」笔记把字段写成两列表格
    （`| 维度 | 内容 |`），**没有任何 `#` 标题**。只按标题解析的话这类笔记
    会一个字都抽不出来——而它恰恰是结构化程度最高、最适合填汇总表的一种。

    表头行 `| 维度 | 内容 |` 与分隔行 `|---|---|` 会因为字段名归类失败
    或值全是 `-` 而被自动跳过。
    """
    out: dict[str, str] = {}
    for m in _MD_TABLE_ROW_RE.finditer(text or ""):
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 2:
            continue
        key = cells[0]
        val = " ".join(c for c in cells[1:] if c).strip()
        if not key or not val:
            continue
        # 分隔行：值只由 -、: 和空格组成
        if not val.strip("-: "):
            continue
        f = classify(key)
        if f and f not in out:
            out[f] = val
    return out


def parse_note(raw: str) -> ParsedNote:
    """解析笔记正文，切成规范字段。

    对三种形态都容错：
      · HTML 形态的 Zotero 笔记（先把 <h1>..<h6> 转成 `#` 标题）
      · 原样的 Markdown 标题分节文本
      · 两列 Markdown 表格（Butler「文献表格」用的形态，没有 # 标题）
    """
    text = strip_butler_meta(html_to_text(raw))
    out = ParsedNote(raw=text)

    matches = list(_MD_HEAD_RE.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        f = classify(title)
        if f:
            # 同一字段出现多次时拼接，而不是覆盖
            if out.sections.get(f):
                out.sections[f] += "\n" + body
            else:
                out.sections[f] = body
        else:
            out.other[title[:40]] = body

    # 表格字段补标题没覆盖到的部分。
    # 标题分节在前、表格在后：一篇笔记同时有两者时，标题正文通常更详细，
    # 只在标题没有给出这个字段时才用表格里的一行式结论。
    for f, v in table_fields(text).items():
        if not out.sections.get(f):
            out.sections[f] = v
            out.tabular.add(f)

    # 兜底：一个字段都没认出来时整篇当 summary。
    # 注意这里**不能**要求 other 为空——表格笔记的顶部往往只有一个
    # 无法归类的标题（如 `## 📊 文献表格 - 标题`），它进 other，会把
    # 整篇笔记卡在这里，落得一个字段都写不出的结果。
    if not out.sections and text.strip():
        out.sections["summary"] = text.strip()

    return out


# ---------------------------------------------------------------- 拉取


def is_butler_note(data: dict[str, Any]) -> bool:
    """判断一个子条目是不是 Butler 生成的笔记。"""
    if (data.get("itemType") or "") != "note":
        return False
    note = data.get("note") or ""
    if not note.strip():
        return False
    # Butler 的笔记包含精读结构；普通随手记通常没有多级标题
    return bool(_MD_HEAD_RE.search(html_to_text(note)) or "AI" in note[:400])


@dataclass
class FetchReport:
    checked: int = 0
    with_note: int = 0
    no_note: int = 0
    no_pdf: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def fetch_notes(
    cfg: Config,
    store: Store,
    client: ZoteroClient,
    papers: list[Paper],
    verbose: bool = True,
) -> FetchReport:
    """回读 Zotero 里 Butler 生成的笔记，存入本地缓存。

    只用读请求（免认证），可以安全地反复轮询。
    """
    rep = FetchReport()
    links = store.zotero_links()
    total = len(papers)

    for i, p in enumerate(papers, 1):
        link = links.get(p.key)
        if not link:
            continue
        rep.checked += 1
        if verbose:
            print(f"  [{i}/{total}] {(p.title or p.key)[:50]} … ", end="", flush=True)
        try:
            kids = client.children(link["zotero_key"])
        except ZoteroError as e:
            rep.errors.append((p.title[:50], str(e).splitlines()[0][:90]))
            if verbose:
                print("查询失败")
            continue

        has_pdf = any((k.get("data") or {}).get("itemType") == "attachment" for k in kids)
        notes = [
            k for k in kids
            if is_butler_note(k.get("data") or {})
        ]
        if not notes:
            if not has_pdf:
                # 注意口径：能走到这里，说明本地关联记录里 attached_pdf 为真，
                # 但去 Zotero 实际查 children 却没找到附件 —— 这是「同步漂移」，
                # 不是「本来就没 PDF」（那种在进本函数之前就被过滤掉了）。
                # 两个计数含义不同，所以提示文案也要不同，否则会和
                # `digest status` 的「缺 PDF」对不上，让人以为其中一个是错的。
                rep.no_pdf += 1
                if verbose:
                    print("本地记录有 PDF，但 Zotero 里查不到附件（可跑 zotero sync 重建关联）")
            else:
                rep.no_note += 1
                if verbose:
                    print("尚未生成笔记")
            continue

        saved = store.save_notes(p.key, link["zotero_key"], [
            {
                "key": n["key"],
                "title": (n.get("data") or {}).get("title") or "笔记",
                "content": (n.get("data") or {}).get("note") or "",
            }
            for n in notes
        ])
        rep.with_note += 1
        if verbose:
            print(f"✓ {len(notes)} 篇笔记" + (f"（{saved}）" if saved else ""))

    return rep


def parsed_for(store: Store, paper_key: str) -> ParsedNote:
    """取出某篇论文已缓存的全部笔记，合并解析。

    为什么是合并而不是只挑一篇：同一篇文献常有多篇笔记（Butler 会分别生成
    「文献表格」与「长文总结」两个任务）。早先按「内容最长」选一篇，结果选中
    长文总结，把表格笔记里那些与汇总表列一一对应的干净字段（研究问题 /
    研究方法 / 主要发现 / 创新点 / 局限性）全丢了。

    合并规则：每个字段分别挑最优值——来自表格的优先（那是 Butler 专门为
    填表写的一行式结论），同类型里取更长的那个。
    """
    notes = store.get_notes(paper_key)
    if not notes:
        return ParsedNote()

    merged = ParsedNote()
    # 长的先处理，便于后面的比较逻辑保持一致
    ordered = sorted(notes, key=lambda n: len(n.get("content") or ""), reverse=True)
    for n in ordered:
        p = parse_note(n.get("content") or "")
        for f, v in p.sections.items():
            cur = merged.sections.get(f)
            if not cur:
                merged.sections[f] = v
                if f in p.tabular:
                    merged.tabular.add(f)
                continue
            # 已经是表格值就不再被覆盖；同为表格或同为正文时取更长的
            if f in merged.tabular:
                continue
            if f in p.tabular or len(v) > len(cur):
                merged.sections[f] = v
                if f in p.tabular:
                    merged.tabular.add(f)
        merged.other.update(p.other)
    return merged


def digest_status(cfg: Config, store: Store) -> dict[str, Any]:
    """初读提炼进度总览。"""
    links = store.zotero_links()
    if not links:
        return {"total": 0, "done": 0, "pending": 0, "no_pdf": 0, "unknown": 0}

    done = pending = no_pdf = 0
    pending_rows: list[tuple[str, str]] = []
    papers = {p.key: p for p in store.get_papers()}

    for pk, link in links.items():
        p = papers.get(pk)
        title = (p.title if p else pk)[:60]
        if store.get_notes(pk):
            done += 1
            continue
        if link.get("attached_pdf"):
            pending += 1
            pending_rows.append((title, "已入队，待 Butler 精读"))
        else:
            no_pdf += 1
            pending_rows.append((title, "缺 PDF，Butler 无法精读"))

    return {
        "total": len(links),
        "done": done,
        "pending": pending,
        "no_pdf": no_pdf,
        "unknown": 0,
        "pending_rows": pending_rows,
    }


# ---------------------------------------------------------------- 汇总导出

DIGEST_COLUMNS = [
    ("序号", "idx", 5),
    ("标题", "title", 52),
    ("期刊", "journal", 24),
    ("年份", "year", 6),
    ("分区", "rank", 11),
    ("一句话总结", "summary", 46),
    ("方法", "method", 52),
    ("数据", "data", 40),
    ("结论/创新", "conclusion", 52),
    ("局限", "limitation", 34),
    ("有笔记", "has_note", 8),
    ("有PDF", "has_pdf", 8),
    ("DOI", "doi", 28),
]


def digest_rows(cfg: Config, store: Store, papers: list[Paper]) -> list[dict[str, Any]]:
    """生成汇总表的行数据。"""
    links = store.zotero_links()
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(papers, 1):
        note = parsed_for(store, p.key)
        link = links.get(p.key) or {}
        rows.append({
            "idx": i,
            "title": p.title or "",
            "journal": p.journal or "",
            "year": p.year,
            "rank": "; ".join(f"{k}={v}" for k, v in (p.journal_rank or {}).items()),
            "summary": note.field("summary", 400),
            "method": note.field("method", 600),
            "data": note.field("data", 400),
            "conclusion": note.field("conclusion", 400) or note.field("contribution", 400),
            "limitation": note.field("limitation", 300),
            "has_note": "是" if note.has_content else "否",
            "has_pdf": "是" if link.get("attached_pdf") else "否",
            "doi": p.doi or "",
            # 未归类的小节也留着，避免信息丢失
            "_other": note.other,
            "_fields": note.sections,
        })
    return rows


def render_digest_table(rows: list[dict[str, Any]], limit: int = 15) -> str:
    """终端预览：只显示最关键的几列，否则一行放不下。"""
    import unicodedata

    if not rows:
        return "（没有可汇总的论文）"

    def width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

    def cut(s: str, w: int) -> str:
        out, cur = "", 0
        for c in str(s or ""):
            cw = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            if cur + cw > w:
                return out + "…"
            out += c
            cur += cw
        return out

    def pad(s: str, w: int) -> str:
        return s + " " * max(0, w - width(s))

    heads = ["#", "标题", "有笔记", "有PDF", "一句话总结"]
    widths = [4, 44, 7, 6, 56]
    lines = [f"共 {len(rows)} 篇", ""]
    lines.append("  ".join(pad(h, w) for h, w in zip(heads, widths)))
    lines.append("  ".join("-" * w for w in widths))
    for r in rows[:limit]:
        cells = [
            str(r["idx"]),
            cut(r["title"], widths[1]),
            r["has_note"],
            r["has_pdf"],
            cut(r["summary"] or "（无笔记）", widths[4]),
        ]
        lines.append("  ".join(pad(c, w) for c, w in zip(cells, widths)))
    if len(rows) > limit:
        lines.append(f"… 另有 {len(rows) - limit} 篇，导出后查看完整内容")
    return "\n".join(lines)


def export_digest(
    cfg: Config, rows: list[dict[str, Any]], basename: str, formats: list[str]
) -> list[Any]:
    """导出初读提炼汇总表。默认给出三种格式，其中 md 最适合直接读。"""
    from .exporters import to_xlsx_rows

    out: list[Any] = []
    base = cfg.export_dir / basename
    base.parent.mkdir(parents=True, exist_ok=True)

    if "xlsx" in formats:
        out.append(to_xlsx_rows(
            rows, base.with_suffix(".xlsx"), DIGEST_COLUMNS,
            {"idx", "year"}, sheet_name=basename[:31],
        ))

    if "csv" in formats:
        import csv

        p = base.with_suffix(".csv")
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=[h for h, _, _ in DIGEST_COLUMNS])
            w.writeheader()
            for r in rows:
                w.writerow({h: r.get(k, "") for h, k, _ in DIGEST_COLUMNS})
        out.append(p)

    if "md" in formats:
        p = base.with_suffix(".md")
        lines = ["# 初读提炼汇总", ""]
        for r in rows:
            lines.append(f"## {r['idx']}. {r['title']}")
            meta = "　".join(x for x in [
                r.get("journal") or "",
                str(r.get("year") or ""),
                r.get("rank") or "",
                f"DOI: {r['doi']}" if r.get("doi") else "",
            ] if x)
            if meta:
                lines.append(f"*{meta}*")
            lines.append("")
            if r["has_note"] != "是":
                lines.append("> 尚无精读笔记。"
                             + ("（缺 PDF，Butler 无法精读）" if r["has_pdf"] != "是" else ""))
                lines.append("")
                continue
            for fname, label in (
                ("summary", "一句话总结"),
                ("method", "方法"),
                ("data", "数据"),
                ("conclusion", "结论 / 创新点"),
                ("limitation", "局限"),
            ):
                v = r.get(fname)
                if v:
                    lines.append(f"**{label}**：{v}")
                    lines.append("")
            # 未归类的小节也一并保留，避免丢信息
            for k, v in (r.get("_other") or {}).items():
                lines.append(f"**{k}**：{v}")
                lines.append("")
            lines.append("---")
            lines.append("")
        p.write_text("\n".join(lines), encoding="utf-8")
        out.append(p)

    return out
