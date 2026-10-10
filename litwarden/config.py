"""配置加载。使用标准库 tomllib，无第三方依赖。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAME = "config.toml"
EXAMPLE_CONFIG_NAME = "config.example.toml"


class ConfigError(RuntimeError):
    pass


@dataclass
class SourceConfig:
    name: str
    enabled: bool = False
    api_key: str = ""


@dataclass
class Config:
    root: Path
    data_dir: Path
    email: str = ""
    request_interval: float = 0.4
    timeout: int = 30
    verify_ssl: bool = True
    # auto = 先用 TLS 1.3，失败重试自动降级到 1.2；也可固定为 "1.2" / "1.3"
    tls: str = "auto"
    retries: int = 4
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    journal_rank_enabled: list[str] = field(default_factory=list)
    zotero: dict[str, Any] = field(default_factory=dict)
    butler: dict[str, Any] = field(default_factory=dict)

    # ---- 派生路径 ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "litwarden.sqlite3"

    @property
    def journal_rank_dir(self) -> Path:
        return self.data_dir / "journal_rank"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.journal_rank_dir, self.export_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)

    def enabled_sources(self) -> list[SourceConfig]:
        """按生信场景的优先级返回已启用的数据源。

        排序有意固定：主力源在前，便于阅读日志与结果合并时的字段优先级。
        """
        priority = ["europepmc", "pubmed", "arxiv", "crossref", "openalex", "semanticscholar"]
        out = [s for s in self.sources.values() if s.enabled]
        out.sort(key=lambda s: priority.index(s.name) if s.name in priority else 99)
        return out


def find_config(start: Path | None = None) -> Path | None:
    """从 start 向上查找 config.toml。"""
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        p = d / DEFAULT_CONFIG_NAME
        if p.is_file():
            return p
    return None


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        path = find_config()
    if path is None:
        raise ConfigError(
            f"未找到 {DEFAULT_CONFIG_NAME}。\n"
            f"请先复制模板：cp {EXAMPLE_CONFIG_NAME} {DEFAULT_CONFIG_NAME}"
        )
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    root = path.parent
    general = data.get("general", {})

    email = (general.get("email") or "").strip()
    if not email or email == "you@example.com":
        raise ConfigError(
            "请先在 config.toml 的 [general] email 填写你的真实邮箱。\n"
            "Crossref / Unpaywall / NCBI 都要求用它标识请求方；\n"
            "Unpaywall 会直接拒绝 example.com 这类占位邮箱。"
        )

    data_dir = Path(general.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = root / data_dir

    sources: dict[str, SourceConfig] = {}
    for name, sc in (data.get("sources") or {}).items():
        sources[name] = SourceConfig(
            name=name,
            enabled=bool(sc.get("enabled", False)),
            api_key=(sc.get("api_key") or "").strip(),
        )

    cfg = Config(
        root=root,
        data_dir=data_dir,
        email=email,
        request_interval=float(general.get("request_interval", 0.4)),
        timeout=int(general.get("timeout", 30)),
        verify_ssl=bool(general.get("verify_ssl", True)),
        tls=str(general.get("tls", "auto")),
        retries=int(general.get("retries", 4)),
        sources=sources,
        journal_rank_enabled=list((data.get("journal_rank") or {}).get("enabled", [])),
        zotero=dict(data.get("zotero") or {}),
        butler=dict(data.get("butler") or {}),
    )

    if not cfg.enabled_sources():
        raise ConfigError("config.toml 的 [sources] 里至少需要启用一个数据源。")

    cfg.ensure_dirs()
    return cfg
