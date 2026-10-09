"""Filterer —— 过滤、打分与排序。

关键词匹配度用 Okapi BM25 本地计算（零依赖），标题按 3 倍权重计入，
因为标题命中的相关性远高于摘要里出现一次。

排序默认不是「总引用量」而是「期刊等级 -> 年均引用 -> 关键词匹配度」：
新论文天然总引用低，只按总引用排会让候选池全是十年前的老论文。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .journalrank import RANK_ORDER, rank_index
from .models import Paper

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with",
    "by", "is", "are", "was", "we", "our", "this", "that", "these", "those",
    "from", "as", "at", "it", "its", "be", "can", "using", "use", "used",
}


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOP]


class Bm25:
    """Okapi BM25，k1=1.5 b=0.75（通用的默认参数）。"""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tokens = [tokenize(d) for d in docs]
        self.n = len(self.tokens)
        self.avgdl = (sum(len(t) for t in self.tokens) / self.n) if self.n else 0.0
        self.tf = [Counter(t) for t in self.tokens]
        df: Counter[str] = Counter()
        for toks in self.tokens:
            df.update(set(toks))
        self.idf = {
            term: math.log(1 + (self.n - d + 0.5) / (d + 0.5))
            for term, d in df.items()
        }

    def scores(self, query: str) -> list[float]:
        q_terms = tokenize(query)
        if not q_terms or not self.n:
            return [0.0] * self.n
        out = [0.0] * self.n
        for i, tf in enumerate(self.tf):
            dl = len(self.tokens[i]) or 1
            s = 0.0
            for term in q_terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1e-9)))
            out[i] = s
        return out


# ---------------------------------------------------------------- 过滤条件


@dataclass
class Filters:
    year_from: int | None = None
    year_to: int | None = None
    min_cites: int | None = None
    min_cites_per_year: float | None = None
    # {"sjr": "Q2"} 表示要求 SJR 至少 Q2
    rank_min: dict[str, str] = field(default_factory=dict)
    oa_only: bool = False
    require_abstract: bool = False
    require_doi: bool = False
    # 只要预印本 / 排除预印本
    only_preprints: bool = False
    exclude_preprints: bool = False
    # 命中这些源之一即可（空表示不限）
    from_sources: list[str] = field(default_factory=list)
    # 标题/摘要必须包含的任意词（不区分大小写）
    must_include: list[str] = field(default_factory=list)
    # 仅标题必须包含的任意词。
    # 这是区分「方法论文」与「应用论文」最有效的旋钮：
    # BM25 只看词频，无法判断一篇论文是提出新方法还是拿现成方法去分析某个生物学问题。
    # 而「deconvolution」出现在标题里，基本就说明它是一篇方法论文。
    title_must: list[str] = field(default_factory=list)
    # 标题/摘要包含任一即剔除
    exclude_terms: list[str] = field(default_factory=list)
    # 关键词匹配度下限（0-1，本地 BM25 归一化后的值）。
    # 这是防止「按引用量排序把全局高被引但不相关的论文带进来」的关键闸门：
    # Crossref / Europe PMC 的 cited 排序是全局的，对模糊检索式会严重漂移。
    min_keyword: float | None = None
    # 剔除更正/勘误类记录。这类记录是原论文的附属条目（标题常为
    # "Correction to: ..." / "Author Correction"），对候选池是纯噪声。
    exclude_corrections: bool = False
    # 期刊等级表不完整时，是否保留「未收录」的论文。
    # 默认 False（用户明确设了等级门槛，查不到就不该混进来），
    # 但等级表覆盖率低时这会让结果看起来"没东西"，所以提供这个开关。
    rank_allow_unknown: bool = False


# 更正/勘误类记录的标题前缀。命中即认为是原论文的附属条目。
_CORRECTION_PREFIXES = (
    "correction to:",
    "correction:",
    "author correction",
    "publisher correction",
    "erratum",
    "corrigendum",
    "editorial expression of concern",
    "withdrawn:",
)


def is_correction(p: Paper) -> bool:
    t = (p.title or "").strip().lower()
    return t.startswith(_CORRECTION_PREFIXES)


def is_preprint(p: Paper) -> bool:
    if p.extra.get("is_preprint"):
        return True
    if p.arxiv_id:
        return True
    t = (p.type or "").lower()
    if "preprint" in t or t == "ppr":
        return True
    j = (p.journal or "").lower()
    return "preprint" in j or "biorxiv" in j or "medrxiv" in j or j.startswith("arxiv")


def score_keywords(papers: list[Paper], query_text: str) -> list[float]:
    """给每篇论文算关键词匹配度，归一化到 0-1。"""
    docs = []
    for p in papers:
        title = p.title or ""
        abstract = p.abstract or ""
        # 标题按 3 倍权重计入
        docs.append(f"{title} {title} {title} {abstract}")
    bm = Bm25(docs)
    raw = bm.scores(query_text)
    top = max(raw) if raw else 0.0
    if top <= 0:
        return [0.0] * len(papers)
    return [round(s / top, 4) for s in raw]


def passes(p: Paper, f: Filters) -> tuple[bool, str]:
    """返回 (是否通过, 未通过原因)。"""
    if f.year_from and (p.year or 0) < f.year_from:
        return False, f"年份 {p.year} < {f.year_from}"
    if f.year_to and (p.year or 9999) > f.year_to:
        return False, f"年份 {p.year} > {f.year_to}"
    if f.min_cites is not None:
        if (p.cited_by or 0) < f.min_cites:
            return False, f"引用 {p.cited_by or 0} < {f.min_cites}"
    if f.min_cites_per_year is not None:
        cpy = p.cites_per_year
        if cpy is None or cpy < f.min_cites_per_year:
            return False, f"年均引用 {cpy} < {f.min_cites_per_year}"
    if f.oa_only and not p.is_oa:
        return False, "非开放获取"
    if f.require_abstract and not p.abstract:
        return False, "缺摘要"
    if f.require_doi and not p.doi:
        return False, "缺 DOI"

    pre = is_preprint(p)
    if f.only_preprints and not pre:
        return False, "非预印本"
    if f.exclude_preprints and pre:
        return False, "预印本已排除"

    if f.exclude_corrections and is_correction(p):
        return False, "更正/勘误类记录"

    if f.from_sources and not (set(f.from_sources) & set(p.sources)):
        return False, f"未命中源 {f.from_sources}"

    hay = f"{p.title or ''} {p.abstract or ''}".lower()

    if f.must_include:
        if not any(w.lower() in hay for w in f.must_include):
            return False, f"缺少必需词 {f.must_include}"

    if f.title_must:
        tl = (p.title or "").lower()
        if not any(w.lower() in tl for w in f.title_must):
            return False, f"标题缺少必需词 {f.title_must}"

    for w in f.exclude_terms:
        if w.lower() in hay:
            return False, f"命中排除词 {w!r}"

    for system, minimum in f.rank_min.items():
        got = p.journal_rank.get(system)
        if not got and f.rank_allow_unknown:
            continue
        if not _rank_passes(p, system, minimum):
            return False, f"{system} 等级 {got or '未收录'} 低于 {minimum}"

    if f.min_keyword is not None:
        if p.keyword_score is None:
            return False, "未计算关键词匹配度（请带 --query）"
        if p.keyword_score < f.min_keyword:
            return False, f"关键词匹配度 {p.keyword_score:.2f} < {f.min_keyword}"

    return True, ""


def _rank_passes(p: Paper, system: str, minimum: str) -> bool:
    """期刊等级判断。

    注意一个业务取舍：如果该论文所在期刊在等级表里查不到，
    默认按「不通过」处理（因为用户明确要求了等级门槛，查不到就不该混进来）。
    想放宽就设 Filters.rank_allow_unknown = True，入口是 passes()，不是这里。
    """
    got = p.journal_rank.get(system)
    if not got:
        return False
    gi = rank_index(system, got)
    mi = rank_index(system, minimum)
    if mi is None:
        return True
    return gi is not None and gi <= mi


# ---------------------------------------------------------------- 排序

SORT_KEYS = ("rank", "cites", "cites_per_year", "year", "keyword", "title")


def sort_papers(papers: list[Paper], sort_by: str, systems: list[str]) -> list[Paper]:
    # 显式校验。早先没有这一步，参数写错（如 --sort cite 少个 s）会静默
    # 落到默认的 rank 排序，用户拿到的是「另一种排序」而不是报错，很难察觉。
    if sort_by not in SORT_KEYS:
        raise ValueError(
            f"未知排序方式：{sort_by!r}（可用：{', '.join(SORT_KEYS)}）"
        )
    if sort_by == "title":
        return sorted(papers, key=lambda p: (p.title or "").lower())
    if sort_by == "year":
        return sorted(papers, key=lambda p: (p.year or 0, p.cited_by or 0), reverse=True)
    if sort_by == "cites":
        return sorted(papers, key=lambda p: p.cited_by or 0, reverse=True)
    if sort_by == "cites_per_year":
        return sorted(papers, key=lambda p: p.cites_per_year or 0.0, reverse=True)
    if sort_by == "keyword":
        return sorted(papers, key=lambda p: p.keyword_score or 0.0, reverse=True)

    # 默认 "rank"：期刊等级 -> 年均引用 -> 关键词匹配度 -> 总引用
    def key(p: Paper) -> tuple:
        best = 99
        for s in systems:
            idx = rank_index(s, p.journal_rank.get(s))
            if idx is not None:
                best = min(best, idx)
        return (best, -(p.cites_per_year or 0.0), -(p.keyword_score or 0.0), -(p.cited_by or 0))

    return sorted(papers, key=key)


# ---------------------------------------------------------------- 主入口


@dataclass
class FilterResult:
    kept: list[Paper]
    dropped: list[tuple[Paper, str]]
    keyword_scored: bool = False

    @property
    def stats(self) -> dict[str, int]:
        return {"input": len(self.kept) + len(self.dropped), "kept": len(self.kept),
                "dropped": len(self.dropped)}


def apply(
    papers: list[Paper],
    filters: Filters,
    query_text: str = "",
    sort_by: str = "rank",
    systems: list[str] | None = None,
    top: int | None = None,
) -> FilterResult:
    systems = systems or []

    if query_text.strip():
        scores = score_keywords(papers, query_text)
        for p, s in zip(papers, scores):
            p.keyword_score = s
        keyword_scored = True
    else:
        keyword_scored = False

    kept: list[Paper] = []
    dropped: list[tuple[Paper, str]] = []
    for p in papers:
        ok, why = passes(p, filters)
        (kept if ok else dropped).append(p if ok else (p, why))

    kept = sort_papers(kept, sort_by, systems)
    if top:
        kept = kept[:top]

    return FilterResult(kept=kept, dropped=dropped, keyword_scored=keyword_scored)


__all__ = ["Bm25", "Filters", "FilterResult", "SORT_KEYS", "apply", "score_keywords",
           "sort_papers", "is_preprint", "is_correction", "RANK_ORDER"]
