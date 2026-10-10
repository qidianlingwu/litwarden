# 科研 AI 工具与 Agent 工作流综合调研

> 本文是 litwarden 立项前对科研 AI 工具与 Agent 工作流的前置调研，面向想了解工具全景、可编程数据地基与工作流设计要点的读者。
>
> 文档定位：调研报告。litwarden 的架构决策与模块设计见 [`architecture.md`](./architecture.md)。
> 调研日期：2026-10-08　｜　调研方式：多搜索引擎 + 权威来源交叉验证（高校图书馆 LibGuides、CASRAI 指南、官方发布渠道）

---

## 0. 一句话结论

初始评估的 9 个工具覆盖了「发现 → 提取 → 核验 → 深度研究」四个点，但漏掉了三整层：

1. 流程骨架层：检索策略构建、系统综述协议与注册、质量/偏倚评估、PRISMA 报告，这层决定一个工作流能不能被称为"文献综述"，而不是"AI 代读了几篇论文"。
2. 学术诚信层：撤稿检查、统计自洽性核查（statcheck/GRIM/SPRITE）、PubPeer，Scite 只是这一层的入口。
3. 可编程地基层：OpenAlex / Crossref / Semantic Scholar API / Unpaywall，这是构建 Agent 时唯一必须自行掌握的部分。上面那些网页工具大部分没有 API，无法被工作流真正调用。

> 术语约定：下文的「初始清单」，指立项时最初评估的 9 个工具 ，
> Semantic Scholar、ResearchRabbit、Connected Papers、Elicit、Consensus、
> SciSpace、Scite.ai、ChatGPT / Gemini / Perplexity 的 Deep Research、秘塔 AI 搜索。
> 本文所有对比均以此为基线，标注「初始清单未覆盖」的即为本次调研补充的。

另外需要说明：检索"科研 AI 工具"时，结果里大量是国内 SEO 软文（"掌桥科研""笔捷 AI"这类论文代写包装成科研工具），本文只收录有图书馆 LibGuides 背书、有学术引用、或有公开 API/开源仓库的工具。

---

## 1. 工具全景（按科研流程）

图例：`⭐` = 初始清单未覆盖、建议补充　｜　`🔌` = 有 API / MCP，可被 Agent 直接调用　｜　`🆓` = 有免费档

### 1.1 选题与研究缺口（初始清单未覆盖）

| 工具 | 口碑/依据 | 定位 | 备注 |
|---|---|---|---|
| ⭐ Inciteful | HKUST 图书馆对比收录，学术圈公认免费 | 输出维度最多：Top Authors / Institutions / Journals，还能自动找出 Review Papers | 🆓🔌 数据源最全（MAG+S2+Unpaywall+Crossref+OpenCitations）；找综述类和综述缺口很好用 |
| ⭐ Prophy | 学术评审圈在用 | AI 推荐审稿人 + 检测研究新颖性/重复性 | 可用于反查"某个点子是不是已经有人做过" |
| ⭐ Publish or Perish | 老牌，引用指标标准工具 | 引文指标分析、领域影响力摸底 | 配合研究缺口判断 |
| ⭐ Dimensions / Lens.org | 机构订阅主流 | 文献+专利+基金一体，适合"技术→产业"链路调研 | Dimensions 有 API；Lens 免费档大方 |
| ⭐ Undermind | HKUST 图书馆收录，YC 投资（Y Combinator 公司页），研究者免费 | 唯一以"自然语言研究问题"为入口的检索，读全文+图表，专找别人漏掉的论文 | 免费试用查询次数受限；补全优先级最高 |
| AI 研究空白发现类（TutorGPT、scientific-gap-finder 等） | 新兴，质量参差 | 自动生成"候选研究缺口" | 输出只能当假设，必须人工回原文验证，极易幻觉 |

### 1.2 文献发现与引文图谱（初始清单已有 3 个 + 补充）

| 工具 | 免费状态 | 数据源 | 排序机制 | 特色 |
|---|---|---|---|---|
| Semantic Scholar（初始清单已有） | 🆓🔌 | 自有 | 语义相似 | 2.3 亿+，有正式 API，是唯一能当数据源用的 |
| ResearchRabbit（初始清单已有） | 免费档约 20 次基础检索 | S2 + PubMed | 共引 + 作者模式 + 摘要相似 | 作者网络推荐最强；支持 Zotero 导入 |
| Connected Papers（初始清单已有） | 每月 5 张免费图（含全功能） | Semantic Scholar | 共被引 + 文献耦合 | 单篇种子最省心；有每周新论文提醒 |
| ⭐ Litmaps | 免费 2 张图，每图 ≤100 篇 | Crossref + S2 + OpenAlex | PageRank 重要度 | 时间线视图看研究演进，支持邮件协作 |
| ⭐ Undermind | 研究者免费（查询受限） | Semantic Scholar | 主题匹配 + 引文网络中心性 | 自然语言提问式，见上 |
| ⭐ AMiner | 部分免费 🆓 | 清华自建，3 亿+ 文献、6000 万学者 | — | 国产，智谱 GLM 驱动，覆盖"搜读写做"；国内网络可用 |
| ⭐ 玻尔 Bohrium | 部分免费 | 深势科技 + 北京科学智能研究院 | — | "读文献 → 做计算 → 写论文"闭环，理工科/计算方向强 |
| PapersFlow | 付费（7 天试用） | 4.74 亿+ | — | 主打 反证据检索（counter-evidence discovery），思路罕见 |

> 做 Agent 的关键判断：Connected Papers / ResearchRabbit / Litmaps 都没有可用 API，只能靠浏览器自动化或人工导出。真正能进流水线的是 Semantic Scholar API + OpenAlex。

### 1.3 检索策略与综述协议（初始清单未覆盖，但是硬需求）

| 环节 | 工具/标准 | 说明 |
|---|---|---|
| ⭐ 检索策略构建 | 布尔检索式、同义词表、数据库选择、灰色文献 | 这一环目前没有好 AI 工具，必须 Agent 辅助人工做。但它决定综述质量上限 |
| ⭐ 综述方案注册 | PROSPERO（医学）、OSF、INPLASY | PRISMA 要求健康类主题开题前注册方案 |
| ⭐ 报告规范 | PRISMA 2020（27 项清单 + 流程图）、PRISMA-ScR（范围综述）、PRISMA-S（检索报告） | 参照 CASRAI 2026-08 指南，工具只是"围绕这些阶段聚集"，不替代方案本身 |

### 1.4 筛选 / 数据提取 / 系统综述工作台（初始清单仅覆盖 Elicit 一部分）

依据 CASRAI《AI Tools for Systematic Literature Reviews》（2026-08 更新），这一层是独立的专业工具层，与"开放发现工具"（SciSpace、Connected Papers 等）明确区分：

| 工具 | 负责环节 | 定价 | 备注 |
|---|---|---|---|
| ⭐ Rayyan | 仅筛选（导入/去重/标题摘要筛选） | 🆓 起（大团队付费） | 200 万+ 用户，最广泛使用的 AI 筛选工具；ML 相关性排序随标注逐步改进；支持双人盲筛+冲突解决；可由筛选记录直接生成 PRISMA 流程图 |
| ⭐ ASReview | 筛选（主动学习） | 🆓 开源 | 乌得勒支大学，van de Schoot 等发表于 *Nature Machine Intelligence* (2021)，现 LAB v2；每做一次判定就重训模型 |
| ⭐ Covidence | 全流程工作台（筛选→全文→提取→偏倚评估） | 付费（按团队） | Cochrane 官方推荐，用于新 Cochrane 综述 |
| ⭐ DistillerSR | 数据提取 + 偏倚评估 | 付费（企业级） | 药械监管申报场景，强调审计追踪 |
| ⭐ EPPI-Reviewer | 全流程 | 付费 | 教育/社科领域主流 |
| Elicit（初始清单已有） | 筛选 + 数据提取 | 🆓 档 / Plus $12、Pro $49 每月 | 官方指引：对照基准集测 recall 与 precision，而非看它给的漂亮答案 |
| ⭐ MetaReview / SciTrack | PRISMA 流程图 + 全流程 | — | 2026 新兴自动化工具，模板严格遵循 PRISMA 2020 |

### 1.5 精读与 PDF 交互

| 工具 | 关键差异 | 备注 |
|---|---|---|
| SciSpace | 2.8 亿+ 索引上解释密集段落、跨语言翻译 | 定位是"难读论文的私人导师"；有独立基准白皮书（200 条查询） |
| ⭐ NotebookLM | 只用用户上传的资料，不上网检索 → 可信度最高 | 🆓 完全免费；擅长"这些研究的主要分歧是什么"并给出处；能生成音频概览 |
| ⭐ Scholarcy | 自动生成结构化摘要卡片 | 与 Zotero 组合在中文文献处理上有实测提升 |
| ⭐ Humata.ai / ChatPDF | PDF 问答 | 通用型 |
| ⭐ 小绿鲸 | 内置 10 个翻译引擎、全文翻译保格式、AI 自动识别并解读图表 | 国产，英文文献阅读主力 |
| ⭐ Scholaread 靠岸学术 | 支持 CAJ 格式、多端同步 | 国产，处理知网 CAJ 文件的少数选择 |
| ⭐ 知云文献翻译 | 免费、页面简洁、多引擎可切换 | 无全文翻译、无平板 |

### 1.6 引用核验与学术诚信（Scite 只是入口，后面有一整层）

| 工具 | 作用 | 备注 |
|---|---|---|
| Scite.ai（初始清单已有） | 看被引时是 supporting / disputing / mentioning，防断章取义 | 已被 Research Solutions 收购（2023-11）；2026-02 推出 Scite MCP，可直连 ChatGPT / Claude ➜ 对 Agent 项目是重大利好 🔌 |
| ⭐ Retraction Watch Database | 撤稿记录库 | 2025 年跨过 6 万条；已被 Crossref 收购并整合，可通过 Crossref API 查 |
| ⭐ 免费撤稿批量检查（researchmisconduct.medleg.ro） | 粘贴 DOI 列表/参考文献，批量查撤稿 | 免费无注册，7.1 万+ 撤稿记录 |
| ⭐ statcheck | 自动重算论文报告的 p 值是否与统计量自洽 | 不用原始数据就能查 |
| ⭐ GRIM / GRIMMER / SPRITE / DEBIT | 检查均值、标准差是否"数学上不可能" | readingtheevidence、CASRAI 均有专文 |
| ⭐ p-curve / z-curve | 检验发表偏倚/统计功效 | — |
| ⭐ PubPeer | 同行评议后的公开评论 | 与撤稿库互补 |
| ⭐ paperguard（开源中文） | 给 PDF/TXT 自动扫统计自洽性、数字一致性、图片完整性 | GitHub 开源，灵感来自 PubPeer / Bik |
| ⭐ research-integrity-screener（开源） | 把 statcheck / p-curve / z-curve / GRIM / SPRITE 五法封装成一个工具 | GitHub |
| ⭐ Scite / SciSpace 的自动化撤稿检查 | 参考文献列表自动查撤稿 | SciSpace 2026-08 有专文介绍 |

> CASRAI 提到：撤稿论文已污染数千篇 Meta 分析。所以"撤稿检查"和"引用上下文核验"必须内置到工作流，不能当可选项。

### 1.7 写作 / 润色 / 投稿前检查

| 工具 | 定位 | 备注 |
|---|---|---|
| ⭐ Paperpal | CACTUS 旗下，覆盖检索+引用+润色+改写+投稿前检查 | 300 万学者；STM 出版数据训练，语言建议贴合期刊偏好；🆓 档 / Prime $25 每月 |
| ⭐ Writefull | Overleaf 深度集成 + 学术短语库（10 万+ 标准学术表达） | LaTeX 党首选 |
| ⭐ Trinka | 期刊格式检查 | — |
| Grammarly Academic | 通用语言 | — |
| ⭐ 沁言学术（原边写边搜） | 一站式：超级智能体 + 文献管理 + AI 精读 + 选题 + 问答 + 写作 + 团队协作 | 国产，4 亿+ 文献资源 |
| ⭐ 星火科研助手 | 中科院文献情报中心 + 科大讯飞 | 文献综述 + 辅助写作 + 润色 |
| ⭐ 秘塔 AI 搜索（初始清单已有） | 学术模式 + 深度研究 + 一键生成大纲/思维导图并导出 | 国产，无广告 |

### 1.8 国产学术平台（初始清单基本未覆盖）

| 工具 | 说明 |
|---|---|
| ⭐ AMiner | 清华开发，覆盖 220 个国家 1000 万用户，为国家 400 余期刊推送。2025-09 升级：GLM 旗舰模型 + 3 亿级文献 + "搜/读/写/做"全流程。有学术问答 API 能力 |
| ⭐ 玻尔 Bohrium | 深势科技 + AISI，"全球首个覆盖读文献-做计算-写论文"的平台 |
| ⭐ 知网研学 / 万方 / 维普 | 中文核心数据库，中文综述不可绕过 |
| ⭐ NoteExpress / EndNote / 医学文献王 | 文献管理老牌，EndNote 可直连 PubMed / WoS / Scopus |
| ⭐ Kimi 学术 / 智谱 / 豆包学术版 | 通用大模型的学术模式，易用性强 |

### 1.9 开源 Agent 框架与 MCP 组件（做工作流的直接积木）

这是与项目需求最相关的一节。已有大量现成组件，无需从零实现：

| 组件 | 类型 | 说明 |
|---|---|---|
| ⭐ GPT Researcher (`assafelovic/gpt-researcher`) | 开源 Agent | 最著名的开源深度研究 agent；DetailedReport 组件架构直接受斯坦福 STORM 启发；规划→检索→阅读→引用全链路 |
| ⭐ Stanford STORM | 开源 | 生成式综述大纲 + 写作，GPT Researcher 的 DetailedReport 灵感来源 |
| ⭐ PaperQA2 (FutureHouse) | 开源 | 科学文献 RAG，主打可溯源的文献问答 |
| ⭐ Ai2 ScholarQA / Ai2 OpenScholar | 开源（Allen AI + 华盛顿大学） | 检索增强型学术问答，学术基准常客 |
| ⭐ traceable-research-mcp | MCP | 本地 MCP，跑在 OpenAlex + S2 + Crossref + Unpaywall 上，导出双语的、每条实证论断都链接到 evidence ID 的报告；无需 LLM key；不绕过付费墙，思路和项目需求高度重合 |
| ⭐ `54yyyu/zotero-mcp` (~4k★) | MCP | 把 Zotero 文献库接入任意 MCP agent：讨论论文、生成摘要、分析引用 |
| ⭐ `blazickjp/arxiv-mcp-server` (~2.8k★) | MCP | 任意 MCP agent 里直接检索+抓取 arXiv |
| ⭐ `openags/paper-search-mcp` (~2.1k★) | MCP | 跨 arXiv/PubMed/bioRxiv/medRxiv 多源检索下载 |
| ⭐ `Galaxy-Dawn/claude-scholar` (~4.1k★) | Skill+MCP | 半自动科研助手，集成 Zotero + Obsidian + MCP |
| ⭐ `LeiChaoX/qinyan-academic-skills` | Skill | 沁言学术库，177 个研究 Agent |
| ⭐ `flonat/claude-research` | Skill | 面向 PhD 的完整科研基建，50 个 skills，含 `/bib-validate`、`/pre-submission-report` |
| ⭐ `ai4s-research/ai4s-skills` | Skill | 7 个 AI4S skill：主题探索→文献综述→可运行实验→写作→诚信审计 |
| ⭐ `companion-inc/feynman` | CLI | 开源 AI 研究 agent（基于 alphaXiv），20 个研究 skill |

> 参考仓库：`brycewang-stanford/Auto-Research-Skills` 收录了 3,433 个 skills、87 个仓库（含端到端系统 karpathy/autoresearch、SakanaAI/AI-Scientist-v2、HKUDS/AI-Researcher 等）。注意其 README 自己声明：同名技能存在冲突，需先查 `catalog/collisions.json`，不要全量装载。

---

## 2. 完整科研流程地图（补全初始清单遗漏的环节）

初始版本：`发现 → 综述/证据提取 → 引用核验 → 深度研究`

完整版（12 环，标 ⚠️ 的是初始清单遗漏且删不掉的）：

```
0. 选题与可行性
   ├─ 研究问题框架化（PICO / PEO / SPIDER / FINER）
   ├─ ⚠️ 研究缺口识别 + 新颖性核查      → Undermind / Inciteful / Prophy
   └─ 可行性（数据、样本、方法、算力）

1. ⚠️ 综述协议与注册                    → PROSPERO / OSF / INPLASY + PRISMA-P
2. ⚠️ 检索策略构建                      → 布尔式 + 同义词表 + 多库 + 灰色文献（需可复现、需报告）
3. 文献发现                            → Semantic Scholar / OpenAlex / Undermind / ResearchRabbit / AMiner
4. ⚠️ 去重与文献库管理                  → DOI 对齐 + Zotero / NoteExpress
5. 标题摘要筛选（双人盲筛）              → Rayyan / ASReview / Covidence + Elicit
6. ⚠️ 全文获取（合法 OA 路径）          → Unpaywall / PMC / arXiv / bioRxiv + 机构订阅 + ILL
7. 全文筛选 + 结构化数据提取             → Elicit / Covidence / DistillerSR
8. ⚠️ 质量与偏倚风险评估                → RoB 2 / ROBINS-I / NOS / GRADE（判断本身必须人工）
9. ⚠️ 综合与证据分级                    → 叙述性综合 / Meta 分析 / GRADE Summary of Findings 表
10. 引用核验与诚信检查
    ├─ 引用上下文（支持/反驳）           → Scite
    └─ ⚠️ 撤稿检查 + 统计自洽性          → Retraction Watch / statcheck / GRIM / PubPeer
11. 写作与报告
    ├─ ⚠️ PRISMA 2020 清单 + 流程图     → 27 项 + 流程图，计数必须与筛选记录对得上
    └─ 语言润色与投稿前检查              → Paperpal / Writefull / Trinka
12. ⚠️ 可复现性与持续监测               → 检索记录留痕、数据/代码存档、新论文提醒、更新综述
```

三个最容易翻车的地方（也是 Agent 最能发挥作用的地方）：

1. 第 2 环：AI 工具普遍跳过检索策略，直接给"我搜到 50 篇"。这样的综述无法通过同行评议，因为不可复现。
2. 第 11 环的计数一致性：PRISMA 流程图要求 `识别数 = 去重后数 + 各阶段排除数` 严格对齐。如果筛选过程没落盘，最后是补不出来的，这是必须在工作流设计第一天就埋进去的。
3. 第 10 环：撤稿论文污染 Meta 分析是已证实的系统性问题，不是理论风险。

---

## 3. Agent 工作流设计建议

### 3.1 核心判断：不要做成"10 个工具串成一条全自动流水线"

三条理由：

1. 大部分好用的工具没有 API（Connected Papers、ResearchRabbit、Litmaps、Consensus、秘塔），只能人工或浏览器自动化，做不了稳定的自动化节点。
2. 合规要求 AI 不得替代判断。Cochrane、Campbell Collaboration、JBI、Collaboration for Environmental Evidence 的联合立场声明（2025-10-31）背书 RAISE 建议：AI 只能辅助，任何做出或提示资格判定、数据提取、偏倚判断的 AI 使用都必须披露系统名 + 版本 + 角色；双人独立筛选不可被取代。
3. 正确形态是"可溯源状态机 + 人审门"，而不是"一键生成综述"。

### 3.2 三层架构

```
┌──────────────────────────────────────────────────────────┐
│ 编排层 Orchestrator                                       │
│  · 状态机（12 环，每环有明确输入/输出契约）                 │
│  · 人审门（Gate）：第 1、5、8 环必须人工确认才能往下走      │
│  · 全链路日志 → 直接喂给 PRISMA 流程图                     │
│  · 模式切换：正式系统综述模式 / 快速叙述综述模式            │
├──────────────────────────────────────────────────────────┤
│ Agent 层（每个 stage 一个专职 subagent）                   │
│  SearchPlanner   → 把研究问题拆成布尔检索式 + 同义词表      │
│  Discoverer      → 多库并行检索 + DOI 对齐 + 去重           │
│  Screener        → 标题摘要预筛（排序，不拍板）             │
│  Extractor       → 结构化提取 PICO 字段（批量、便宜模型）   │
│  RiskAssessor    → 偏倚评估表草案（必须人审）               │
│  CitationVerifier→ Scite 上下文 + 撤稿批量查 + statcheck    │
│  Synthesizer     → 证据分级 + 论证搭建（贵模型）            │
│  IntegrityAuditor→ 反向质检：每条论断是否真有出处           │
├──────────────────────────────────────────────────────────┤
│ 工具层 Tool / MCP                                          │
│  数据地基（必须有）：OpenAlex · Semantic Scholar API ·      │
│    Crossref · Unpaywall · PubMed E-utilities · arXiv       │
│  MCP（可直接接）：scite-mcp · zotero-mcp · arxiv-mcp ·      │
│    paper-search-mcp · traceable-research-mcp               │
│  半自动（导出/浏览器）：Connected Papers · ResearchRabbit ·  │
│    Litmaps · Consensus · 秘塔 · 玻尔                        │
│  开源骨架（二次开发）：PaperQA2 · GPT Researcher · STORM    │
└──────────────────────────────────────────────────────────┘
```

### 3.3 六条设计硬约束

| # | 约束 | 原因 |
|---|---|---|
| 1 | 每个论断必须回链到 evidence_id（DOI + 页码/段落），拿不出出处的句子不许进输出 | 最危险的是"流畅但无引用的通用 LLM"，会自信编造不存在的参考文献。`traceable-research-mcp` 就是这套思路，可直接借鉴 |
| 2 | 检索式、去重数、各阶段排除数全部落盘 | 否则 PRISMA 流程图和 27 项清单后补不出来 |
| 3 | AI 只做排序与预标注，资格判定由人确认 | RAISE 建议 + 双人盲筛要求 |
| 4 | 分级两种模式：正式系统综述 / 快速叙述综述 | 避免用轻流程冒充 PRISMA 综述，也避免用小课题套重流程 |
| 5 | 全文只走合法 OA 路径（Unpaywall / PMC / arXiv / bioRxiv + 机构订阅） | 不绕过付费墙和访问控制 |
| 6 | 模型分级用：便宜模型做提取与筛选排序，贵模型做综合与论证 | 提取是吞吐量问题，综合是质量问题 |

### 3.4 MVP 建议路径

不要一上来就实现全部 12 环。建议：

- Step 1（1～2 周）：只做第 2～7 环，检索式构建 → 多库检索 → 去重 → 筛选 → 提取。基于 OpenAlex + S2 + Crossref + Unpaywall 四个免费 API，配 PaperQA2 或 traceable-research-mcp 做骨架。产出物：一个带 evidence_id 的证据表。
- Step 2：加第 10 环诚信层（Scite MCP + 撤稿批量查 + statcheck）。这层最能体现差异化，因为市面上几乎没人把它做进流水线。
- Step 3：加第 11 环，让状态机自动吐 PRISMA 流程图。
- Step 4：第 0、1、8、9 环最后做，因为这些依赖人的学术判断，AI 增益最小、风险最大。

---

## 4. 需要使用者决定的三个设计问题

1. 课题领域：偏生物医学（PRISMA/Cochrane 规范成熟、工具链最全），还是偏 AI/CS（arXiv 优先、更新快、几乎不做正式系统综述）？这决定流程严格度和数据源权重。
2. 综述类型：正式系统综述（要注册、要 PRISMA、要双人筛选），还是快速叙述综述（只要可溯源 + 不幻觉）？
3. 部署形态：本地优先（数据不外流，走 OpenAlex/arXiv/Zotero 本地库），还是云端 + 商业工具（Consensus/Elicit/Scite，需信用卡 + 海外网络）？国内网络与支付环境会显著改变选型，国产替代（AMiner / 玻尔 / 沁言 / 秘塔）在可用性上明显占优，但在 PRISMA 规范性上弱。

---

## 附：调研中观察到的两个"信息陷阱"

1. 国内搜索结果里"科研 AI 工具"软文占比极高。"掌桥科研 AI""笔捷 AI""okbiye"这类，本质是论文代写服务的包装，搜索结果往往自称"2026 最新实测榜单 TOP1"，且多个域名共用同一套文案。它们不是科研工具，不应用作流程节点。
2. 厂商自荐的对比文章要打折看。例如 Ponder 的《Best AI Tools for Literature Review》（2026-07）内容质量不错、五阶段工作流划分很准，但推荐表里把 Ponder 自己列为"综合阶段"答案；SciSpace 的 200 条查询基准白皮书同理。把它们的流程划分当参考，把它们的排名当广告。
