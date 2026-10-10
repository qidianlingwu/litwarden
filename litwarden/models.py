"""文献数据模型与跨源归一化。

关键设计：显式记录「引用量来自哪个源、什么时候取的」。
Crossref 的 is-referenced-by-count 与 OpenAlex 的 cited_by_count 差异很大，
不记录来源就无法复现筛选结果。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------- 归一化

_DOI_PREFIX_RE = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+")
_ARXIV_PREFIX_RE = re.compile(r"^\s*(?:arxiv:|https?://arxiv\.org/abs/)", re.I)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.I)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_doi(raw: str | None) -> str | None:
    """归一化 DOI：去前缀、小写、截掉尾部标点。"""
    if not raw:
        return None
    s = _DOI_PREFIX_RE.sub("", str(raw).strip())
    m = _DOI_RE.search(s)
    if not m:
        return None
    return m.group(0).rstrip(".,;)]}").lower()


def normalize_arxiv(raw: str | None) -> str | None:
    """归一化 arXiv ID：去前缀、去版本号。"""
    if not raw:
        return None
    s = _ARXIV_PREFIX_RE.sub("", str(raw).strip())
    s = _ARXIV_VERSION_RE.sub("", s)
    s = s.strip().strip("/")
    return s.lower() or None


def normalize_pmid(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^0-9]", "", str(raw))
    return s or None


def normalize_title(title: str | None) -> str:
    """标题归一化，用于无 DOI/PMID 时的兜底配对。"""
    if not title:
        return ""
    return _NON_ALNUM_RE.sub(" ", str(title).lower()).strip()


def normalize_journal(name: str | None) -> str:
    """期刊名归一化，用于查等级表。去掉常见后缀噪声、去标点、小写。"""
    if not name:
        return ""
    s = str(name).lower()
    s = re.sub(r"\b(the|journal of|international journal of)\b", " ", s)
    s = _NON_ALNUM_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_issn(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^0-9xX]", "", str(raw)).upper()
    if len(s) != 8:
        return None
    return f"{s[:4]}-{s[4:]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 模型


@dataclass
class Paper:
    """一篇文献在候选池中的统一表示。"""

    title: str = ""
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    issn: str | None = None
    year: int | None = None
    cited_by: int | None = None
    cited_by_source: str | None = None
    cited_by_fetched_at: str | None = None
    is_oa: bool | None = None
    oa_url: str | None = None
    url: str | None = None
    type: str | None = None
    # 命中该文献的数据源列表，用于判断可信度与字段来源
    sources: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- 本地派生字段（不入 API 层）----
    keyword_score: float | None = None
    journal_rank: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """稳定标识。优先级 DOI > PMID > arXiv > 标题+年份。"""
        if self.doi:
            return f"doi:{self.doi}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        h = hashlib.sha1(normalize_title(self.title).encode()).hexdigest()[:12]
        return f"title:{h}:{self.year or 'na'}"

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""

    @property
    def author_str(self) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    @property
    def cites_per_year(self) -> float | None:
        """年均引用。

        新论文天然总引用低，按总引用排序会让候选池全是老论文，
        所以筛选默认提供年均引用排序。
        """
        if self.cited_by is None or not self.year:
            return None
        age = max(1, datetime.now().year - self.year)
        return round(self.cited_by / age, 2)

    def merge(self, other: "Paper") -> "Paper":
        """合并同一篇文献在不同源的记录，就地修改并返回 self。"""
        self.sources = sorted(set(self.sources) | set(other.sources))

        # 结构性字段：谁有取谁
        self.doi = self.doi or other.doi
        self.pmid = self.pmid or other.pmid
        self.arxiv_id = self.arxiv_id or other.arxiv_id
        self.issn = self.issn or other.issn
        self.journal = self.journal or other.journal
        self.year = self.year or other.year
        self.type = self.type or other.type
        self.url = self.url or other.url
        self.oa_url = self.oa_url or other.oa_url

        # 标题：取更长且非全大写的那个（部分源会返回截断标题）
        if other.title and (not self.title or len(other.title) > len(self.title)):
            self.title = other.title

        # 摘要：取更长
        if other.abstract and (not self.abstract or len(other.abstract) > len(self.abstract)):
            self.abstract = other.abstract

        # 作者：取更完整的列表
        if len(other.authors) > len(self.authors):
            self.authors = other.authors

        # 引用量：取更大的，并记录来源（可追溯性要求）
        if other.cited_by is not None:
            if self.cited_by is None or other.cited_by > self.cited_by:
                self.cited_by = other.cited_by
                # 写成一行三元表达式很容易读错，注意这里必须显式加括号：
                #   other.cited_by_source or other.sources[0] if other.sources else X
                # 会被解析成 (A or B) if cond else X，而不是 A or (B if cond else X)。
                # 语义：优先用上游显式标注的来源；没有就退回该记录命中的数据源
                # （单源记录时就是提供引用数的那家；多源时只能取第一个，属已知近似）。
                src = other.cited_by_source or (
                    other.sources[0] if other.sources else None
                )
                self.cited_by_source = src
                self.cited_by_fetched_at = other.cited_by_fetched_at or _now()

        # OA：任一说 open 即 open；同时保留可用的 OA 链接
        if other.is_oa:
            self.is_oa = True
        elif self.is_oa is None:
            self.is_oa = other.is_oa

        self.extra.update({k: v for k, v in other.extra.items() if v not in (None, "")})
        return self

    def to_row(self) -> dict[str, Any]:
        import json

        return {
            "key": self.key,
            "doi": self.doi,
            "pmid": self.pmid,
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": json.dumps(self.authors, ensure_ascii=False),
            "journal": self.journal,
            "issn": self.issn,
            "year": self.year,
            "cited_by": self.cited_by,
            "cited_by_source": self.cited_by_source,
            "cited_by_fetched_at": self.cited_by_fetched_at,
            "is_oa": None if self.is_oa is None else int(self.is_oa),
            "oa_url": self.oa_url,
            "url": self.url,
            "type": self.type,
            "sources": json.dumps(self.sources, ensure_ascii=False),
            "extra": json.dumps(self.extra, ensure_ascii=False),
            "updated_at": _now(),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Paper":
        import json

        def _j(v: Any, default: Any) -> Any:
            if not v:
                return default
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return default

        return cls(
            title=row.get("title") or "",
            doi=row.get("doi"),
            pmid=row.get("pmid"),
            arxiv_id=row.get("arxiv_id"),
            abstract=row.get("abstract"),
            authors=_j(row.get("authors"), []),
            journal=row.get("journal"),
            issn=row.get("issn"),
            year=row.get("year"),
            cited_by=row.get("cited_by"),
            cited_by_source=row.get("cited_by_source"),
            cited_by_fetched_at=row.get("cited_by_fetched_at"),
            is_oa=None if row.get("is_oa") is None else bool(row.get("is_oa")),
            oa_url=row.get("oa_url"),
            url=row.get("url"),
            type=row.get("type"),
            sources=_j(row.get("sources"), []),
            extra=_j(row.get("extra"), {}),
        )


def dedupe(papers: Iterable[Paper]) -> list[Paper]:
    """两轮去重：先按标识配对，再按标题配对。

    第一轮：DOI / PMID / arXiv 交叉配对（同一论文在不同源可能只有其中一种标识）。
    第二轮：标题 + 年份配对。必要性来自实测——同一篇论文会有多个 DOI 变体，
    例如 eLife 的版本化 DOI：
        10.7554/elife.88431
        10.7554/elife.88431.3
    两者标题年份完全一致，但 DOI 字符串不同，第一轮抓不住。
    """
    merged: dict[str, Paper] = {}
    order: list[str] = []
    # 交叉索引：别名 -> 主 key
    alias: dict[str, str] = {}

    for p in papers:
        candidates = []
        if p.doi:
            candidates.append(f"doi:{p.doi}")
        if p.pmid:
            candidates.append(f"pmid:{p.pmid}")
        if p.arxiv_id:
            candidates.append(f"arxiv:{p.arxiv_id}")
        candidates.append(p.key)

        hit = next((alias[c] for c in candidates if c in alias), None)
        if hit is None:
            primary = p.key
            merged[primary] = p
            order.append(primary)
        else:
            primary = hit
            merged[primary].merge(p)

        for c in candidates:
            alias[c] = primary

    first_pass = [merged[k] for k in order]

    # ---- 第二轮：标题 + 年份 ----
    # 标题太短（<20 字符）不足以作为身份依据，跳过以免误合并。
    out: list[Paper] = []
    by_title: dict[str, list[Paper]] = {}
    for p in first_pass:
        nt = normalize_title(p.title)
        if len(nt) < 20:
            out.append(p)
            continue
        bucket = by_title.get(nt)
        if bucket is None:
            by_title[nt] = [p]
            out.append(p)
            continue
        # 已存在同名标题：年份相同、或有一方缺年份、或相差 1 年内，视为同一篇
        target = next(
            (
                q for q in bucket
                if q.year is None
                or p.year is None
                or abs(p.year - q.year) <= 1
            ),
            None,
        )
        if target is None:
            bucket.append(p)
            out.append(p)
        else:
            target.merge(p)

    return out
