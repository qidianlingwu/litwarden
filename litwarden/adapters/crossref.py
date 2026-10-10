"""Crossref 适配器。

作用不是「发现」而是「元数据校准」：DOI 权威来源、期刊全名、ISSN、引用计数。
无 key 可用（带 mailto 会进入更快的礼貌池）。
"""

from __future__ import annotations

from typing import Any

from ..models import Paper, normalize_doi, normalize_issn
from .base import Adapter, SearchQuery

API = "https://api.crossref.org/works"

_SELECT = ",".join(
    [
        "DOI",
        "title",
        "subtitle",
        "author",
        "issued",
        "published",
        "container-title",
        "short-container-title",
        "ISSN",
        "is-referenced-by-count",
        "abstract",
        "type",
        "URL",
        "link",
    ]
)

_SORT = {
    "relevance": ("relevance", "desc"),
    "cited": ("is-referenced-by-count", "desc"),
    "date": ("published", "desc"),
}


class CrossrefAdapter(Adapter):
    name = "crossref"
    provides_citations = True

    def search(self, q: SearchQuery) -> list[Paper]:
        params: dict[str, Any] = {
            "rows": min(q.limit, 100),
            "select": _SELECT,
            "mailto": self.email,
        }
        if q.text.strip():
            params["query.bibliographic"] = q.text.strip()
        if q.author:
            params["query.author"] = q.author.strip()
        if q.journal:
            params["query.container-title"] = q.journal.strip()

        filters = ["type:journal-article"]
        extras = q.extras or {}
        if extras.get("crossref_type"):
            filters = [f"type:{extras['crossref_type']}"]
        if q.year_from:
            filters.append(f"from-pub-date:{q.year_from}-01-01")
        if q.year_to:
            filters.append(f"until-pub-date:{q.year_to}-12-31")
        params["filter"] = ",".join(filters)

        sort, order = _SORT.get(q.sort, _SORT["relevance"])
        params["sort"] = sort
        params["order"] = order

        data = self.http.get_json(API, params)
        items = ((data.get("message") or {}).get("items")) or []
        return [self._to_paper(it) for it in items if it.get("title")]

    # -------------------------------------------------- 映射

    def _to_paper(self, it: dict[str, Any]) -> Paper:
        title = self.clean_text((it.get("title") or [""])[0])
        subtitle = (it.get("subtitle") or [""])[0]
        if subtitle:
            title = f"{title}: {self.clean_text(subtitle)}"

        authors: list[str] = []
        for a in it.get("author") or []:
            if a.get("family"):
                nm = f"{a.get('given', '')} {a['family']}".strip()
                authors.append(nm)
            elif a.get("name"):
                authors.append(self.clean_text(a["name"]))

        year = None
        for k in ("issued", "published"):
            dp = ((it.get(k) or {}).get("date-parts") or [[None]])[0]
            if dp and dp[0]:
                year = self.parse_year(dp[0])
                if year:
                    break

        container = (it.get("container-title") or [""])[0] or (
            it.get("short-container-title") or [""]
        )[0]

        issn = None
        for i in it.get("ISSN") or []:
            issn = normalize_issn(i)
            if issn:
                break

        # OA 链接：Crossref 的 link 字段偶尔给全文
        oa_url = None
        for ln in it.get("link") or []:
            if ln.get("content-type") == "application/pdf":
                oa_url = ln.get("URL")
                break

        return Paper(
            title=title,
            doi=normalize_doi(it.get("DOI")),
            abstract=self.clean_text(it.get("abstract")) or None,
            authors=authors,
            journal=self.clean_text(container) or None,
            issn=issn,
            year=year,
            cited_by=it.get("is-referenced-by-count"),
            cited_by_source=self.name,
            is_oa=True if oa_url else None,
            oa_url=oa_url,
            url=it.get("URL"),
            type=self.clean_text(it.get("type")),
            sources=[self.name],
        )
