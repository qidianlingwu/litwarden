"""Zotero 本地 API 客户端的几个易错点回归测试。

这里守的是四个**会静默出错**的坑，都是实测踩出来的：

  1. 批量删除时 `itemKey` 必须是逗号分隔的**单个**参数。
     写成重复参数（`itemKey=a&itemKey=b`）服务端不报错、返回 204，
     但只删掉第一个 —— 实测 28 个 key 只删掉 1 个，而调用方毫不知情。
  2. DELETE 返回 204 且**没有响应体**，拿不到逐条结果，
     所以不能按请求数记账，必须删完读回来核对。
  3. 多对象写操作必须带 `If-Unmodified-Since-Version`，否则 428。
  4. 核对用的 `GET /items?itemKey=a,b` 会把**子条目一并返回**，
     不能直接拿返回条数当「还剩几个」。
"""

import unittest

from litwarden.zotero import ZoteroClient


def _requested_keys(params) -> list[str]:
    """从请求参数里取出 itemKey 列表（兼容 dict 与键值对两种形态）。"""
    raw = None
    if isinstance(params, dict):
        raw = params.get("itemKey")
    else:
        for k, v in params or []:
            if k == "itemKey":
                raw = v
    return [s for s in (raw or "").split(",") if s]


class _Recorder(ZoteroClient):
    """只记录请求，不真的发。

    GET 的模拟刻意贴着真实行为，因为这正是当年测试漏掉 bug 的地方：
    `GET /items?itemKey=a,b` 返回的**不只是**这两个条目，而是连同它们的
    子条目（附件、笔记）一起返回，并且受 `limit` 截断。
    `_count_existing` 就得同时防住这两点。
    """

    prefix = "/api/users/0"
    _version = 7

    def __init__(self, alive=(), children_each: int = 2):
        self.seen: list[tuple] = []
        self._alive = set(alive)
        self._children_each = children_each

    def ensure_write(self):  # noqa: D102
        pass

    def _library_version(self):  # noqa: D102
        return self._version

    def _request(self, method, path, params=None, **kw):  # noqa: D102
        self.seen.append((method, path, params, kw))
        if method != "GET":
            return 204, None, {}

        items: list[dict] = []
        for k in _requested_keys(params):
            if k in self._alive:
                items.append({"key": k})
                # 子条目：附件与笔记，真实 API 会一并返回
                items.extend(
                    {"key": f"{k}-child{i}"} for i in range(self._children_each)
                )

        p = params or {}
        limit = int(p.get("limit") or 100)
        start = int(p.get("start") or 0)
        return 200, items[start : start + limit], {}


class TestDeleteItems(unittest.TestCase):
    def test_itemkey_is_one_comma_joined_param(self):
        """关键：逗号分隔的单个参数，不能是重复参数。"""
        c = _Recorder()
        c.delete_items(["AAA", "BBB", "CCC"])
        deletes = [s for s in c.seen if s[0] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        params = deletes[0][2]
        self.assertEqual(params, [("itemKey", "AAA,BBB,CCC")])
        # 绝不能出现重复键
        self.assertEqual(len(params), 1)

    def test_version_precondition_is_sent(self):
        c = _Recorder()
        c.delete_items(["AAA"])
        _, _, _, kw = next(s for s in c.seen if s[0] == "DELETE")
        self.assertEqual(kw["extra_headers"], {"If-Unmodified-Since-Version": "7"})

    def test_empty_input_makes_no_request(self):
        c = _Recorder()
        self.assertEqual(c.delete_items([]), 0)
        self.assertEqual(c.seen, [])

    def test_all_deleted_counts_everything(self):
        c = _Recorder(alive=set())
        self.assertEqual(c.delete_items(["A", "B", "C"]), 3)

    def test_partial_delete_is_not_over_reported(self):
        """3 条只删掉 2 条（A 还在）→ 必须报 2。

        A 带着 2 个子条目，若把子条目也算成「还剩几条」，
        记账会变成 3 - 3 = 0，把 2 条真实删除报成什么都没删。
        """
        c = _Recorder(alive={"A"}, children_each=2)
        self.assertEqual(c.delete_items(["A", "B", "C"]), 2)

    def test_nothing_deleted_reports_zero_not_negative(self):
        """一条都没删掉时报 0。子条目虚高会算出负数。"""
        c = _Recorder(alive={"A", "B", "C"}, children_each=3)
        self.assertEqual(c.delete_items(["A", "B", "C"]), 0)


class TestCountExisting(unittest.TestCase):
    def test_children_are_not_counted(self):
        """子条目不能算进「还在的条目」。"""
        c = _Recorder(alive={"A"}, children_each=5)
        self.assertEqual(c._count_existing(["A", "B"]), 1)

    def test_truncated_page_does_not_look_deleted(self):
        """返回被 limit 截断时，没取到的 key 不能被当成已删除。

        60 个 key、每个带 3 个子条目 → 共 240 条，必须翻页才数得完。
        """
        keys = [f"K{i}" for i in range(60)]
        c = _Recorder(alive=set(keys), children_each=3)
        self.assertEqual(c._count_existing(keys), 60)

    def test_nothing_alive_is_zero(self):
        c = _Recorder(alive=set(), children_each=4)
        self.assertEqual(c._count_existing(["A", "B"]), 0)

    def test_empty_input_makes_no_request(self):
        c = _Recorder()
        self.assertEqual(c._count_existing([]), 0)
        self.assertEqual(c.seen, [])


if __name__ == "__main__":
    unittest.main()
