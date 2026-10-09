"""适配器注册表。"""

from __future__ import annotations

from ..config import Config
from ..http import HttpClient
from .arxiv import ArxivAdapter
from .base import Adapter, SearchQuery
from .crossref import CrossrefAdapter
from .europepmc import EuropePmcAdapter
from .openalex import OpenAlexAdapter
from .pubmed import PubMedAdapter
from .semanticscholar import SemanticScholarAdapter
from .unpaywall import UnpaywallClient

REGISTRY: dict[str, type[Adapter]] = {
    EuropePmcAdapter.name: EuropePmcAdapter,
    PubMedAdapter.name: PubMedAdapter,
    ArxivAdapter.name: ArxivAdapter,
    CrossrefAdapter.name: CrossrefAdapter,
    OpenAlexAdapter.name: OpenAlexAdapter,
    SemanticScholarAdapter.name: SemanticScholarAdapter,
}


def build_adapters(cfg: Config, http: HttpClient) -> list[Adapter]:
    """按 config.toml 构建可用适配器。

    未配置 key 的源会被跳过（而不是报错中断），保证「无 key 也能跑通」。
    """
    out: list[Adapter] = []
    for sc in cfg.enabled_sources():
        cls = REGISTRY.get(sc.name)
        if cls is None:
            continue
        ad = cls(http, api_key=sc.api_key, email=cfg.email)
        if not ad.available():
            continue
        out.append(ad)
    return out


__all__ = [
    "REGISTRY",
    "Adapter",
    "SearchQuery",
    "build_adapters",
    "UnpaywallClient",
]
