"""检索适配器基类。

每个数据源实现 search()，把异构返回统一成 list[Paper]。
适配器只负责「查」，不负责去重与排序（那是 finder / filterer 的职责）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..http import HttpClient
from ..models import Paper

_TAG_RE = re.compile(r"<[^>]+>")

# 构造检索式时丢弃的停用词。中文场景通过不切词规避，英文靠这个表。
STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with",
    "by", "is", "are", "was", "were", "be", "been", "at", "as", "from",
    "that", "this", "these", "those", "it", "its", "we", "our", "their",
    "using", "use", "used", "via", "into", "than", "then", "but", "not",
}


def content_terms(text: str, min_len: int = 2) -> list[str]:
    """把自然语言检索式拆成内容词，用于构造布尔检索。"""
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.]*", text or "")
    out: list[str] = []
    for t in terms:
        low = t.lower()
        if len(t) < min_len or low in STOPWORDS:
            continue
        if t not in out:
            out.append(t)
    return out


@dataclass
class SearchQuery:
    """统一检索条件。各适配器按自己的能力映射到目标 API 语法。"""

    text: str = ""
    author: str | None = None
    journal: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    limit: int = 50
    # relevance | cited | date
    sort: str = "relevance"
    # 附加的源特有过滤（如 europepmc 的 SRC:PPR 只要预印本）
    extras: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.text.strip()
            or (self.author or "").strip()
            or (self.journal or "").strip()
        )

    def describe(self) -> str:
        parts = []
        if self.text:
            parts.append(f"keywords={self.text!r}")
        if self.author:
            parts.append(f"author={self.author!r}")
        if self.journal:
            parts.append(f"journal={self.journal!r}")
        if self.year_from or self.year_to:
            parts.append(f"years={self.year_from or '*'}-{self.year_to or '*'}")
        return ", ".join(parts) or "(空检索式)"


class Adapter:
    name: str = "base"
    # 该源是否提供引用量（没有的会在合并时由其他源补上）
    provides_citations: bool = False
    # 该源是否需要 API key
    requires_key: bool = False

    def __init__(self, http: HttpClient, api_key: str = "", email: str = "") -> None:
        self.http = http
        self.api_key = api_key
        self.email = email

    def available(self) -> bool:
        return not self.requires_key or bool(self.api_key)

    def unavailable_reason(self) -> str:
        return f"{self.name} 需要 API key，但 config.toml 里未配置"

    def search(self, q: SearchQuery) -> list[Paper]:  # pragma: no cover - 抽象
        raise NotImplementedError

    # -------------------------------------------------- 工具

    @staticmethod
    def clean_text(s: str | None) -> str:
        """去掉 JATS/HTML 标签并压缩空白。

        顺序很重要：必须先反转义实体，再剥标签。
        否则 `&lt;i&gt;HMGB1&lt;/i&gt;` 会先被跳过标签剥离、
        再被反转义成 `<i>HMGB1</i>`，把标签残留到标题里。
        """
        if not s:
            return ""
        s = (
            s.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&#x2010;", "-")
            .replace("&nbsp;", " ")
        )
        s = _TAG_RE.sub(" ", s)
        return re.sub(r"\s+", " ", s).strip()

    @staticmethod
    def parse_year(s: str | int | None) -> int | None:
        if s is None:
            return None
        m = re.search(r"(1[89]\d{2}|20\d{2})", str(s))
        return int(m.group(1)) if m else None
