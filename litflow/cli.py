"""litflow 命令行入口。

M1（本次交付）可用命令：
    doctor              环境体检：配置 / 数据源连通性 / Zotero 本地 API / 等级表
    search              多源检索，存候选池并生成一次 run
    filter              对候选池筛选排序，更新决策并可导出
    runs                列出历史运行
    show                查看某次运行的论文
    rank import <体系>   导入期刊等级表
    rank stats          查看已导入的等级表规模
    sources             查看数据源启用状态
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from . import __version__
from .adapters import REGISTRY, SearchQuery, build_adapters
from .config import Config, ConfigError, load_config
from .exporters import render_table, to_bibtex, to_csv, to_json, to_markdown, to_xlsx
from .filterer import SORT_KEYS, Filters, apply
from .finder import enrich_citations, enrich_oa, search as do_search
from .http import HttpClient
from .journalrank import SUPPORTED, attach_ranks, load_system
from .models import Paper
from .store import Store

# 阶段名清单（供 argparse 帮助文本用）。放在这里避免在 build_parser 里 import 整个 workflow。
STAGE_NAMES = ["search", "filter", "export", "zotero", "digest-fetch", "digest-export"]


# ---------------------------------------------------------------- 工具


def _mask_id(value: str | None, keep: int = 2) -> str:
    """遮蔽实例标识，避免诊断输出被贴到公开处时泄漏。

    Server-ID 本身不是密钥（写请求还需要本地 key），但它是本机 Zotero 库的
    唯一标识。`doctor` 与 `zotero check` 的输出经常被整段贴进 issue，所以默认遮蔽。

    需要完整值时可自行请求：curl -D - -o /dev/null http://127.0.0.1:23119/api/users/0/items?limit=1
    """
    if not value:
        return "（未获取）"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def _http(cfg: Config) -> HttpClient:
    return HttpClient(
        email=cfg.email,
        timeout=cfg.timeout,
        interval=cfg.request_interval,
        verify_ssl=cfg.verify_ssl,
        tls=cfg.tls,
        retries=cfg.retries,
    )


def _parse_kv_list(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        if ":" not in it:
            raise SystemExit(f"参数格式错误：{it!r}，应为 '体系:等级'，例如 sjr:Q2")
        k, v = it.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _split(csv_arg: str | None) -> list[str]:
    if not csv_arg:
        return []
    return [x.strip() for x in csv_arg.split(",") if x.strip()]


def _load_run_papers(store: Store, run_id: int | None) -> tuple[int, list[Paper]]:
    rid = run_id or store.latest_run_id()
    if rid is None:
        raise SystemExit("还没有任何运行记录，先执行 `litflow search`。")
    rows = store.get_run_papers(rid)
    if not rows:
        raise SystemExit(f"运行 #{rid} 里没有论文记录。")
    keys = [r["paper_key"] for r in rows]
    papers = store.get_papers(keys)
    order = {k: i for i, k in enumerate(keys)}
    papers.sort(key=lambda p: order.get(p.key, 1 << 30))
    return rid, papers


def _export(papers: list[Paper], basename: str, cfg: Config, formats: list[str],
            query: str = "") -> list[Path]:
    out: list[Path] = []
    base = cfg.export_dir / basename
    base.parent.mkdir(parents=True, exist_ok=True)
    if "csv" in formats:
        out.append(to_csv(papers, base.with_suffix(".csv")))
    if "tsv" in formats:
        out.append(to_csv(papers, base.with_suffix(".tsv"), delimiter="\t"))
    if "md" in formats:
        out.append(to_markdown(papers, base.with_suffix(".md"), query=query))
    if "bib" in formats:
        out.append(to_bibtex(papers, base.with_suffix(".bib")))
    if "json" in formats:
        out.append(to_json(papers, base.with_suffix(".json")))
    if "xlsx" in formats:
        out.append(to_xlsx(papers, base.with_suffix(".xlsx"), sheet_name=base.name[:31]))
    return out


def _err(msg: str) -> int:
    print(f"错误：{msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- 子命令


def cmd_doctor(args: argparse.Namespace, cfg: Config) -> int:
    print(f"litflow {__version__}  环境体检")
    print("=" * 62)

    store = Store(cfg.db_path)

    print(f"配置文件      : {cfg.root / 'config.toml'}")
    print(f"数据目录      : {cfg.data_dir}")
    print(f"邮箱          : {cfg.email}")
    print(f"候选池        : {cfg.db_path}（已存 {store.count_papers()} 篇）")
    print()

    print("数据源：")
    http = _http(cfg)
    # SSL 上下文是懒加载的，这里主动触发一次，好把证书来源打印出来
    http.context_for(0)

    from .http import ssl_source

    print(f"  HTTPS 证书库 : {ssl_source()}")
    if "未找到" in ssl_source():
        print("    修复：执行 /Applications/Python\\ 3.14/Install\\ Certificates.command")
        print("    或临时在 config.toml 设 verify_ssl = false（不安全，仅排查用）")

    adapters = build_adapters(cfg, http)
    enabled = {a.name for a in adapters}
    for name in REGISTRY:
        sc = cfg.sources.get(name)
        if sc is None or not sc.enabled:
            print(f"  [ 未启用 ] {name}")
            continue
        if name not in enabled:
            print(f"  [ 缺 key  ] {name} —— 需要 API key，config.toml 里未填写")
            continue
        print(f"  [  已启用] {name}")

    if args.online:
        print()
        print("连通性测试（会发起真实请求）：")
        q = SearchQuery(text="single cell RNA sequencing", limit=1)
        for ad in adapters:
            try:
                got = ad.search(q)
                print(f"  ✓ {ad.name:16} 返回 {len(got)} 条")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {ad.name:16} {str(e).splitlines()[0][:110]}")

    print()
    print("期刊等级表：")
    st = store.journal_rank_stats()
    if not st:
        print(f"  （空）请把等级表放到 {cfg.journal_rank_dir}/ 后执行 `litflow rank import sjr`")
        print("  SJR 官方免费 CSV：https://www.scimagojr.com/journalrank.php （页底 Export）")
    for k, v in st.items():
        print(f"  {k:6} {v} 条")

    print()
    print("Zotero 本地 API：")
    _zotero_probe(cfg)
    return 0


def _zotero_probe(cfg: Config) -> None:
    import urllib.error
    import urllib.request

    base = (cfg.zotero or {}).get("base_url", "http://127.0.0.1:23119")
    try:
        req = urllib.request.Request(f"{base}/api/users/0/items?limit=1",
                                     headers={"Zotero-API-Version": "3"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"  ✓ 可读（HTTP {resp.status}），响应头 Zotero-API-Version="
                  f"{resp.headers.get('Zotero-API-Version')}，"
                  f"Server-ID={_mask_id(resp.headers.get('Zotero-Server-ID'))}")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print("  ✗ 403 Forbidden —— 本地 API 未开启")
            print("     修复：Zotero -> 设置 -> Advanced -> 勾选")
            print("     「Allow other applications on this computer to communicate with Zotero」")
        else:
            print(f"  ✗ HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 连不上 {base}（Zotero 没在运行？）：{e}")


def cmd_sources(args: argparse.Namespace, cfg: Config) -> int:
    print(f"{'源':<16}{'状态':<10}{'需 key':<8}说明")
    print("-" * 70)
    desc = {
        "europepmc": "生信主力：PubMed 文献 + bioRxiv/medRxiv 预印本，含引用数与 OA 标记",
        "pubmed": "NCBI 官方，MeSH 检索强",
        "arxiv": "预印本，覆盖 q-bio / eess.SP 等分类",
        "crossref": "DOI 与期刊元数据校准",
        "openalex": "被引数 + 期刊信息（2026 起需免费 key）",
        "semanticscholar": "被引数 + TLDR + OA 直链（需免费 key）",
    }
    for name, cls in REGISTRY.items():
        sc = cfg.sources.get(name)
        if sc is None:
            state = "未配置"
        elif not sc.enabled:
            state = "未启用"
        elif cls.requires_key and not sc.api_key:
            state = "缺 key"
        else:
            state = "✓ 启用"
        print(f"{name:<16}{state:<10}{'是' if cls.requires_key else '否':<8}{desc.get(name, '')}")
    return 0


def cmd_search(args: argparse.Namespace, cfg: Config) -> int:
    q = SearchQuery(
        text=args.query or "",
        author=args.author,
        journal=args.journal,
        year_from=args.year_from,
        year_to=args.year_to,
        limit=args.limit,
        sort=args.sort,
    )
    if args.preprints_only:
        q.extras["epmc_src"] = "PPR"

    only = _split(args.source)
    http = _http(cfg)
    adapters = build_adapters(cfg, http)
    if only:
        adapters = [a for a in adapters if a.name in only]
        if not adapters:
            return _err(f"--source 指定的源都不可用：{only}")

    # 空检索式在这里就拦下来。若留到 finder 里再抛错，用户会先看到
    # 「检索式 / 数据源 / 检索中……」三行表头才看到错误，像是流程跑了一半才失败。
    if q.is_empty():
        return _err("检索条件为空：至少提供关键词、作者或期刊之一（用 -q 指定关键词）")

    print(f"检索式：{q.describe()}")
    print(f"数据源：{', '.join(a.name for a in adapters)}")
    print("检索中……")

    try:
        report = do_search(cfg, http, q, adapters=adapters)
    except Exception as e:  # noqa: BLE001
        return _err(str(e))

    print(f"完成，耗时 {report.elapsed}s，原始命中 {report.raw_count} 条，去重后 {len(report.papers)} 条")
    for name, n in report.per_source.items():
        print(f"  · {name:<16} {n} 条")
    for name, msg in report.errors.items():
        print(f"  ! {name:<16} 失败：{msg.splitlines()[0][:120]}")
    if http.fallback_count:
        print(f"  （其中 {http.fallback_count} 次请求靠自动降级到 TLS 1.2 成功）")
    print()

    papers = report.papers
    store = Store(cfg.db_path)

    # PubMed 不返回被引次数，且它的结果排序与 Europe PMC 不同，
    # 大量 PubMed 记录不会与其他源合并，导致引用量整片缺失。
    # 用 Europe PMC 按 PMID/DOI 批量反查，一次请求可补 40 篇。
    if not args.no_cite_enrich:
        filled = enrich_citations(http, papers)
        if filled:
            print(f"引用量补全 {filled} 篇（Europe PMC 按 PMID/DOI 反查）")
        missing = sum(1 for p in papers if p.cited_by is None)
        if missing:
            print(f"仍有 {missing} 篇无引用量（多为 arXiv 预印本，Europe PMC 不索引）")
        print()

    if args.oa_enrich:
        print("用 Unpaywall 补全开放获取链接……")
        hit = enrich_oa(cfg, http, papers, limit=args.oa_limit)
        print(f"  补全 {hit} 篇的 OA 链接\n")

    systems = cfg.journal_rank_enabled
    hit = attach_ranks(store, papers, systems)
    if systems:
        print(f"期刊等级命中 {hit}/{len(papers)} 篇（体系：{', '.join(systems)}）\n")

    store.upsert_papers(papers)

    # 关键词匹配度必须在写 run_papers 之前算好。
    # 早先是先 attach_run_papers 再在 preview 副本上算分，导致
    # run_papers.keyword_score 全是 NULL（只有后续 filter 阶段才补上），
    # 只跑检索没跑筛选的 run 就永久缺这个字段。
    if q.text.strip() and not args.no_keyword_score:
        from .filterer import score_keywords

        for p, s in zip(papers, score_keywords(papers, q.text)):
            p.keyword_score = s

    run_id = store.create_run(args.label or q.describe(), {
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
    print(f"已保存 运行 #{run_id}（{len(papers)} 篇候选）\n")

    # 检索阶段的预览按关键词匹配度排，便于一眼看出哪几篇最相关；
    # 正式的等级/引用排序留给 `litflow filter --sort rank`
    preview = sorted(papers, key=lambda p: p.keyword_score or 0.0, reverse=True)

    print(render_table(preview, query=q.describe(), limit=args.preview))

    if args.export:
        paths = _export(preview, f"run{run_id}_search", cfg, _split(args.export),
                        query=q.describe())
        print()
        for p in paths:
            print(f"已导出：{p}")
    print()
    print(f"下一步：litflow filter --run {run_id} --min-cites 20 --rank sjr:Q2 --export csv")
    return 0


def cmd_filter(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.db_path)
    rid, papers = _load_run_papers(store, args.run)
    print(f"运行 #{rid}，候选 {len(papers)} 篇")

    systems = cfg.journal_rank_enabled
    rank_hit = attach_ranks(store, papers, systems)
    if systems:
        pct = round(100 * rank_hit / len(papers)) if papers else 0
        print(f"期刊等级覆盖 {rank_hit}/{len(papers)}（{pct}%），体系：{', '.join(systems)}")
        if pct < 60:
            print("  ⚠️ 覆盖率偏低。未收录的期刊在 --rank 条件下会被剔除，")
            print("     结果可能远少于预期。要么补齐等级表，要么加 --rank-allow-unknown。")

    # 关键词用哪一句：
    #   优先 --query；没给就复用这次运行原本的检索式。
    #   复用很重要——否则筛选阶段算不出匹配度，--min-keyword 就失效了。
    query_text = args.query or ""
    if not query_text:
        run = store.get_run(rid)
        query_text = ((run or {}).get("recipe") or {}).get("query", {}).get("text", "") or ""
        if query_text:
            print(f"（沿用本次运行的检索式：{query_text!r}）")

    filters = Filters(
        year_from=args.year_from,
        year_to=args.year_to,
        min_cites=args.min_cites,
        min_cites_per_year=args.min_cites_per_year,
        rank_min=_parse_kv_list(args.rank),
        oa_only=args.oa,
        require_abstract=args.require_abstract,
        require_doi=args.require_doi,
        only_preprints=args.only_preprints,
        exclude_preprints=args.exclude_preprints,
        from_sources=_split(args.source),
        must_include=_split(args.must),
        title_must=_split(args.title_must),
        exclude_terms=_split(args.exclude),
        min_keyword=args.min_keyword,
        exclude_corrections=args.exclude_corrections,
        rank_allow_unknown=args.rank_allow_unknown,
    )

    unknown = set(filters.rank_min) - set(SUPPORTED)
    if unknown:
        return _err(f"未知的等级体系 {sorted(unknown)}，可用：{', '.join(SUPPORTED)}")

    # 用了某种等级门槛，但该体系的表压根没导入 —— 这会把候选全部筛成 0 篇，
    # 而不加 --show-reasons 时看不出原因，很容易误判成「这个检索式没结果」。
    have = store.journal_rank_stats()
    missing = [s for s in filters.rank_min if not have.get(s)]
    if missing:
        print(f"⚠️  等级体系 {', '.join(missing)} 的等级表尚未导入，"
              f"用它作门槛会把候选全部剔除。")
        print(f"    先导入：litflow rank import {missing[0]}")
        print(f"    或在筛选时去掉 --rank，或加 --rank-allow-unknown 放宽未收录的期刊。")
        print()

    res = apply(papers, filters, query_text=query_text, sort_by=args.sort,
                systems=systems, top=args.top)

    print(f"筛选后 {len(res.kept)} 篇，剔除 {len(res.dropped)} 篇")
    if args.show_reasons and res.dropped:
        from collections import Counter

        # 把 "关键词匹配度 0.42 < 0.5" 这类带具体数值的原因归并成一条，
        # 否则会有几十条只差一个数字的重复行
        def bucket(reason: str) -> str:
            for sep in (" < ", " > "):
                if sep in reason:
                    return reason.split(sep)[0] + sep.strip() + reason.split(sep)[1].split(",", 1)[-1]
            return reason

        print("剔除原因统计：")
        for reason, n in Counter(bucket(w) for _, w in res.dropped).most_common(10):
            print(f"  · {reason}  ×{n}")
    print()

    print(render_table(res.kept, query=query_text, limit=args.limit))

    # 把决策写回 run_papers
    store.attach_run_papers(rid, [
        {"run_id": rid, "paper_key": p.key, "keyword_score": p.keyword_score,
         "rank": None, "decision": "include", "note": None} for p in res.kept
    ] + [
        {"run_id": rid, "paper_key": p.key, "keyword_score": p.keyword_score,
         "rank": None, "decision": "exclude", "note": why} for p, why in res.dropped
    ])

    if args.export:
        paths = _export(res.kept, args.name or f"run{rid}_filtered", cfg,
                        _split(args.export), query=query_text)
        print()
        for p in paths:
            print(f"已导出：{p}")
    return 0


def cmd_runs(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.db_path)
    runs = store.list_runs(args.limit)
    if not runs:
        print("还没有任何运行记录。")
        return 0
    print(f"{'#':<6}{'时间':<22}{'候选':<8}标签")
    print("-" * 78)
    import json as _json

    for r in runs:
        stats = _json.loads(r["stats"] or "{}")
        print(f"{r['id']:<6}{(r['created_at'] or '')[:19]:<22}"
              f"{stats.get('deduped', '-'):<8}{(r['label'] or '')[:40]}")
    print()
    print("查看某次结果：litflow show --run <编号>")
    return 0


def cmd_show(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.db_path)
    rid, papers = _load_run_papers(store, args.run)
    if args.decision:
        rows = {r["paper_key"]: r for r in store.get_run_papers(rid)}
        papers = [p for p in papers
                  if (rows.get(p.key) or {}).get("decision") == args.decision]
    print(render_table(papers, limit=args.limit))
    return 0


def cmd_rank(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.db_path)
    if args.action == "import":
        systems = _split(args.system) or list(SUPPORTED)
        total = 0
        for s in systems:
            try:
                n = load_system(cfg, store, s)
                print(f"✓ 导入 {s}：{n} 条")
                total += n
            except (FileNotFoundError, ValueError) as e:
                print(f"✗ {s}：{e}")
        if total:
            print(f"\n共 {total} 条。现在可以在 filter 里用 --rank sjr:Q2 这类条件。")
        else:
            return 1
        return 0

    st = store.journal_rank_stats()
    if not st:
        print(f"（空）等级表目录：{cfg.journal_rank_dir}")
        return 0
    print("已导入的期刊等级表：")
    for k, v in st.items():
        print(f"  {k:<6} {v} 条")
    if args.probe:
        r = store.lookup_journal_rank(args.issn, args.probe, list(st))
        print(f"\n查询 {args.probe!r}：{r or '未收录'}")
    return 0


# ---------------------------------------------------------------- M2：Zotero


def _zotero_client(cfg: Config, store: Store):
    """构造 Zotero 客户端。

    本地写授权 key 缓存在本地 SQLite（kv_cache），不改用户的 config.toml。
    config.toml 里显式填了 api_key 时优先用它。
    """
    from .zotero import ZoteroClient

    z = cfg.zotero or {}
    cached = store.cache_get("zotero_local_key") or ""

    def _save_key(key: str, permanent: bool | None) -> None:
        # 只缓存永久 key。一次性 key 首次写入后即失效，
        # 缓存它只会让下次写入先撞一次 401 再弹窗。
        if permanent:
            store.cache_set("zotero_local_key", key)
        else:
            store.cache_set("zotero_local_key", "")

    client = ZoteroClient(
        base_url=z.get("base_url", "http://127.0.0.1:23119"),
        user_id=z.get("user_id", "0"),
        api_key=(z.get("api_key") or cached),
    )
    client.key_sink = _save_key
    return client


def cmd_zotero(args: argparse.Namespace, cfg: Config) -> int:
    from .librarian import (
        FAVORITE_COLLECTION,
        ImportOptions,
        import_papers,
        set_status,
        status_overview,
    )
    from .zotero import ZoteroError

    store = Store(cfg.db_path)

    if getattr(args, "reset_key", False):
        store.cache_set("zotero_local_key", "")
        print("已清除本地缓存的 Zotero 写授权 key。下次写入会重新弹窗。")

    client = _zotero_client(cfg, store)
    action = args.action

    # ---------------- check
    if action == "check":
        print("Zotero 本地 API 体检")
        print("=" * 56)
        try:
            info = client.ping()
        except ZoteroError as e:
            return _err(str(e))
        print(f"  Zotero 版本     : {info.get('zotero_version')}")
        print(f"  本地 API 版本   : {info.get('api_version')}")
        print(f"  Server-ID      : {_mask_id(info.get('server_id'))}")
        print(f"  写授权          : ", end="")
        if client.local_key:
            print("已缓存（从本地 SQLite 读取）")
            print(f"  集合数          : {len(client.list_collections(limit=1000))}")
            print(f"  条目数(顶层)    : {len(client.list_items(limit=3000))}")
        else:
            print("未授权")
            print("     首次导入时 Zotero 会弹窗，选「Always Allow」即可，之后不再打扰。")
        ov = status_overview(cfg, store)
        print()
        print(f"  litflow 已导入 Zotero 的文献：{ov['total']} 篇")
        if ov["total"]:
            for s, n in ov["by_status"]:
                print(f"     {ov['prefix']}{s:<8} {n}")
        return 0

    # ---------------- collections
    if action == "collections":
        try:
            cols = client.list_collections(limit=1000)
        except ZoteroError as e:
            return _err(str(e))
        if not cols:
            print("（还没有任何集合）")
            return 0
        print(f"{'集合名':<40}{'条目数':<8}key")
        print("-" * 76)
        for c in sorted(cols, key=lambda x: (x.get("data") or {}).get("name", "")):
            d = c.get("data") or {}
            n = (c.get("meta") or {}).get("numItems", "")
            print(f"{d.get('name', '')[:38]:<40}{str(n):<8}{c.get('key')}")
        print()
        print(f"提示：收藏集合用 `--collection \"{FAVORITE_COLLECTION}\"`")
        return 0

    # ---------------- status
    if action == "status":
        ov = status_overview(cfg, store)
        if not ov["total"]:
            print("还没有任何文献导入 Zotero。先跑 `litflow zotero import --run <编号>`")
            return 0
        print(f"litflow 已导入 {ov['total']} 篇，阅读状态：")
        print()
        width = max(len(s) for s, _ in ov["by_status"]) if ov["by_status"] else 4
        for s, n in ov["by_status"]:
            bar = "█" * min(40, n)
            print(f"  {ov['prefix']}{s:<{width}}  {n:>4}  {bar}")
        if ov["unmarked"]:
            print(f"  (未标记)  {ov['unmarked']:>4}")
        for k, v in ov["other"].items():
            print(f"  {k:<{width}}  {v:>4}")
        print()
        print("改状态：litflow zotero mark 在读 --run <编号> --top 5")
        return 0

    # ---------------- sync：从 Zotero 反向重建本地关联表
    if action == "sync":
        from .librarian import sync_links

        try:
            matched, skipped, problems = sync_links(cfg, store, client)
        except ZoteroError as e:
            return _err(str(e))
        print(f"已从 Zotero 重建关联：新增 {matched} 条" + (f"，跳过 {skipped} 条" if skipped else ""))
        for p in problems:
            print(f"  · {p}")
        if matched:
            print()
            print("现在可以用：litflow zotero mark <状态> --run <编号>")
        return 0

    # ---------------- audit：批量体检，不用逐条点开
    if action == "audit":
        from .librarian import audit, sync_links

        if not store.zotero_links():
            print("本地关联表是空的，先重建一次…")
            try:
                sync_links(cfg, store, client)
            except ZoteroError as e:
                return _err(str(e))
        try:
            rep = audit(cfg, store, client)
        except ZoteroError as e:
            return _err(str(e))

        if not rep["total"]:
            print("没有任何已导入 Zotero 的文献。先跑 `litflow zotero import --run <编号>`")
            return 0

        print(f"已导入并关联的文献：{rep['total']} 篇")
        print(f"  ✓ 有 PDF 附件        {rep['ok']}")
        print(f"  ✗ 没有 PDF 附件      {rep['no_pdf']}")
        if rep["no_pdf_fixable"]:
            print(f"     其中 {rep['no_pdf_fixable']} 篇有开放获取链接，可以补抓")
        if rep.get("repaired"):
            print(f"  ↻ 已修正 {rep['repaired']} 条本地附件记录"
                  f"（本地标记与 Zotero 实际不符，通常是附件被删或条目被重建）")
        if rep["missing"]:
            print(f"  ! Zotero 里已找不到    {rep['missing']}（可能在回收站或被删）")

        missing_pdf = [r for r in rep["rows"] if r["exists"] and not r["has_pdf"]]
        if missing_pdf:
            print()
            print("没有 PDF 的（最多列 20 条）：")
            print(f"  {'标题':<58}{'可补抓':<8}状态")
            print("  " + "-" * 76)
            for r in missing_pdf[:20]:
                fix = "可补抓" if r["oa_url"] else "无 OA 链接"
                print(f"  {r['title'][:56]:<58}{fix:<8}{r['status'] or ''}")
            if len(missing_pdf) > 20:
                print(f"  …另有 {len(missing_pdf) - 20} 条")
            print()
            if rep["no_pdf_fixable"]:
                print("批量补抓：litflow zotero attach-pdf")
        return 0

    # ---------------- attach-pdf：只补附件，不新建条目
    if action == "attach-pdf":
        from .librarian import attach_pdfs

        links = store.zotero_links()
        if not links:
            print("本地关联表是空的，先重建一次…")
            from .librarian import sync_links

            try:
                sync_links(cfg, store, client)
            except ZoteroError as e:
                return _err(str(e))
            links = store.zotero_links()
        if not links:
            return _err("还没有任何已导入 Zotero 的文献。先跑 `litflow zotero import --run <编号>`。")

        cand = [
            p for p in store.get_papers()
            if p.key in links and p.oa_url and not links[p.key].get("attached_pdf")
        ]
        # --collection 原先在本分支被静默忽略：候选一律从全部关联里选，
        # 于是「只想给某个集合补 PDF」实际会给所有集合都补一遍，
        # 既慢又容易触发对方的限流。import 分支能认这个参数，这里也应该认。
        if args.collection:
            before = len(cand)
            cand = [p for p in cand if links[p.key].get("collection") == args.collection]
            print(f"按集合「{args.collection}」限定：{before} -> {len(cand)} 篇")
        if args.top:
            cand = cand[: args.top]
        if not cand:
            print("没有可补抓的对象（要么都已有附件，要么都没有开放获取链接）。")
            print("用 `litflow zotero audit` 看详细情况。")
            return 0

        print(f"准备为 {len(cand)} 篇补抓开放获取 PDF（只走合法 OA 链接，不绕过付费墙）")
        if args.dry_run:
            for p in cand[:10]:
                print(f"  · {p.title[:60]}")
            print("（dry-run，未实际抓取）")
            return 0

        n, problems = attach_pdfs(cfg, store, client, cand, http=_http(cfg))
        print()
        print(f"成功 {n} 篇，失败 {len(problems)} 篇")

        # 失败原因归类，比逐条看更容易发现模式
        if problems:
            from collections import Counter

            def bucket(msg: str) -> str:
                for key in ("403", "404", "429", "超时", "连接被重置", "不是 PDF", "超过"):
                    if key in msg:
                        return {
                            "403": "出版商不允许程序化下载（403）",
                            "404": "链接已失效（404）",
                            "429": "被限流（429）",
                            "超时": "下载超时",
                            "连接被重置": "连接被重置",
                            "不是 PDF": "链接不是 PDF（多为落地页）",
                            "超过": "文件过大",
                        }[key]
                return "其他"

            print()
            print("失败原因归类：")
            for reason, cnt in Counter(bucket(m) for _, m in problems).most_common():
                print(f"  · {reason}  ×{cnt}")

            print()
            print("失败明细（最多 15 条）：")
            for title, msg in problems[:15]:
                print(f"  ! {title} —— {msg}")
            if len(problems) > 15:
                print(f"  ! 另有 {len(problems) - 15} 条")

        # 把失败清单导出成 CSV，方便去机构库/浏览器批量下载
        if problems:
            import csv as _csv

            out = cfg.export_dir / "未下载到PDF_需手动获取.csv"
            failed_titles = {t for t, _ in problems}
            rows = []
            for p in cand:
                if p.title[:56] not in failed_titles:
                    continue
                rows.append({
                    "标题": p.title,
                    "期刊": p.journal or "",
                    "年份": p.year or "",
                    "DOI": p.doi or "",
                    "PMID": p.pmid or "",
                    "PMCID": p.extra.get("pmcid") or "",
                    "开放获取链接": p.oa_url or "",
                    "失败原因": next((m for t, m in problems if t == p.title[:56]), ""),
                    "EuropePMC全文XML": (
                        f"https://www.ebi.ac.uk/europepmc/webservices/rest/"
                        f"{p.extra['pmcid']}/fullTextXML"
                        if p.extra.get("pmcid") else ""
                    ),
                })
            with out.open("w", newline="", encoding="utf-8-sig") as f:
                w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print()
            print(f"失败清单已导出：{out}")
            print("（里面含 DOI 与 EuropePMC 全文 XML 链接，可用机构 VPN 或浏览器批量获取）")

        print()
        print("复查：litflow zotero audit")
        return 0

    # ---------------- cleanup-empty
    if action == "cleanup-empty":
        links = store.zotero_links()
        parents = [l["zotero_key"] for l in links.values()]
        try:
            orphans = client.empty_attachments(parents or None)
        except ZoteroError as e:
            return _err(str(e))
        if not orphans:
            print("没有发现空壳附件。")
            return 0
        print(f"发现 {len(orphans)} 个空壳附件（上传中途失败留下的残骸，无 md5）：")
        for o in orphans[:15]:
            print(f"  · {(o.get('data') or {}).get('title', '')[:60]}  ({o['key']})")
        if len(orphans) > 15:
            print(f"  · 另有 {len(orphans) - 15} 个")
        if not args.yes:
            print()
            print("确认删除请加 --yes 重新执行。")
            return 0
        try:
            n = client.delete_items([o["key"] for o in orphans])
        except ZoteroError as e:
            return _err(str(e))
        print(f"\n已删除 {n} 个空壳附件。")
        return 0

    # ---------------- import / mark 需要论文列表
    rid, papers = _load_run_papers(store, args.run)

    if args.decision:
        rows = {r["paper_key"]: r for r in store.get_run_papers(rid)}
        papers = [p for p in papers
                  if (rows.get(p.key) or {}).get("decision") == args.decision]
    if args.oa:
        papers = [p for p in papers if p.oa_url]
    if args.top:
        papers = papers[: args.top]

    if not papers:
        return _err("筛选后没有论文。检查 --run / --decision / --top / --oa 参数。")

    # ---------------- import
    if action == "import":
        opts = ImportOptions(
            collection=args.collection or "litflow 导入",
            # 注意：不能直接写 args.status。未传 --status 时它是 None，
            # 会把 ImportOptions 里 "未读" 的默认值覆盖掉，导致状态标签丢失。
            status=args.status or "未读",
            attach_pdf=args.attach_pdf,
            only_oa=args.oa,
            dry_run=args.dry_run,
            limit=args.top,
        )
        print(f"准备导入 运行 #{rid} 的 {len(papers)} 篇到集合「{opts.collection}」")
        print(f"阅读状态标签：{opts.status}，抓取 PDF：{'是' if opts.attach_pdf else '否'}")
        if args.dry_run:
            print("（dry-run：不会真的写入 Zotero）")
        else:
            print("首次写入 Zotero 会弹窗申请授权，请选「Always Allow」。")

        try:
            res = import_papers(cfg, store, client, papers, opts, http=_http(cfg))
        except ZoteroError as e:
            return _err(str(e))

        print()
        print(res.summary)
        if res.collection_key:
            print(f"集合 key：{res.collection_key}")
        for key, why in res.skipped[:10]:
            print(f"  · 跳过：{why}")
        if len(res.skipped) > 10:
            print(f"  · 另有 {len(res.skipped) - 10} 条跳过")
        for title, msg in res.failed[:10]:
            print(f"  ! 失败：{title[:50]} —— {msg[:90]}")
        if res.pdf_problems:
            print(f"  PDF 抓取未成功 {len(res.pdf_problems)} 篇：")
            for title, msg in res.pdf_problems[:5]:
                print(f"    · {title} —— {msg}")
            if len(res.pdf_problems) > 5:
                print(f"    · 另有 {len(res.pdf_problems) - 5} 篇")
        print()
        print("下一步：")
        print(f"  litflow zotero status                 # 看阅读状态分布")
        print(f"  在 Zotero 里确认条目 → 开启 AI Butler 的「自动扫描新文献」")
        return 0

    # ---------------- mark
    if action == "mark":
        # 支持两种写法：`mark 在读` 与 `mark --status 在读`
        status = args.status or args.value
        if not status:
            return _err("mark 需要指定状态，例如：litflow zotero mark 在读 --run 11")
        try:
            ok, problems = set_status(cfg, store, client, papers, status)
        except (ZoteroError, ValueError) as e:
            return _err(str(e))
        print(f"已把 {ok} 篇标为「{status}」")
        for p in problems[:10]:
            print(f"  ! {p}")
        if len(problems) > 10:
            print(f"  ! 另有 {len(problems) - 10} 条未处理")
        return 0

    return _err(f"未知的 zotero 动作：{action}")


# ---------------------------------------------------------------- M3：初读提炼


def cmd_digest(args: argparse.Namespace, cfg: Config) -> int:
    from .digester import (
        digest_rows,
        digest_status,
        export_digest,
        fetch_notes,
        parsed_for,
        render_digest_table,
    )
    from .zotero import ZoteroError

    store = Store(cfg.db_path)
    action = args.action

    # ---------------- status
    if action == "status":
        rep = digest_status(cfg, store)
        if not rep["total"]:
            print("还没有已导入 Zotero 的文献。")
            print("先跑：litflow zotero import --run <编号> --attach-pdf")
            return 0
        total = rep["total"]
        print(f"初读提炼进度（共 {total} 篇已导入 Zotero）")
        print("=" * 56)
        print(f"  ✓ 已有精读笔记   {rep['done']:>4}")
        print(f"  … 待 Butler 精读 {rep['pending']:>4}")
        print(f"  ✗ 缺 PDF（读不了）{rep['no_pdf']:>4}")

        pct = round(100 * rep["done"] / total) if total else 0
        filled = int(pct / 4)
        print(f"\n  进度 [{'█' * filled}{'·' * (25 - filled)}] {pct}%")

        if rep["pending"] or rep["no_pdf"]:
            print()
            print("尚未完成的（最多 15 条）：")
            for title, why in rep["pending_rows"][:15]:
                print(f"  · {title:<62} {why}")
            if len(rep["pending_rows"]) > 15:
                print(f"  · 另有 {len(rep['pending_rows']) - 15} 条")

        if rep["no_pdf"]:
            print()
            print("⚠️ 缺 PDF 的没法精读。补抓：litflow zotero attach-pdf")
        if rep["pending"]:
            print()
            print("待精读的在 Butler 里排队。确认已开启：")
            print("  AI Butler 仪表盘 → 界面设置 → 勾选「自动扫描新文献」")
            print("拉取笔记：litflow digest fetch")
        return 0

    # ---------------- fetch
    if action == "fetch":
        client = _zotero_client(cfg, store)
        links = store.zotero_links()
        if not links:
            return _err("还没有已导入 Zotero 的文献。先跑 `litflow zotero import`。")

        papers = [p for p in store.get_papers()
                  if p.key in links and links[p.key].get("attached_pdf")]
        if args.top:
            papers = papers[: args.top]
        if not papers:
            print("没有带 PDF 的已导入文献，Butler 无从精读。")
            print("先补抓 PDF：litflow zotero attach-pdf")
            return 0

        print(f"回读 {len(papers)} 篇文献的 Butler 笔记（纯读请求，可反复执行）")
        try:
            rep = fetch_notes(cfg, store, client, papers)
        except ZoteroError as e:
            return _err(str(e))

        print()
        # FetchReport 是 dataclass，必须用属性访问。
        # 这里早先写成了 rep['with_note'] 这样的字典下标，导致本命令的汇总输出
        # 每次都以 TypeError 收场（workflow / web 两处是对的，只有这里漏改）。
        # no_pdf 的口径：本地记录说已附 PDF，但实际查 Zotero 找不到附件。
        # 它与 `digest status` 里那个「缺 PDF」不是同一件事，标签必须区分开。
        print(f"已有笔记 {rep.with_note} 篇；尚未生成 {rep.no_note} 篇；"
              f"附件已失效 {rep.no_pdf} 篇")
        for title, msg in rep.errors[:8]:
            print(f"  ! {title} —— {msg}")
        print()
        if rep.no_note:
            print("Butler 可能还在处理，稍后重跑本命令即可（已生成的不会重复拉）。")
        print("导出汇总表：litflow digest export")
        return 0

    # ---------------- export
    if action == "export":
        rid, papers = _load_run_papers(store, args.run)
        if args.decision:
            rows_map = {r["paper_key"]: r for r in store.get_run_papers(rid)}
            papers = [p for p in papers
                      if (rows_map.get(p.key) or {}).get("decision") == args.decision]
        # 只导出已导入 Zotero 的（没导入就没有笔记，导出也没意义）
        links = store.zotero_links()
        if args.imported_only:
            papers = [p for p in papers if p.key in links]
        if args.top:
            papers = papers[: args.top]
        if not papers:
            return _err("没有可导出的论文。检查 --run / --decision 参数。")

        rows = digest_rows(cfg, store, papers)
        print(render_digest_table(rows, limit=args.limit))

        formats = _split(args.export) or ["xlsx", "csv", "md"]
        name = args.name or f"初读提炼_运行{rid}"
        paths = export_digest(cfg, rows, name, formats)
        print()
        for p in paths:
            print(f"已导出：{p}")

        done = sum(1 for r in rows if r["has_note"] == "是")
        if done < len(rows):
            print()
            print(f"提示：{len(rows) - done} 篇还没有笔记，导出内容里对应行是空的。")
            print("先 `litflow digest fetch` 回读一遍，等 Butler 处理完再导出。")
        return 0

    # ---------------- show
    if action == "show":
        rid, papers = _load_run_papers(store, args.run)
        target = None
        if args.key:
            for p in papers:
                if args.key in (p.key, p.doi or ""):
                    target = p
                    break
        elif args.top:
            target = papers[args.top - 1] if 0 < args.top <= len(papers) else None
        if target is None:
            return _err("没找到对应论文。用 --key <内部键或DOI> 或 --top <序号> 指定。")

        notes = store.get_notes(target.key)

        # --raw：直接打印 Butler 生成的原始笔记。
        # 用途：如果解析结果不对（字段没识别出来 / 归错类），
        # 先看原文就能判断是「同义词表缺词」还是「笔记结构完全不同」。
        if args.raw:
            print(f"标题：{target.title}")
            print(f"笔记数：{len(notes)}")
            print("=" * 70)
            if not notes:
                print("（还没有笔记。先 litflow digest fetch，并确认 Butler 已开启自动扫描）")
                return 0
            from .digester import html_to_text

            for n in notes:
                print(f"\n--- 笔记 {n.get('note_key')}｜{n.get('title')}｜"
                      f"拉取于 {n.get('fetched_at')} ---")
                print(html_to_text(n.get("content") or ""))
            print()
            print("=" * 70)
            print("上面是把 HTML 转成文本后的结果。")
            print("若字段没被正确识别，把这份原文发给开发者补齐同义词表即可。")
            return 0

        note = parsed_for(store, target.key)
        print(f"标题：{target.title}")
        print(f"期刊：{target.journal}（{target.year}）")
        print(f"DOI ：{target.doi}")
        print("=" * 70)
        if not note.has_content:
            print("（还没有笔记。先 litflow digest fetch，并确认 Butler 已开启自动扫描）")
            return 0
        from .digester import FIELD_LABELS

        for f, label in FIELD_LABELS.items():
            if f == "summary":
                continue
            v = note.sections.get(f)
            if v:
                print(f"\n【{label}】")
                print(v)
        if note.sections.get("summary"):
            print(f"\n【一句话总结】\n{note.sections['summary']}")
        if note.other:
            print("\n【其他小节】")
            for k, v in note.other.items():
                print(f"\n— {k} —\n{v}")
        return 0

    return _err(f"未知的 digest 动作：{action}")


# ---------------------------------------------------------------- M4：工作流


def cmd_recipe(args: argparse.Namespace, cfg: Config) -> int:
    from .workflow import STAGE_LABELS, init_recipes, list_recipes, load_recipe, recipes_dir

    action = args.action

    if action == "init":
        created = init_recipes(cfg, force=args.force)
        d = recipes_dir(cfg)
        if created:
            for p in created:
                print(f"已生成模板：{p}")
        else:
            print(f"模板已存在（未覆盖）：{d / 'recipe-template.toml'}")
            print("要强制覆盖加 --force")
        print()
        print("新建一套流程：")
        print(f"  cp \"{d}/recipe-template.toml\" \"{d}/我的课题.toml\"")
        print("  # 编辑里面的检索词与筛选条件")
        print("  litflow run 我的课题")
        return 0

    if action == "list":
        rs = list_recipes(cfg)
        if not rs:
            print(f"{recipes_dir(cfg)} 下还没有配方。")
            print("先生成模板：litflow recipe init")
            return 0
        print(f"{'名字':<26}{'检索式':<40}阶段")
        print("-" * 92)
        for r in rs:
            q = str(r.search.get("query") or "")
            if len(q) > 38:
                q = q[:35] + "…"
            stages = [s for s in STAGE_LABELS if r.stage_enabled(s)]
            print(f"{r.name[:24]:<26}{q:<40}{'→'.join(stages)}")
        print()
        print("执行：litflow run <名字>          查看：litflow recipe show <名字>")
        return 0

    if action == "show":
        if not args.name:
            return _err("show 需要指定配方名：litflow recipe show <名字>")
        try:
            r = load_recipe(cfg, args.name)
        except Exception as e:  # noqa: BLE001
            return _err(str(e))
        print(f"配方：{r.name}")
        if r.description:
            print(f"说明：{r.description}")
        print(f"文件：{r.path}")
        print("=" * 66)
        print(r.path.read_text(encoding="utf-8"))
        return 0

    return _err(f"未知的 recipe 动作：{action}")


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    from .workflow import RecipeError, STAGES, STAGE_LABELS, load_recipe, run_recipe

    try:
        recipe = load_recipe(cfg, args.recipe)
    except Exception as e:  # noqa: BLE001
        return _err(str(e))

    store = Store(cfg.db_path)

    if args.list_stages:
        print(f"配方「{recipe.name}」的阶段：")
        for s in STAGES:
            mark = "✓" if recipe.stage_enabled(s) else "（已禁用）"
            print(f"  {s:<16}{STAGE_LABELS[s]:<18}{mark}")
        print()
        print("只跑某几段：litflow run <配方> --only filter,export")
        print("跳过某几段：litflow run <配方> --skip zotero")
        return 0

    only = _split(args.only)
    skip = _split(args.skip)
    unknown = (set(only) | set(skip)) - set(STAGES)
    if unknown:
        return _err(f"未知阶段 {sorted(unknown)}，可用：{', '.join(STAGES)}")

    print(f"执行配方「{recipe.name}」" + (f"（{recipe.description}）" if recipe.description else ""))
    if only:
        print(f"只执行：{', '.join(only)}")
    if skip:
        print(f"跳过：{', '.join(skip)}")
    if args.run:
        print(f"复用运行 #{args.run}")
    if args.dry_run:
        stages = [s for s in STAGES if (not only or s in only) and s not in skip
                  and recipe.stage_enabled(s)]
        print()
        print("dry-run，将依次执行：")
        for i, s in enumerate(stages, 1):
            print(f"  {i}. {s:<16}{STAGE_LABELS[s]}")
        print()
        print("实际执行去掉 --dry-run 即可。")
        return 0

    try:
        results = run_recipe(
            cfg, store, recipe, _http(cfg),
            only=only, skip=skip, reuse_run_id=args.run,
        )
    except RecipeError as e:
        return _err(str(e))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130

    print()
    print("=" * 66)
    print("执行结果")
    ok = sum(1 for r in results if r.ok)
    for r in results:
        flag = "✓" if r.ok else "✗"
        print(f"  {flag} {STAGE_LABELS.get(r.stage, r.stage):<18}{r.summary}")
    print(f"\n{ok}/{len(results)} 个阶段成功")
    if ok == len(results):
        print()
        print("产物都在 data/exports/ 下。")
    return 0 if ok == len(results) else 1


# ---------------------------------------------------------------- M5：Web 界面


def cmd_web(args: argparse.Namespace, cfg: Config) -> int:
    from .web import serve

    try:
        serve(cfg, host=args.host, port=args.port,
              open_browser=not args.no_open)
    except RuntimeError as e:
        return _err(str(e))
    return 0


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="litflow",
        description="本地优先的文献工作流：检索 -> 筛选 -> 初读提炼 -> 管理",
    )
    p.add_argument("--config", help="指定 config.toml 路径")
    p.add_argument("-V", "--version", action="version", version=f"litflow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境体检")
    d.add_argument("--online", action="store_true", help="额外发起真实请求测试连通性")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sources", help="查看数据源启用状态")
    s.set_defaults(func=cmd_sources)

    se = sub.add_parser("search", help="多源检索")
    se.add_argument("-q", "--query", help="关键词（支持自然语言短语）")
    se.add_argument("--author", help="作者")
    se.add_argument("--journal", help="期刊")
    se.add_argument("--year-from", type=int, help="起始年份")
    se.add_argument("--year-to", type=int, help="结束年份")
    se.add_argument("--limit", type=int, default=40, help="每源取回条数（默认 40）")
    se.add_argument("--sort", choices=["relevance", "cited", "date"], default="relevance")
    se.add_argument("--source", help="只用指定源，逗号分隔，如 europepmc,arxiv")
    se.add_argument("--preprints-only", action="store_true", help="只要预印本（Europe PMC SRC:PPR）")
    se.add_argument("--no-cite-enrich", action="store_true", dest="no_cite_enrich",
                    help="跳过引用量补全（默认会用 Europe PMC 按 PMID/DOI 批量反查）")
    se.add_argument("--oa-enrich", action="store_true", help="用 Unpaywall 补全 OA 链接（较慢）")
    se.add_argument("--oa-limit", type=int, default=50, help="OA 补全最多处理多少篇")
    se.add_argument("--no-keyword-score", action="store_true", help="不算关键词匹配度")
    se.add_argument("--preview", type=int, default=20, help="终端预览条数")
    se.add_argument("--label", help="这次运行的标签")
    se.add_argument("--export", help="导出格式，逗号分隔：xlsx,csv,tsv,md,bib,json（xlsx = 可直接用 Excel 打开的文献表格）")
    se.set_defaults(func=cmd_search)

    f = sub.add_parser("filter", help="筛选排序候选池")
    f.add_argument("--run", type=int, help="运行编号（默认最近一次）")
    f.add_argument("--query", "-q", help="关键词，用于计算匹配度并排序")
    f.add_argument("--year-from", type=int)
    f.add_argument("--year-to", type=int)
    f.add_argument("--min-cites", type=int, help="最低总引用")
    f.add_argument("--min-cites-per-year", type=float, dest="min_cites_per_year",
                   help="最低年均引用（推荐用来避免全是老论文）")
    f.add_argument("--rank", action="append",
                   help="期刊等级门槛，可重复。如 --rank sjr:Q2 --rank ccf:B")
    f.add_argument("--oa", action="store_true", help="只要开放获取")
    f.add_argument("--require-abstract", action="store_true", help="必须带摘要")
    f.add_argument("--require-doi", action="store_true", help="必须带 DOI")
    f.add_argument("--only-preprints", action="store_true", help="只要预印本")
    f.add_argument("--exclude-preprints", action="store_true", help="排除预印本")
    f.add_argument("--source", help="只保留命中这些源的（逗号分隔）")
    f.add_argument("--must", help="标题/摘要必须包含任一（逗号分隔）")
    f.add_argument("--title-must", dest="title_must",
                   help="仅标题必须包含任一（逗号分隔）。"
                        "区分方法论文与应用论文最有效的旋钮，"
                        "例如 --title-must deconvolution 可滤掉「用了某方法做生物学分析」的论文")
    f.add_argument("--exclude", help="标题/摘要包含任一即剔除（逗号分隔）")
    f.add_argument("--exclude-corrections", action="store_true",
                   help="剔除「Correction to:」这类更正/勘误记录（原论文的附属条目，属噪声）")
    f.add_argument("--rank-allow-unknown", action="store_true", dest="rank_allow_unknown",
                   help="期刊等级表未收录的论文也保留。等级表不完整时用它避免结果被清空")
    f.add_argument("--min-keyword", type=float, dest="min_keyword",
                   help="关键词匹配度下限 0-1。按引用量排序时强烈建议加上，"
                        "例如 --min-keyword 0.3，否则会混入全局高被引但不相关的论文")
    f.add_argument("--sort", choices=list(SORT_KEYS), default="rank",
                   help="排序方式（默认 rank：期刊等级优先）")
    f.add_argument("--top", type=int, help="只取前 N 篇")
    f.add_argument("--limit", type=int, default=30, help="终端预览条数")
    f.add_argument("--show-reasons", action="store_true", help="显示剔除原因统计")
    f.add_argument("--name", help="导出文件名（不带扩展名）")
    f.add_argument("--export", help="导出格式，逗号分隔：xlsx,csv,tsv,md,bib,json（xlsx = 可直接用 Excel 打开的文献表格）")
    f.set_defaults(func=cmd_filter)

    r = sub.add_parser("runs", help="列出历史运行")
    r.add_argument("--limit", type=int, default=20)
    r.set_defaults(func=cmd_runs)

    sh = sub.add_parser("show", help="查看某次运行的论文")
    sh.add_argument("--run", type=int)
    sh.add_argument("--decision", choices=["candidate", "include", "exclude"])
    sh.add_argument("--limit", type=int, default=50)
    sh.set_defaults(func=cmd_show)

    z = sub.add_parser("zotero", help="Zotero 集成：导入文献、管理阅读状态")
    z.add_argument("action",
                   choices=["check", "collections", "import", "mark", "status",
                            "audit", "attach-pdf", "sync", "cleanup-empty"],
                   help="check=体检；collections=列集合；import=导入条目；"
                        "attach-pdf=只补 PDF 附件；audit=批量体检附件与状态；"
                        "mark=改阅读状态；status=状态总览；"
                        "sync=从 Zotero 反向重建本地关联表；"
                        "cleanup-empty=清理上传失败留下的空壳附件")
    z.add_argument("--yes", action="store_true", help="cleanup-empty 时确认删除")
    z.add_argument("value", nargs="?",
                   help="mark 动作的状态值，如：litflow zotero mark 在读 --run 11")
    z.add_argument("--run", type=int, help="运行编号（默认最近一次）")
    z.add_argument("--collection", help='目标集合名，默认「litflow 导入」')
    z.add_argument("--status", help="阅读状态（import 默认未读；mark 时必填）")
    z.add_argument("--decision", choices=["candidate", "include", "exclude"],
                   help="只处理该次运行中某种决策的论文（通常用 include）")
    z.add_argument("--top", type=int, help="只处理前 N 篇")
    z.add_argument("--oa", action="store_true", help="只处理有开放获取链接的")
    z.add_argument("--attach-pdf", action="store_true", dest="attach_pdf",
                   help="抓取开放获取 PDF 并作为附件上传（只走合法 OA 链接，不绕过付费墙）")
    z.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="只演练不写入")
    z.add_argument("--reset-key", action="store_true", dest="reset_key",
                   help="清除本地缓存的写授权 key（配合 check 用）。"
                        "上次弹窗点的是 Allow 而非 Always Allow 时，用它重置")
    z.set_defaults(func=cmd_zotero)

    dg = sub.add_parser("digest", help="初读提炼：回读 AI Butler 的笔记并汇总")
    dg.add_argument("action", choices=["status", "fetch", "export", "show"],
                    help="status=进度总览；fetch=回读笔记；export=导出汇总表；show=看某篇的笔记")
    dg.add_argument("--run", type=int, help="运行编号（默认最近一次）")
    dg.add_argument("--decision", choices=["candidate", "include", "exclude"],
                    help="只处理该次运行中某种决策的论文")
    dg.add_argument("--key", help="show 时指定论文（内部键或 DOI）")
    dg.add_argument("--raw", action="store_true",
                    help="show 时打印 Butler 笔记的原始内容（排查解析问题用）")
    dg.add_argument("--top", type=int, help="只处理前 N 篇；show 时表示第 N 篇")
    dg.add_argument("--imported-only", action="store_true", dest="imported_only",
                    help="export 时只导出已导入 Zotero 的（没导入的必然没有笔记）")
    dg.add_argument("--limit", type=int, default=15, help="终端预览条数")
    dg.add_argument("--name", help="导出文件名（不带扩展名）")
    dg.add_argument("--export", help="导出格式，逗号分隔：xlsx,csv,md（默认全出）")
    dg.set_defaults(func=cmd_digest)

    rn = sub.add_parser("run", help="执行工作流配方：一条命令跑完整流程")
    rn.add_argument("recipe", help="配方名，对应 recipes/<名字>.toml")
    rn.add_argument("--only", help=f"只执行这些阶段，逗号分隔（{'/'.join(STAGE_NAMES)}）")
    rn.add_argument("--skip", help="跳过这些阶段，逗号分隔")
    rn.add_argument("--run", type=int, help="复用已有的运行编号，不重新检索")
    rn.add_argument("--list-stages", action="store_true", dest="list_stages",
                    help="列出配方包含哪些阶段")
    rn.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="只打印将要执行的阶段，不实际执行")
    rn.set_defaults(func=cmd_run)

    rc = sub.add_parser("recipe", help="工作流配方管理")
    rc.add_argument("action", choices=["list", "show", "init"],
                    help="list=列出配方；show=查看内容；init=生成模板")
    rc.add_argument("name", nargs="?", help="配方名（show 用）")
    rc.add_argument("--force", action="store_true", help="init 时覆盖已有模板")
    rc.set_defaults(func=cmd_recipe)

    wb = sub.add_parser("web", help="启动本地 Web 界面（浏览器里点选操作）")
    wb.add_argument("--host", default="127.0.0.1",
                    help="监听地址，默认只绑本机（无鉴权，不要对外暴露）")
    wb.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    wb.add_argument("--no-open", action="store_true", dest="no_open",
                    help="不自动打开浏览器")
    wb.set_defaults(func=cmd_web)

    rk = sub.add_parser("rank", help="期刊等级表管理")
    rk.add_argument("action", nargs="?", choices=["import", "stats"], default="stats")
    rk.add_argument("system", nargs="?", help="体系名（import 时可用逗号分隔多个）")
    rk.add_argument("--probe", help="按期刊名试查（配合 stats）")
    rk.add_argument("--issn", help="配合 --probe 用 ISSN 查")
    rk.set_defaults(func=cmd_rank)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _err(str(e))

    try:
        return int(args.func(args, cfg) or 0)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        if args.cmd == "doctor":
            traceback.print_exc()
            return 1
        return _err(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
