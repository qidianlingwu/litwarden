"""SQLite 存储层。

定位：只存「候选池 + 运行记录 + 笔记缓存 + 期刊等级表」。
文献库的唯一事实来源始终是 Zotero，这里不做第二份文库。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import Paper

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    key                 TEXT PRIMARY KEY,
    doi                 TEXT,
    pmid                TEXT,
    arxiv_id            TEXT,
    title               TEXT,
    abstract            TEXT,
    authors             TEXT,
    journal             TEXT,
    issn                TEXT,
    year                INTEGER,
    cited_by            INTEGER,
    cited_by_source     TEXT,
    cited_by_fetched_at TEXT,
    is_oa               INTEGER,
    oa_url              TEXT,
    url                 TEXT,
    type                TEXT,
    sources             TEXT,
    extra               TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_doi  ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_pmid ON papers(pmid);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);

-- 一次工作流运行。recipe 是可重跑的配方快照。
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT,
    recipe      TEXT,
    created_at  TEXT,
    stats       TEXT
);

-- 运行 <-> 候选论文 的多对多，附带该次运行的派生打分与决策
CREATE TABLE IF NOT EXISTS run_papers (
    run_id        INTEGER NOT NULL,
    paper_key     TEXT NOT NULL,
    keyword_score REAL,
    rank          TEXT,
    decision      TEXT,
    note          TEXT,
    PRIMARY KEY (run_id, paper_key)
);

-- Zotero-AI-Butler 生成的笔记回读缓存
CREATE TABLE IF NOT EXISTS butler_notes (
    paper_key  TEXT NOT NULL,
    zotero_key TEXT,
    note_key   TEXT,
    title      TEXT,
    content    TEXT,
    fetched_at TEXT,
    PRIMARY KEY (paper_key, note_key)
);

-- 期刊等级表。system 取值：sjr / ccf / cas / jcr
CREATE TABLE IF NOT EXISTS journal_rank (
    system      TEXT NOT NULL,
    match_key   TEXT NOT NULL,
    match_type  TEXT NOT NULL,   -- issn | title
    quartile    TEXT,
    metric      TEXT,
    value       TEXT,
    journal     TEXT,
    PRIMARY KEY (system, match_key, match_type)
);
CREATE INDEX IF NOT EXISTS idx_rank_journal ON journal_rank(journal);

-- 通用键值缓存（如 Unpaywall 查询结果、DOI 元数据）
CREATE TABLE IF NOT EXISTS kv_cache (
    k          TEXT PRIMARY KEY,
    v          TEXT,
    fetched_at TEXT
);

-- litflow 候选池 与 Zotero 条目 的对应关系。
-- M3 要靠它知道「哪篇论文在 Zotero 里对应哪个条目」，才能回读 Butler 的笔记。
CREATE TABLE IF NOT EXISTS zotero_links (
    paper_key     TEXT PRIMARY KEY,
    zotero_key    TEXT NOT NULL,
    collection    TEXT,
    status        TEXT,
    attached_pdf  INTEGER DEFAULT 0,
    imported_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_zlink_zotero ON zotero_links(zotero_key);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -------------------------------------------------- papers

    def upsert_papers(self, papers: Iterable[Paper]) -> int:
        rows = [p.to_row() for p in papers]
        if not rows:
            return 0
        sql = """
        INSERT INTO papers ({cols}) VALUES ({ph})
        ON CONFLICT(key) DO UPDATE SET
            doi=excluded.doi, pmid=excluded.pmid, arxiv_id=excluded.arxiv_id,
            title=excluded.title, abstract=excluded.abstract, authors=excluded.authors,
            journal=excluded.journal, issn=excluded.issn, year=excluded.year,
            cited_by=COALESCE(excluded.cited_by, papers.cited_by),
            cited_by_source=COALESCE(excluded.cited_by_source, papers.cited_by_source),
            cited_by_fetched_at=COALESCE(excluded.cited_by_fetched_at, papers.cited_by_fetched_at),
            is_oa=COALESCE(excluded.is_oa, papers.is_oa),
            oa_url=COALESCE(excluded.oa_url, papers.oa_url),
            url=COALESCE(excluded.url, papers.url),
            type=COALESCE(excluded.type, papers.type),
            sources=excluded.sources, extra=excluded.extra, updated_at=excluded.updated_at
        """.format(
            cols=",".join(rows[0].keys()),
            ph=",".join(f":{k}" for k in rows[0]),
        )
        with self.tx() as c:
            c.executemany(sql, rows)
        return len(rows)

    def get_papers(self, keys: Iterable[str] | None = None) -> list[Paper]:
        if keys is None:
            cur = self.conn.execute("SELECT * FROM papers ORDER BY year DESC, cited_by DESC")
        else:
            keys = list(keys)
            if not keys:
                return []
            ph = ",".join("?" * len(keys))
            cur = self.conn.execute(f"SELECT * FROM papers WHERE key IN ({ph})", keys)
        return [Paper.from_row(dict(r)) for r in cur.fetchall()]

    def count_papers(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])

    # -------------------------------------------------- runs

    def create_run(self, label: str, recipe: dict[str, Any]) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO runs (label, recipe, created_at) VALUES (?,?,?)",
                (label, json.dumps(recipe, ensure_ascii=False), _now()),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET stats=? WHERE id=?",
                (json.dumps(stats, ensure_ascii=False), run_id),
            )

    def attach_run_papers(self, run_id: int, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self.tx() as c:
            c.executemany(
                """INSERT INTO run_papers (run_id, paper_key, keyword_score, rank, decision, note)
                   VALUES (:run_id, :paper_key, :keyword_score, :rank, :decision, :note)
                   ON CONFLICT(run_id, paper_key) DO UPDATE SET
                     keyword_score=excluded.keyword_score,
                     rank=excluded.rank,
                     decision=excluded.decision,
                     note=excluded.note""",
                records,
            )

    def get_run_papers(self, run_id: int) -> list[dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM run_papers WHERE run_id=?", (run_id,))
        return [dict(r) for r in cur.fetchall()]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("recipe", "stats"):
            try:
                d[k] = json.loads(d[k]) if d[k] else {}
            except (TypeError, ValueError):
                d[k] = {}
        return d

    def latest_run_id(self) -> int | None:
        row = self.conn.execute("SELECT MAX(id) AS m FROM runs").fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT id, label, created_at, stats FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    # -------------------------------------------------- butler notes

    def save_notes(self, paper_key: str, zotero_key: str | None,
                   notes: list[dict[str, Any]]) -> int:
        rows = [
            (paper_key, zotero_key, n.get("key"), n.get("title"), n.get("content"), _now())
            for n in notes
        ]
        if not rows:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO butler_notes (paper_key, zotero_key, note_key, title, content, fetched_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(paper_key, note_key) DO UPDATE SET
                     content=excluded.content, fetched_at=excluded.fetched_at""",
                rows,
            )
        return len(rows)

    def get_notes(self, paper_key: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM butler_notes WHERE paper_key=? ORDER BY fetched_at DESC", (paper_key,)
        )
        return [dict(r) for r in cur.fetchall()]

    # -------------------------------------------------- journal rank

    def replace_journal_rank(self, system: str, rows: list[dict[str, Any]]) -> int:
        with self.tx() as c:
            c.execute("DELETE FROM journal_rank WHERE system=?", (system,))
            c.executemany(
                """INSERT OR REPLACE INTO journal_rank
                   (system, match_key, match_type, quartile, metric, value, journal)
                   VALUES (:system, :match_key, :match_type, :quartile, :metric, :value, :journal)""",
                rows,
            )
        return len(rows)

    def lookup_journal_rank(self, issn: str | None, journal: str | None,
                            systems: list[str]) -> dict[str, str]:
        """按 ISSN 优先、期刊名兜底，返回 {system: label}。"""
        from .models import normalize_journal

        out: dict[str, str] = {}
        if not systems:
            return out
        ph = ",".join("?" * len(systems))
        if issn:
            cur = self.conn.execute(
                f"""SELECT system, quartile, value FROM journal_rank
                    WHERE system IN ({ph}) AND match_type='issn' AND match_key=?""",
                (*systems, issn),
            )
            for r in cur.fetchall():
                out[r["system"]] = r["quartile"] or r["value"] or ""
        jn = normalize_journal(journal)
        if jn:
            cur = self.conn.execute(
                f"""SELECT system, quartile, value FROM journal_rank
                    WHERE system IN ({ph}) AND match_type='title' AND match_key=?""",
                (*systems, jn),
            )
            for r in cur.fetchall():
                out.setdefault(r["system"], r["quartile"] or r["value"] or "")
        return out

    def journal_rank_stats(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT system, COUNT(*) AS n FROM journal_rank GROUP BY system"
        )
        return {r["system"]: int(r["n"]) for r in cur.fetchall()}

    # -------------------------------------------------- zotero 关联

    def link_zotero(self, paper_key: str, zotero_key: str,
                    collection: str | None = None, status: str | None = None,
                    attached_pdf: int = 0) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO zotero_links
                   (paper_key, zotero_key, collection, status, attached_pdf, imported_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(paper_key) DO UPDATE SET
                     zotero_key=excluded.zotero_key,
                     collection=COALESCE(excluded.collection, zotero_links.collection),
                     status=COALESCE(excluded.status, zotero_links.status),
                     -- 同一个 Zotero 条目取 MAX：其余写入路径（如导入）并不知道
                     -- 附件情况，传 0 时不该抹掉已知的 1。
                     -- 但 zotero_key 变了说明是重新导入到了新条目，旧的「已附 PDF」
                     -- 属于另一个条目，继续沿用就是脏数据（实测出现过 3/5 条
                     -- 本地标记为 1、实际没有任何附件）。
                     attached_pdf=CASE
                       WHEN zotero_links.zotero_key = excluded.zotero_key
                         THEN MAX(excluded.attached_pdf, zotero_links.attached_pdf)
                       ELSE excluded.attached_pdf
                     END,
                     imported_at=excluded.imported_at""",
                (paper_key, zotero_key, collection, status, attached_pdf, _now()),
            )

    def set_attached_pdf(self, paper_key: str, attached: bool) -> None:
        """按实测结果强制设置附件标记，不做任何合并。

        存在的理由：link_zotero 的 MAX 语义保证「0 不会覆盖 1」，
        因此当附件在 Zotero 侧被删掉后，标记无法回退。体检（audit）
        拿到真实附件数后必须走这个入口才能把标记修正回来，
        否则 attach-pdf 会因为 `attached_pdf != 1` 判断而永远跳过这篇。
        """
        with self.tx() as c:
            c.execute(
                "UPDATE zotero_links SET attached_pdf=? WHERE paper_key=?",
                (int(attached), paper_key),
            )

    def get_zotero_link(self, paper_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM zotero_links WHERE paper_key=?", (paper_key,)
        ).fetchone()
        return dict(row) if row else None

    def zotero_links(self, paper_keys: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
        if paper_keys is None:
            cur = self.conn.execute("SELECT * FROM zotero_links")
        else:
            keys = list(paper_keys)
            if not keys:
                return {}
            ph = ",".join("?" * len(keys))
            cur = self.conn.execute(
                f"SELECT * FROM zotero_links WHERE paper_key IN ({ph})", keys
            )
        return {r["paper_key"]: dict(r) for r in cur.fetchall()}

    def set_zotero_status(self, zotero_key: str, status: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE zotero_links SET status=? WHERE zotero_key=?", (status, zotero_key)
            )

    def zotero_status_counts(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT COALESCE(status,'(未标)') AS s, COUNT(*) AS n "
            "FROM zotero_links GROUP BY s"
        )
        return {r["s"]: int(r["n"]) for r in cur.fetchall()}

    # -------------------------------------------------- kv cache

    def cache_get(self, key: str) -> Any | None:
        row = self.conn.execute("SELECT v FROM kv_cache WHERE k=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["v"])
        except (TypeError, ValueError):
            return None

    def cache_set(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO kv_cache (k, v, fetched_at) VALUES (?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), _now()),
            )
