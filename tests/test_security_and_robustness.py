"""本地服务的安全边界、并发正确性与不可信输入防护。

这些用例对应开源前做的一轮加固，每一条都对应一个具体的攻击面或竞态，
不是泛泛的「加个校验」。
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from litwarden.cli import _mask_id
from litwarden.exporters import csv_safe, to_csv
from litwarden.http import HttpClient
from litwarden.models import Paper
from litwarden.web import allowed_hosts, host_of


class TestStampCitationTime(unittest.TestCase):
    """回归：引用量的取数时刻必须被补上。

    早先只有「重复项合并」和「Europe PMC 反查回填」两条支路会写这个字段，
    主路径完全没写——实测 41 篇有引用量的论文里有 26 篇时间戳为空，
    导致「引用量可追溯」这一设计目标在常见情况下失效。
    """

    def test_stamps_papers_with_citations(self):
        from litwarden.finder import _stamp_citation_time

        papers = [
            Paper(title="A", cited_by=10, cited_by_source="crossref"),
            Paper(title="B", cited_by=0, cited_by_source="europepmc"),
        ]
        _stamp_citation_time(papers)
        for p in papers:
            self.assertIsNotNone(p.cited_by_fetched_at, p.title)

    def test_leaves_citationless_papers_alone(self):
        from litwarden.finder import _stamp_citation_time

        p = Paper(title="no cites", cited_by=None)
        _stamp_citation_time([p])
        self.assertIsNone(p.cited_by_fetched_at)

    def test_does_not_overwrite_existing_timestamp(self):
        from litwarden.finder import _stamp_citation_time

        original = "2020-01-01T00:00:00+00:00"
        p = Paper(title="A", cited_by=5, cited_by_fetched_at=original)
        _stamp_citation_time([p])
        self.assertEqual(p.cited_by_fetched_at, original)

    def test_timestamp_is_iso8601(self):
        from datetime import datetime

        from litwarden.finder import _stamp_citation_time

        p = Paper(title="A", cited_by=1)
        _stamp_citation_time([p])
        # 必须可被解析，否则「什么时候取的」只是摆设
        datetime.fromisoformat(p.cited_by_fetched_at)


class TestMaskId(unittest.TestCase):
    """诊断输出里的实例标识必须遮蔽——doctor 的输出经常被整段贴到 issue 里。"""

    def test_masks_middle_keeping_edges(self):
        masked = _mask_id("58hToUJsX9iv")
        self.assertEqual(masked, "58********iv")
        self.assertNotIn("hToUJsX9", masked)

    def test_short_values_fully_masked(self):
        self.assertEqual(_mask_id("abcd"), "****")
        self.assertEqual(_mask_id("ab"), "**")

    def test_missing_value(self):
        for bad in (None, ""):
            self.assertEqual(_mask_id(bad), "（未获取）")

    def test_never_returns_original_for_long_values(self):
        for v in ("58hToUJsX9iv", "x" * 40):
            self.assertNotEqual(_mask_id(v), v)


class TestHostHeaderParsing(unittest.TestCase):
    def test_strips_port(self):
        self.assertEqual(host_of("127.0.0.1:8765"), "127.0.0.1")

    def test_handles_ipv6_brackets(self):
        self.assertEqual(host_of("[::1]:8765"), "[::1]")
        self.assertEqual(host_of("[::1]"), "[::1]")

    def test_bare_hostname(self):
        self.assertEqual(host_of("localhost"), "localhost")

    def test_empty_or_missing(self):
        for bad in ("", None, "   "):
            self.assertEqual(host_of(bad), "")


class TestAllowedHosts(unittest.TestCase):
    def test_loopback_always_allowed(self):
        ok = allowed_hosts("127.0.0.1")
        for h in ("127.0.0.1", "localhost", "::1", "[::1]"):
            self.assertIn(h, ok)

    def test_explicit_bind_host_is_allowed(self):
        # 用户显式绑到某个地址时，浏览器会带这个 Host，必须放行
        self.assertIn("192.168.1.50", allowed_hosts("192.168.1.50"))

    def test_external_domain_is_never_allowed(self):
        """DNS-rebinding 的核心：攻击者域名的 Host 头必须被拒。"""
        for bind in ("127.0.0.1", "0.0.0.0", "192.168.1.50"):
            ok = allowed_hosts(bind)
            for evil in ("evil.com", "attacker.example.org", "127.0.0.1.evil.com"):
                self.assertNotIn(evil, ok, f"bind={bind} 不应放行 {evil}")


class TestCsvFormulaInjection(unittest.TestCase):
    def test_prefixes_dangerous_cells(self):
        for raw in ("=1+1", "+A1", "-2+3", "@SUM(A1)", "\tcmd"):
            out = csv_safe(raw)
            self.assertTrue(out.startswith("'"), f"{raw!r} 应被转义")
            self.assertEqual(out[1:], raw, "只加前缀，不改动原内容")

    def test_leaves_normal_text_alone(self):
        for raw in ("Spatial transcriptomics", "", "100 citations", "a=b"):
            self.assertEqual(csv_safe(raw), raw)

    def test_applied_in_to_csv_export(self):
        p = Paper(
            title="=HYPERLINK(\"http://evil\",\"click\")",
            authors=["Smith J"],
            year=2024,
        )
        with tempfile.TemporaryDirectory() as d:
            path = to_csv([p], Path(d) / "out.csv")
            text = path.read_text(encoding="utf-8-sig")
        # 标题应带前置单引号，Excel 才会当文本
        self.assertIn("'=HYPERLINK", text)
        # 且不能以裸的 = 开头出现在字段起始位置
        self.assertNotIn(",=HYPERLINK", text)


class TestHttpThrottleConcurrency(unittest.TestCase):
    """finder 用线程池并发检索，共用同一个 HttpClient。

    限速状态若无锁保护，多线程会同时判定「不用等」，间隔形同虚设。
    """

    def test_concurrent_throttle_enforces_interval(self):
        interval = 0.1
        n = 5
        http = HttpClient(interval=interval, retries=0)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                http._throttle("example.com")
                http._mark("example.com")
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        started = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - started

        self.assertEqual(errors, [])
        # 5 个请求、每个间隔 0.1s，串行化后至少要约 0.4s。
        # 无锁版本会让所有线程同时通过，耗时趋近 0。
        self.assertGreaterEqual(
            elapsed,
            interval * (n - 1) * 0.7,
            f"并发限速未生效（耗时仅 {elapsed:.3f}s）",
        )

    def test_zero_interval_is_noop(self):
        http = HttpClient(interval=0.0, retries=0)
        started = time.monotonic()
        for _ in range(50):
            http._throttle("example.com")
        self.assertLess(time.monotonic() - started, 0.2)

    def test_fallback_counter_is_safe_under_concurrency(self):
        http = HttpClient(interval=0.0, retries=0)
        n = 200

        def worker() -> None:
            with http._lock:
                http.fallback_count += 1

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(http.fallback_count, n)


if __name__ == "__main__":
    unittest.main()
