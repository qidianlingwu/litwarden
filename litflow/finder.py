"""Finder —— 多源并行检索 + 跨源归一化去重。

设计要点：
  · 单源失败不中断整体（记录到 errors，继续用其他源的结果）。
  · 并行度默认 4，配合 HttpClient 的每主机限速，不会打爆任何单个 API。
  · 去重走 models.dedupe()：DOI / PMID / arXiv 交叉配对。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .adapters import Adapter, SearchQuery
from .config import Config
from .http import HttpError
from .models import Paper, dedupe


@dataclass
class SearchReport:
    papers: list[Paper] = field(default_factory=list)
    per_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    raw_count: int = 0
    elapsed: float = 0.0
    query_desc: str = ""

    @property
    def deduped_away(self) -> int:
        return max(0, self.raw_count - len(self.papers))


def search(
    cfg: Config,
    http,
    query: SearchQuery,
    adapters: list[Adapter] | None = None,
    max_workers: int = 4,
) -> SearchReport:
    from .adapters import build_adapters

    adapters = adapters if adapters is not None else build_adapters(cfg, http)
    if not adapters:
        raise RuntimeError(
            "没有任何可用的数据源。请检查 config.toml 的 [sources] 段，"
            "至少启用 europepmc / pubmed / arxiv / crossref 之一。"
        )
    if query.is_empty():
        raise ValueError("检索条件为空：至少提供关键词、作者或期刊之一。")

    report = SearchReport(query_desc=query.describe())
    started = time.monotonic()

    raw: list[Paper] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(adapters))) as pool:
        futures = {pool.submit(_safe_search, ad, query): ad for ad in adapters}
        for fut in as_completed(futures):
            ad = futures[fut]
            try:
                papers = fut.result()
                report.per_source[ad.name] = len(papers)
                raw.extend(papers)
            except Exception as e:  # noqa: BLE001 - 单源失败必须隔离
                report.per_source[ad.name] = 0
                report.errors[ad.name] = str(e)

    report.raw_count = len(raw)
    # 引用量一旦存在，就必须同时记录「取自哪个源、什么时候取的」——
    # 不同源的引用数差异可以很大，不记录来源与时刻，筛选结果就无法复现。
    # 统一在这里补齐，而不是让每个适配器各自 remember：
    # 适配器只回答「这个源的引用数是多少」，取数时刻由编排层统一认定，
    # 这样以后新增数据源也不会漏。
    # （早先漏了这一步：实测 41 篇有引用量的论文中有 26 篇时间戳为空，
    #   只有「重复项合并」和「Europe PMC 反查回填」两条支路会写时间戳。）
    _stamp_citation_time(raw)
    report.papers = dedupe(raw)
    report.elapsed = round(time.monotonic() - started, 2)
    return report


def _stamp_citation_time(papers: list[Paper]) -> None:
    """给所有带引用量的论文补上取数时刻（就地修改）。"""
    now = _now_iso()
    for p in papers:
        if p.cited_by is not None and not p.cited_by_fetched_at:
            p.cited_by_fetched_at = now


def _safe_search(ad: Adapter, query: SearchQuery) -> list[Paper]:
    """把常见错误转成带上下文的可读信息，便于在报告里展示。"""
    try:
        return ad.search(query)
    except HttpError as e:
        raise HttpError(f"[{ad.name}] {e}", status=e.status, url=e.url) from None


def enrich_oa(cfg: Config, http, papers: list[Paper], limit: int | None = None) -> int:
    """可选：用 Unpaywall 补全 OA 全文链接。

    只对「有 DOI 且当前没有 OA 链接」的论文发起查询，避免无谓请求。
    """
    from .adapters import UnpaywallClient

    client = UnpaywallClient(http, cfg.email)
    targets = [p for p in papers if p.doi and not p.oa_url]
    if limit:
        targets = targets[:limit]
    hit = 0
    for p in targets:
        try:
            client.enrich(p)
            if p.oa_url:
                hit += 1
        except HttpError:
            # 补全失败不影响主流程
            continue
    return hit


EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def enrich_citations(http, papers: list[Paper], batch_size: int = 40) -> int:
    """用 Europe PMC 按标识批量反查被引次数。

    为什么需要这一步：
        PubMed 的 efetch 不返回被引次数，而 PubMed 与 Europe PMC 的检索结果
        排序策略不同（一个给最相关、一个默认给最新），所以大量 PubMed 记录
        不会与其他源合并 —— 实测 38 篇 PubMed 独有记录里 0 篇有引用量，
        而排序、筛选都依赖引用量。
        Europe PMC 支持 `EXT_ID:< pmid > OR DOI:"< doi >"` 批量反查，
        一次请求能补 40 篇，代价极低。

    对 arXiv 预印本无效（Europe PMC 不索引 arXiv），这是数据源本身的限制。
    """
    targets = [p for p in papers if p.cited_by is None and (p.pmid or p.doi)]
    if not targets:
        return 0

    by_pmid = {p.pmid: p for p in targets if p.pmid}
    by_doi = {p.doi: p for p in targets if p.doi}
    filled = 0

    for chunk in _chunked(targets, batch_size):
        terms = [
            f"EXT_ID:{p.pmid}" if p.pmid else f'DOI:"{p.doi}"'
            for p in chunk
        ]
        query = " OR ".join(terms)
        try:
            data = http.get_json(
                EPMC_SEARCH,
                {
                    "query": query,
                    "format": "json",
                    "pageSize": min(len(terms), 100),
                    "resultType": "core",
                },
            )
        except HttpError:
            # 补不上引用量不该中断整个检索
            continue

        # 不假设返回结构。数据源偶发返回数组或其它类型，
        # 直接 .get() 会抛 AttributeError，而这里没有 try 兜底，
        # 会让一次已经跑成功的检索在最后一步整体失败。
        if not isinstance(data, dict):
            continue
        rl = data.get("resultList")
        for r in (rl.get("result") if isinstance(rl, dict) else None) or []:
            if not isinstance(r, dict):
                continue
            cited = r.get("citedByCount")
            if not isinstance(cited, int):
                continue
            from .models import normalize_doi as _nd
            from .models import normalize_pmid as _np

            pmid = _np(r.get("pmid"))
            doi = _nd(r.get("doi"))
            hit = (by_pmid.get(pmid) if pmid else None) or (by_doi.get(doi) if doi else None)
            if hit is not None and hit.cited_by is None:
                hit.cited_by = cited
                hit.cited_by_source = "europepmc:lookup"
                hit.cited_by_fetched_at = _now_iso()
                filled += 1

    return filled


def _chunked(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
