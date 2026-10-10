"""Librarian —— 文献管理：把候选池写进 Zotero，并管理阅读状态。

设计要点：
  · Zotero 是文献库的唯一事实来源。litwarden 只做「写进去 + 记住对应关系」。
  · 对应关系写在本地 SQLite 的 zotero_links 表（paper_key <-> zotero_key），
    M3 要靠它知道去哪读 Butler 生成的笔记。
  · 阅读状态用保留标签 `wf:未读 / wf:在读 / wf:已初读 / wf:已精读`，
    而不是集合——因为一篇论文可以属于多个主题集合，但阅读状态是互斥的单一状态。
    标签在 Zotero 里也能直接筛选。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .filterer import is_preprint
from .models import Paper
from .store import Store
from .zotero import ImportResult, ZoteroClient, ZoteroError, download_file

DEFAULT_COLLECTION = "litwarden 导入"
FAVORITE_COLLECTION = "⭐ 收藏"

# Zotero-AI-Butler 在条目入库后**等 2 秒**就来判定附件是否就绪
# （AutoScan 里的 delay(2e3)），而且它的就绪判定只看附件条目在不在、
# 不查磁盘上是否真有文件。所以附件必须在条目创建后的这 2 秒内传完。
_BUTLER_READY_WINDOW = 2.0

_INITIALS_RE = re.compile(r"^[A-Z]{1,3}$")


# ---------------------------------------------------------------- 字段映射


def split_creator(raw: str) -> dict[str, str]:
    """把作者字符串拆成 Zotero 的 firstName / lastName。

    要处理三种写法：
        "Satterstrom, Frank"  → 逗号分隔，姓在前
        "Satterstrom FK"      → Europe PMC 的「姓 + 首字母大写缩写」
        "Frank Satterstrom"   → 常规「名 姓」

    判定缩写时要求全大写（^[A-Z]{1,3}$），这样 "Wang Li" 里的 "Li"
    （首字母大写、其余小写）不会被误判成缩写。
    """
    raw = (raw or "").strip()
    if not raw:
        return {"firstName": "", "lastName": ""}
    if "," in raw:
        last, first = raw.split(",", 1)
        return {"firstName": first.strip(), "lastName": last.strip()}

    parts = raw.split()
    if len(parts) == 1:
        return {"firstName": "", "lastName": parts[0]}
    if _INITIALS_RE.match(parts[-1]):
        return {"firstName": parts[-1], "lastName": " ".join(parts[:-1])}
    if re.fullmatch(r"[A-Z]\.?", parts[-1]):
        return {"firstName": parts[-1], "lastName": " ".join(parts[:-1])}
    return {"firstName": " ".join(parts[:-1]), "lastName": parts[-1]}


def _repository(p: Paper) -> tuple[str, str]:
    """推断预印本仓库名与归档号。"""
    if p.arxiv_id:
        return "arXiv", p.arxiv_id
    j = (p.journal or "").lower()
    if "biorxiv" in j:
        return "bioRxiv", ""
    if "medrxiv" in j:
        return "medRxiv", ""
    if "research square" in j:
        return "Research Square", ""
    return "Preprint", ""


def map_item_type(p: Paper) -> str:
    t = (p.type or "").lower()
    if "proceedings" in t or "conference" in t or "aaai" in t:
        return "conferencePaper"
    if is_preprint(p):
        return "preprint"
    return "journalArticle"


def paper_to_item(
    p: Paper,
    collection_key: str | None,
    status: str | None,
    status_prefix: str,
    item_type: str | None = None,
) -> dict[str, Any]:
    itype = item_type or map_item_type(p)

    creators = []
    for i, a in enumerate(p.authors):
        c = split_creator(a)
        if c["lastName"] or c["firstName"]:
            creators.append({"creatorType": "author", **c})

    tags: list[dict[str, str]] = []
    if status:
        tags.append({"tag": f"{status_prefix}{status}"})
    if p.is_oa:
        tags.append({"tag": f"{status_prefix}OA"})

    item: dict[str, Any] = {
        "itemType": itype,
        "title": p.title or "(无标题)",
        "creators": creators,
        "date": str(p.year) if p.year else "",
        "abstractNote": p.abstract or "",
        # extra 里存 litwarden 的内部键，用于可靠地识别「这篇是不是已经导入过」
        "extra": f"litwarden-key: {p.key}",
        "tags": tags,
        "url": p.url or (f"https://doi.org/{p.doi}" if p.doi else ""),
    }
    if p.doi:
        item["DOI"] = p.doi
    if collection_key:
        item["collections"] = [collection_key]

    if itype == "preprint":
        repo, aid = _repository(p)
        item["repository"] = repo
        if aid:
            item["archiveID"] = aid
        if p.journal and repo == "Preprint":
            # 仓库未知时把来源写进 extra，避免丢信息
            item["extra"] = f"{item['extra']}\nsource: {p.journal}"
    else:
        if p.journal:
            item["publicationTitle"] = p.journal
        if p.issn:
            item["ISSN"] = p.issn
        if itype == "conferencePaper" and p.journal:
            item["proceedingsTitle"] = p.journal
            item.pop("publicationTitle", None)

    return item


# ---------------------------------------------------------------- 去重


def existing_identifiers(
    client: ZoteroClient, collection_key: str | None
) -> tuple[set[str], set[str]]:
    """返回 (已存在的 DOI 集合, 已存在的 litwarden-key 集合)。"""
    items = (
        client.collection_items(collection_key)
        if collection_key
        else client.list_items(limit=3000)
    )
    dois: set[str] = set()
    keys: set[str] = set()
    for it in items:
        d = it.get("data") or {}
        if d.get("DOI"):
            dois.add(str(d["DOI"]).strip().lower())
        extra = d.get("extra") or ""
        for line in str(extra).splitlines():
            if line.strip().lower().startswith("litwarden-key:"):
                keys.add(line.split(":", 1)[1].strip())
    return dois, keys


# ---------------------------------------------------------------- 导入


@dataclass
class ImportOptions:
    collection: str = DEFAULT_COLLECTION
    status: str = "未读"
    attach_pdf: bool = False
    only_oa: bool = False
    dry_run: bool = False
    limit: int | None = None


def import_papers(
    cfg: Config,
    store: Store,
    client: ZoteroClient,
    papers: list[Paper],
    opts: ImportOptions,
    http: Any = None,
) -> ImportResult:
    res = ImportResult()
    prefix = (cfg.butler or {}).get("status_prefix", "wf:")

    if opts.only_oa:
        papers = [p for p in papers if p.oa_url]

    if opts.limit:
        papers = papers[: opts.limit]
    if not papers:
        return res

    if opts.dry_run:
        for p in papers:
            res.created.append({"key": "(dry-run)", "data": {"title": p.title}})
        return res

    collection_key = client.ensure_collection(opts.collection)
    res.collection_key = collection_key

    dois, keys = existing_identifiers(client, collection_key)

    payload: list[dict[str, Any]] = []
    mapping: list[Paper] = []
    already: list[Paper] = []
    for p in papers:
        if p.key in keys:
            res.skipped.append((p.key, "已在 Zotero 中（litwarden-key 命中）"))
            already.append(p)
            continue
        if p.doi and p.doi.lower() in dois:
            res.skipped.append((p.key, f"DOI 已存在：{p.doi}"))
            already.append(p)
            continue
        payload.append(paper_to_item(p, collection_key, opts.status, prefix))
        mapping.append(p)
        if p.doi:
            dois.add(p.doi.lower())
        keys.add(p.key)

    if not payload:
        # 所有论文都已导入过。但仍然要往下走：
        # --attach-pdf 应当能当「补附件」用（之前只导元数据、没抓 PDF 的情况）。
        if opts.attach_pdf:
            res.attached = _attach_pdfs(store, client, papers, res, http=http, email=cfg.email)
        return res

    # 先把 PDF 下载好，再创建 Zotero 条目。
    # 顺序反了会踩竞态：条目一旦入库，Butler 的「自动扫描新文献」会立刻来读，
    # 而它的 PDF 可能还在下载中 —— Butler 以「未找到 PDF 文件路径」失败且不重试，
    # 这篇论文就永远没有笔记。详见 prefetch_pdfs 的说明。
    prefetched: dict[str, tuple[str, bytes]] = {}
    if opts.attach_pdf:
        prefetched = prefetch_pdfs(store, papers, http, cfg.email)

    # 每篇走三步，顺序不能换：
    #   ① 先把附件传成**独立条目**（没有父条目，Butler 不处理它的 add 事件）
    #   ② 再创建父条目（此刻 Butler 才开始它的 2 秒倒计时）
    #   ③ 立刻把附件认亲到父条目下
    #
    # 为什么不按直觉来（建条目 → 传附件）：
    #   Butler 在父条目入库后等 2 秒就判定「附件可否分析」，而它的判定只看
    #   附件**条目**在不在、**不查磁盘上是否真有文件**；真正提取 PDF 时才查
    #   文件路径。于是「条目已存在、文件还没落盘」的窗口必然以
    #   「未找到 PDF 文件路径」失败，且带 suppressTaskRetry —— Butler 永不
    #   重试，这篇论文就永久没有笔记。
    #   实测差距只有 230 毫秒：文件比那次判定晚 230ms 落盘，照样失败。
    #   （原先那版是「先把所有条目批量建好、再统一传附件」，排在后面的论文
    #   要等前面几篇传完，2 秒早就过了，必然失败。改成逐篇交错后每篇只赌
    #   自己那一次上传，但实测一次上传约 2 秒，正好压在判定线上，仍以
    #   230ms 之差落败 —— 所以光靠提速赢不了。）
    #   三步走之后，父条目出现时文件早已就位。实测窗口从 2.0s 降到 0.1s，
    #   Butler 任务从 failed 变成 completed。
    oa_cache: dict[str, Any] = {}
    for item_payload, p in zip(payload, mapping):
        att_key: str | None = None
        if opts.attach_pdf and p.oa_url:
            att_key = _upload_orphan_attachment(
                client, p, prefetched, res, http, cfg.email, oa_cache
            )

        item = _create_with_fallback(
            client, item_payload, p, collection_key, opts.status, prefix, res
        )
        if item is None:
            # 父条目没建成，别在库里留下孤儿附件
            if att_key:
                try:
                    client.delete_items([att_key])
                except ZoteroError:
                    res.pdf_problems.append(
                        ((p.title or "")[:56], "父条目创建失败，孤儿附件未能清理")
                    )
            continue
        key = item.get("key") or (item.get("data") or {}).get("key")
        if not key:
            continue

        # 认亲要尽量快——父条目刚刚入库，倒计时已经在跑
        t0 = time.monotonic()
        linked_pdf = 0
        if att_key:
            try:
                client.set_parent(att_key, key)
                linked_pdf = 1
                res.attached += 1
            except ZoteroError as e:
                res.pdf_problems.append(
                    ((p.title or "")[:56], f"附件认亲失败：{str(e)[:100]}")
                )
        spent = time.monotonic() - t0

        store.link_zotero(p.key, key, collection_key, opts.status,
                          attached_pdf=linked_pdf)
        res.created.append(item)

        # 只在**确实挂上了附件**时才谈窗口：下载失败的情况下耗时长短与
        # Butler 无关，提示会误导（这条判断也是实测踩出来的）。
        if linked_pdf and spent > _BUTLER_READY_WINDOW:
            # 超窗不必然失败（Butler 后面还有几轮退避重试），但风险明显
            # 上升，得让用户知道是哪几篇需要盯一下
            print(f"    ⚠️ 父条目就绪到附件挂上耗时 {spent:.1f}s，超过 Butler 的 "
                  f"{_BUTLER_READY_WINDOW:.0f}s 判定窗口，"
                  f"这篇可能需要在 Butler 里手动重试")

    if opts.attach_pdf and already:
        # 补附件路径：这些论文库里已经有了，本次被跳过。它们也可能缺 PDF，
        # 单独批量补一次——早已入库、Butler 早已扫过，不存在 2 秒窗口问题。
        #
        # 只传 already 而不是 papers：本次新建的条目上面已经逐篇试过了，
        # 再传一遍会把失败的下载重来一次（网络白跑、错误提示翻倍）。
        res.attached += _attach_pdfs(store, client, already, res, http=http,
                                     email=cfg.email, prefetched=prefetched)

    return res


def _pdf_targets(store: Store, papers: list[Paper]) -> list[Paper]:
    """需要补 PDF 的论文：有开放获取链接、且本地记录里还没有附件。"""
    return [
        p for p in papers
        if p.oa_url and (store.get_zotero_link(p.key) or {}).get("attached_pdf") != 1
    ]


def _fetch_pdf(
    p: Paper,
    http: Any,
    email: str,
    oa_cache: dict[str, dict[str, Any] | None] | None = None,
) -> tuple[str | None, bytes | None, str]:
    """按候选链接逐个尝试下载，返回 (文件名, 内容, 失败原因)。"""
    oa: dict[str, Any] | None = None
    if http is not None and p.doi and email:
        cache = oa_cache if oa_cache is not None else {}
        if p.doi not in cache:
            try:
                from .adapters import UnpaywallClient

                cache[p.doi] = UnpaywallClient(http, email).lookup(p.doi)
            except Exception:  # noqa: BLE001
                cache[p.doi] = None
        oa = cache.get(p.doi)

    last_err = ""
    for url in _pdf_candidates(p, oa):
        try:
            blob = download_file(url, timeout=40)
        except (ZoteroError, OSError, ValueError) as e:
            last_err = str(e)[:130]
            continue
        if not blob[:5].startswith(b"%PDF"):
            last_err = "链接指向的不是 PDF（多半是 landing page）"
            continue
        fname = re.sub(r"[^\w\u4e00-\u9fa5.\-]", "_", (p.title or p.key)[:60]) + ".pdf"
        return fname, blob, ""
    return None, None, last_err or "下载失败"


def prefetch_pdfs(
    store: Store,
    papers: list[Paper],
    http: Any = None,
    email: str = "",
    verbose: bool = True,
) -> dict[str, tuple[str, bytes]]:
    """先把 PDF 下载下来，返回 {paper_key: (文件名, 内容)}。

    为什么必须放在「创建 Zotero 条目」之前：
        条目一旦出现在库里，Zotero-AI-Butler 的「自动扫描新文献」会立刻
        抓住它去精读。如果这时 PDF 还没上传完，Butler 会以
        「PDF 文本提取失败：未找到 PDF 文件路径」失败，并且带
        suppressTaskRetry=true —— 它不会自己重试，这篇论文就永远没有笔记。
        实测这个窗口可以长达 37 秒（一篇 57 MB 的 PDF），必然踩中。

        把耗时的下载挪到建条目之前，窗口就只剩「上传」本身：写本地磁盘，
        几百 MB 也只要一两秒。

    代价是这些 PDF 会同时占用内存。批量导入时建议配合 --top 控制规模。
    """
    out: dict[str, tuple[str, bytes]] = {}
    targets = _pdf_targets(store, papers)
    if not targets:
        return out

    oa_cache: dict[str, dict[str, Any] | None] = {}
    total = len(targets)
    for i, p in enumerate(targets, 1):
        if verbose:
            print(f"  [预下载 {i}/{total}] {(p.title or p.key)[:44]} … ",
                  end="", flush=True)
        fname, blob, err = _fetch_pdf(p, http, email, oa_cache)
        if blob is None:
            if verbose:
                print(f"失败（{err[:46]}）")
            continue
        out[p.key] = (fname, blob)
        if verbose:
            print(f"✓ {len(blob) // 1024} KB")
    return out


def _create_with_fallback(
    client: ZoteroClient,
    item_payload: dict[str, Any],
    paper: Paper,
    collection_key: str,
    status: str | None,
    prefix: str,
    res: ImportResult,
) -> dict[str, Any] | None:
    """创建单个条目；类型不被接受时降级成 journalArticle 重试一次。

    原先是整批创建、再按失败列表回查下标来重试。改成单条创建后，
    「哪个条目失败」这件事天然就是确定的，不必再靠 `payload.index()` 反查
    （那个反查在两条完全相同的 item 上会取错下标）。
    """
    created, failures = client.create_items([item_payload])
    if created:
        return created[0][1]

    msg = failures[0][1] if failures else "未知错误"
    if "preprint" in (item_payload.get("itemType") or ""):
        alt = paper_to_item(paper, collection_key, status, prefix,
                            item_type="journalArticle")
        created2, failures2 = client.create_items([alt])
        if created2:
            return created2[0][1]
        if failures2:
            msg = failures2[0][1]

    res.failed.append((paper.title or "", msg))
    return None


def _upload_orphan_attachment(
    client: ZoteroClient,
    paper: Paper,
    prefetched: dict[str, tuple[str, bytes]],
    res: ImportResult,
    http: Any,
    email: str,
    oa_cache: dict[str, Any],
) -> str | None:
    """把 PDF 传成一个**没有父条目**的独立附件，返回附件 key。

    这是「先上传、后认亲」的第一步，为什么不按直觉来见 import_papers 里的
    长注释。要点：独立附件的 add 事件 Butler 不处理，所以这段中间态是安全的；
    等父条目出现时文件早就躺在磁盘上了。
    """
    got = prefetched.get(paper.key)
    if got is not None:
        fname, content = got
    else:
        # 预下载阶段没拿到（例如那一步失败），这里现取现传
        fname, content, err = _fetch_pdf(paper, http, email, oa_cache)
        if content is None or fname is None:
            res.pdf_problems.append(((paper.title or "")[:56], err))
            return None
    try:
        return client.upload_attachment(None, fname, content, "application/pdf")
    except (ZoteroError, OSError, ValueError) as e:
        res.pdf_problems.append(
            ((paper.title or "")[:56], f"{type(e).__name__}: {str(e)[:110]}")
        )
        return None


def _attach_pdfs(
    store: Store,
    client: ZoteroClient,
    papers: list[Paper],
    res: ImportResult,
    http: Any = None,
    email: str = "",
    verbose: bool = True,
    prefetched: dict[str, tuple[str, bytes]] | None = None,
) -> int:
    """为已导入的、有 OA 链接的论文抓取并上传 PDF。

    只走合法开放获取链接（Unpaywall / PMC / arXiv / bioRxiv 等），
    不绕过任何付费墙。

    单篇失败绝不中断整体；失败原因逐条记进 res.pdf_problems，
    并会打印进度，避免看起来像卡死（实测每篇可能要几十秒）。
    """
    targets = _pdf_targets(store, papers)
    if not targets:
        return 0

    done = prefetched or {}
    oa_cache: dict[str, dict[str, Any] | None] = {}
    total = len(targets)
    count = 0

    for i, p in enumerate(targets, 1):
        link = store.get_zotero_link(p.key)
        if not link:
            continue

        if verbose:
            # 交错模式下每次只传一篇（total == 1），此时不显示 [1/1] 这种
            # 没有信息量的序号
            tag = f"[{i}/{total}] " if total > 1 else ""
            print(f"  {tag}{(p.title or p.key)[:52]} … ", end="", flush=True)

        got = done.get(p.key)
        if got is not None:
            # 走预下载的内容：上传本身很快，条目「有 PDF 但没附件」的窗口最小
            fname, content = got
        else:
            # 没预下载（例如单独跑 attach-pdf 的补附件路径）就现下现传
            fname, content, err = _fetch_pdf(p, http, email, oa_cache)
            if content is None or fname is None:
                if verbose:
                    print("失败")
                res.pdf_problems.append((p.title[:56], err))
                continue

        try:
            client.upload_attachment(link["zotero_key"], fname, content, "application/pdf")
            store.link_zotero(p.key, link["zotero_key"], link.get("collection"),
                              link.get("status"), attached_pdf=1)
            count += 1
            if verbose:
                print(f"✓ {len(content) // 1024} KB")
        except (ZoteroError, OSError, ValueError) as e:
            if verbose:
                print("上传失败")
            # 附件上传失败不该让整个导入失败，但必须记下来
            res.pdf_problems.append((p.title[:56], f"{type(e).__name__}: {str(e)[:110]}"))
            _ = used
            continue
    return count


_ARXIV_VERSION_RE = re.compile(r"v\d+$")


def _pdf_candidates(p: Paper, oa: dict[str, Any] | None = None) -> list[str]:
    """给出候选 PDF 链接，按「最可能真的能下到 PDF」排序。

    实测教训：Europe PMC 给的 oa_url 经常是**出版商落地页**而不是 PDF，
    而且 Springer / Elsevier / Wiley 会 403 掉程序化请求。
    所以顺序很重要：
      1. Unpaywall 的 url_for_pdf —— 专门指向 PDF，质量最高
      2. 已知允许程序化访问的仓库（PMC / arXiv / bioRxiv / medRxiv）
      3. 原始 oa_url 兜底
    """
    urls: list[str] = []

    def add(u: str | None) -> None:
        if u and u not in urls:
            urls.append(u)

    # 1) Unpaywall 的直链（优先）
    if oa:
        best = oa.get("best_oa_location") or {}
        add(best.get("url_for_pdf"))
        for loc in oa.get("oa_locations") or []:
            if loc.get("url_for_pdf"):
                add(loc["url_for_pdf"])
                break

    # 2) PMC 开放全文。
    #    实测：www.ncbi.nlm.nih.gov 在本机网络下经常 ConnectionReset / URLError，
    #    而 pmc.ncbi.nlm.nih.gov 这个新域名更稳；两个都放上做冗余。
    #    注意 2026 年新收录的论文往往还没有 PDF（NCBI 只放了全文页），
    #    这种情况会走到第 5 步兜底并最终报错，属于数据源本身的限制。
    pmcid = p.extra.get("pmcid")
    if pmcid:
        add(f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/")
        add(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/")

    # 3) arXiv：去掉版本号走最新版更稳
    if p.oa_url:
        m = re.match(r"(https?://arxiv\.org/pdf/[^/]+?)v\d+$", p.oa_url)
        if m:
            add(m.group(1))
    if p.arxiv_id:
        add(f"https://arxiv.org/pdf/{p.arxiv_id}")

    # 4) 预印本平台的固定 PDF 路径（由 DOI 推导）
    if p.doi:
        d = p.doi.lower()
        if d.startswith("10.1101/"):
            for host in ("biorxiv", "medrxiv"):
                add(f"https://www.{host}.org/content/{p.doi}v1.full.pdf")
        if d.startswith("10.21203/"):
            add(f"https://www.researchsquare.com/article/{p.doi}.pdf")

    # 5) 原始链接兜底
    add(p.oa_url)
    return urls


# ---------------------------------------------------------------- 阅读状态


def set_status(
    cfg: Config, store: Store, client: ZoteroClient,
    papers: list[Paper], status: str,
) -> tuple[int, list[str]]:
    """给论文对应的 Zotero 条目打上阅读状态标签（互斥，会替换旧的 wf: 状态）。"""
    prefix = (cfg.butler or {}).get("status_prefix", "wf:")
    valid = (cfg.butler or {}).get("statuses") or ["未读", "在读", "已初读", "已精读"]
    if status not in valid:
        raise ValueError(f"未知状态 {status!r}，可用：{', '.join(valid)}")

    links = store.zotero_links([p.key for p in papers])
    ok, problems = 0, []
    for p in papers:
        link = links.get(p.key)
        if not link:
            problems.append(f"{p.title[:50]} —— 还没导入 Zotero")
            continue
        try:
            item = client.item(link["zotero_key"])
            data = item.get("data") or {}
            kept = [
                t for t in (data.get("tags") or [])
                if not str(t.get("tag", "")).startswith(prefix)
            ]
            kept.append({"tag": f"{prefix}{status}"})
            client.update_item(link["zotero_key"], {"tags": kept})
            store.set_zotero_status(link["zotero_key"], status)
            ok += 1
        except ZoteroError as e:
            problems.append(f"{p.title[:50]} —— {str(e).splitlines()[0][:80]}")
    return ok, problems


def sync_links(
    cfg: Config, store: Store, client: ZoteroClient
) -> tuple[int, int, list[str]]:
    """从 Zotero 反向重建本地 zotero_links 表。

    什么时候需要它：
      · 本地关联表被清空或丢失（换机器、误删、早期版本 bug）
      · 论文是先手工加进 Zotero 的
    不修的话 `mark`（改阅读状态）和 M3（回读 Butler 笔记）都会找不到对应条目。

    匹配依据优先用条目 extra 里的 `litwarden-key: <内部键>`（最可靠），
    退化到 DOI 匹配。
    """
    papers = store.get_papers()
    by_key = {p.key: p for p in papers}
    by_doi = {p.doi.lower(): p for p in papers if p.doi}

    # 集合 key -> 名称
    col_names: dict[str, str] = {}
    for c in client.list_collections(limit=1000):
        col_names[c["key"]] = (c.get("data") or {}).get("name", "")

    links = store.zotero_links()
    matched = skipped = 0
    problems: list[str] = []

    for item in client.list_items(limit=5000):
        d = item.get("data") or {}
        if d.get("itemType") in ("attachment", "note"):
            continue

        paper: Paper | None = None
        extra = str(d.get("extra") or "")
        for line in extra.splitlines():
            if line.strip().lower().startswith("litwarden-key:"):
                paper = by_key.get(line.split(":", 1)[1].strip())
                break
        if paper is None and d.get("DOI"):
            paper = by_doi.get(str(d["DOI"]).strip().lower())
        if paper is None:
            skipped += 1
            continue

        if paper.key in links:
            skipped += 1
            continue

        cols = d.get("collections") or []
        col_name = col_names.get(cols[0]) if cols else None
        status = None
        for tg in d.get("tags") or []:
            tag = str(tg.get("tag", ""))
            if tag.startswith(("wf:", "litwarden:")):
                tail = tag.split(":", 1)[1]
                if tail and tail != "OA":
                    status = tail
                    break
        store.link_zotero(paper.key, item["key"], col_name, status)
        matched += 1

    if skipped:
        problems.append(
            f"{skipped} 个 Zotero 条目没有对应的 litwarden 记录"
            "（可能是你手工加进 Zotero 的，或来自已删除的旧检索）"
        )
    return matched, skipped, problems


def audit(cfg: Config, store: Store, client: ZoteroClient) -> dict[str, Any]:
    """批量体检：一次性统计附件与状态的真实情况，不用逐条点开看。

    返回每个已关联论文的：
      · 在 Zotero 里是否真的存在
      · 有没有 PDF 附件（直接查 Zotero 的子条目，不看本地缓存）
      · 本地是否有可用的 OA 链接（决定能不能补抓）
    """
    links = store.zotero_links()
    if not links:
        return {"total": 0, "rows": [], "ok": 0, "missing": 0,
                "no_pdf": 0, "no_pdf_fixable": 0}

    papers = {p.key: p for p in store.get_papers()}
    rows: list[dict[str, Any]] = []
    ok = missing = no_pdf = fixable = 0
    repaired = 0

    for pk, link in links.items():
        p = papers.get(pk)
        row: dict[str, Any] = {
            "paper_key": pk,
            "title": (p.title if p else pk)[:70],
            "zotero_key": link["zotero_key"],
            "status": link.get("status"),
            "exists": True,
            "has_pdf": False,
            "oa_url": (p.oa_url if p else None),
        }
        try:
            kids = client.children(link["zotero_key"])
            row["has_pdf"] = any(
                (k.get("data") or {}).get("itemType") == "attachment" for k in kids
            )
        except ZoteroError:
            row["exists"] = False
            missing += 1
            rows.append(row)
            continue

        # 用实测结果修正本地标记。
        # link_zotero 的 MAX 语义保证「0 不会覆盖 1」（防止导入路径误清标志），
        # 但也意味着附件在 Zotero 侧被删掉后，本地标记无法回退。而 attach-pdf
        # 正是靠这个标记决定跳过与否 —— 不修的话这篇论文永远补不到 PDF，
        # 同时 digest status 还会把它算进「待 Butler 精读」。
        if int(link.get("attached_pdf") or 0) != int(row["has_pdf"]):
            store.set_attached_pdf(pk, row["has_pdf"])
            repaired += 1

        if row["has_pdf"]:
            ok += 1
        else:
            no_pdf += 1
            if row["oa_url"]:
                fixable += 1
        rows.append(row)

    return {
        "total": len(links),
        "rows": rows,
        "ok": ok,
        "missing": missing,
        "no_pdf": no_pdf,
        "no_pdf_fixable": fixable,
        "repaired": repaired,
    }


def attach_pdfs(
    cfg: Config, store: Store, client: ZoteroClient, papers: list[Paper],
    http: Any = None, verbose: bool = True,
) -> tuple[int, list[tuple[str, str]]]:
    """只补 PDF 附件，不新建条目。

    单独拆出来是因为这是最容易失败、也最需要单独重试的一步：
    开放获取链接会失效、出版商会给 landing page 而不是 PDF。
    失败原因会逐条返回，不用自己一篇篇点开排查。
    """
    res = ImportResult()
    n = _attach_pdfs(store, client, papers, res, http=http, email=cfg.email,
                     verbose=verbose)
    return n, res.pdf_problems


def status_overview(cfg: Config, store: Store) -> dict[str, Any]:
    """阅读状态总览（数据来自本地 zotero_links 表，不查 Zotero）。"""
    links = store.zotero_links()
    counts = store.zotero_status_counts()
    prefix = (cfg.butler or {}).get("status_prefix", "wf:")
    ordered = (cfg.butler or {}).get("statuses") or ["未读", "在读", "已初读", "已精读"]
    table = [(s, counts.get(s, 0)) for s in ordered]
    other = {k: v for k, v in counts.items() if k not in ordered and k != "(未标)"}
    return {
        "total": len(links),
        "by_status": table,
        "other": other,
        "unmarked": counts.get("(未标)", 0),
        "prefix": prefix,
    }
