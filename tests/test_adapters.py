"""适配器的解析工具、查询构造与错误提示。

不联网：用桩替掉 HttpClient。这里验证的重点是**契约与提示质量**——
论文数据会变，但「返回什么结构」「配错时报什么错」不该变。

错误提示尤其值得固化：使用者配错 key 时，第一眼看到的就是这几行文字。
"""

from __future__ import annotations

import unittest
from typing import Any

from litwarden.adapters import SearchQuery
from litwarden.adapters.arxiv import _text_clause
from litwarden.adapters.base import Adapter, content_terms
from litwarden.adapters.openalex import OpenAlexAdapter
from litwarden.adapters.semanticscholar import SemanticScholarAdapter
from litwarden.http import HttpError


class StubHttp:
    """替代 HttpClient：要么按状态码抛 HttpError，要么返回固定 JSON。"""

    def __init__(self, status: int | None = None, payload: Any = None) -> None:
        self.status = status
        self.payload = payload
        self.calls: list[tuple] = []

    def get_json(self, url: str, params: dict | None = None,
                 headers: dict | None = None) -> Any:
        self.calls.append((url, params, headers))
        if self.status:
            raise HttpError(f"HTTP {self.status} from {url}", status=self.status, url=url)
        return self.payload


class TestCleanText(unittest.TestCase):
    """must 先反转义实体再剥标签，顺序错了会把标签残留进标题。"""

    def test_unescapes_before_stripping_tags(self):
        self.assertEqual(Adapter.clean_text("&lt;i&gt;HMGB1&lt;/i&gt; release"), "HMGB1 release")

    def test_strips_plain_tags(self):
        self.assertEqual(Adapter.clean_text("<p>Hello <b>world</b></p>"), "Hello world")

    def test_common_entities(self):
        self.assertEqual(Adapter.clean_text("A &amp; B"), "A & B")
        self.assertEqual(Adapter.clean_text('say &quot;hi&quot;'), 'say "hi"')
        self.assertEqual(Adapter.clean_text("x&#x2010;y"), "x-y")
        self.assertEqual(Adapter.clean_text("a&nbsp;b"), "a b")

    def test_collapses_whitespace(self):
        self.assertEqual(Adapter.clean_text("  a \n\n  b  "), "a b")

    def test_empty(self):
        for bad in (None, ""):
            self.assertEqual(Adapter.clean_text(bad), "")


class TestParseYear(unittest.TestCase):
    def test_extracts_four_digit_year(self):
        self.assertEqual(Adapter.parse_year("2024 Jan"), 2024)
        self.assertEqual(Adapter.parse_year(2021), 2021)
        self.assertEqual(Adapter.parse_year("published 1999"), 1999)

    def test_rejects_junk(self):
        for bad in (None, "", "no year here", "123"):
            self.assertIsNone(Adapter.parse_year(bad), bad)


class TestContentTerms(unittest.TestCase):
    def test_drops_stopwords_and_single_chars(self):
        self.assertEqual(content_terms("the a of in"), [])
        self.assertEqual(content_terms("a b c"), [])

    def test_keeps_hyphens_and_dots_inside_terms(self):
        self.assertEqual(
            content_terms("single-cell RNA-seq v1.2"),
            ["single-cell", "RNA-seq", "v1.2"],
        )

    def test_deduplicates_preserving_order(self):
        self.assertEqual(content_terms("RNA graph RNA"), ["RNA", "graph"])

    def test_empty(self):
        self.assertEqual(content_terms(""), [])
        self.assertEqual(content_terms(None), [])


class TestArxivQueryBuilding(unittest.TestCase):
    """早先把整句包成短语，对多词长句几乎零命中，这里固化修正后的行为。"""

    def test_single_term_uses_phrase(self):
        self.assertEqual(_text_clause("cryoem"), 'all:"cryoem"')

    def test_multi_term_uses_and(self):
        self.assertEqual(
            _text_clause("graph neural network"),
            'all:("graph" AND "neural" AND "network")',
        )

    def test_stopwords_are_removed(self):
        self.assertEqual(
            _text_clause("the graph of networks"),
            'all:("graph" AND "networks")',
        )

    def test_empty_query(self):
        self.assertEqual(_text_clause(""), "")
        self.assertEqual(_text_clause("the and of"), "")


class TestSearchQuery(unittest.TestCase):
    def test_is_empty(self):
        self.assertTrue(SearchQuery().is_empty())
        self.assertFalse(SearchQuery(text="x").is_empty())
        self.assertFalse(SearchQuery(author="Smith").is_empty())
        self.assertFalse(SearchQuery(journal="Nature").is_empty())

    def test_describe_includes_year_range(self):
        d = SearchQuery(text="abc", year_from=2020, year_to=2024).describe()
        self.assertIn("abc", d)
        self.assertIn("2020-2024", d)

    def test_describe_open_ended_years(self):
        self.assertIn("2020-*", SearchQuery(text="x", year_from=2020).describe())


class TestOpenAlexErrors(unittest.TestCase):
    """配错 key 时的提示必须能直接指向原因，否则使用者无从下手。"""

    def _search(self, status: int):
        ad = OpenAlexAdapter(StubHttp(status=status), api_key="fake")
        return ad.search(SearchQuery(text="graph", limit=1))

    def test_401_message_points_at_api_key(self):
        with self.assertRaises(HttpError) as ctx:
            self._search(401)
        msg = str(ctx.exception)
        self.assertIn("401", msg)
        self.assertIn("api_key", msg)
        self.assertIn("enabled", msg, "忘记开 enabled 是高频原因，必须提示")

    def test_message_warns_about_llm_keys(self):
        """把 DeepSeek / OpenAI 的 key 填进来是实际发生过的错误用法。"""
        with self.assertRaises(HttpError) as ctx:
            self._search(401)
        msg = str(ctx.exception)
        self.assertIn("DeepSeek", msg)
        self.assertIn("大模型", msg)

    def test_429_also_gets_guidance(self):
        with self.assertRaises(HttpError) as ctx:
            self._search(429)
        self.assertIn("api_key", str(ctx.exception))

    def test_other_status_passes_through(self):
        with self.assertRaises(HttpError) as ctx:
            self._search(500)
        self.assertEqual(ctx.exception.status, 500)
        self.assertNotIn("排查方向", str(ctx.exception))

    def test_successful_parse(self):
        payload = {"results": [
            # DOI 的注册机构号必须是 4-9 位数字，否则 normalize_doi 会（正确地）拒绝
            {"id": "https://openalex.org/W1",
             "doi": "https://doi.org/10.1038/s41586-021-01358-2",
             "title": "A paper", "publication_year": 2024, "cited_by_count": 5},
            {"id": "W2", "title": "", "publication_year": 2024},   # 无标题应被丢弃
        ]}
        ad = OpenAlexAdapter(StubHttp(payload=payload), api_key="fake")
        papers = ad.search(SearchQuery(text="x", limit=10))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].doi, "10.1038/s41586-021-01358-2")
        self.assertEqual(papers[0].cited_by, 5)
        self.assertEqual(papers[0].cited_by_source, "openalex")

    def test_sends_api_key_as_query_param(self):
        """OpenAlex 用查询参数传 key，不是请求头。"""
        stub = StubHttp(payload={"results": []})
        OpenAlexAdapter(stub, api_key="secret").search(SearchQuery(text="x", limit=1))
        _, params, _ = stub.calls[0]
        self.assertEqual(params.get("api_key"), "secret")


class TestSemanticScholarErrors(unittest.TestCase):
    def _search(self, status: int):
        ad = SemanticScholarAdapter(StubHttp(status=status), api_key="fake")
        return ad.search(SearchQuery(text="graph", limit=1))

    def test_429_mentions_rate_limit(self):
        with self.assertRaises(HttpError) as ctx:
            self._search(429)
        self.assertIn("限流", str(ctx.exception))

    def test_403_explains_invalid_key(self):
        with self.assertRaises(HttpError) as ctx:
            self._search(403)
        msg = str(ctx.exception)
        self.assertIn("403", msg)
        self.assertIn("api_key", msg)

    def test_403_warns_about_llm_keys(self):
        with self.assertRaises(HttpError) as ctx:
            self._search(403)
        self.assertIn("大模型", str(ctx.exception))

    def test_non_dict_response_returns_empty(self):
        """源改成返回数组时不该抛 AttributeError 打断整次检索。"""
        for payload in ([], "unexpected", None):
            with self.subTest(payload=payload):
                ad = SemanticScholarAdapter(StubHttp(payload=payload), api_key="k")
                self.assertEqual(ad.search(SearchQuery(text="x", limit=1)), [])

    def test_successful_parse(self):
        payload = {"data": [
            {"paperId": "S1", "title": "A study", "year": 2023, "citationCount": 9,
             "externalIds": {"DOI": "10.1038/s41586-021-01358-2", "ArXiv": "2101.00001v2"}},
            {"paperId": "S2", "title": "", "year": 2023},   # 无标题应被丢弃
        ]}
        ad = SemanticScholarAdapter(StubHttp(payload=payload), api_key="k")
        papers = ad.search(SearchQuery(text="x", limit=10))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].cited_by, 9)
        self.assertEqual(papers[0].doi, "10.1038/s41586-021-01358-2")
        self.assertEqual(papers[0].arxiv_id, "2101.00001", "版本号应被剥掉")
        self.assertEqual(papers[0].cited_by_source, "semanticscholar")

    def test_requires_key_flag(self):
        self.assertTrue(SemanticScholarAdapter(StubHttp(), api_key="").requires_key)
        self.assertFalse(SemanticScholarAdapter(StubHttp(), api_key="").available())
        self.assertTrue(SemanticScholarAdapter(StubHttp(), api_key="k").available())


if __name__ == "__main__":
    unittest.main()
