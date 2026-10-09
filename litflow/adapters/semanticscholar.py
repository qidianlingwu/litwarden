"""Semantic Scholar 适配器。

优势：被引次数、TLDR 摘要、openAccessPdf 直链，且能给出 arXiv/PubMed 外部 ID
（对跨源去重很有价值）。无 key 时会被限流（429），建议申请免费 key。
"""

from __future__ import annotations

from typing import Any

from ..http import HttpError
from ..models import Paper, normalize_arxiv, normalize_doi, normalize_pmid
from .base import Adapter, SearchQuery

API = "https://api.semanticscholar.org/graph/v1/paper/search"

_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "year",
        "publicationDate",
        "citationCount",
        "influentialCitationCount",
        "authors",
        "venue",
        "publicationVenue",
        "journal",
        "externalIds",
        "openAccessPdf",
        "publicationTypes",
        "tldr",
    ]
)


class SemanticScholarAdapter(Adapter):
    name = "semanticscholar"
    provides_citations = True
    requires_key = True

    def search(self, q: SearchQuery) -> list[Paper]:
        builder: list[str] = []
        if q.text.strip():
            builder.append(q.text.strip())
        if q.author:
            builder.append(q.author.strip())

        params: dict[str, Any] = {
            "query": " ".join(builder) or q.journal or "",
            "limit": min(q.limit, 100),
            "fields": _FIELDS,
        }
        if q.year_from or q.year_to:
            params["year"] = f"{q.year_from or ''}-{q.year_to or ''}"
        if q.journal:
            params["venue"] = q.journal.strip()
        if q.sort == "cited":
            params["sort"] = "citationCount:desc"

        headers = {"x-api-key": self.api_key} if self.api_key else {}
        try:
            data = self.http.get_json(API, params, headers=headers)
        except HttpError as e:
            if e.status == 429:
                raise HttpError(
                    "Semantic Scholar 限流（HTTP 429）。无 key 时共享池很拥挤，"
                    "建议申请免费 key 并填到 config.toml。\n"
                    "申请：https://www.semanticscholar.org/product/api#api-key-form",
                    status=429,
                    url=e.url,
                ) from None
            if e.status in (401, 403):
                # 最常见的实际原因是把大模型的 key 填到了这里。
                # 裸的 HTTP 403 提示让人无从下手，所以把排查方向直接写出来。
                raise HttpError(
                    f"Semantic Scholar 拒绝请求（HTTP {e.status}）。\n"
                    "排查方向：\n"
                    "  · config.toml 里 [sources.semanticscholar].api_key 无效（填错或已过期）\n"
                    "  · 这里要填的是 Semantic Scholar 自己的 key，不是大模型（DeepSeek / OpenAI 等）的 key\n"
                    "  · 免费申请：https://www.semanticscholar.org/product/api#api-key-form\n"
                    f"原始信息：{e}",
                    status=e.status,
                    url=e.url,
                ) from None
            raise

        # 不假设返回结构，避免源改格式时抛 AttributeError 打断整次检索
        if not isinstance(data, dict):
            return []

        out: list[Paper] = []
        for it in data.get("data") or []:
            p = self._to_paper(it)
            if p and p.title:
                out.append(p)
        return out

    # -------------------------------------------------- 映射

    def _to_paper(self, it: dict[str, Any]) -> Paper | None:
        ext = it.get("externalIds") or {}
        oa = it.get("openAccessPdf") or {}
        tldr = (it.get("tldr") or {}).get("text")

        venue = it.get("venue")
        if not venue:
            pv = it.get("publicationVenue") or {}
            venue = pv.get("name")
        if not venue:
            venue = (it.get("journal") or {}).get("name")

        authors = [a.get("name") for a in (it.get("authors") or []) if a.get("name")]

        year = it.get("year")
        if not year and it.get("publicationDate"):
            year = self.parse_year(it["publicationDate"])

        abstract = self.clean_text(it.get("abstract"))
        if not abstract and tldr:
            abstract = f"[TLDR] {self.clean_text(tldr)}"

        return Paper(
            title=self.clean_text(it.get("title")) or "",
            doi=normalize_doi(ext.get("DOI")),
            pmid=normalize_pmid(ext.get("PubMed")),
            arxiv_id=normalize_arxiv(ext.get("ArXiv")),
            abstract=abstract or None,
            authors=authors,
            journal=venue,
            year=year,
            cited_by=it.get("citationCount"),
            cited_by_source=self.name,
            is_oa=bool(oa.get("url")) if oa else None,
            oa_url=oa.get("url"),
            url=f"https://www.semanticscholar.org/paper/{it.get('paperId')}",
            type=",".join(it.get("publicationTypes") or []) or None,
            sources=[self.name],
            extra={"s2_id": it.get("paperId"), "tldr": tldr},
        )
