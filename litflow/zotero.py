"""Zotero 本地 API 客户端。

Zotero 桌面端在 `localhost:23119/api/` 暴露了 Web API v3 的本地实现：
  · 读请求免认证，但必须带 `Zotero-API-Version` 头
  · 写请求（Zotero 10+）需要本地 API key，且必须带 `Zotero-Server-ID` 头
  · 本地 key 通过 `POST /api/local/authorize` 申请，Zotero 会弹窗，
    用户选 "Always Allow" 可拿永久 key

本模块自带 urllib 实现（不复用 http.HttpClient），因为本地 API 需要
PUT/DELETE、自定义头、以及不带 TLS 的明文 HTTP，需求差异较大。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_VERSION = "3"

# 申请写授权时需要用户在 Zotero 弹窗里点按钮，超时要给足
AUTH_TIMEOUT = 180


class ZoteroError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class ImportResult:
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (paper_key, 原因)
    failed: list[tuple[str, str]] = field(default_factory=list)
    attached: int = 0
    # PDF 抓取失败明细。不能静默吞掉——否则用户看到「附带 PDF 0 个」
    # 完全不知道是链接失效、网络问题还是代码 bug。
    pdf_problems: list[tuple[str, str]] = field(default_factory=list)
    collection_key: str | None = None

    @property
    def summary(self) -> str:
        return (
            f"新建 {len(self.created)} 条，跳过 {len(self.skipped)} 条，"
            f"失败 {len(self.failed)} 条，附带 PDF {self.attached} 个"
        )


class ZoteroClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:23119",
        user_id: str = "0",
        api_key: str = "",
        timeout: int = 30,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.user_id = str(user_id or "0")
        self.api_key = api_key
        self.timeout = timeout
        self._server_id: str | None = None
        self._version = 0
        self._local_key: str | None = api_key or None
        self._authorized_denied = False
        # 上一次授权拿到的是不是永久 key（弹窗点了 Always Allow）。
        # None = 还没授权过。
        self.auth_permanent: bool | None = None
        # 拿到新 key 时的回调，由调用方决定存哪（litflow 存在本地 SQLite 的 kv_cache 里，
        # 不改用户的 config.toml）
        self.key_sink: Any = None

    @property
    def local_key(self) -> str | None:
        return self._local_key

    # -------------------------------------------------- 基础请求

    @property
    def prefix(self) -> str:
        return f"/api/users/{self.user_id}"

    def _headers(self, write: bool = False) -> dict[str, str]:
        h = {
            "Zotero-API-Version": API_VERSION,
            "User-Agent": "litflow/0.1 (literature workflow)",
            "Accept": "application/json",
        }
        if write:
            if self._local_key:
                h["Zotero-API-Key"] = self._local_key
            if self._server_id:
                h["Zotero-Server-ID"] = self._server_id
            # 防重复提交令牌（Zotero 要求写 POST 时带上）
            h["Zotero-Write-Token"] = secrets.token_hex(16)
        return h

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        write: bool = False,
        allow_reauth: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        url = f"{self.base}{path}"
        if params:
            # params 允许是 dict，也允许是 [(k, v), ...]。
            # 后者是给 DELETE /items?itemKey=a&itemKey=b 这种**重复键**用的，
            # 用 dict 表达不了。早先这里直接 params.items()，导致 delete_items
            # 一调用就 AttributeError——cleanup-empty 那条命令因此一直是坏的。
            if isinstance(params, dict):
                params = {k: v for k, v in params.items() if v not in (None, "")}
            if params:
                url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

        headers = self._headers(write)
        if extra_headers:
            headers.update(extra_headers)
        data = None
        if raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = content_type

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._capture(resp.headers)
                payload = resp.read()
                parsed = self._parse(payload, resp.headers)
                return resp.status, parsed, dict(resp.headers)
        except urllib.error.HTTPError as e:
            self._capture(e.headers)
            detail = ""
            try:
                detail = e.read()[:400].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass

            # 写请求被拒（key 失效/未授权）→ 重新申请一次再重试
            if write and e.code == 401 and allow_reauth and not self._authorized_denied:
                self._local_key = None
                self.authorize()
                return self._request(
                    method, path, params, body, raw_body, content_type, write,
                    allow_reauth=False, extra_headers=extra_headers,
                )
            raise ZoteroError(self._explain(e.code, detail), status=e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ZoteroError(
                f"连不上 Zotero 本地 API（{self.base}）。\n"
                f"  原因：{e}\n"
                "  按顺序检查：\n"
                "    1. Zotero 是否正在运行（进程在 ≠ 本地 API 已就绪，\n"
                "       刚重启后需要几秒到几十秒才开始监听）\n"
                "    2. Zotero 窗口是否有弹窗挡着（同步冲突、版本提示等都会阻塞启动）\n"
                "    3. 设置 → Advanced 是否仍勾选\n"
                "       「Allow other applications on this computer to communicate with Zotero」\n"
                "    4. 尝试完全退出 Zotero（Cmd+Q）后重新打开\n"
                "  确认可用：litflow zotero check"
            ) from None

    @staticmethod
    def _parse(payload: bytes, headers: Any) -> Any:
        if not payload.strip():
            return None
        ct = (headers.get("Content-Type") or "").lower()
        if "json" in ct or payload[:1] in (b"[", b"{"):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload.decode("utf-8", "replace")
        return payload.decode("utf-8", "replace")

    def _capture(self, headers: Any) -> None:
        sid = headers.get("Zotero-Server-ID")
        if sid:
            self._server_id = sid
        ver = headers.get("Last-Modified-Version")
        if ver and ver.isdigit():
            self._version = int(ver)

    @staticmethod
    def _explain(status: int, detail: str) -> str:
        hints = {
            403: "本地 API 未开启。请到 Zotero → 设置 → Advanced → 勾选\n"
                 "     「Allow other applications on this computer to communicate with Zotero」",
            401: "写权限未授权或 key 已失效。删除 config.toml 里 [zotero] api_key 后重试，"
                 "会重新弹窗申请。",
            412: "Zotero-Server-ID 不匹配（本地库可能被替换过）",
            428: "写请求缺少 Zotero-Server-ID 头",
        }
        msg = f"Zotero 本地 API 返回 HTTP {status}"
        if status in hints:
            msg += f"\n  {hints[status]}"
        if detail:
            msg += f"\n  原始信息：{detail[:300]}"
        return msg

    def ping(self) -> dict[str, Any]:
        """探测本地 API 是否可用。"""
        _, _, headers = self._request("GET", "/api/", {"format": "json"})
        return {
            "ok": True,
            "zotero_version": headers.get("X-Zotero-Version"),
            "api_version": headers.get("Zotero-API-Version"),
            "schema_version": headers.get("Zotero-Schema-Version"),
            "server_id": self._server_id,
        }

    # -------------------------------------------------- 写授权

    def authorize(self, app_name: str = "litflow 文献工作流") -> str:
        """申请本地写授权。Zotero 会弹窗，选 Always Allow 可得永久 key。"""
        if not self._server_id:
            self.ping()
        if not self._server_id:
            raise ZoteroError("拿不到 Zotero-Server-ID，无法申请写授权。")

        if self._authorized_denied:
            raise ZoteroError("你上次拒绝了授权。请在 Zotero 里点 Always Allow 后重试。")

        url = f"{self.base}/api/local/authorize"
        headers = {
            "Content-Type": "application/json",
            "Zotero-API-Version": API_VERSION,
            "Zotero-Server-ID": self._server_id,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps({"appName": app_name}).encode(),
            headers=headers,
            method="POST",
        )
        # 这个请求会阻塞到用户在 Zotero 弹窗里点按钮为止，
        # 所以超时必须远大于普通 API 调用——30 秒根本不够人反应。
        #
        # 另外 Zotero 对「会弹窗的请求」有节流：每分钟最多 5 次，超出返回 429。
        # 如果用户点的是 Allow（一次性 key）而不是 Always Allow，
        # 每次写入都要重新弹窗，很容易触发这个限制。
        # 所以这里对 429 做等待重试，而不是直接失败。
        data: Any = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=AUTH_TIMEOUT) as resp:
                    data = json.loads(resp.read())
                break
            except TimeoutError:
                raise ZoteroError(
                    f"等待授权超时（{AUTH_TIMEOUT} 秒）。\n"
                    "  请切换到 Zotero 窗口，应该有一个授权弹窗，点「Always Allow」。\n"
                    "  然后重新执行刚才的命令。\n"
                    "  如果没看到弹窗，检查 Zotero → 设置 → Advanced 是否勾选了\n"
                    "  「Allow other applications on this computer to communicate with Zotero」。"
                ) from None
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read()[:200].decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
                if e.code == 403:
                    self._authorized_denied = True
                    raise ZoteroError(
                        "你在弹窗里拒绝了授权。请重试并在 Zotero 弹窗中选「Always Allow」。"
                    ) from None
                if e.code == 429:
                    if attempt == 2:
                        raise ZoteroError(
                            "Zotero 授权弹窗被限流（每分钟最多 5 次），重试仍失败。\n"
                            "  原因通常是上次弹窗点的是「Allow」（一次性授权）\n"
                            "  而不是「Always Allow」（永久授权）。\n"
                            "  请等 1 分钟后重试，并在弹窗里务必点「Always Allow」。"
                        ) from None
                    wait = 65
                    retry_after = e.headers.get("Retry-After")
                    if retry_after and str(retry_after).isdigit():
                        wait = max(5, int(retry_after))
                    print(f"  Zotero 授权弹窗被限流，等待 {wait} 秒后重试……")
                    time.sleep(wait)
                    continue
                raise ZoteroError(f"申请写授权失败 HTTP {e.code}：{body}") from None

        key = (data or {}).get("key")
        if not key:
            raise ZoteroError(f"申请写授权失败，返回内容异常：{data}")

        self._local_key = key
        self.auth_permanent = bool((data or {}).get("remember"))
        if not self.auth_permanent:
            print(
                "  ⚠️  Zotero 返回的是「一次性」授权（弹窗里点的是 Allow）。\n"
                "      这意味着每次写入都会重新弹窗，很容易撞上每分钟 5 次的限流。\n"
                "      建议：删掉 data/ 下的授权缓存后重试，并在弹窗里点「Always Allow」。"
            )
        if callable(self.key_sink):
            try:
                # 把「是否永久」一并交出去：一次性 key 首次写入后就失效，
                # 缓存它反而会让下次写入先撞一次 401 再弹窗。
                self.key_sink(key, self.auth_permanent)
            except Exception:  # noqa: BLE001 - 存 key 失败不该中断主流程
                pass
        return key

    def ensure_write(self) -> None:
        """确保可以发起写请求。

        两个前提缺一不可，早先只检查了第一个，导致有缓存 key 时
        写请求因缺少 Zotero-Server-ID 头而 428：
          · Zotero-Server-ID —— 每次写请求都要带，由任意一次读请求的响应头得到
          · 本地 API key
        """
        if not self._server_id:
            try:
                self.ping()
            except ZoteroError:
                pass
        if not self._local_key:
            self.authorize()

    # -------------------------------------------------- 集合

    def list_collections(self, limit: int = 100) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            data = self._request(
                "GET", f"{self.prefix}/collections",
                {"limit": min(limit, 100), "start": start, "format": "json"},
            )[1]
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < min(limit, 100):
                break
            start += len(data)
        return out

    def find_collection(self, name: str) -> dict[str, Any] | None:
        target = name.strip().lower()
        for c in self.list_collections(limit=1000):
            if (c.get("data") or {}).get("name", "").strip().lower() == target:
                return c
        return None

    def create_collection(self, name: str, parent: str | None = None) -> str:
        self.ensure_write()
        payload = [{"name": name}]
        if parent:
            payload[0]["parentCollection"] = parent
        _, data, _ = self._request(
            "POST", f"{self.prefix}/collections", body=payload, write=True
        )
        ok = (data or {}).get("successful") or {}
        if not ok:
            raise ZoteroError(f"创建集合失败：{data}")
        return next(iter(ok.values()))["key"]

    def ensure_collection(self, name: str) -> str:
        got = self.find_collection(name)
        if got:
            return got["key"]
        return self.create_collection(name)

    # -------------------------------------------------- 条目

    def list_items(self, limit: int = 1000, top_only: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        page = min(limit, 100)
        while len(out) < limit:
            params: dict[str, Any] = {"limit": page, "start": start, "format": "json"}
            if top_only:
                params["itemType"] = "-attachment || note"
            data = self._request("GET", f"{self.prefix}/items", params)[1]
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < page:
                break
            start += len(data)
        return out

    def item(self, key: str) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/items/{key}", {"format": "json"})[1]

    def children(self, key: str) -> list[dict[str, Any]]:
        data = self._request(
            "GET", f"{self.prefix}/items/{key}/children", {"format": "json"}
        )[1]
        return data if isinstance(data, list) else []

    def create_items(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[dict[str, Any], str]]]:
        """批量创建条目。

        返回：
          created  = [(在 items 中的下标, 条目对象), ...]
          failures = [(原始 item 字典, 错误信息), ...]

        **为什么返回下标而不是只返回条目对象**：
        Zotero 创建接口在 successful 里只给 key 与 version，通常**不含完整 data**
        （没有 extra / DOI / title）。早先靠返回的 data 去反查论文就全部匹配失败，
        导致 zotero_links 表一条都没写。而 successful 的键就是请求数组的下标，
        用它可以直接对应回 items，比事后猜可靠得多。

        失败时把原始 item 一并返回，方便调用方改写后重试
        （例如 preprint 类型不被接受时降级成 journalArticle）。

        Zotero 要求单次 POST 不超过 50 条。
        """
        self.ensure_write()
        created: list[tuple[int, dict[str, Any]]] = []
        failures: list[tuple[dict[str, Any], str]] = []
        for i in range(0, len(items), 50):
            chunk = items[i : i + 50]
            _, data, _ = self._request(
                "POST", f"{self.prefix}/items", body=chunk, write=True
            )
            for idx, item in (data or {}).get("successful", {}).items():
                try:
                    gi = i + int(idx)
                except (ValueError, TypeError):
                    continue
                created.append((gi, item))
            for idx, err in (data or {}).get("failed", {}).items():
                try:
                    src = chunk[int(idx)]
                except (ValueError, IndexError, TypeError):
                    src = {}
                failures.append((src, err.get("message", str(err))))
        return created, failures

    def collection_items(self, collection_key: str) -> list[dict[str, Any]]:
        data = self._request(
            "GET", f"{self.prefix}/collections/{collection_key}/items",
            {"format": "json", "limit": 100},
        )[1]
        return data if isinstance(data, list) else []

    def update_item(self, key: str, patch: dict[str, Any]) -> dict[str, Any]:
        """局部更新条目（PATCH）。

        先取最新 version 作为前置条件，否则并发场景下会 412 Precondition Failed。
        """
        self.ensure_write()
        cur = self.item(key)
        version = cur.get("version") or (cur.get("data") or {}).get("version")
        extra = {"If-Unmodified-Since-Version": str(version)} if version else {}
        _, data, _ = self._request(
            "PATCH", f"{self.prefix}/items/{key}", body=patch, write=True,
            extra_headers=extra,
        )
        return data or {}

    def list_tags(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"{self.prefix}/tags", {"format": "json", "limit": 100})[1]
        return data if isinstance(data, list) else []

    # -------------------------------------------------- 附件

    def upload_attachment(
        self,
        parent_key: str | None,
        filename: str,
        content: bytes,
        mime: str | None = None,
    ) -> str:
        """上传 stored-file 附件。

        parent_key 为 None 时创建一个**独立附件**（没有父条目），
        之后可以再用 set_parent 挂到父条目下。这样做是为了绕开
        Zotero-AI-Butler 的一个判定缺陷，详见 set_parent 的说明。

        严格按 Zotero 本地 API 文档的三步流程（Zotero 10+）：
          1) POST /items 创建 attachment 条目（只有元数据，没有内容）→ itemKey
          2) POST /items/{itemKey}/file
             带 md5 / filename / filesize / mtime 头与 If-None-Match: *
             → 返回 {"url": ".../api/local/uploads/<uploadKey>"}
             若该 md5 已存在则返回 {"exists": 1}，无需再传
          3) POST <url> 上传文件本体 → 201
          4) POST /items/{itemKey}/file?upload=<uploadKey> → 204 确认注册

        关键点：上传地址必须由第 2 步从服务端取，不能自己拼
        （自己拼 /api/local/uploads/<itemKey> 会 404）。

        限制（文档明确）：只接受 stored-file（imported_file / imported_url）；
        单文件 < 4 GB；不支持二进制 diff，必须整文件上传。
        """
        self.ensure_write()
        mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        md5 = hashlib.md5(content).hexdigest()
        # 注意：mtime 必须是毫秒，不是秒
        mtime_ms = int(time.time() * 1000)
        filesize = len(content)

        # ---- 1) 创建附件条目（只写元数据，不含文件内容）
        item: dict[str, Any] = {
            "itemType": "attachment",
            "linkMode": "imported_file",
            "title": filename,
            "filename": filename,
            "contentType": mime,
        }
        if parent_key:
            item["parentItem"] = parent_key
        _, data, _ = self._request(
            "POST", f"{self.prefix}/items", body=[item], write=True
        )
        ok = (data or {}).get("successful") or {}
        if not ok:
            raise ZoteroError(f"创建附件条目失败：{data}")
        att_key = next(iter(ok.values()))["key"]

        file_path = f"{self.prefix}/items/{att_key}/file"

        # ---- 2) 申请上传授权
        # 官方文档明确：md5 / filename / filesize / mtime 全部放在
        # application/x-www-form-urlencoded 的请求体里，不是请求头。
        # 请求头只承担 Content-Type 与条件请求头 If-None-Match。
        form = urllib.parse.urlencode(
            {
                "md5": md5,
                "filename": filename,
                "filesize": filesize,
                "mtime": mtime_ms,
            }
        ).encode()
        _, auth, _ = self._request(
            "POST", file_path,
            raw_body=form,
            content_type="application/x-www-form-urlencoded",
            write=True,
            extra_headers={"If-None-Match": "*"},
        )

        if isinstance(auth, dict) and auth.get("exists"):
            # 文件已在服务器上并已关联到该条目，无需再传
            return att_key

        if not isinstance(auth, dict) or not auth.get("url"):
            raise ZoteroError(f"Zotero 未返回上传地址：{auth}")

        upload_url = auth["url"]
        upload_key = auth.get("uploadKey") or str(upload_url).rstrip("/").rsplit("/", 1)[-1]
        # prefix / suffix 是 multipart/form-data 的边界串，文件必须包在中间
        prefix = auth.get("prefix") or ""
        suffix = auth.get("suffix") or ""
        file_ct = auth.get("contentType") or mime

        # ---- 3) 上传文件本体
        body = prefix.encode() + content + suffix.encode() if (prefix or suffix) else content
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={
                "Content-Type": file_ct,
                "Content-Length": str(len(body)),
                "Zotero-API-Version": API_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 180)) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read()[:200].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise ZoteroError(f"上传附件内容失败 HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ZoteroError(f"上传附件内容失败：{e}") from None

        # ---- 4) 注册上传，之后条目才会回填 md5 / mtime / filesize
        reg = urllib.parse.urlencode({"upload": upload_key}).encode()
        self._request(
            "POST", file_path,
            raw_body=reg,
            content_type="application/x-www-form-urlencoded",
            write=True,
            extra_headers={"If-None-Match": "*"},
        )
        return att_key

    def set_parent(self, key: str, parent_key: str) -> None:
        """把已有条目挂到父条目下。

        用途：先上传、后认亲。Zotero-AI-Butler 在父条目入库后等 2 秒就
        判定「附件是否可分析」，而它的判定只看附件条目在不在、**不查磁盘上
        是否真有文件**；等真正提取时才查文件路径，于是「条目在、文件还没到」
        的窗口必然失败，且失败带 suppressTaskRetry——永不重试。

        实测：文件比判定晚 230 毫秒落盘，就这样也判定为可用。

        所以改成先把附件建成**独立条目**并把文件传完（独立附件的 add 事件
        Butler 不处理），再创建父条目，最后调本方法把附件挂上去。这样父条目
        的 2 秒判定到来时，文件早已在磁盘上。
        """
        # 复用 update_item：它已经处理好「先取最新 version 再 PATCH」的
        # 前置条件，自己拼 PATCH 会踩 412
        self.update_item(key, {"parentItem": parent_key})

    def empty_attachments(self, parent_keys: list[str] | None = None) -> list[dict[str, Any]]:
        """找出「空壳附件」：linkMode=imported_file 但没有 md5 的附件条目。

        这类条目是上传中途失败留下的残骸（比如只跑到第 1 步）。
        正常完成注册的 imported_file 附件一定有 md5。
        """
        out: list[dict[str, Any]] = []
        for item in self.list_items(limit=3000, top_only=False):
            d = item.get("data") or {}
            if d.get("itemType") != "attachment":
                continue
            if d.get("linkMode") != "imported_file":
                continue
            if d.get("md5"):
                continue
            pid = d.get("parentItem")
            if parent_keys and pid not in parent_keys:
                continue
            out.append(item)
        return out

    def _library_version(self) -> int:
        """取当前库版本，供多对象写操作做前置条件。

        Zotero 只在**响应头** `Last-Modified-Version` 里给版本号，而
        ensure_write 走的授权流程不一定经过带该头的读请求（实测走完后
        _version 仍是 0）。所以这里兜一次轻量读，避免写请求拿 0 去撞 428。
        """
        if not self._version:
            self._request("GET", f"{self.prefix}/items", params={"limit": 1})
        return self._version

    def _count_existing(self, keys: list[str]) -> int:
        """数这批 key 里还有几个真的在库里。用来核对删除结果。"""
        if not keys:
            return 0
        _, data, _ = self._request(
            "GET", f"{self.prefix}/items",
            params={"itemKey": ",".join(keys), "limit": 100},
        )
        return len(data) if isinstance(data, list) else 0

    def delete_items(self, keys: list[str]) -> int:
        """批量删除条目。用于清理上传失败留下的空壳附件。

        三个坑全是实测踩出来的，而且都能**静默出错**：

          1. `itemKey` 必须是**逗号分隔的单个参数**（`?itemKey=a,b,c`）。
             写成重复参数（`?itemKey=a&itemKey=b`）服务端不报错、返回 204，
             但**只删掉第一个** —— 实测 28 个 key 只删掉 1 个。
          2. 必须带 `If-Unmodified-Since-Version`，否则直接 428。
             删除属于多对象写操作，Zotero 要求用库版本做前置条件。
          3. 本地 API 的 DELETE 返回 **204 且没有响应体**，拿不到逐条结果。
             所以不能按请求数记账（那样会把「漏删 27 条」报成「成功 28 条」），
             只能删完再读回来核对。

        早先这三点全缺，`zotero cleanup-empty` 从来没有真正跑对过。
        """
        if not keys:
            return 0
        self.ensure_write()
        deleted = 0
        for i in range(0, len(keys), 50):
            chunk = keys[i : i + 50]
            # 每批都重新取版本：上一批删除后库版本会变，用旧值会 412
            ver = self._library_version()
            extra = {"If-Unmodified-Since-Version": str(ver)} if ver else {}
            self._request(
                "DELETE", f"{self.prefix}/items",
                params=[("itemKey", ",".join(chunk))],
                write=True, extra_headers=extra,
            )
            deleted += len(chunk) - self._count_existing(chunk)
        return deleted


def download_file(
    url: str,
    timeout: int = 60,
    max_bytes: int = 80 * 1024 * 1024,
    verify_ssl: bool = True,
) -> bytes:
    """下载 PDF。仅用于开放获取（OA）链接，不绕过任何访问控制。

    必须复用 http.build_ssl_context 的证书兜底与 TLS 降级策略：
    macOS 上 python.org 版 Python 默认没有 CA 根证书，
    裸 urlopen 会直接报 CERTIFICATE_VERIFY_FAILED。
    """
    from .http import build_ssl_context

    # User-Agent 说明：
    #   很多出版商会直接 403 掉不带浏览器特征的请求（实测 Springer / Elsevier / Wiley
    #   对 "litflow/0.1" 这类 UA 一律 403）。这里保留浏览器标识，同时如实标注工具身份
    #   与联系方式——这是学术抓取工具常见的礼貌做法。
    #   注意：只用于抓取合法开放获取副本，不会用于绕过付费墙。
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
        "litflow/0.1 (literature workflow; OA-only)"
    )

    last_err: Exception | None = None
    # 每个 URL 最多试两轮：先 TLS 1.3，失败降级 TLS 1.2
    for attempt in range(2):
        max_version = ("1.3", "1.2")[attempt]
        ctx = build_ssl_context(verify_ssl, max_version=max_version)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ua,
                "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                chunks: list[bytes] = []
                total = 0
                while True:
                    b = resp.read(65536)
                    if not b:
                        break
                    total += len(b)
                    if total > max_bytes:
                        raise ZoteroError(f"文件超过 {max_bytes // 1024 // 1024} MB，已放弃")
                    chunks.append(b)
                return b"".join(chunks)
        except ZoteroError:
            raise
        except urllib.error.HTTPError as e:
            last_err = e
            # 4xx 是明确的拒绝，换 TLS 版本没用，直接放弃
            if 400 <= e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e

    raise ZoteroError(_download_reason(last_err))


def _download_reason(err: Exception | None) -> str:
    """把底层异常翻译成能指导下一步动作的中文说明。"""
    if isinstance(err, urllib.error.HTTPError):
        code = err.code
        if code == 403:
            return "下载被拒绝（403）：该出版商不允许程序化下载，需到官网手动取"
        if code == 404:
            return "链接已失效（404）：开放获取链接过期或被移除"
        if code == 429:
            return "请求过于频繁（429）：稍后重试"
        return f"下载失败：HTTP {code}"
    s = str(err or "")
    if "timed out" in s or "timeout" in s.lower():
        return "下载超时：服务器响应过慢"
    if "Connection reset" in s:
        return "连接被重置：网络中断或对端拒绝"
    return f"下载失败：{s[:110]}"
