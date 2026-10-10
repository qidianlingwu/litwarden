# litflow 架构设计

> 本文记录 litflow（本地优先的文献工作流工具）的架构决策、模块划分与实测结论，适合想理解设计意图或参与开发的读者。
>
> 文档定位：架构设计说明。项目用法与命令参考见根目录 `README.md`；立项前的工具选型调研见 [`ai-research-tools-survey.md`](./ai-research-tools-survey.md)。

> 目标：搭建一个「查找 → 筛选 → 初读提炼 → 管理」的可复用自动化工作流。
> 定位：文献工作流工具，不是综述生成器。
> 设计日期：2026-10-08

---

## 0. 前提条件与数据源可用性（实测结论）

| 检查项 | 结论 | 影响 |
|---|---|---|
| Zotero 版本 | 需要 10+ | 本地写接口自 Zotero 10 起提供，这是整个集成方案的关键前提 |
| Zotero 本地 API | 默认关闭，直接访问返回 403 | 需在 设置 → Advanced 勾选 `Allow other applications on this computer to communicate with Zotero` |
| Zotero `Server-ID` | 写请求必须携带该头 | 每个 Zotero 实例的 Server-ID 不同，使用前需按实例查询 |
| Zotero-AI-Butler | 可选插件，无对外 API | 集成方式需专门设计，见第 4 节 |
| Python | 3.11+（零第三方依赖） | 全部标准库实现，无需 `pip install` |
| Node / npm | 非必需 | 仅影响前端技术选型，见第 8 节 |
| OpenAlex API | 匿名共享 IP 的预算会被耗尽，返回 429「Insufficient budget」 | 2026 年起须注册并携带自己的免费 key |
| Semantic Scholar API | 无 key 时 429 限流 | 建议申请免费 key |
| Crossref API | 正常，无需 key | 可作主力元数据源 |
| PubMed E-utilities | 正常，无需 key | 生物医学主力 |
| Unpaywall | 需真实邮箱（`example.com` 这类占位邮箱会被拒） | 用真实邮箱即可，免费 |

结论：Data 层要有「无 key 降级路径」。 不能把工作流绑死在必须申请 key 的服务上。

---

## 1. 五项功能标准 → 技术方案映射

| # | 需求标准 | 技术方案 | 数据/接口 |
|---|---|---|---|
| 1 | 文献查找：按关键词/作者/期刊检索 | 多源并行检索 + DOI 归一化去重 | Crossref（无 key）+ PubMed（无 key）+ OpenAlex/S2（有 key 时增强）+ arXiv |
| 2 | 文献筛选：年份 / 引用量 / 期刊等级 / 关键词匹配度 过滤与排序 | 本地可组合的 filter DSL + 多维排序 | 元数据来自检索层；期刊等级见第 6 节；关键词匹配度本地算 |
| 3 | 初读提炼：集成 Zotero-AI-Butler | 松耦合：投喂 PDF → 插件自动精读 → 回读笔记（见第 4 节） | Zotero 本地 API（读笔记）+ 文件写入 |
| 4 | 文献管理：分类 / 标签 / 收藏 / BibTeX 导出 / Zotero 同步 / 阅读状态 | 以 Zotero 为唯一事实来源，工作流做视图与操作层 | Zotero 本地 API 读写（collections / tags / items / notes） |
| 5 | 工作流串联：打通四环节，可重复执行 | 状态机 + 可保存的「工作流配方（recipe）」 | 本地 SQLite 存运行记录 |

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  交互层                                                       │
│  · 阶段 1：CLI（最快跑通，先证明链路可行）                     │
│  · 阶段 2：本地 Web UI（表格筛选 + 批量操作 + 进度可视化）      │
├──────────────────────────────────────────────────────────────┤
│  工作流编排层  Workflow Orchestrator                          │
│  · Recipe = 可保存的「检索式 + 筛选条件 + 初读策略 + 归档规则」 │
│  · 每步落盘 → 可重跑、可审计、可对比两次运行结果                │
├──────────────────────────────────────────────────────────────┤
│  四个核心模块                                                  │
│  ① Finder      多源检索 → DOI 归一化 → 去重 → 候选池            │
│  ② Filterer    年份/引用量/期刊等级/关键词匹配度 → 排序与过滤    │
│  ③ Digester    投喂 PDF 到 Zotero → 等 Butler 出笔记 → 回读     │
│  ④ Librarian   分类/标签/收藏/BibTeX 导出/阅读状态 同步 Zotero  │
├──────────────────────────────────────────────────────────────┤
│  适配层 Adapters                                              │
│  crossref · pubmed · openalex · semanticscholar · arxiv       │
│  unpaywall(OA 全文定位) · zotero-local-api · journal-rank      │
├──────────────────────────────────────────────────────────────┤
│  存储  本地 SQLite（运行记录 / 候选池 / 笔记缓存 / 期刊等级表） │
└──────────────────────────────────────────────────────────────┘
```

核心设计原则：
1. Zotero 是唯一事实来源，工作流不另建文献库，避免"两边数据不一致"。本地 SQLite 只存运行记录与缓存。
2. 每一步落盘，检索结果、筛选决策、初读状态都有记录，工作流可重跑、可增量执行。
3. 无 key 也能跑，先跑通 Crossref + PubMed + Unpaywall，key 是增强项不是前提。

---

## 3. 模块 ①②：查找与筛选

### 3.1 Finder：多源检索与去重

```
检索式（关键词 / 作者 / 期刊 / 年份区间）
  ├─→ Crossref          → DOI, title, issued, is-referenced-by-count, container-title
  ├─→ PubMed            → PMID, 标题, 摘要, MeSH
  ├─→ OpenAlex          → DOI, cited_by_count, primary_location.source, best_oa_location
  ├─→ Semantic Scholar  → DOI, citationCount, openAccessPdf
  └─→ arXiv             → arXiv ID, 分类, PDF 链接
        ↓
  归一化：优先 DOI → 无 DOI 用 PMID → 无则 (标题归一化 + 年份) 模糊匹配
        ↓
  去重合并：同一论文保留并合并各源字段（引用量取最大值、取有摘要的那个）
        ↓
  候选池（本地 SQLite 表 papers）
```

关键点：引用量必须记录「取自哪个源、什么时候取的」。Crossref 的 `is-referenced-by-count` 和 OpenAlex 的 `cited_by_count` 差异很大，不记录来源就没法复现筛选结果。

### 3.2 Filterer：过滤与排序

| 维度 | 实现 |
|---|---|
| 年份 | 本地过滤，直接用 `issued` |
| 引用量 | 本地过滤，按年归一化可选（新论文天然引用少，建议提供「年均引用」排序） |
| 期刊等级 | 见第 6 节 |
| 关键词匹配度 | 本地计算：BM25 over 标题+摘要（零依赖）；可选升级为 LLM 相关性打分 |
| 阅读状态 | 从 Zotero 标签/集合反查（`未读 / 在读 / 已初读 / 已精读`） |

排序默认值：`期刊等级 → 年均引用 → 关键词匹配度`，而不是单纯按总引用量（会全是老论文）。

---

## 4. 模块 ③：Zotero-AI-Butler 集成方案（本设计最关键的一环）

### 4.1 实测得到的硬约束

根据 Zotero-AI-Butler 的 README 与文档确认：

> 它没有任何对外 API、CLI 或事件钩子（hook / notifier）。所有交互都是 Zotero GUI 操作。

它提供的触发方式只有四种：
1. 右键条目 →「召唤AI管家进行分析」（手动）
2. 右键 →「多轮对话重新精读」（手动）
3. 自动巡航：仪表盘 → 界面设置 →「自动扫描新文献」（默认关闭，开启后新 PDF 拖入并完成元数据检索即自动分析）
4. 批量：仪表盘 →「扫描未分析论文」→ 勾选 → 加入队列（手动）

笔记输出：Markdown 笔记，存为该条目下的子笔记（这点很关键，可以被 Zotero 本地 API 直接读到）。

### 4.2 集成方案：松耦合「投喂—等待—回读」

不试图驱动 Butler，而是利用它自身的自动化开关，把它当成一个黑盒处理器：

```
① 工作流筛选出目标论文
        ↓
② 工作流通过 Zotero 本地 API 写入条目 + 附件
   （附件必须是 stored-file：imported_file / imported_url）
        ↓
③ 在 Butler 里开启「自动扫描新文献」  ← 一次性设置
        ↓
④ Butler 检测到新 PDF → 自动精读 → 生成 Markdown 笔记
        ↓
⑤ 工作流轮询 Zotero 本地 API，检测该条目下是否出现新笔记
   （读免认证，可安全高频轮询）
        ↓
⑥ 回读笔记 → 存入本地缓存 → 在工作流 UI 里展示/检索/对比
```

为什么这样设计：
- 不需要改 Butler 源码，不需要 hook（它本来也没有）
- 写入用 Zotero 本地 API（读免认证、写需申请本地 key，见 4.3）
- 轮询是纯读操作，零风险
- 使用者仍可随时在 Zotero 里手动补跑

降级路径（如果 Butler 自动扫描没触发）：工作流把待读队列导出成一个清单，在 Zotero 里用「扫描未分析论文」批量勾选，半自动但依然比逐篇右键快。

### 4.3 Zotero 写入的三道门槛（必须先解决）

| 门槛 | 解决方式 |
|---|---|
| ① 本地 API 未开启 | 设置 → Advanced → 勾选 `Allow other applications on this computer to communicate with Zotero` |
| ② 写请求需要本地 API Key | `POST /api/local/authorize`，带 `Zotero-Server-ID: <本机实例的 Server-ID>`，body `{"appName":"..."}` → Zotero 弹窗，点 Always Allow 拿永久 key |
| ③ 弹窗防刷屏 | 每分钟最多 5 次会弹窗的请求，超出返回 429 + `Retry-After`。所以 key 要缓存复用 |

写接口的限制（实测文档确认）：
- 仅支持 API v3，不支持 `format=atom`
- 默认不分页（一次返回全部匹配对象，大库要注意）
- 不支持部分文件上传（`PATCH .../file` → 405），必须整文件上传，< 4 GB
- 只能上传 `imported_file` / `imported_url` 类型的 stored-file 附件
- 本地版本号与 Web API 版本号完全无关，不要混用

---

## 5. 模块 ④：文献管理

| 能力 | 接口 |
|---|---|
| 分类（集合） | `POST/PUT/DELETE /api/users/0/collections` |
| 标签 | 条目 JSON 的 `tags` 字段；删除标签用本地 API 专有端点 |
| 收藏 | Zotero 无「收藏」概念 → 映射为专用集合 `⭐ 收藏` 或专属标签 |
| BibTeX 导出 | 两种：① 本地 API 读条目 → 在工作流侧生成 BibTeX（可控性强，推荐）；② 调用 Zotero 内置导出（`Better BibTeX` 更佳） |
| 与 Zotero 同步 | Zotero 自身负责云同步；工作流只写本地库，Zotero 同步时自动上传 |
| 阅读状态跟踪 | Zotero 无原生字段 → 用保留标签前缀：`wf:未读` / `wf:在读` / `wf:已初读` / `wf:已精读`。工作流读写这些标签，Zotero 里也能直接看到和筛选 |

阅读状态用标签而不是集合，理由：一篇论文可以同时属于多个主题集合，但阅读状态是单一互斥状态，标签语义更贴切，且 Zotero 支持按标签快速筛选。

---

## 6. 期刊等级数据：分层策略（这是个坑，必须说清）

没有任何免费 API 能直接给出 JCR 分区或中科院分区。 它们是订阅版权数据。所以分三层：

| 层 | 数据 | 可得性 | 说明 |
|---|---|---|---|
| L1 内置（推荐默认） | Scimago SJR 年度 CSV | ✅ 官方免费下载 | 提供 SJR 指标 + Q1/Q2/Q3/Q4 分区，覆盖广，可离线查表 |
| L1 内置（CS 专用） | CCF 推荐目录（A/B/C 类） | ✅ 公开目录 | 计算机领域的事实标准，可整理成 JSON |
| L2 可选导入 | 中科院分区表 | ⚠️ 版权敏感 | 设计成使用者自行导入 CSV，工作流不内置、不分发 |
| L3 在线查询 | LetPub / 科研通 / JCR | ⚠️ JCR 需机构订阅 | 只做"一键跳转查询"，不做自动抓取（避免 ToS 风险） |

设计含义：`journal_rank` 表设计成可插拔的多个等级体系（`sjr` / `ccf` / `cas` / `jcr`），每条记录带 `source` 字段。使用者装哪个体系，过滤时就能用哪个。

---

## 7. MVP 里程碑

| 阶段 | 交付物 | 验收标准 |
|---|---|---|
| M1 打通链路（最优先） | CLI：`search → filter → 导出候选池为表格` | 跑一次真实检索，得到带 DOI/年份/引用量/SJR 分区的候选表 |
| M2 接 Zotero | CLI：`候选池 → 写入 Zotero 条目 + 附件 + 阅读状态标签` | Zotero 里出现条目，标签正确，云同步正常 |
| M3 接 Butler | CLI：`轮询笔记 → 回读 → 存本地缓存 → 输出汇总视图` | Butler 自动精读后的笔记能被工作流读到并展示 |
| M4 工作流串联 | Recipe 机制：保存一套参数，一条命令重跑 | 换关键词重跑，全程无需手工干预 |
| M5 Web UI | 表格筛选 + 批量操作 + 进度可视化 | 不写命令行也能用 |

完成度：M1~M5 已全部实现并通过验证（各阶段实测结论见附录 A）；M1 之后每一步都可独立使用，不会出现"做了一半不能用"的状态。

---

## 8. 需要使用者决定的三项配置

### ① 技术形态（影响后面全部代码）

| 选项 | 说明 | 优点 | 代价 |
|---|---|---|---|
| A. 本地 Python 应用（FastAPI + 轻前端）← 设计阶段倾向 | 本机跑一个服务，浏览器打开 | Zotero 本地 API 本来就必须本机访问；数据不出本地 | 需要装几个 Python 包 |
| B. CodeBuddy MCP Server + Skill | 把四个模块做成 MCP 工具，在 CodeBuddy 里对话驱动 | 无需前端；可直接在现有 AI 环境中使用 | 依赖 CodeBuddy 会话；批量操作体验不如 UI |
| C. CodeBuddy Agent SDK Web 应用（Node/TS 模板） | 按既定开发规范从模板搭建 | 有完整 Web 壳；可加对话式交互 | 依赖 Node 运行时，需额外安装；Zotero 本地 API 仍需本机代理 |

> 实施阶段最终选定纯标准库 CLI方案，未采用 FastAPI，理由见附录 A1。

### ② 数据源权重（可按课题领域调整）

- 理工/CS：arXiv + Crossref + CCF 目录
- 生物医学：PubMed 优先 + MeSH 检索
- 通用：三者均衡

### ③ 两项使用前置条件

- 开启 Zotero 本地 API（设置 → Advanced → 勾选那一项），否则所有读写都是 403
- 注册免费 API key：OpenAlex（必填，2026 年起匿名额度已耗尽）、Semantic Scholar（建议）；Unpaywall 只需真实邮箱

---

## 附：两个已在调研中排除的方案

1. 不自建 PDF 精读：项目假设使用者已安装 Zotero-AI-Butler，且它支持本地直连多种模型（Gemini / OpenAI / Anthropic / OpenAI 兼容 / 火山方舟 / Ollama），重复实现并无收益。工作流只负责「喂进去、读回来」。
2. 不依赖网页版文献工具做自动化节点：Connected Papers / ResearchRabbit / Litmaps / Consensus 均无 API，做不了稳定节点。它们的价值保留为"手动深挖时的跳转入口"，写进工作流的推荐链接里即可。

---

## 附录 A：M1 实施实录（设计定稿后实测发现的六个问题）

M1 已落地并跑通。以下六个问题都是实测才暴露的，与设计阶段的预期不同，记录在此供 M2~M5 参考。

### A1. 技术形态最终定为：纯标准库 CLI（不是 FastAPI）

设计阶段倾向 FastAPI，实施时改为零第三方依赖。理由：

- 目标环境为 Python 3.14，部分第三方包尚无 wheel，`pip install` 会直接卡住。
- 所有需要的能力标准库都能提供：HTTP（`urllib`）、XML（`ElementTree`）、SQLite、BM25（自实现约 40 行）、CSV、网页服务（`http.server`）。
- 科研数据本地处理，依赖越少越好审计。

结论：M5 的 Web UI 也用 `http.server` 实现，不引入 FastAPI/Flask。

### A2. macOS 的 HTTPS 证书问题（macOS 专属坑）

python.org 安装包装出来的 Python 默认不含 CA 根证书，所有 HTTPS 请求报
`CERTIFICATE_VERIFY_FAILED`。而 `curl` 用系统钥匙串，所以看起来"网络正常"。

已实现自动兜底：按顺序尝试 `/etc/ssl/cert.pem`（macOS 必有）等系统证书库，
最后才尝试 `certifi`。`doctor` 会打印实际生效的证书来源。

### A3. TLS 1.3 在特定网络环境下成功率只有约 1/3（重要）

在特定网络环境下实测对照（每项 12 次请求）：

| 配置 | 成功率 |
|---|---|
| 默认（TLS 1.3） | 4/12 |
| 限制 ECDH 曲线 X25519 | 2/12 |
| 限制 ECDH 曲线 prime256v1 | 8/12 |
| 最高 TLS 1.2 | 10/12 |

报错为 `SSL: UNEXPECTED_EOF_WHILE_READING`。而同一时刻 `curl` 走 TLS 1.3 基本正常
（2/3 成功，与纯网络抖动一致），说明不是纯网络问题，而是 Python 侧 TLS 1.3 握手
被中间设备干扰（很可能与 OpenSSL 3.5 新增的混合密钥交换导致 ClientHello 变大有关）。

最终策略 `tls = "auto"`：第 1 次用 TLS 1.3，重试时自动降级到 TLS 1.2。
命中降级的次数会在检索输出里显示。

### A4. Europe PMC 默认排序不是相关性，是日期倒序（影响最大）

实测确认：Europe PMC 不带 `sort` 参数时，返回的是最新论文。搜一个领域，
30 条结果全是当年（2026）的文章，2020 年的经典 benchmark 完全不出现。

这直接导致一个反直觉现象：Europe PMC 与 PubMed 的 DOI 交集为 0
（因为一个给最新、一个给最相关），去重逻辑看起来"失效"，实际是数据源行为差异。

修法：相关性模式下并取两路，默认（最新）+ `CITED desc`（经典），再合并。
效果：候选池同时包含 2020 年 1039 引用的 benchmark 和 2026 年的新方法。

### A5. 按引用量排序会严重漂移，必须配相关性下限

Crossref / Europe PMC 的 cited 排序是全局的。对模糊检索式搜 scRNA-seq 的
batch effect，会返回 Cell 上的自闭症外显子组测序研究（2140 引用）这类全局高被引
但不相关的论文。

修法：新增 `--min-keyword`（本地 BM25 匹配度下限，0-1）。`filter` 默认复用该次运行的
检索式来计算匹配度，所以不用重复传 `-q`。

这条经验应写进 M2~M5 的所有默认参数建议里。

### A6. 期刊等级数据只能"可插拔 + 使用者导入"

再次确认设计阶段的判断：没有任何免费 API 能给出 JCR / 中科院分区。
SJR 官方 CSV 免费可用（已验证能解析其分号分隔 + 表头拆两行的格式）；
中科院分区表版权敏感，设计成使用者自行导入。

一条记录同时写 ISSN 和刊名两种匹配键，因为预印本通常没有 ISSN，
只按 ISSN 建索引会漏掉大量论文。

### 附：M1 验收结果（真实数据）

```
检索：single-cell RNA sequencing batch effect correction, 2020 年至今
  → 4 源并行，原始命中 125 条，去重后 114 条
筛选：--rank sjr:Q1 --min-cites-per-year 2 --min-keyword 0.25
  → 10 篇（全部 Q1，年均引用 4~357，关键词匹配度 0.27~1.00）
    含经典：2020 benchmark of batch-effect correction（1039 引用）
    含前沿：2025 FedscGen（联邦学习）、2024 scDisInFact（Nature Comm）
导出：CSV / Markdown / BibTeX 三种格式均校验通过
```
