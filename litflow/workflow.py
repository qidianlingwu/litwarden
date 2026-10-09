"""Workflow —— Recipe 机制：把整条流程存成可复用的配方。

设计要点：
  · Recipe 是一个**人可读、可手改的 TOML 文件**，放在项目根的 `recipes/` 下。
    不藏在数据库里，方便你直接编辑、对比、抄给别人。
  · 分阶段执行，每段都能单独跑（`--only filter`），失败不必从头再来。
  · 阶段之间传递 run_id：search 产生一次 run，后面几段都基于它。
    run_id 存在本地 SQLite 的 kv_cache（键 `recipe:<名字>:last_run_id`），
    **不回写用户的配方文件**，避免污染你手写的注释。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .adapters import SearchQuery, build_adapters
from .config import Config, ConfigError
from .exporters import to_bibtex, to_csv, to_markdown, to_xlsx
from .filterer import Filters, apply as filter_apply
from .finder import enrich_citations, enrich_oa, search as finder_search
from .journalrank import attach_ranks
from .models import Paper
from .store import Store
from .zotero import ZoteroError

# 阶段在配方里的顺序，必须按这个顺序执行（后面的依赖前面的产物）
STAGES = ["search", "filter", "export", "zotero", "digest-fetch", "digest-export"]

STAGE_LABELS = {
    "search": "检索",
    "filter": "筛选",
    "export": "导出文献调研表",
    "zotero": "写入 Zotero",
    "digest-fetch": "回读精读笔记",
    "digest-export": "导出初读提炼表",
}

RECIPE_TEMPLATE = '''# litflow 工作流配方
# 复制这个文件改名即可新建一套流程：cp recipe-template.toml 我的课题.toml
# 然后：litflow run 我的课题

name = ""
description = ""

# ---------------------------------------------------------------- 检索
[search]
query = "spatial transcriptomics deconvolution"
# 可选：按作者 / 期刊限定
# author = ""
# journal = ""
year_from = 2023
# year_to = 2026

# 每个源取回的条数
limit = 40
# relevance（默认，兼顾最新与经典）| cited（偏经典）| date（偏最新）
sort = "relevance"
# 只用这些源；留空则用 config.toml 里启用的全部
sources = []

# 用 Unpaywall 补全开放获取链接（较慢，每篇一次请求）
oa_enrich = false
# 只要预印本
preprints_only = false

# 检索完立刻导出候选表（可选格式：xlsx,csv,tsv,md,bib,json）
export = []


# ---------------------------------------------------------------- 筛选
[filter]
# 引用量门槛。推荐用「年均引用」，否则候选池会全是老论文
# min_cites = 20
min_cites_per_year = 2.0

# 关键词匹配度下限（0-1）。
# ⚠️ 按引用量排序时强烈建议加上，否则会混入全局高被引但不相关的论文
min_keyword = 0.4

# 期刊等级门槛，用「体系:等级」写法
# rank = { sjr = "Q1" }
# 等级表收录不全时，未收录的会被 --rank 剔除，可以设 true 放宽
rank_allow_unknown = true

# 只要标题包含的方法词——区分「提出方法」与「使用方法」最有效的旋钮
title_must = ["deconvolution"]
# title_must = []

# 标题/摘要必须包含任一 / 包含任一即剔除
must = []
exclude = []

# 剔除「Correction to:」这类勘误记录
exclude_corrections = true

# 只要开放获取 / 只要预印本 / 排除预印本
oa_only = false
only_preprints = false
exclude_preprints = false

# 排序：rank（期刊等级优先，默认）| cites | cites_per_year | year | keyword
sort = "rank"
# 只取前 N 篇
# top = 50

# 导出格式（可选：xlsx,csv,tsv,md,bib,json）
export = ["xlsx", "csv", "md", "bib"]


# ---------------------------------------------------------------- 写入 Zotero
[zotero]
# 设 false 则跳过这一段
enable = true
# 目标集合，不存在会自动创建
collection = "litflow 导入"
# 阅读状态标签（会写成 wf:<状态>）
status = "未读"
# 取哪种决策的论文：include（筛过的，推荐）| candidate（全部候选）
decision = "include"
# 抓取开放获取 PDF 作为附件（只走合法 OA 链接，不绕过付费墙）
attach_pdf = false
# 限制写入条数（首次试跑时建议设成 5）
# top = 5


# ---------------------------------------------------------------- 初读提炼
[digest]
# 设 false 则跳过这一段
enable = true
# 只导出已导入 Zotero 的（没导入的必然没有笔记）
imported_only = true
export = ["xlsx", "csv", "md"]
'''


class RecipeError(RuntimeError):
    pass


# ---------------------------------------------------------------- 配方


@dataclass
class Recipe:
    name: str
    path: Path | None = None
    description: str = ""
    search: dict[str, Any] = field(default_factory=dict)
    filter: dict[str, Any] = field(default_factory=dict)
    zotero: dict[str, Any] = field(default_factory=dict)
    digest: dict[str, Any] = field(default_factory=dict)

    def stage_enabled(self, stage: str) -> bool:
        if stage in ("search", "filter", "export"):
            return True
        if stage == "zotero":
            return bool(self.zotero.get("enable", True))
        if stage in ("digest-fetch", "digest-export"):
            return bool(self.digest.get("enable", True))
        return False

    def snapshot(self) -> dict[str, Any]:
        """存进 runs 表的快照，用于追溯这次运行用了什么参数。"""
        return {
            "name": self.name,
            "search": self.search,
            "filter": self.filter,
        }


def recipes_dir(cfg: Config) -> Path:
    d = cfg.root / "recipes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def recipe_path(cfg: Config, name: str) -> Path:
    """按名字定位配方文件。支持带不带 .toml 后缀。"""
    d = recipes_dir(cfg)
    stem = name[:-5] if name.endswith(".toml") else name
    p = d / f"{stem}.toml"
    if p.is_file():
        return p
    # 允许用路径直接指定
    alt = Path(name).expanduser()
    if alt.is_file():
        return alt
    raise RecipeError(
        f"找不到配方 {name!r}。\n"
        f"可用配方在 {d}/ 下，执行 `litflow recipe list` 查看。"
    )


def load_recipe(cfg: Config, name: str) -> Recipe:
    p = recipe_path(cfg, name)
    with p.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise RecipeError(f"配方 {p} 的 TOML 语法有误：\n  {e}") from None
    return Recipe(
        name=data.get("name") or p.stem,
        path=p,
        description=data.get("description", ""),
        search=dict(data.get("search") or {}),
        filter=dict(data.get("filter") or {}),
        zotero=dict(data.get("zotero") or {}),
        digest=dict(data.get("digest") or {}),
    )


def list_recipes(cfg: Config) -> list[Recipe]:
    out: list[Recipe] = []
    for p in sorted(recipes_dir(cfg).glob("*.toml")):
        try:
            out.append(load_recipe(cfg, p.stem))
        except RecipeError:
            continue
    return out


def init_recipes(cfg: Config, force: bool = False) -> list[Path]:
    """生成配方模板。"""
    d = recipes_dir(cfg)
    created: list[Path] = []
    tpl = d / "recipe-template.toml"
    if force or not tpl.exists():
        tpl.write_text(RECIPE_TEMPLATE, encoding="utf-8")
        created.append(tpl)
    return created


# ---------------------------------------------------------------- 阶段执行


@dataclass
class StageResult:
    stage: str
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


def _load_run_papers(store: Store, run_id: int) -> list[Paper]:
    keys = [r["paper_key"] for r in store.get_run_papers(run_id)]
    papers = store.get_papers(keys)
    order = {k: i for i, k in enumerate(keys)}
    papers.sort(key=lambda p: order.get(p.key, 1 << 30))
    return papers


def _export_papers(cfg: Config, papers: list[Paper], base: str, formats: list[str],
                   query: str = "") -> list[Path]:
    out: list[Path] = []
    p = cfg.export_dir / base
    p.parent.mkdir(parents=True, exist_ok=True)
    if "xlsx" in formats:
        out.append(to_xlsx(papers, p.with_suffix(".xlsx"), sheet_name=base[:31]))
    if "csv" in formats:
        out.append(to_csv(papers, p.with_suffix(".csv")))
    if "tsv" in formats:
        out.append(to_csv(papers, p.with_suffix(".tsv"), delimiter="\t"))
    if "md" in formats:
        out.append(to_markdown(papers, p.with_suffix(".md"), query=query))
    if "bib" in formats:
        out.append(to_bibtex(papers, p.with_suffix(".bib")))
    return out


def stage_search(cfg: Config, store: Store, r: Recipe, http: Any,
                 log: Callable[[str], None]) -> tuple[StageResult, int | None]:
    s = r.search
    query_text = str(s.get("query") or "").strip()
    if not query_text and not s.get("author") and not s.get("journal"):
        raise RecipeError(
            "配方里 [search] 至少要有一项：query / author / journal。\n"
            f"请编辑 {r.path}"
        )

    q = SearchQuery(
        text=query_text,
        author=s.get("author"),
        journal=s.get("journal"),
        year_from=s.get("year_from"),
        year_to=s.get("year_to"),
        limit=int(s.get("limit", 40)),
        sort=str(s.get("sort", "relevance")),
    )
    if s.get("preprints_only"):
        q.extras["epmc_src"] = "PPR"

    adapters = build_adapters(cfg, http)
    want = [x for x in (s.get("sources") or []) if x]
    if want:
        adapters = [a for a in adapters if a.name in want]
        if not adapters:
            raise RecipeError(f"[search] sources 指定的源都不可用：{want}")

    log(f"检索式：{q.describe()}")
    log(f"数据源：{', '.join(a.name for a in adapters)}")
    report = finder_search(cfg, http, q, adapters=adapters)
    papers = report.papers

    log(f"原始命中 {report.raw_count} 条，去重后 {len(papers)} 条，耗时 {report.elapsed}s")
    for name, n in report.per_source.items():
        log(f"  · {name:<16} {n} 条")
    for name, msg in report.errors.items():
        log(f"  ! {name:<16} 失败：{msg.splitlines()[0][:110]}")

    if s.get("oa_enrich"):
        log("用 Unpaywall 补全开放获取链接…")
        enrich_oa(cfg, http, papers)

    filled = enrich_citations(http, papers) if papers else 0
    if filled:
        log(f"引用量补全 {filled} 篇")

    systems = cfg.journal_rank_enabled
    hit = attach_ranks(store, papers, systems)
    if systems:
        log(f"期刊等级命中 {hit}/{len(papers)} 篇")

    store.upsert_papers(papers)
    run_id = store.create_run(r.name, {
        "recipe": r.snapshot(),
        "query": {"text": q.text, "author": q.author, "journal": q.journal,
                  "year_from": q.year_from, "year_to": q.year_to,
                  "limit": q.limit, "sort": q.sort, "extras": q.extras},
        "sources": [a.name for a in adapters],
        "stages": ["search"],
    })
    store.attach_run_papers(run_id, [
        {"run_id": run_id, "paper_key": p.key, "keyword_score": p.keyword_score,
         "rank": None, "decision": "candidate", "note": None}
        for p in papers
    ])
    store.finish_run(run_id, {"raw": report.raw_count, "deduped": len(papers),
                              "elapsed": report.elapsed, "per_source": report.per_source})

    det = {"run_id": run_id, "papers": len(papers), "raw": report.raw_count,
           "errors": report.errors}
    return StageResult("search", True, f"运行 #{run_id}，{len(papers)} 篇候选", det), run_id


def stage_filter(cfg: Config, store: Store, r: Recipe, http: Any, run_id: int,
                 log: Callable[[str], None]) -> StageResult:
    f = r.filter
    papers = _load_run_papers(store, run_id)
    systems = cfg.journal_rank_enabled
    attach_ranks(store, papers, systems)

    rank_min = {str(k).lower(): str(v) for k, v in (f.get("rank") or {}).items()}

    filters = Filters(
        year_from=f.get("year_from"),
        year_to=f.get("year_to"),
        min_cites=f.get("min_cites"),
        min_cites_per_year=f.get("min_cites_per_year"),
        rank_min=rank_min,
        oa_only=bool(f.get("oa_only", False)),
        require_abstract=bool(f.get("require_abstract", False)),
        require_doi=bool(f.get("require_doi", False)),
        only_preprints=bool(f.get("only_preprints", False)),
        exclude_preprints=bool(f.get("exclude_preprints", False)),
        from_sources=list(f.get("from_sources") or []),
        must_include=list(f.get("must") or []),
        title_must=list(f.get("title_must") or []),
        exclude_terms=list(f.get("exclude") or []),
        min_keyword=f.get("min_keyword"),
        exclude_corrections=bool(f.get("exclude_corrections", False)),
        rank_allow_unknown=bool(f.get("rank_allow_unknown", False)),
    )

    query_text = str(r.search.get("query") or "")
    res = filter_apply(papers, filters, query_text=query_text,
                       sort_by=str(f.get("sort", "rank")), systems=systems,
                       top=f.get("top"))
    log(f"筛选后 {len(res.kept)} 篇，剔除 {len(res.dropped)} 篇")

    store.attach_run_papers(run_id, [
        {"run_id": run_id, "paper_key": p.key, "keyword_score": p.keyword_score,
         "rank": None, "decision": "include", "note": None} for p in res.kept
    ] + [
        {"run_id": run_id, "paper_key": p.key, "keyword_score": p.keyword_score,
         "rank": None, "decision": "exclude", "note": why} for p, why in res.dropped
    ])

    written: list[Path] = []
    if f.get("export"):
        written = _export_papers(cfg, res.kept, f"{r.name}_文献调研表",
                                 list(f["export"]), query=query_text)
    det = {"kept": len(res.kept), "dropped": len(res.dropped),
           "exports": [str(x) for x in written]}
    return StageResult("filter", True, f"纳入 {len(res.kept)} 篇", det)


def stage_export(cfg: Config, store: Store, r: Recipe, run_id: int,
                 log: Callable[[str], None]) -> StageResult:
    """把当前 run 的纳入结果再导一次（filter 段没配 export 时用得上）。"""
    f = r.filter
    papers = _load_run_papers(store, run_id)
    rows = {x["paper_key"]: x for x in store.get_run_papers(run_id)}
    kept = [p for p in papers if (rows.get(p.key) or {}).get("decision") == "include"]
    if not kept:
        return StageResult("export", True, "本次运行没有纳入的论文，跳过导出")

    formats = list(f.get("export") or ["xlsx", "csv", "md"])
    written = _export_papers(cfg, kept, f"{r.name}_文献调研表", formats,
                             query=str(r.search.get("query") or ""))
    for p in written:
        log(f"  {p}")
    return StageResult("export", True, f"导出 {len(written)} 个文件",
                       {"exports": [str(x) for x in written]})


def stage_zotero(cfg: Config, store: Store, r: Recipe, http: Any, run_id: int,
                 log: Callable[[str], None]) -> StageResult:
    from .cli import _zotero_client
    from .librarian import ImportOptions, import_papers

    z = r.zotero
    papers = _load_run_papers(store, run_id)
    decision = str(z.get("decision", "include"))
    rows = {x["paper_key"]: x for x in store.get_run_papers(run_id)}
    papers = [p for p in papers if (rows.get(p.key) or {}).get("decision") == decision]
    if z.get("oa_only"):
        papers = [p for p in papers if p.oa_url]
    if z.get("top"):
        papers = papers[: int(z["top"])]
    if not papers:
        return StageResult("zotero", True, f"没有 decision={decision} 的论文，跳过")

    client = _zotero_client(cfg, store)
    opts = ImportOptions(
        collection=str(z.get("collection") or "litflow 导入"),
        status=str(z.get("status") or "未读"),
        attach_pdf=bool(z.get("attach_pdf", False)),
        only_oa=bool(z.get("oa_only", False)),
    )
    log(f"写入集合「{opts.collection}」，共 {len(papers)} 篇"
        + ("，并抓取 OA PDF" if opts.attach_pdf else ""))
    try:
        res = import_papers(cfg, store, client, papers, opts, http=http)
    except ZoteroError as e:
        return StageResult("zotero", False, str(e).splitlines()[0], {"error": str(e)})

    for k, why in res.skipped[:5]:
        log(f"  · 跳过：{why}")
    for title, msg in res.failed[:5]:
        log(f"  ! 失败：{title[:50]} —— {msg[:80]}")
    for title, msg in res.pdf_problems[:5]:
        log(f"  ! PDF：{title} —— {msg}")
    return StageResult("zotero", True, res.summary,
                       {"created": len(res.created), "attached": res.attached})


def stage_digest_fetch(cfg: Config, store: Store, r: Recipe,
                       log: Callable[[str], None]) -> StageResult:
    from .cli import _zotero_client
    from .digester import fetch_notes

    client = _zotero_client(cfg, store)
    links = store.zotero_links()
    if not links:
        return StageResult("digest-fetch", True, "还没有文献导入 Zotero，跳过")
    papers = [p for p in store.get_papers()
              if p.key in links and links[p.key].get("attached_pdf")]
    if not papers:
        return StageResult("digest-fetch", True, "没有带 PDF 的已导入文献，无法精读")
    try:
        rep = fetch_notes(cfg, store, client, papers, verbose=False)
    except ZoteroError as e:
        return StageResult("digest-fetch", False, str(e).splitlines()[0])

    # FetchReport 是 dataclass，用属性访问而不是字典下标
    msg = (f"已有笔记 {rep.with_note} 篇，待精读 {rep.no_note} 篇，"
           f"缺 PDF {rep.no_pdf} 篇")
    return StageResult("digest-fetch", True, msg,
                       {"with_note": rep.with_note, "no_note": rep.no_note,
                        "no_pdf": rep.no_pdf})


def stage_digest_export(cfg: Config, store: Store, r: Recipe, run_id: int,
                        log: Callable[[str], None]) -> StageResult:
    from .digester import digest_rows, export_digest

    papers = _load_run_papers(store, run_id)
    rows_map = {x["paper_key"]: x for x in store.get_run_papers(run_id)}
    papers = [p for p in papers
              if (rows_map.get(p.key) or {}).get("decision") == "include"]
    d = r.digest
    if d.get("imported_only", True):
        links = store.zotero_links()
        papers = [p for p in papers if p.key in links]
    if not papers:
        return StageResult("digest-export", True, "没有可汇总的论文，跳过")

    rows = digest_rows(cfg, store, papers)
    formats = list(d.get("export") or ["xlsx", "csv", "md"])
    written = export_digest(cfg, rows, f"{r.name}_初读提炼", formats)
    for p in written:
        log(f"  {p}")
    done = sum(1 for x in rows if x["has_note"] == "是")
    return StageResult("digest-export", True,
                       f"{done}/{len(rows)} 篇有笔记，导出 {len(written)} 个文件",
                       {"with_note": done, "total": len(rows),
                        "exports": [str(x) for x in written]})


# ---------------------------------------------------------------- 编排


def run_recipe(
    cfg: Config,
    store: Store,
    recipe: Recipe,
    http: Any,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    reuse_run_id: int | None = None,
    log: Callable[[str], None] = print,
) -> list[StageResult]:
    """按顺序执行配方里的各阶段。"""
    only = only or []
    skip = skip or []

    stages = [
        s for s in STAGES
        if (not only or s in only)
        and s not in skip
        and recipe.stage_enabled(s)
    ]
    if not stages:
        raise RecipeError("没有可执行的阶段。检查配方里的 enable 设置与 --only/--skip。")

    state_key = f"recipe:{recipe.name}:last_run_id"
    run_id = reuse_run_id or store.cache_get(state_key)
    run_id = int(run_id) if run_id else None

    results: list[StageResult] = []
    for stage in stages:
        label = STAGE_LABELS.get(stage, stage)
        log("")
        log(f"── {label} {'─' * max(0, 46 - len(label))}")

        # 每个阶段单独隔离失败：一个阶段崩了不该让整个运行挂掉，
        # 也不该甩一堆 traceback 给用户。记录下来并提示如何续跑。
        try:
            if stage == "search":
                res, new_id = stage_search(cfg, store, recipe, http, log)
                run_id = new_id
                store.cache_set(state_key, run_id)
            elif stage in ("filter", "export", "zotero", "digest-export"):
                if run_id is None:
                    raise RecipeError(
                        f"阶段 {stage} 需要一次检索运行，但找不到。\n"
                        "先执行检索阶段（或先单跑一次 `litflow search`），"
                        "或用 `--run <编号>` 指定。"
                    )
                if stage == "filter":
                    res = stage_filter(cfg, store, recipe, http, run_id, log)
                elif stage == "export":
                    res = stage_export(cfg, store, recipe, run_id, log)
                elif stage == "zotero":
                    res = stage_zotero(cfg, store, recipe, http, run_id, log)
                else:
                    res = stage_digest_export(cfg, store, recipe, run_id, log)
            else:  # digest-fetch
                res = stage_digest_fetch(cfg, store, recipe, log)
        except RecipeError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            res = StageResult(stage, False, f"{type(e).__name__}: {e}")

        log(f"→ {res.summary}")
        results.append(res)
        if not res.ok:
            remaining = [s for s in stages[stages.index(stage) + 1:]]
            log("  该阶段失败，后续阶段中止。")
            if remaining:
                log(f"  修好后只跑剩下的：--only {','.join(remaining)}")
            break

    return results
