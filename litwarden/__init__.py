"""litwarden —— 本地优先的文献工作流。

四个核心模块：
    Finder     多源检索 -> DOI 归一化 -> 去重
    Filterer   年份 / 引用量 / 期刊等级 / 关键词匹配度 过滤与排序
    Digester   投喂 PDF 到 Zotero -> 等 AI Butler 出笔记 -> 回读
    Librarian  分类 / 标签 / 收藏 / BibTeX 导出 / 阅读状态

设计原则见 docs/文献工作流-架构设计.md
"""

__version__ = "0.1.0"
