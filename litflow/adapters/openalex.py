"""OpenAlex 适配器。

重要变化：2026 年起 OpenAlex 改为额度制，匿名请求走的是「同网络 IP 共享的每日预算」，
很容易被耗尽并返回 Insufficient budget。因此必须配自己的免费 key。
见 https://help.openalex.org/api/authentication/

提供能力：被引次数、期刊信息、OA 位置、抽象文本（倒排索引形式）。
"""

from __future__ import annotations

from typing import Any

from ..http import HttpError
from ..models import Paper, normalize_doi, normalize_issn
from .base import Adapter, SearchQuery

API = "https://api.openalex.org/works"

_SELECT = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "cited_by_count",
        "type",
        "type_crossref",
        "primary_location",
        "best_oa_location",
        "authorships",
        "abstract_inverted_index",
        "ids",
    ]
)

_SORT = {
    "relevance": None,  # OpenAlex 无 sort 即为相关性
    "cited": "cited_by_count:desc",
    "date": "publication_date:desc",
}


class OpenAlexAdapter(Adapter):
    name = "openalex"
    provides_citations = True
    requires_key = True

    def search(self, q: SearchQuery) -> list[Paper]:
        filters: list[str] = []
        if q.text.strip():
            filters.append(f"title_and_abstract.search:{_quote(q.text.strip())}")
        if q.author:
            filters.append(f"raw_author_name.search:{_quote(q.author.strip())}")
        if q.journal:
            filters.append(f"primary_location.source.display_name.search:{_quote(q.journal.strip())}")
        if q.year_from:
            filters.append(f"from_publication_date:{q.year_from}-01-01")
        if q.year_to:
            filters.append(f"to_publication_date:{q.year_to}-12-31")
        extras = q.extras or {}
        if extras.get("oa_only"):
            filters.append("is_oa:true")
        if extras.get("openalex_type"):
            filters.append(f"type:{extras['openalex_type']}")

        params: dict[str, Any] = {
            "per-page": min(q.limit, 200),
            "select": _SELECT,
            "api_key": self.api_key,
        }
        if filters:
            params["filter"] = ",".join(filters)
        sort = _SORT.get(q.sort)
        if sort:
            params["sort"] = sort

        try:
            data = self.http.get_json(API, params)
        except HttpError as e:
            if e.status in (401, 403, 429):
                raise HttpError(
                    f"OpenAlex 拒绝请求（HTTP {e.status}）。\n"
                    "排查方向：\n"
                    "  · config.toml 里 [sources.openalex].api_key 无效（填错或已过期）\n"
                    "  · 未配置 api_key，且匿名共享 IP 额度已用尽\n"
                    "  · 忘记把 enabled 改成 true，导致 key 根本没被用上\n"
                    "注意这里要填的是 OpenAlex 自己的 key，不是大模型（DeepSeek / OpenAI 等）的 key。\n"
                    "免费申请：https://help.openalex.org/api/authentication/\n"
                    f"原始信息：{e}",
                    status=e.status,
                    url=e.url,
                ) from None
            raise

        results = data.get("results") or []
        return [p for p in (self._to_paper(r) for r in results) if p and p.title]

    # -------------------------------------------------- 映射

    def _to_paper(self, r: dict[str, Any]) -> Paper | None:
        pl = r.get("primary_location") or {}
        src = pl.get("source") or {}
        journal = src.get("display_name")
        issn = None
        for i in (src.get("issn_l"), *(src.get("issn") or [])):
            issn = normalize_issn(i)
            if issn:
                break

        authors: list[str] = []
        for a in r.get("authorships") or []:
            nm = (a.get("author") or {}).get("display_name")
            if nm:
                authors.append(nm)

        best = r.get("best_oa_location") or {}
        oa_url = best.get("pdf_url") or best.get("landing_page_url")
        is_oa = bool(best) or (r.get("open_access") or {}).get("is_oa")

        ids = r.get("ids") or {}
        pmid = None
        if ids.get("pmid"):
            pmid = str(ids["pmid"]).rsplit("/", 1)[-1]

        return Paper(
            title=self.clean_text(r.get("title") or r.get("display_name")) or "",
            doi=normalize_doi(r.get("doi") or ids.get("doi")),
            pmid=pmid,
            abstract=_reconstruct_abstract(r.get("abstract_inverted_index")),
            authors=authors,
            journal=journal,
            issn=issn,
            year=r.get("publication_year"),
            cited_by=r.get("cited_by_count"),
            cited_by_source=self.name,
            is_oa=is_oa,
            oa_url=oa_url,
            url=r.get("id") or pl.get("landing_page_url"),
            type=r.get("type_crossref") or r.get("type"),
            sources=[self.name],
            extra={"openalex_id": r.get("id")},
        )


def _quote(s: str) -> str:
    """OpenAlex 的 search 过滤器不支持裸引号，直接返回原文即可。"""
    return s


def _reconstruct_abstract(inv: dict[str, list[int]] | None) -> str | None:
    """OpenAlex 用「词 -> 位置列表」的倒排索引表示摘要，需要还原成文本。"""
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)
