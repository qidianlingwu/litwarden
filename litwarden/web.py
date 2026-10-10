"""本地 Web 界面。

用标准库 `http.server` 起一个只监听本机的服务，浏览器打开即用。
刻意不引入 Flask/FastAPI —— 与 M1 定下的「零第三方依赖」一致
（本机 Python 3.14 装包容易踩 wheel 缺失）。

架构：
    · 静态页面在 `webui.INDEX_HTML`，单文件、无 CDN，可离线
    · 耗时操作（检索、Zotero 写入、补 PDF）放到后台线程跑，
      返回 job_id，前端轮询 /api/job/<id> 拿日志与进度
      —— 因为一次检索可能几十秒，同步阻塞会让界面像卡死
    · 只绑定 127.0.0.1，不对外暴露（这是本地工具，没有鉴权）
"""

from __future__ import annotations

import json
import os
import socket
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from . import __version__
from .adapters import build_adapters
from .config import Config
from .journalrank import attach_ranks
from .preview import abstract_preview
from .store import Store
from .webui import INDEX_HTML

# 下载导出文件时的 MIME。Excel 用标准的 zip 型，浏览器仍会按后缀另存。
_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".bib": "text/plain",
    ".json": "application/json",
    ".txt": "text/plain",
}


# ---------------------------------------------------------------- 任务注册表


class Job:
    def __init__(self, name: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.status = "running"          # running | done | error
        self.progress: float | None = None
        self.log: list[str] = []
        self.result: Any = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def say(self, msg: str) -> None:
        with self._lock:
            # 日志上限，避免长时间任务把内存吃满
            if len(self.log) < 800:
                self.log.append(str(msg))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id, "name": self.name, "status": self.status,
                "progress": self.progress, "log": list(self.log),
                "result": self.result if self.status == "done" else None,
                "error": self.error,
            }


class Jobs:
    def __init__(self) -> None:
        self._items: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, name: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(name)
        with self._lock:
            self._items[job.id] = job
            # 只保留最近 40 个，避免无限增长
            if len(self._items) > 40:
                for k in list(self._items)[:-40]:
                    self._items.pop(k, None)

        def runner() -> None:
            try:
                job.result = fn(job)
                job.progress = 100
                job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.error = f"{type(e).__name__}: {e}"
                job.status = "error"
                job.say(f"[失败] {job.error}")
                job.say(traceback.format_exc(limit=3))

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, jid: str) -> Job | None:
        with self._lock:
            return self._items.get(jid)


# ---------------------------------------------------------------- 序列化


def paper_json(p, idx: int | None = None) -> dict[str, Any]:
    d = {
        "key": p.key,
        "title": p.title,
        "journal": p.journal,
        "year": p.year,
        "cited_by": p.cited_by,
        "cites_per_year": p.cites_per_year,
        "keyword_score": p.keyword_score,
        "rank_str": "; ".join(f"{k}={v}" for k, v in (p.journal_rank or {}).items()),
        "is_oa": p.is_oa,
        "oa_url": p.oa_url,
        "doi": p.doi,
        # 论文表要让人在**导入之前**就能判断值不值得读，所以把摘要压成速览
        # 一起带上。完整摘要仍留在导出表的最后一列，需要看全文时去那里。
        "abstract_preview": abstract_preview(p.abstract),
    }
    if idx is not None:
        d["__i"] = idx
    return d


def _filters_from_payload(f: dict[str, Any]):
    from .filterer import Filters

    return Filters(
        min_cites=f.get("min_cites"),
        min_cites_per_year=f.get("min_cites_per_year"),
        rank_min={str(k).lower(): str(v) for k, v in (f.get("rank") or {}).items()},
        oa_only=bool(f.get("oa_only")),
        only_preprints=bool(f.get("only_preprints")),
        exclude_preprints=bool(f.get("exclude_preprints")),
        must_include=list(f.get("must") or []),
        title_must=list(f.get("title_must") or []),
        exclude_terms=list(f.get("exclude") or []),
        min_keyword=f.get("min_keyword"),
        exclude_corrections=bool(f.get("exclude_corrections")),
        rank_allow_unknown=bool(f.get("rank_allow_unknown")),
    )


def _run_papers(store: Store, run_id: int):
    keys = [r["paper_key"] for r in store.get_run_papers(run_id)]
    papers = store.get_papers(keys)
    order = {k: i for i, k in enumerate(keys)}
    papers.sort(key=lambda p: order.get(p.key, 1 << 30))
    return papers


# ---------------------------------------------------------------- 请求处理

# SQLite 连接是线程绑定的，而 ThreadingHTTPServer 每个请求都在新线程里处理，
# 所以不能共用一个 Store 实例，否则报
# "SQLite objects created in a thread can only be used in that same thread"。
# 这里给每个线程一份独立连接（本地单用户工具，没必要上连接池）。
_store_tls = threading.local()


def thread_store(cfg: Config) -> Store:
    s = getattr(_store_tls, "store", None)
    if s is None:
        s = Store(cfg.db_path)
        _store_tls.store = s
    return s


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def allowed_hosts(bind_host: str) -> set[str]:
    """算出允许的 Host 头集合。

    为什么要做这件事：本地服务**没有任何鉴权**，唯一能挡住的防线就是
    校验 Host 头。否则存在 DNS-rebinding 攻击——攻击者把自己的域名
    解析到 127.0.0.1，他页面里的 JS 与本地 API 就成了同源，可以绕过
    CORS 直接调用 /api/zotero/import（改写 Zotero 文献库）、
    /api/recipe/run、/api/download（读你的导出文件）。

    规则：回环地址永远放行；绑到具体地址时加上那个地址；
    绑 0.0.0.0/:: 时加上本机所有网卡地址（用户明确想对外提供时才走这条）。
    """
    ok = set(_LOOPBACK_HOSTS)
    if bind_host not in ("0.0.0.0", "::", ""):
        ok.add(bind_host.lower())
    else:
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            ok.update(i.lower() for i in ips)
        except OSError:
            pass
    return ok


def host_of(header_value: str | None) -> str:
    """从 Host 头里剥出主机名（去掉端口，处理 IPv6 的方括号形式）。

    单独抽出来是为了可测：`[::1]:8765` -> `[::1]`，`127.0.0.1:8765` -> `127.0.0.1`。
    """
    raw = (header_value or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):                 # IPv6 字面量
        return raw.split("]", 1)[0] + "]"
    if ":" in raw:
        return raw.rsplit(":", 1)[0]
    return raw


class Handler(BaseHTTPRequestHandler):
    cfg: Config
    jobs: Jobs
    allowed_hosts: set[str] = set(_LOOPBACK_HOSTS)
    server_version = f"litwarden/{__version__}"

    @property
    def store(self) -> Store:
        """当前线程专属的 Store。

        注意：写成 property 而不是实例属性 —— 后台任务线程里也会访问它，
        那样才能拿到属于那个线程的连接。
        """
        return thread_store(self.cfg)

    # ---- 基础 ----

    def _host_allowed(self) -> bool:
        """校验 Host 头，挡住 DNS-rebinding。"""
        host = host_of(self.headers.get("Host"))
        return bool(host) and host.lower() in self.allowed_hosts

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # 默认会往 stderr 刷访问日志，本地工具没必要
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 明确关连接。响应体可能上百 KB（论文列表），
        # 复用连接一旦出现长度不一致就会让客户端读到错位的内容
        # （表现为 JSON 解析报 "Invalid control character"）。
        # 本地工具不值得为省一次握手冒这个风险。
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode())

    def _err(self, msg: str, code: int = 400) -> None:
        self._json({"error": msg}, code)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    @property
    def query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _qv(self, key: str, default: str = "") -> str:
        return (self.query.get(key) or [default])[0]

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._host_allowed():
            return self._err("Host 头不被允许：本地服务只接受回环/本机地址访问。", 403)
        try:
            if path in ("/", "/index.html"):
                return self._send(200, INDEX_HTML.encode(), "text/html")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path == "/api/overview":
                return self._json(self.api_overview())
            if path.startswith("/api/job/"):
                j = self.jobs.get(path.rsplit("/", 1)[-1])
                return self._json(j.snapshot() if j else {"error": "任务不存在"})
            if path == "/api/zotero/status":
                return self._json(self.api_zotero_status())
            if path == "/api/zotero/audit":
                return self._json(self.api_zotero_audit())
            if path == "/api/digest/status":
                return self._json(self.api_digest_status())
            if path == "/api/digest/rows":
                return self._json(self.api_digest_rows())
            if path == "/api/digest/note":
                return self._json(self.api_digest_note())
            if path == "/api/recipes":
                return self._json(self.api_recipes())
            if path == "/api/exports":
                return self._json(self.api_exports())
            if path == "/api/download":
                return self._download()
            return self._err(f"未知路径 {path}", 404)
        except Exception as e:  # noqa: BLE001
            return self._err(f"{type(e).__name__}: {e}", 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._host_allowed():
            return self._err("Host 头不被允许：本地服务只接受回环/本机地址访问。", 403)
        try:
            if path == "/api/search":
                return self._json(self.api_search())
            if path == "/api/filter":
                return self._json(self.api_filter())
            if path == "/api/export":
                return self._json(self.api_export())
            if path == "/api/zotero/import":
                return self._json(self.api_zotero_import())
            if path == "/api/zotero/sync":
                return self._json(self.api_zotero_sync())
            if path == "/api/zotero/attach-pdf":
                return self._json(self.api_zotero_attach())
            if path == "/api/digest/fetch":
                return self._json(self.api_digest_fetch())
            if path == "/api/digest/export":
                return self._json(self.api_digest_export())
            if path == "/api/recipes/init":
                return self._json(self.api_recipes_init())
            if path == "/api/recipe/run":
                return self._json(self.api_recipe_run())
            return self._err(f"未知路径 {path}", 404)
        except Exception as e:  # noqa: BLE001
            return self._err(f"{type(e).__name__}: {e}", 500)

    # ---- 概览 ----

    def api_overview(self) -> dict[str, Any]:
        cfg, store = self.cfg, self.store
        sources = []
        for name in ("europepmc", "pubmed", "arxiv", "crossref", "openalex", "semanticscholar"):
            sc = cfg.sources.get(name)
            sources.append({
                "name": name,
                "enabled": bool(sc and sc.enabled),
                "requires_key": name in ("openalex", "semanticscholar"),
                "has_key": bool(sc and sc.api_key),
            })
        # 构造适配器只是为了数出「有几个源可用」，不会真的发请求
        adapters = build_adapters(cfg, _http(cfg))
        z = self.api_zotero_status()
        return {
            "version": __version__,
            "sources": sources,
            "sources_total": len(sources),
            "sources_enabled": len(adapters),
            "journal_rank_total": sum(store.journal_rank_stats().values()),
            "papers_total": store.count_papers(),
            "runs": store.list_runs(30),
            "zotero": z,
        }

    # ---- 检索 ----

    def api_search(self) -> dict[str, Any]:
        p = self._body()
        cfg = self.cfg

        def work(job: Job) -> dict[str, Any]:
            from .filterer import score_keywords
            from .finder import enrich_citations, enrich_oa, search as finder_search
            from .adapters import SearchQuery

            # ⚠️ 必须在任务线程里取 store。
            # self.store 是 thread-local 的，若在请求线程里提前取好再传进来，
            # 后台线程用它会报 "SQLite objects created in a thread can only be
            # used in that same thread"。
            store = self.store

            q = SearchQuery(
                text=(p.get("query") or "").strip(),
                author=(p.get("author") or "").strip() or None,
                journal=(p.get("journal") or "").strip() or None,
                year_from=p.get("year_from"),
                year_to=p.get("year_to"),
                limit=int(p.get("limit") or 40),
                sort=p.get("sort") or "relevance",
            )
            if p.get("preprints_only"):
                q.extras["epmc_src"] = "PPR"
            if q.is_empty():
                raise ValueError("检索条件为空：至少填关键词、作者或期刊之一")

            http = _http(cfg)
            adapters = build_adapters(cfg, http)
            want = [x for x in (p.get("sources") or []) if x]
            if want:
                adapters = [a for a in adapters if a.name in want]
            if not adapters:
                raise ValueError("没有可用的数据源")

            job.say(f"检索式：{q.describe()}")
            job.say(f"数据源：{', '.join(a.name for a in adapters)}")
            report = finder_search(cfg, http, q, adapters=adapters)
            job.say(f"原始命中 {report.raw_count} 条，去重后 {len(report.papers)} 条，"
                    f"耗时 {report.elapsed}s")
            for name, n in report.per_source.items():
                job.say(f"  · {name:<16} {n} 条")
            for name, msg in report.errors.items():
                job.say(f"  ! {name} 失败：{msg.splitlines()[0][:110]}")

            papers = report.papers
            if p.get("oa_enrich") and papers:
                job.say("用 Unpaywall 补全开放获取链接…")
                enrich_oa(cfg, http, papers)

            if papers:
                filled = enrich_citations(http, papers)
                if filled:
                    job.say(f"引用量补全 {filled} 篇")

            systems = cfg.journal_rank_enabled
            hit = attach_ranks(store, papers, systems)
            if systems:
                job.say(f"期刊等级命中 {hit}/{len(papers)} 篇")

            store.upsert_papers(papers)

            # 打分必须在 attach_run_papers 之前完成，否则 run_papers.keyword_score
            # 会写成 NULL（早先就是把顺序放反了）。
            if q.text.strip():
                for x, sc in zip(papers, score_keywords(papers, q.text)):
                    x.keyword_score = sc

            run_id = store.create_run(
                f"[web] {q.describe()[:60]}",
                {"query": {"text": q.text, "author": q.author, "journal": q.journal,
                           "year_from": q.year_from, "year_to": q.year_to,
                           "limit": q.limit, "sort": q.sort},
                 "sources": [a.name for a in adapters], "stages": ["search"]},
            )
            store.attach_run_papers(run_id, [
                {"run_id": run_id, "paper_key": x.key, "keyword_score": x.keyword_score,
                 "rank": None, "decision": "candidate", "note": None} for x in papers
            ])
            store.finish_run(run_id, {"raw": report.raw_count, "deduped": len(papers),
                                      "elapsed": report.elapsed,
                                      "per_source": report.per_source})
            job.say(f"已保存 运行 #{run_id}")

            if q.text.strip():
                papers.sort(key=lambda z: z.keyword_score or 0.0, reverse=True)

            return {
                "run_id": run_id,
                "raw": report.raw_count,
                "errors": {k: v.splitlines()[0][:160] for k, v in report.errors.items()},
                "papers": [paper_json(x, i) for i, x in enumerate(papers, 1)],
            }

        return {"job_id": self.jobs.create("search", work).id}

    # ---- 筛选 ----

    def api_filter(self) -> dict[str, Any]:
        p = self._body()
        run_id = p.get("run_id")
        if not run_id:
            return {"papers": [], "stats": {"input": 0, "dropped": 0}, "rank_coverage": ""}

        from .filterer import apply as filter_apply

        store = self.store
        papers = _run_papers(store, int(run_id))
        systems = self.cfg.journal_rank_enabled
        rank_hit = attach_ranks(store, papers, systems)

        f = p.get("filters") or {}
        query_text = ""
        run = store.get_run(int(run_id))
        if run:
            query_text = ((run.get("recipe") or {}).get("query") or {}).get("text", "") or ""

        res = filter_apply(papers, _filters_from_payload(f), query_text=query_text,
                           sort_by=f.get("sort") or "rank", systems=systems)
        pct = round(100 * rank_hit / len(papers)) if papers else 0
        return {
            "papers": [paper_json(x, i) for i, x in enumerate(res.kept, 1)],
            "stats": {"input": len(papers), "dropped": len(res.dropped),
                      "kept": len(res.kept)},
            "rank_coverage": f"{rank_hit}/{len(papers)}（{pct}%）" if systems else "",
        }

    # ---- 导出 ----

    def api_export(self) -> dict[str, Any]:
        from .exporters import to_bibtex, to_csv, to_markdown, to_xlsx

        p = self._body()
        keys = p.get("keys") or []
        papers = [x for x in self.store.get_papers(keys)]
        order = {k: i for i, k in enumerate(keys)}
        papers.sort(key=lambda z: order.get(z.key, 1 << 30))
        if not papers:
            raise ValueError("没有选中的论文")

        base = self.cfg.export_dir / f"选中论文_{len(papers)}篇"
        base.parent.mkdir(parents=True, exist_ok=True)
        files = [
            to_xlsx(papers, base.with_suffix(".xlsx"), sheet_name=base.name[:31]),
            to_csv(papers, base.with_suffix(".csv")),
            to_markdown(papers, base.with_suffix(".md")),
            to_bibtex(papers, base.with_suffix(".bib")),
        ]
        return {"files": [str(f) for f in files]}

    # ---- 导出文件浏览与下载 ----

    def _export_path(self, name: str):
        """把文件名解析成导出目录下的真实路径；非法返回 None。

        只接受纯文件名（不含目录分隔符、不以 . 开头）。这是必须的防护：
        本地服务没有鉴权，若允许传入任意路径，它会变成一个任意文件读取接口。
        """
        name = (name or "").strip()
        if not name or name.startswith(".") or name != os.path.basename(name):
            return None
        root = self.cfg.export_dir.resolve()
        p = (root / name).resolve()
        # realpath 之后必须仍在导出目录内，同时挡住符号链接绕过
        if p.parent != root or not p.is_file():
            return None
        return p

    def api_exports(self) -> dict[str, Any]:
        """列出 data/exports/ 下的全部产物，供界面浏览与下载。

        为什么要这个接口：导出动作本来只回一个文件名列表，用户关掉提示
        就再也找不到了，只能去 Finder 里翻目录。界面里需要一个常驻入口。
        """
        root = self.cfg.export_dir
        files: list[dict[str, Any]] = []
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                files.append({
                    "name": entry.name,
                    "ext": entry.suffix.lstrip(".").lower(),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return {"dir": str(root), "files": files}

    def _download(self) -> None:
        p = self._export_path(self._qv("name"))
        if p is None:
            return self._err("文件不存在，或文件名不合法", 404)
        try:
            data = p.read_bytes()
        except OSError as e:
            return self._err(f"读取失败：{e}", 500)

        ctype = _MIME.get(p.suffix.lower(), "application/octet-stream")
        if ctype.startswith("text/") or ctype == "application/json":
            ctype += "; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 中文文件名必须用 RFC 5987 的 filename*，否则浏览器保存时是乱码
        q = quote(p.name)
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{q}"; filename*=UTF-8\'\'{q}',
        )
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- Zotero ----

    def _zclient(self):
        from .cli import _zotero_client

        return _zotero_client(self.cfg, self.store)

    def api_zotero_status(self) -> dict[str, Any]:
        from .zotero import ZoteroError

        try:
            client = self._zclient()
            info = client.ping()
        except ZoteroError as e:
            return {"ok": False, "error": str(e).splitlines()[0], "linked": 0}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "linked": 0}

        cols = 0
        try:
            cols = len(client.list_collections(limit=1000))
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": True,
            "version": info.get("zotero_version"),
            "server_id": info.get("server_id"),
            "has_key": bool(client.local_key),
            "collections": cols,
            "linked": len(self.store.zotero_links()),
        }

    def api_zotero_import(self) -> dict[str, Any]:
        from .librarian import ImportOptions, import_papers
        from .zotero import ZoteroError

        p = self._body()
        keys = set(p.get("keys") or [])
        if not keys:
            raise ValueError("没有选中的论文")
        opts = ImportOptions(
            collection=(p.get("collection") or "litwarden 导入").strip(),
            status=(p.get("status") or "未读"),
            attach_pdf=bool(p.get("attach_pdf")),
        )
        papers = [x for x in self.store.get_papers(list(keys)) if x.key in keys]

        def work(job: Job) -> dict[str, Any]:
            job.say(f"写入集合「{opts.collection}」，共 {len(papers)} 篇")
            if opts.attach_pdf:
                job.say("会抓取开放获取 PDF，这一步较慢，请耐心等待…")
            client = self._zclient()
            try:
                res = import_papers(self.cfg, self.store, client, papers, opts,
                                    http=_http(self.cfg))
            except ZoteroError as e:
                raise ValueError(str(e).splitlines()[0]) from None
            job.say(res.summary)
            return {
                "summary": res.summary,
                "created": len(res.created),
                "attached": res.attached,
                "skipped": res.skipped[:30],
                "failed": res.failed[:30],
                "pdf_problems": res.pdf_problems[:30],
            }

        return {"job_id": self.jobs.create("zotero-import", work).id}

    def api_zotero_sync(self) -> dict[str, Any]:
        from .librarian import sync_links

        matched, skipped, _ = sync_links(self.cfg, self.store, self._zclient())
        return {"matched": matched, "skipped": skipped}

    def api_zotero_audit(self) -> dict[str, Any]:
        from .librarian import audit, sync_links

        store = self.store
        client = self._zclient()
        if not store.zotero_links():
            sync_links(self.cfg, store, client)
        rep = audit(self.cfg, store, client)
        rep.pop("rows", None)
        return rep

    def api_zotero_attach(self) -> dict[str, Any]:
        from .librarian import attach_pdfs

        def work(job: Job) -> dict[str, Any]:
            store = self.store
            links = store.zotero_links()
            cand = [x for x in store.get_papers()
                    if x.key in links and x.oa_url and not links[x.key].get("attached_pdf")]
            if not cand:
                job.say("没有可补抓的对象")
                return {"attached": 0, "problems": 0, "manifest": False}
            job.say(f"准备为 {len(cand)} 篇补抓开放获取 PDF")
            client = self._zclient()
            n, problems = attach_pdfs(self.cfg, store, client, cand,
                                      http=_http(self.cfg), verbose=False)
            job.say(f"成功 {n} 篇，失败 {len(problems)} 篇")
            return {"attached": n, "problems": len(problems), "manifest": bool(problems)}

        return {"job_id": self.jobs.create("zotero-attach", work).id}

    # ---- 提炼 ----

    def api_digest_status(self) -> dict[str, Any]:
        from .digester import digest_status

        return digest_status(self.cfg, self.store)

    def api_digest_fetch(self) -> dict[str, Any]:
        from .digester import fetch_notes

        def work(job: Job) -> dict[str, Any]:
            store = self.store
            links = store.zotero_links()
            papers = [x for x in store.get_papers()
                      if x.key in links and links[x.key].get("attached_pdf")]
            if not papers:
                job.say("没有带 PDF 的已导入文献，Butler 无从精读")
                return {"with_note": 0, "no_note": 0, "no_pdf": 0}
            job.say(f"回读 {len(papers)} 篇文献的笔记…")
            rep = fetch_notes(self.cfg, store, self._zclient(), papers, verbose=False)
            job.say(f"已有笔记 {rep.with_note} 篇，尚未生成 {rep.no_note} 篇，"
                    f"缺 PDF {rep.no_pdf} 篇")
            return {"with_note": rep.with_note, "no_note": rep.no_note,
                    "no_pdf": rep.no_pdf}

        return {"job_id": self.jobs.create("digest-fetch", work).id}

    def api_digest_rows(self) -> dict[str, Any]:
        from .digester import digest_rows

        store = self.store
        rid = self._qv("run_id")
        links = store.zotero_links()
        if rid:
            papers = _run_papers(store, int(rid))
            rows_map = {r["paper_key"]: r for r in store.get_run_papers(int(rid))}
            papers = [x for x in papers
                      if (rows_map.get(x.key) or {}).get("decision") == "include"]
            papers = [x for x in papers if x.key in links]
        else:
            papers = [x for x in store.get_papers() if x.key in links]

        rows = digest_rows(self.cfg, store, papers)
        for r in rows:
            r.pop("_other", None)
            r.pop("_fields", None)
        return {"rows": rows,
                "with_note": sum(1 for r in rows if r["has_note"] == "是")}

    def api_digest_note(self) -> dict[str, Any]:
        from .digester import FIELD_LABELS, html_to_text, parsed_for

        key = self._qv("key")
        if not key:
            raise ValueError("缺少 key")
        papers = {x.key: x for x in self.store.get_papers([key])}
        p = papers.get(key)
        note = parsed_for(self.store, key)
        # 一篇文献可能有多篇笔记（Butler 会分别生成表格与长文两种）。
        # 这里把每一篇都拼进去，别只给最长的那篇——否则用户在界面上
        # 永远看不到另一篇，会以为笔记丢了。
        notes = self.store.get_notes(key)
        parts: list[str] = []
        ordered = sorted(notes, key=lambda n: len(n.get("content") or ""), reverse=True)
        for i, n in enumerate(ordered, 1):
            t = html_to_text(n.get("content") or "").strip()
            if not t:
                continue
            if len(ordered) > 1:
                label = n.get("title") or "笔记"
                parts.append(f"===== 第 {i} 篇：{label} =====\n{t}")
            else:
                parts.append(t)
        raw = "\n\n".join(parts)
        return {
            "title": p.title if p else key,
            "fields": note.sections,
            "other": note.other,
            "labels": FIELD_LABELS,
            "raw": raw,
        }

    def api_digest_export(self) -> dict[str, Any]:
        from .digester import digest_rows, export_digest

        p = self._body()
        store, cfg = self.store, self.cfg
        rid = p.get("run_id")
        links = store.zotero_links()
        if rid:
            papers = _run_papers(store, int(rid))
            rows_map = {r["paper_key"]: r for r in store.get_run_papers(int(rid))}
            papers = [x for x in papers
                      if (rows_map.get(x.key) or {}).get("decision") == "include"]
            papers = [x for x in papers if x.key in links]
        else:
            papers = [x for x in store.get_papers() if x.key in links]
        if not papers:
            raise ValueError("没有可汇总的论文")
        rows = digest_rows(cfg, store, papers)
        files = export_digest(cfg, rows, f"初读提炼_{len(rows)}篇", ["xlsx", "csv", "md"])
        return {"files": [str(f) for f in files]}

    # ---- 配方 ----

    def api_recipes(self) -> dict[str, Any]:
        from .workflow import STAGES, list_recipes

        out = []
        for r in list_recipes(self.cfg):
            out.append({
                "name": r.name,
                "description": r.description,
                "query": r.search.get("query") or "",
                "stages": [s for s in STAGES if r.stage_enabled(s)],
                "text": (r.path.read_text(encoding="utf-8") if r.path else ""),
            })
        return {"recipes": out}

    def api_recipes_init(self) -> dict[str, Any]:
        from .workflow import init_recipes

        return {"created": [str(p) for p in init_recipes(self.cfg)]}

    def api_recipe_run(self) -> dict[str, Any]:
        from .workflow import load_recipe, run_recipe

        p = self._body()
        name = p.get("name")
        if not name:
            raise ValueError("缺少配方名")
        recipe = load_recipe(self.cfg, name)
        only = [x for x in (p.get("only") or []) if x]

        def work(job: Job) -> dict[str, Any]:
            results = run_recipe(self.cfg, self.store, recipe, _http(self.cfg),
                                 only=only, skip=p.get("skip") or [],
                                 log=job.say)
            return {
                "ok": sum(1 for r in results if r.ok),
                "total": len(results),
                "stages": [{"stage": r.stage, "ok": r.ok, "summary": r.summary}
                           for r in results],
            }

        return {"job_id": self.jobs.create("recipe-run", work).id}


# ---------------------------------------------------------------- 辅助


def _http(cfg: Config):
    from .http import HttpClient

    return HttpClient(email=cfg.email, timeout=cfg.timeout,
                      interval=cfg.request_interval, verify_ssl=cfg.verify_ssl,
                      tls=cfg.tls, retries=cfg.retries)


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True) -> None:
    """启动本地 Web 服务（阻塞）。"""
    store = Store(cfg.db_path)
    handler = type("BoundHandler", (Handler,), {
        "cfg": cfg,
        "jobs": Jobs(),
        "allowed_hosts": allowed_hosts(host),
    })

    # 端口被占就往后试几个
    last_err: Exception | None = None
    for p in range(port, port + 12):
        try:
            httpd = ThreadingHTTPServer((host, p), handler)
            break
        except OSError as e:
            last_err = e
            continue
    else:
        raise RuntimeError(f"{host}:{port}~{port + 11} 都不可用：{last_err}")

    url = f"http://{host}:{p}/"
    print(f"litwarden Web 界面已启动：{url}")
    print(f"  数据目录：{cfg.data_dir}")
    print(f"  候选池：{store.count_papers()} 篇")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print()
        print(f"  ⚠️  注意：{host} 不是回环地址，同网络的其他机器可能访问到这个界面。")
        print("     界面没有任何鉴权，能改写 Zotero 与导出文件——仅在你信任的网络下这样用。")
    print()
    print("按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        store.close()
