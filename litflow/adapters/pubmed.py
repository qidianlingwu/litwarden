"""PubMed 适配器（NCBI E-utilities）。

无 key 可用（官方建议带 email 与 tool 参数，会有更高配额）。
两段式调用：esearch 拿 PMID 列表 -> efetch 拿结构化 XML。

注意：efetch 不返回被引次数。这是有意的字段缺口，
引用量留给 Europe PMC / OpenAlex 在合并阶段补上。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..models import Paper, normalize_doi, normalize_issn, normalize_pmid
from .base import Adapter, SearchQuery

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# PubMed 支持的排序键
_SORT = {
    "relevance": "relevance",
    "date": "pub_date",
}


class PubMedAdapter(Adapter):
    name = "pubmed"
    provides_citations = False  # efetch 不给引用量

    def search(self, q: SearchQuery) -> list[Paper]:
        term = self._build_term(q)
        if not term:
            return []

        common = {"tool": "litflow", "email": self.email}

        es = self.http.get_json(
            ESEARCH,
            {
                "db": "pubmed",
                "term": term,
                "retmax": min(q.limit, 200),
                "retmode": "json",
                "sort": _SORT.get(q.sort, "relevance"),
                **common,
            },
        )
        idlist = ((es.get("esearchresult") or {}).get("idlist")) or []
        if not idlist:
            return []

        root = self.http.get_xml(
            EFETCH,
            {
                "db": "pubmed",
                "id": ",".join(str(i) for i in idlist),
                "retmode": "xml",
                **common,
            },
        )

        papers: list[Paper] = []
        for art in root.iter("PubmedArticle"):
            p = self._to_paper(art)
            if p and p.title:
                papers.append(p)
        return papers

    # -------------------------------------------------- 查询构造

    def _build_term(self, q: SearchQuery) -> str:
        clauses: list[str] = []
        if q.text.strip():
            clauses.append(f"({q.text.strip()})")
        if q.author:
            clauses.append(f'"{q.author.strip()}"[au]')
        if q.journal:
            j = q.journal.strip()
            # 期刊全名与缩写都试
            clauses.append(f'("{j}"[Journal] OR "{j}"[ta])')
        if q.year_from or q.year_to:
            lo = q.year_from or 1800
            hi = q.year_to or 2100
            clauses.append(f'("{lo}"[dp] : "{hi}"[dp])')
        return " AND ".join(clauses)

    # -------------------------------------------------- 映射

    def _to_paper(self, art: ET.Element) -> Paper | None:
        cit = art.find("MedlineCitation")
        if cit is None:
            return None
        artel = cit.find("Article")
        if artel is None:
            return None

        pmid = normalize_pmid(_text(cit.find("PMID")))

        title = self.clean_text(_iter_text(artel.find("ArticleTitle")))

        # 摘要：多个 AbstractText 段拼接，带 Label 前缀
        abstract_parts: list[str] = []
        for node in artel.findall("./Abstract/AbstractText"):
            seg = self.clean_text(_iter_text(node))
            if not seg:
                continue
            label = (node.get("Label") or "").strip()
            abstract_parts.append(f"{label}: {seg}" if label else seg)
        abstract = " ".join(abstract_parts) or None

        journal_el = artel.find("Journal")
        journal = iso = issn = None
        year = None
        if journal_el is not None:
            journal = self.clean_text(_text(journal_el.find("Title")))
            iso = self.clean_text(_text(journal_el.find("ISOAbbreviation")))
            for iss in journal_el.findall("ISSN"):
                issn = normalize_issn(_text(iss)) or issn
            pd = journal_el.find("./JournalIssue/PubDate")
            if pd is not None:
                year = self.parse_year(_text(pd.find("Year")) or _text(pd.find("MedlineDate")))

        if year is None:
            pd = artel.find("./ArticleDate")
            if pd is not None:
                year = self.parse_year(_text(pd.find("Year")))

        # 作者
        authors: list[str] = []
        for a in artel.findall("./AuthorList/Author"):
            coll = _text(a.find("CollectiveName"))
            if coll:
                authors.append(self.clean_text(coll))
                continue
            last = _text(a.find("LastName"))
            fore = _text(a.find("ForeName")) or _text(a.find("Initials"))
            if last:
                authors.append(f"{fore} {last}".strip() if fore else last)

        # DOI：先看 ELocationID，再看 PubmedData 的 ArticleIdList
        doi = None
        for el in artel.findall("ELocationID"):
            if (el.get("EIdType") or "").lower() == "doi":
                doi = normalize_doi(_text(el))
                if doi:
                    break
        pmcid = None
        pubdata = art.find("PubmedData")
        if pubdata is not None:
            for aid in pubdata.findall("./ArticleIdList/ArticleId"):
                t = (aid.get("IdType") or "").lower()
                if t == "doi" and not doi:
                    doi = normalize_doi(_text(aid))
                elif t == "pmc":
                    pmcid = _text(aid)
                elif t == "pubmed" and not pmid:
                    pmid = normalize_pmid(_text(aid))

        pubtypes = [
            self.clean_text(_text(pt))
            for pt in artel.findall("./PublicationTypeList/PublicationType")
        ]
        pubtypes = [p for p in pubtypes if p]

        issn = issn or normalize_issn(
            _text((cit.find("MedlineJournalInfo") or ET.Element("x")).find("ISSNLinking"))
        )

        is_oa = None
        oa_url = None
        if pmcid:
            is_oa = True
            oa_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"

        return Paper(
            title=title,
            doi=doi,
            pmid=pmid,
            abstract=abstract,
            authors=authors,
            journal=journal or iso,
            issn=issn,
            year=year,
            cited_by=None,  # 交给其他源补
            is_oa=is_oa,
            oa_url=oa_url,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            type=",".join(pubtypes[:2]) or "Journal Article",
            sources=[self.name],
            extra={"pmcid": pmcid},
        )


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _iter_text(el: ET.Element | None) -> str | None:
    """取出元素内所有文本（ArticleTitle 里可能嵌 <i> <sub> 等标签）。"""
    if el is None:
        return None
    return "".join(el.itertext()).strip() or None
