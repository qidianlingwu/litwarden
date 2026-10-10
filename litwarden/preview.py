"""摘要速览：把长摘要压成一眼能扫的短句。

为什么需要它：用户要在**导入 Zotero 之前**就判断一篇文献值不值得读。原先摘要只
出现在导出表的最后一列（实测中位 1293 字符），网页的论文表里根本没有摘要列，
所以真正读文献只能等导入之后——那时才发现文章讲什么，已经晚了。

为什么取「首句 + 末两句」：学术摘要基本按「背景 → 方法 → 结果 → 结论」写，
首句点问题、末两句落结论；中间的方法细节对「要不要读这篇」帮助最小。

为什么用规则而不是模型：这一步发生在导入之前，本项目的前提是「在此之前不调用
任何外部服务」。规则实现可解释、可测试、离线，代价是个别摘要的取句不够理想。
"""

from __future__ import annotations

import re

__all__ = ["DEFAULT_LIMIT", "abstract_preview", "split_sentences"]

# 速览的字符上限。默认值按「表格里两三行能读完」定。
DEFAULT_LIMIT = 220

# 英文里大量非句末的句点：缩写、图表号、卷页号等。命中就不当句子边界。
_ABBREV = {
    "al", "e.g", "i.e", "eg", "ie", "vs", "cf", "etc", "fig", "figs",
    "eq", "eqs", "no", "nos", "vol", "vols", "pp", "p", "ref", "refs",
    "approx", "ca", "dr", "prof", "mr", "mrs", "ms", "st", "jr", "sr",
    "inc", "ltd", "co", "corp", "dept", "univ", "suppl", "ed", "eds",
}

_TERM = ".!?。！？"
_CJK_TERM = "。！？"


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_cjk(ch: str) -> bool:
    return "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _join(parts: list[str]) -> str:
    """拼句子：中文之间不插空格，否则会出现「难题。 本文」这种怪缝。"""
    out = ""
    for p in parts:
        if out and not (_is_cjk(out[-1]) or _is_cjk(p[:1] or " ")):
            out += " "
        out += p
    return out


def _is_boundary(t: str, i: int) -> bool:
    """判断 t[i] 处的句末标点是否真的断句。

    英文句点有太多非句末用法（et al. / e.g. / 3.5 / J. Smith / U.S.），
    这些都必须放过，否则摘要会被切成一堆碎片，速览反而没法读。
    """
    ch = t[i]
    if ch in _CJK_TERM or ch in "!?":
        return True
    # 以下是英文句点的各种例外
    if i > 0 and i + 1 < len(t) and t[i - 1].isdigit() and t[i + 1].isdigit():
        return False                                  # 小数：3.5
    j = i - 1
    while j >= 0 and (t[j].isalnum() or t[j] in ".-"):
        j -= 1
    word = t[j + 1 : i].lower()
    bare = word.replace(".", "")
    if len(bare) == 1 and bare.isalpha():
        return False                                  # 单字母缩写：J. / A.
    if word in _ABBREV or bare in _ABBREV:
        return False                                  # et al. / e.g. / vs.
    if "." in word and len(bare) <= 2:
        return False                                  # 连写缩写：U.S.
    if i + 1 < len(t) and not t[i + 1].isspace():
        return False                                  # 句点后紧跟字符，没断句
    return True


def split_sentences(text: str | None) -> list[str]:
    """切句。中英标点都认，且不误切常见缩写与小数。"""
    t = _normalize(text)
    if not t:
        return []
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(t)
    while i < n:
        ch = t[i]
        buf.append(ch)
        if ch in _TERM:
            # 连续标点（"?!", "..."）算一个句末，免得切出空句
            while i + 1 < n and t[i + 1] in _TERM:
                i += 1
                buf.append(t[i])
            if _is_boundary(t, i):
                s = "".join(buf).strip()
                if s:
                    out.append(s)
                buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _clip(s: str, limit: int) -> str:
    if limit <= 0 or len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip() + "…"


_SEP = " … "
# 首句至少还要留下这么多字，否则宁可整句丢掉
_MIN_HEAD = 24

# 结构化摘要的分节标记。命中说明这一段就是作者自己写的结论。
_CONCLUSION_HEAD_RE = re.compile(r"^(?:conclusions?|结论|结语)\s*[:：]?", re.I)

# 跟在结论后面的样板段：数据/代码可用性、资助、利益冲突等。这些不是研究结论，
# 对「要不要读这篇」也没有帮助，却常因「取末两句」挤掉真正该看的结论。
_BOILERPLATE_HEAD_RE = re.compile(
    r"^(?:data\s+and\s+code\s+|data\s+|code\s+|software\s+|availability|"
    r"supplementary|supporting\s+information|funding|acknowledg\w*|"
    r"conflicts?\s+of\s+interest|competing\s+interests)\b",
    re.I,
)
_URLISH_RE = re.compile(r"https?://|www\.|github\.com|zenodo|doi\.org|10\.\d{4,}/",
                        re.I)
_AVAIL_VERB_RE = re.compile(r"availab|download|archiv|freely\s+available", re.I)


def _is_boilerplate(sentence: str) -> bool:
    """这句是「数据/代码可用性」之类的样板声明吗？

    必须**同时**有链接特征与「可获取/下载/归档」这类动词才判定，否则会把
    「详细结果见 https://… 的基准」这种正常结论误删。
    """
    t = sentence.strip()
    if _BOILERPLATE_HEAD_RE.match(t):
        return True
    return bool(_URLISH_RE.search(t) and _AVAIL_VERB_RE.search(t))


def _drop_trailing_boilerplate(sents: list[str]) -> list[str]:
    """去掉结尾的样板句，但至少留一句——整篇就是样板时也不能返回空串。"""
    out = list(sents)
    while len(out) > 1 and _is_boilerplate(out[-1]):
        out.pop()
    return out


def _tail_sentences(sents: list[str]) -> list[str]:
    """挑出代表「结果/结论」的句子，最多两句。

    顺序很关键：**先**从整句列表末尾把所有样板句砍掉，**再**取末两句或结论段。
    反过来先取末两句的话，一旦结尾连着两句样板（真实摘要里相当常见），
    「至少留一句」的保护会把剩下那句样板留下来当结论，真正的结论反而进不了
    候选——COBRA 那篇就是这么错的。
    """
    base = _drop_trailing_boilerplate(sents)
    picked = base[-2:]
    for i, s in enumerate(base):
        if _CONCLUSION_HEAD_RE.match(s.strip()):
            picked = base[i:]
            break
    return picked[:2]


def abstract_preview(text: str | None, limit: int = DEFAULT_LIMIT) -> str:
    """把摘要压成「首句 + 结论」，并保证不超过 limit 个字符。

    尾部优先取结论段：结构化摘要（BMC 那类）以 Availability 段收尾时，机械地
    取「末两句」会选中「代码托管在 GitHub 某处 + 归档在 Zenodo」这种对筛选毫无
    用处的内容（详见 `_tail_sentences`）。

    预算分配是**结论优先**：先按上限给尾部，剩下的才给首句。这条顺序同样是实测
    改出来的——先给首句的话，末两句本身常已接近上限，于是被从中间截断，截掉的
    恰好是筛选时最想看的结论。首句排不下时直接丢掉；背景信息在标题和导出表的
    完整摘要里都还找得到。

    摘要为空时返回空串。要不要显示成「无摘要」是展示层的决定，不混进这个
    纯函数里——那样它才能在导出、界面等不同场合复用。
    """
    sents = split_sentences(text)
    if not sents:
        return ""
    if len(sents) <= 3:
        return _clip(_join(_drop_trailing_boilerplate(sents)), limit)
    head = sents[0]
    tail = _join(_tail_sentences(sents))
    sep = "…" if _is_cjk(head[-1:]) else _SEP
    tail_part = _clip(tail, limit)
    room = limit - len(tail_part) - len(sep)
    if room < _MIN_HEAD:
        return tail_part
    return _clip(head, room) + sep + tail_part
