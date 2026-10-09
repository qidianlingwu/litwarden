"""标准库 HTTP 客户端：限速、重试、退避。

不引入 requests / httpx 的理由见 requirements.txt。
"""

from __future__ import annotations

import gzip
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from . import __version__

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

# macOS / Linux 上常见的系统证书库位置。
# macOS 上用 python.org 官方安装包装出来的 Python 默认不带 CA 根证书，
# 会让所有 HTTPS 请求报 CERTIFICATE_VERIFY_FAILED。这里主动兜底。
_CANDIDATE_CA = [
    os.environ.get("SSL_CERT_FILE"),
    "/etc/ssl/cert.pem",                       # macOS 系统钥匙串导出
    "/etc/pki/tls/certs/ca-bundle.crt",        # RHEL / CentOS
    "/etc/ssl/certs/ca-certificates.crt",      # Debian / Ubuntu
    "/usr/local/etc/openssl@3/cert.pem",       # Intel Homebrew
    "/opt/homebrew/etc/openssl@3/cert.pem",    # Apple Silicon Homebrew
]

_ssl_ctx_cache: dict[tuple[bool, str], ssl.SSLContext] = {}
_ssl_source: str = ""


def build_ssl_context(verify: bool = True, max_version: str = "auto") -> ssl.SSLContext:
    """构造 SSL 上下文。

    max_version:
        "auto" / "1.3"  允许 TLS 1.3
        "1.2"           强制最高 TLS 1.2

    为什么要留 TLS 1.2 这个开关：
        实测在本机网络环境下，Python 3.14 + OpenSSL 3.5 走 TLS 1.3 时
        对 NCBI / Crossref / EBI 的成功率只有约 1/3，报
        `SSL: UNEXPECTED_EOF_WHILE_READING`；强制 TLS 1.2 后升到 8/12 以上。
        而 curl 走 TLS 1.3 却基本正常，说明不是纯网络问题，
        而是 Python 侧 TLS 1.3 握手（很可能与新的混合密钥交换有关）被中间设备干扰。
        因此默认策略是：先用 TLS 1.3，失败重试时自动降级到 TLS 1.2。
    """
    global _ssl_source
    key = (verify, max_version)
    cached = _ssl_ctx_cache.get(key)
    if cached is not None:
        return cached

    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _ssl_source = "已关闭校验（不安全，仅用于排查）"
    else:
        ctx = ssl.create_default_context()
        stats = ctx.cert_store_stats()
        if stats.get("x509_ca", 0) > 0:
            _ssl_source = f"Python 默认证书库（{stats['x509_ca']} 个 CA）"
        else:
            loaded = False
            for p in _CANDIDATE_CA:
                if p and os.path.isfile(p):
                    try:
                        ctx.load_verify_locations(cafile=p)
                        _ssl_source = f"系统证书库 {p}"
                        loaded = True
                        break
                    except (OSError, ssl.SSLError):
                        continue
            if not loaded:
                try:  # 可选依赖，装了就用
                    import certifi  # type: ignore

                    ctx.load_verify_locations(cafile=certifi.where())
                    _ssl_source = f"certifi（{certifi.where()}）"
                except Exception:  # noqa: BLE001
                    _ssl_source = "未找到可用证书库，HTTPS 可能失败"

    if max_version == "1.2":
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2

    _ssl_ctx_cache[key] = ctx
    return ctx


def ssl_source() -> str:
    return _ssl_source


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class HttpClient:
    """带每主机限速与指数退避的极简客户端。"""

    def __init__(
        self,
        email: str = "",
        timeout: int = 30,
        interval: float = 0.4,
        retries: int = 4,
        verify_ssl: bool = True,
        tls: str = "auto",
    ) -> None:
        self.email = email
        self.timeout = timeout
        self.interval = interval
        self.retries = retries
        self.verify_ssl = verify_ssl
        self.tls = tls if tls in ("auto", "1.2", "1.3") else "auto"
        self.fallback_count = 0  # 统计有多少次请求是靠降级成功的
        # finder 用 ThreadPoolExecutor 并发跑多个数据源，但它们共用同一个
        # HttpClient 实例，所以下面这些可变状态都必须加锁保护。
        self._lock = threading.Lock()
        self._last_hit: dict[str, float] = {}
        # 每个域名的 TLS 偏好。一旦某域名在 TLS 1.3 上失败过，
        # 后续对它的请求直接走 TLS 1.2，不再浪费一次失败 + 退避等待。
        # 这是性能关键：不加这个，每个请求都要先撞一次 TLS 1.3。
        self._host_tls: dict[str, str] = {}
        self.user_agent = (
            f"litflow/{__version__} (literature workflow; "
            f"mailto:{email or 'unknown'})"
        )

    # -------------------------------------------------- TLS 策略

    def context_for(self, attempt: int, host: str = "") -> ssl.SSLContext:
        """按尝试序号与域名历史选 SSL 上下文。

        tls="auto"（默认）：第 1 次用 TLS 1.3，失败后重试降级到 TLS 1.2；
        并且把降级结果记在 _host_tls 里，该域名之后的请求直接用 TLS 1.2。
        这是针对本机网络环境的实测结论——TLS 1.3 对该网络下的
        NCBI / Crossref / EBI 成功率只有约 1/3，TLS 1.2 则可达 8/12 以上。
        """
        if self.tls == "1.2":
            return build_ssl_context(self.verify_ssl, max_version="1.2")
        if self.tls == "1.3":
            return build_ssl_context(self.verify_ssl, max_version="1.3")
        # auto
        if host:
            with self._lock:
                downgraded = self._host_tls.get(host) == "1.2"
            if downgraded:
                return build_ssl_context(self.verify_ssl, max_version="1.2")
        if attempt > 0:
            return build_ssl_context(self.verify_ssl, max_version="1.2")
        return build_ssl_context(self.verify_ssl, max_version="1.3")

    # -------------------------------------------------- 内部

    def _throttle(self, host: str) -> None:
        """对单个域名限速。

        并发下必须保证「读上次时间 -> 算等待 -> 预留下次时间」是原子的，
        否则多个线程会同时判定「不用等」，限速形同虚设，很容易被对方限流（429）。
        sleep 特意放在锁外——持锁睡眠会把并发检索退化成串行。
        """
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last_hit.get(host)
            wait = 0.0 if last is None else max(0.0, self.interval - (now - last))
            # 预留：把下次可发请求的时间点先占住，后到的线程据此排队
            self._last_hit[host] = now + wait
        if wait > 0:
            time.sleep(wait)

    def _mark(self, host: str) -> None:
        with self._lock:
            # 取较大值，避免把其他线程已经预留的未来时间点回退掉
            self._last_hit[host] = max(self._last_hit.get(host, 0.0), time.monotonic())

    def _request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(clean, doseq=True)}"

        host = urllib.parse.urlparse(url).netloc
        hdrs = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip",
            "Accept": "*/*",
        }
        if headers:
            hdrs.update(headers)

        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(host)
            req = urllib.request.Request(url, headers=hdrs)
            try:
                with urllib.request.urlopen(
                    req, timeout=self.timeout, context=self.context_for(attempt, host)
                ) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    self._mark(host)
                    if self.tls == "auto" and attempt > 0:
                        with self._lock:
                            self.fallback_count += 1
                            # 记住这个域名在 TLS 1.3 上不通，后续请求直接走 1.2，
                            # 省掉「先撞一次失败 + 退避等待」的开销
                            self._host_tls[host] = "1.2"
                    return raw
            except urllib.error.HTTPError as e:
                self._mark(host)
                status = e.code
                if status not in RETRY_STATUS or attempt == self.retries:
                    detail = ""
                    try:
                        detail = e.read()[:300].decode("utf-8", "replace")
                    except Exception:  # noqa: BLE001
                        pass
                    raise HttpError(
                        f"HTTP {status} from {url}\n{detail}", status=status, url=url
                    ) from None
                wait = self._backoff(attempt, e.headers.get("Retry-After"))
                time.sleep(wait)
                last_err = e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                # 注意：ssl.SSLError 是 OSError 的子类，会走到这里。
                # 下一轮 attempt 会自动切到 TLS 1.2 上下文，这是刻意的降级重试。
                self._mark(host)
                if attempt == self.retries:
                    hint = ""
                    if self.tls == "auto" and "SSL" in str(e):
                        hint = (
                            "\n（TLS 1.3 与 TLS 1.2 都已重试过仍失败。"
                            "若你有代理或 VPN，可尝试切换节点；"
                            "也可在 config.toml 设 tls = \"1.2\" 固定版本。）"
                        )
                    raise HttpError(f"网络错误：{e}（{url}）{hint}", url=url) from None
                time.sleep(self._backoff(attempt, None))
                last_err = e

        raise HttpError(f"重试耗尽：{last_err}（{url}）", url=url)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(60.0, max(1.0, float(retry_after)))
            except ValueError:
                pass
        return min(30.0, (2**attempt) + random.uniform(0, 0.7))

    # -------------------------------------------------- 对外

    def get_text(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> str:
        return self._request(url, params, headers).decode("utf-8", "replace")

    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> Any:
        text = self.get_text(url, params, headers)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise HttpError(f"返回内容不是合法 JSON（{url}）：{text[:200]}") from e

    def get_xml(self, url: str, params: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None) -> ET.Element:
        text = self.get_text(url, params, headers)
        try:
            return ET.fromstring(text)
        except ET.ParseError as e:
            raise HttpError(f"返回内容不是合法 XML（{url}）：{text[:200]}") from e

    def post_json(self, url: str, payload: dict[str, Any] | None = None,
                  headers: dict[str, str] | None = None) -> Any:
        """POST JSON。用于 Zotero 本地 API 授权与写入。"""
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        body = json.dumps(payload or {}).encode()
        host = urllib.parse.urlparse(url).netloc

        self._throttle(host)
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self.context_for(0)
            ) as resp:
                self._mark(host)
                raw = resp.read()
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            self._mark(host)
            detail = ""
            try:
                detail = e.read()[:300].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise HttpError(f"HTTP {e.code} from {url}\n{detail}", status=e.code, url=url) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._mark(host)
            raise HttpError(f"网络错误：{e}（{url}）", url=url) from None
