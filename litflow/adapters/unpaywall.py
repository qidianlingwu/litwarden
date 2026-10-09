"""Unpaywall —— 合法开放获取（OA）全文定位。

它不是检索源，而是「补全器」：拿到 DOI 后查这个 DOI 有没有合法的免费全文。
走的是出版社/仓储的合法 OA 副本（PMC、机构库、预印本等），不绕过付费墙。

注意：Unpaywall 强制要求真实邮箱，example.com 之类的占位邮箱会被直接拒绝。
"""

from __future__ import annotations

from typing import Any

from ..http import HttpError, HttpClient
from ..models import Paper

API = "https://api.unpaywall.org/v2"


class UnpaywallClient:
    def __init__(self, http: HttpClient, email: str) -> None:
        self.http = http
        self.email = email

    def lookup(self, doi: str) -> dict[str, Any] | None:
        if not doi:
            return None
        try:
            return self.http.get_json(f"{API}/{doi}", {"email": self.email})
        except HttpError as e:
            # 404 = 这个 DOI 还不在 Unpaywall 库里，属正常情况，不当错误
            if e.status == 404:
                return None
            raise

    def enrich(self, paper: Paper) -> Paper:
        """补充/修正 paper 的 OA 信息。原地修改并返回。"""
        if not paper.doi:
            return paper
        data = self.lookup(paper.doi)
        if not data:
            return paper

        is_oa = data.get("is_oa")
        if is_oa is not None:
            paper.is_oa = bool(is_oa)

        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        if url:
            paper.oa_url = url
            paper.extra["oa_host"] = best.get("host_type")
            paper.extra["oa_version"] = best.get("version")
            paper.extra["oa_license"] = best.get("license")

        # Unpaywall 还能回填期刊正式名，用于查期刊等级表
        if not paper.journal and data.get("journal_name"):
            paper.journal = data["journal_name"]
        if not paper.issn and data.get("journal_issns"):
            from ..models import normalize_issn

            for i in str(data["journal_issns"]).split(","):
                issn = normalize_issn(i)
                if issn:
                    paper.issn = issn
                    break
        return paper
