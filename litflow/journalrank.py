"""期刊等级：可插拔的多体系查表。

支持的体系（system 取值）：
    sjr  Scimago SJR 分区（Q1-Q4）——官方免费 CSV，推荐默认启用
    ccf  中国计算机学会推荐目录（A/B/C）——计算机方向事实标准
    cas  中科院分区（1区-4区）——版权数据，需用户自行导入
    jcr  JCR 分区（Q1-Q4）——订阅数据，本项目不内置

为什么要做成可插拔：
    没有任何免费 API 能直接给出 JCR / 中科院分区，它们是订阅版权数据。
    所以设计成「用户装哪个体系，过滤时就能用哪个」，而不是硬编码某一种。

CSV 列名用自动探测，尽量容忍各单位导出的格式差异。
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from .config import Config
from .models import Paper, normalize_issn, normalize_journal
from .store import Store

# 每个体系的优劣顺序，索引越小越好
RANK_ORDER: dict[str, list[str]] = {
    "sjr": ["Q1", "Q2", "Q3", "Q4"],
    "jcr": ["Q1", "Q2", "Q3", "Q4"],
    "ccf": ["A", "B", "C"],
    "cas": ["1区", "2区", "3区", "4区"],
}

SUPPORTED = tuple(RANK_ORDER)

_CN_DIGITS = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"}


def normalize_rank(system: str, raw: str | None) -> str | None:
    """把各种写法的分区值归一成体系内的标准标签。

    能处理：Q1 / q1 / 1区 / 一区 / 大类1区 / A / A类 / CCF-A
    """
    if not raw:
        return None
    s = str(raw).strip().upper()
    s = s.replace("CCF-", "").replace("CCF", "").strip()
    for cn, d in _CN_DIGITS.items():
        s = s.replace(cn, d)

    if system in ("sjr", "jcr"):
        m = re.search(r"Q\s*([1-4])", s)
        return f"Q{m.group(1)}" if m else None
    if system == "ccf":
        m = re.search(r"\b([ABC])\b", s) or re.search(r"^([ABC])", s)
        return m.group(1) if m else None
    if system == "cas":
        m = re.search(r"([1-4])\s*区", s) or re.search(r"\b([1-4])\b", s)
        return f"{m.group(1)}区" if m else None
    return s or None


def rank_index(system: str, value: str | None) -> int | None:
    """返回该分区在体系中的优劣序号（0 最好）。无法识别返回 None。"""
    order = RANK_ORDER.get(system)
    if not order or not value:
        return None
    v = normalize_rank(system, value)
    if v is None:
        return None
    try:
        return order.index(v)
    except ValueError:
        return None


def rank_ok(system: str, value: str | None, minimum: str | None) -> bool:
    """value 是否达到 minimum 的要求（含）。未达或无法识别均返回 False。"""
    if minimum is None:
        return True
    si = rank_index(system, value)
    mi = rank_index(system, minimum)
    if mi is None:
        return True
    if si is None:
        return False
    return si <= mi


# ---------------------------------------------------------------- 导入


def _read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _sniff_rows(text: str) -> list[dict[str, str]]:
    """读 CSV，自动判断分隔符。

    Scimago 的官方导出用分号分隔，且表头常被拆成两行（前半段是页签名）。
    策略：找第一行含有 'title'（不区分大小写）的行作为真正的表头。
    """
    lines = text.splitlines()
    best_idx = 0
    for i, ln in enumerate(lines[:8]):
        low = ln.lower()
        if "title" in low and ("issn" in low or "sjr" in low or "quartile" in low):
            best_idx = i
            break
    body = "\n".join(lines[best_idx:])

    delim = ";" if body.count(";") > body.count(",") else ","
    reader = csv.DictReader(io.StringIO(body), delimiter=delim)
    return [{ (k or "").strip(): (v or "").strip() for k, v in row.items() } for row in reader]


def _pick(row: dict[str, str], *candidates: str) -> str | None:
    """按候选列名（小写子串匹配）取第一个非空值。"""
    lower = {k.lower(): v for k, v in row.items()}
    for cand in candidates:
        c = cand.lower()
        for k, v in lower.items():
            if c == k and v:
                return v
    for cand in candidates:
        c = cand.lower()
        for k, v in lower.items():
            if c in k and v:
                return v
    return None


def _rows_from_file(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".json":
        data = json.loads(_read_text(path))
        if isinstance(data, dict):
            data = data.get("items") or []
        return [{str(k): str(v) for k, v in row.items()} for row in data]
    return _sniff_rows(_read_text(path))


def load_system(cfg: Config, store: Store, system: str) -> int:
    """导入 data_dir/journal_rank/ 下匹配该体系的所有文件。"""
    system = system.lower()
    if system not in SUPPORTED:
        raise ValueError(f"不支持的期刊等级体系：{system}（可用：{', '.join(SUPPORTED)}）")

    d = cfg.journal_rank_dir
    files = sorted(
        [p for p in d.iterdir() if p.is_file() and system in p.name.lower()]
    )
    if not files:
        raise FileNotFoundError(
            f"在 {d} 下没找到名字含 '{system}' 的等级表文件。\n"
            f"请放入 CSV 或 JSON，文件名包含体系名，例如 {system}_2024.csv"
        )

    rows: list[dict[str, Any]] = []
    for f in files:
        for raw in _rows_from_file(f):
            rows.extend(_map_rows(system, raw))

    return store.replace_journal_rank(system, pick_best_rows(rows, system))


def _rank_sort_key(system: str, value: str | None) -> int:
    """分区的排序键，越小越优；无法识别视为最差。"""
    idx = rank_index(system, value)
    return idx if idx is not None else 99


def pick_best_rows(
    rows: list[dict[str, Any]], system: str
) -> list[dict[str, Any]]:
    """同一期刊出现多行时，只保留分区更优的那条。

    为什么需要这个：Scimago 的导出让同一期刊在每个学科分类下各占一行，
    分区可能不同（举例：某刊在「生物化学」是 Q1、在「综合医学」是 Q2）。
    导入时若不定规则，最终存下哪条取决于行顺序，筛选结果就不可复现。

    注意这里**不能**用 `rank_index(...) or 99` 做兜底：
    Q1 / A / 1区 的 rank_index 都是 0，而 `0 or 99` 求值为 99，
    会把最好的分区误判成最差的，导致导入后保留 Q2/3/4、丢掉 Q1。
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        k = (r["match_key"], r["match_type"])
        cur = best.get(k)
        if cur is None or _rank_sort_key(system, r["quartile"]) < _rank_sort_key(
            system, cur["quartile"]
        ):
            best[k] = r
    return list(best.values())


def _map_rows(system: str, raw: dict[str, str]) -> list[dict[str, Any]]:
    """把一行原始记录映射成一条或多条查表记录。

    同时写 ISSN 和刊名两种匹配键：查询时 ISSN 优先，刊名兜底。
    很多论文没有 ISSN（预印本尤其常见），只写 ISSN 会导致大量漏查。
    """
    journal = _pick(raw, "title", "journal", "期刊名称", "期刊", "source", "刊名", "name")
    if not journal:
        return []

    issn_raw = _pick(raw, "issn", "issn-l", "eissn", "issn(print)", "印刷issn")
    # Scimago 的 ISSN 列可能是 "1234-5678 / 8765-4321" 形式
    issn = None
    if issn_raw:
        for part in re.split(r"[/,;|\s]+", issn_raw):
            issn = normalize_issn(part)
            if issn:
                break

    quartile = metric_value = None
    if system in ("sjr", "jcr"):
        qt = _pick(raw, "best quartile", "quartile", "分区", "q")
        quartile = normalize_rank(system, qt)
        metric_value = _pick(raw, "sjr", "impact factor", "jif", "影响因子")
    elif system == "ccf":
        cat = _pick(raw, "category", "rank", "class", "等级", "类别")
        quartile = normalize_rank(system, cat)
        metric_value = _pick(raw, "full_name", "abbr", "简称", "刊物全称")
    elif system == "cas":
        qt = _pick(raw, "分区", "大类分区", "zone", "quartile")
        quartile = normalize_rank(system, qt)
        metric_value = _pick(raw, "影响因子", "if", "top")

    if not quartile:
        return []

    metric = "sjr" if system == "sjr" else ("if" if system in ("jcr", "cas") else "category")
    base = {
        "system": system,
        "quartile": quartile,
        "metric": metric,
        "value": metric_value or "",
        "journal": journal,
    }

    out: list[dict[str, Any]] = []
    jn = normalize_journal(journal)
    if jn:
        out.append({**base, "match_key": jn, "match_type": "title"})
    if issn:
        out.append({**base, "match_key": issn, "match_type": "issn"})
    return out


def attach_ranks(store: Store, papers: list[Paper], systems: list[str]) -> int:
    """给候选论文附上期刊等级（原地修改）。返回命中级数。"""
    if not systems:
        return 0
    hit = 0
    for p in papers:
        r = store.lookup_journal_rank(p.issn, p.journal, systems)
        if r:
            p.journal_rank = r
            hit += 1
    return hit
