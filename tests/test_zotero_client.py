"""Zotero 本地 API 客户端的几个易错点回归测试。

这里守的是三个**会静默出错**的坑，都是实测踩出来的：

  1. 批量删除时 `itemKey` 必须是逗号分隔的**单个**参数。
     写成重复参数（`itemKey=a&itemKey=b`）服务端不报错、返回 204，
     但只删掉第一个 —— 实测 28 个 key 只删掉 1 个，而调用方毫不知情。
  2. DELETE 返回 204 且**没有响应体**，拿不到逐条结果，
     所以不能按请求数记账，必须删完读回来核对。
  3. 多对象写操作必须带 `If-Unmodified-Since-Version`，否则 428。
"""

import unittest

from litflow.zotero import ZoteroClient


class _Recorder(ZoteroClient):
    """只记录请求，不真的发。"""

    def __init__(self, alive_after_delete: int = 0):
        self.seen: list[tuple] = []
        self._alive = alive_after_delete

    prefix = "/api/users/0"
    _version = 7

    def ensure_write(self):  # noqa: D102
        pass

    def _library_version(self):  # noqa: D102
        return self._version

    def _request(self, method, path, params=None, **kw):  # noqa: D102
        self.seen.append((method, path, params, kw))
        if method == "GET":
            # 模拟「删完还剩 _alive 条」
            return 200, [{"key": f"X{i}"} for i in range(self._alive)], {}
        return 204, None, {}


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

    def test_count_is_verified_not_assumed(self):
        """返回值必须来自「实际还剩几条」，而不是「发了几条」。"""
        c = _Recorder(alive_after_delete=2)
        # 3 条里还剩 2 条 → 真正删掉的是 1 条
        self.assertEqual(c.delete_items(["A", "B", "C"]), 1)

    def test_all_deleted_counts_everything(self):
        c = _Recorder(alive_after_delete=0)
        self.assertEqual(c.delete_items(["A", "B", "C"]), 3)

    def test_empty_input_makes_no_request(self):
        c = _Recorder()
        self.assertEqual(c.delete_items([]), 0)
        self.assertEqual(c.seen, [])


if __name__ == "__main__":
    unittest.main()
