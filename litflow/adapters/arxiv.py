"""arXiv 适配器。

生命科学方向主要落在 q-bio.* 分类，神经科学与信号处理方向则用
eess.SP / q-bio.NC / cs.LG。适配器不限定分类，检索范围由调用方给的关键词决定。
无 key，但官方要求请求间隔 >= 3 秒；限速由 HttpClient 统一控制。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import Paper, normalize_arxiv, normalize_doi
from .base import Adapter, SearchQuery, content_terms

API = "https://export.arxiv.org/api/query"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

_SORT = {
    "relevance": "relevance",
    "date": "submittedDate",
    # arXiv 没有引用量数据，cited 退化为按提交日期
    "cited": "submittedDate",
}


class ArxivAdapter(Adapter):
    name = "arxiv"
    provides_citations = False

    def search(self, q: SearchQuery) -> list[Paper]:
        sq = self._build_query(q)
        if not sq:
            return []

        root = self.http.get_xml(
            API,
            {
                "search_query": sq,
                "start": 0,
                "max_results": min(q.limit, 100),
                "sortBy": _SORT.get(q.sort, "relevance"),
                "sortOrder": "descending",
            },
        )

        out: list[Paper] = []
        for entry in root.findall("atom:entry", NS):
            p = self._to_paper(entry)
            if p and p.title:
                out.append(p)
        return out

    # -------------------------------------------------- 查询构造

    def _build_query(self, q: SearchQuery) -> str:
        parts: list[str] = []
        if q.text.strip():
            parts.append(_text_clause(q.text))
        if q.author:
            parts.append(f'au:"{q.author.strip()}"')
        # arXiv 没有期刊字段，journal 条件直接忽略（在 finder 里会提示）
        if q.year_from or q.year_to:
            lo = q.year_from or 1991
            hi = q.year_to or 2100
            parts.append(f"submittedDate:[{lo}01010000 TO {hi}12312359]")
        cat = q.extras.get("arxiv_cat")
        if cat:
            parts.append(f"cat:{cat}")
        return " AND ".join(parts)

    # -------------------------------------------------- 映射

    def _to_paper(self, entry: ET.Element) -> Paper | None:
        raw_id = (entry.findtext("atom:id", default="", namespaces=NS) or "").strip()
        arxiv_id = normalize_arxiv(raw_id.rsplit("/", 1)[-1] if raw_id else None)

        title = self.clean_text(entry.findtext("atom:title", default="", namespaces=NS))
        summary = self.clean_text(entry.findtext("atom:summary", default="", namespaces=NS))
        published = entry.findtext("atom:published", default="", namespaces=NS)

        authors = [
            self.clean_text(a.findtext("atom:name", default="", namespaces=NS))
            for a in entry.findall("atom:author", NS)
        ]
        authors = [a for a in authors if a]

        doi = normalize_doi(entry.findtext("arxiv:doi", default="", namespaces=NS))
        journal_ref = self.clean_text(
            entry.findtext("arxiv:journal_ref", default="", namespaces=NS)
        )
        primary_cat = None
        pc = entry.find("arxiv:primary_category", NS)
        if pc is not None:
            primary_cat = pc.get("term")

        pdf_url = None
        for link in entry.findall("atom:link", NS):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href")
                break

        return Paper(
            title=title,
            doi=doi or None,
            arxiv_id=arxiv_id,
            abstract=summary or None,
            authors=authors,
            journal=journal_ref or "arXiv (preprint)",
            year=self.parse_year(published),
            cited_by=None,  # arXiv 不返回引用量
            is_oa=True,
            oa_url=pdf_url or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None),
            url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else raw_id or None,
            type="preprint",
            sources=[self.name],
            extra={
                "arxiv_categories": [c.get("term") for c in entry.findall("atom:category", NS)],
                "primary_category": primary_cat,
                "is_preprint": True,
            },
        )


def _text_clause(text: str) -> str:
    """把自然语言检索式转成 arXiv 能用的布尔式。

    早先的实现直接把整句包成 `all:"..."` 短语，对多词长句几乎零命中
    （arXiv 要求词序完全连续）。改成内容词 AND 组合：
        all:(single-cell AND RNA AND sequencing AND batch)
    单词时仍走短语，保证精度。
    """
    terms = content_terms(text)
    if not terms:
        return ""
    if len(terms) == 1:
        return f'all:"{terms[0]}"'
    return "all:(" + " AND ".join(f'"{t}"' for t in terms) + ")"
