"""Europe PMC 适配器 —— 生信方向的主力源。

为什么把它放第一位：
  · 一个接口同时覆盖 PubMed 文献（SRC:MED）和 bioRxiv/medRxiv 预印本（SRC:PPR），
    预印本对生信很重要，很多方法学论文先上 bioRxiv。
  · 免费、无需 key。
  · 直接返回 citedByCount 与 isOpenAccess，省掉一次交叉查询。
  · 支持字段化检索语法（AUTH: / JOURNAL: / PUB_YEAR: / TITLE_ABS:）。
"""

from __future__ import annotations

from typing import Any

from ..models import Paper, normalize_doi, normalize_issn, normalize_pmid
from .base import Adapter, SearchQuery

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Europe PMC 的排序语法是 "字段 方向"
_SORT = {
    "relevance": None,
    "cited": "CITED desc",
    "date": "P_PDATE_D desc",
}


class EuropePmcAdapter(Adapter):
    name = "europepmc"
    provides_citations = True

    # 相关性模式下并取两种排序。
    # 原因（实测）：Europe PMC 不带 sort 参数时按「日期倒序」返回，
    # 而不是相关性——搜一个领域只会看到当年新论文，经典文献完全不会出现。
    # 因此相关性模式下同时取「默认（最新）」与「CITED desc（经典）」两批再合并。
    _RELEVANCE_SORTS: tuple[str | None, ...] = (None, "CITED desc")

    def search(self, q: SearchQuery) -> list[Paper]:
        query = self._build_query(q)
        sorts = self._sorts_for(q)
        # 多路检索时每路分一半配额，避免总数超出 q.limit 太多
        per_page = max(5, q.limit // len(sorts))

        merged: dict[str, Paper] = {}
        for sort in sorts:
            for p in self._fetch(query, sort, per_page):
                merged.setdefault(p.key, p)
        return list(merged.values())

    def _sorts_for(self, q: SearchQuery) -> tuple[str | None, ...]:
        if q.sort == "relevance":
            return self._RELEVANCE_SORTS
        return (_SORT.get(q.sort),)

    def _fetch(self, query: str, sort: str | None, per_page: int) -> list[Paper]:
        params: dict[str, Any] = {
            "query": query,
            "format": "json",
            "pageSize": min(max(per_page, 1), 100),
            "resultType": "core",
        }
        if sort:
            params["sort"] = sort

        data = self.http.get_json(API, params)
        results = (data.get("resultList") or {}).get("result") or []
        return [self._to_paper(r) for r in results if r.get("title")]

    # -------------------------------------------------- 查询构造

    def _build_query(self, q: SearchQuery) -> str:
        parts: list[str] = []
        if q.text.strip():
            # 短语加引号更精确，但会漏掉同义表达；这里用括号包整体，交给 EPMC 做分词
            parts.append(f"({q.text.strip()})")
        if q.author:
            parts.append(f'AUTH:"{q.author.strip()}"')
        if q.journal:
            parts.append(f'JOURNAL:"{q.journal.strip()}"')
        if q.year_from or q.year_to:
            lo = q.year_from or 1800
            hi = q.year_to or 2100
            parts.append(f"PUB_YEAR:[{lo} TO {hi}]")

        # 源过滤（预印本 / 期刊论文）
        src = q.extras.get("epmc_src")
        if src:
            parts.append(f"SRC:{src}")

        return " AND ".join(parts) if parts else "*"

    # -------------------------------------------------- 映射

    def _to_paper(self, r: dict[str, Any]) -> Paper:
        ji = r.get("journalInfo") or {}
        j = ji.get("journal") or {}

        issn = normalize_issn(j.get("issn")) or normalize_issn(j.get("essn"))

        # 作者：优先 authorList（结构化），退化到 authorString
        authors: list[str] = []
        for a in ((r.get("authorList") or {}).get("author") or []):
            nm = a.get("fullName") or " ".join(
                x for x in [a.get("firstName"), a.get("lastName")] if x
            )
            if nm:
                authors.append(nm.strip())
        if not authors and r.get("authorString"):
            authors = [s.strip() for s in str(r["authorString"]).rstrip(".").split(",") if s.strip()]

        year = (
            self.parse_year(ji.get("yearOfPublication"))
            or self.parse_year(r.get("firstPublicationDate"))
            or self.parse_year(ji.get("printPublicationDate"))
        )

        is_oa = str(r.get("isOpenAccess", "")).upper() == "Y"
        oa_url = self._oa_url(r, is_oa)

        src = r.get("source")  # MED / PPR / PMC / AGR
        ptype = src
        pubtypes = (r.get("pubTypeList") or {}).get("pubType") or []
        if isinstance(pubtypes, list) and pubtypes:
            ptype = f"{src}:{pubtypes[0]}"

        return Paper(
            title=self.clean_text(r.get("title")) or "",
            doi=normalize_doi(r.get("doi")),
            pmid=normalize_pmid(r.get("pmid")) or (
                normalize_pmid(r.get("id")) if src == "MED" else None
            ),
            abstract=self.clean_text(r.get("abstractText")) or None,
            authors=authors,
            journal=j.get("title") or j.get("medlineAbbreviation"),
            issn=issn,
            year=year,
            cited_by=r.get("citedByCount") if isinstance(r.get("citedByCount"), int) else None,
            cited_by_source=self.name,
            # 取数时刻不在这里写：适配器只回答「这个源的引用数是多少」，
            # 「什么时候取的」由 finder 编排层统一补齐（见 finder._stamp_citation_time）。
            is_oa=is_oa if r.get("isOpenAccess") is not None else None,
            oa_url=oa_url,
            url=f"https://europepmc.org/article/{src}/{r.get('id')}",
            type=ptype,
            sources=[self.name],
            extra={
                "epmc_id": r.get("id"),
                "epmc_source": src,
                "pmcid": r.get("pmcid"),
                "is_preprint": src == "PPR",
            },
        )

    @staticmethod
    def _oa_url(r: dict[str, Any], is_oa: bool) -> str | None:
        # 1) 优先用 fullTextUrlList 里标注 OA 的链接
        ftl = (r.get("fullTextUrlList") or {}).get("fullTextUrl") or []
        best_pdf = best_html = None
        for u in ftl:
            if str(u.get("availabilityCode", "")).upper() != "OA":
                continue
            style = u.get("documentStyle")
            if style == "pdf" and not best_pdf:
                best_pdf = u.get("url")
            elif style == "html" and not best_html:
                best_html = u.get("url")
        if best_pdf or best_html:
            return best_pdf or best_html

        # 2) 退化到 EPMC 开放全文接口
        pmcid = r.get("pmcid")
        if is_oa and pmcid:
            return f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
        return None
