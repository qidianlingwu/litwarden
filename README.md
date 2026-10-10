# litwarden

本地优先的科研文献工作流：查找 → 筛选 → 初读提炼 → 管理

[![CI](https://github.com/qidianlingwu/litwarden/actions/workflows/ci.yml/badge.svg)](https://github.com/qidianlingwu/litwarden/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](requirements.txt)
[![License](https://img.shields.io/badge/license-MIT-green)](#许可证)

> 第一次用、不太熟悉 GitHub？看 [新手上路：没用过 GitHub 也能跑起来](docs/getting-started.md)
> 不用注册账号，不用编程基础，跟着做十分钟就能跑出第一份结果。

<details>
<summary><b>English overview</b></summary>

`litwarden` is a local-first literature workflow for researchers. It searches
several open bibliographic sources (Europe PMC, PubMed, arXiv, Crossref),
de-duplicates and ranks the results, writes the selected papers into a local
Zotero library, then reads back the AI-generated reading notes to produce a
structured extraction table.

- Zero third-party dependencies: implemented entirely with the Python standard library
- Local-first: data never leaves your machine; no account, no cloud service
- Traceable: every citation count records which source it came from and when it was fetched, so screening results are reproducible
- Domain-neutral: works for any field; the default source mix is tuned for life sciences
- Not a review generator: it handles the mechanical work (search, dedupe, screen, track state). Academic judgement stays with you.

Requires Python 3.11+. CLI output and documentation are currently in Chinese.

```bash
git clone https://github.com/qidianlingwu/litwarden.git && cd litwarden
cp config.example.toml config.toml        # 至少填上你的邮箱
python3 -m litwarden doctor
python3 -m litwarden search -q "spatial transcriptomics deconvolution" --year-from 2023
```

</details>

---

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | 3.11+ | 用到标准库 `tomllib`，3.11 才进标准库 |
| 第三方依赖 | 零 | 全部标准库实现，无需 `pip install` |
| 操作系统 | macOS / Linux | 入口脚本 `bin/litwarden` 是 POSIX shell |
| Zotero | 10+ | 写接口需要 10+，只读则更低版本即可 |
| Zotero-AI-Butler | 可选 | 仅「初读提炼」环节需要，不装则跳过该阶段 |

---

## 安装

三种方式，按需选一种。

### 方式一：pip 安装（推荐）

```bash
git clone https://github.com/qidianlingwu/litwarden.git
cd litwarden
pip install .
litwarden --version
```

装完在任何目录都能直接敲 `litwarden`。配置默认从当前目录向上查找 `config.toml`，
也可以用 `--config /path/to/config.toml` 显式指定。

### 方式二：不安装，软链到 PATH

```bash
git clone https://github.com/qidianlingwu/litwarden.git
ln -sf "$(pwd)/litwarden/bin/litwarden" ~/.local/bin/litwarden   # 或 /usr/local/bin
litwarden --version
```

`bin/litwarden` 用 `os.path.realpath` 解析自身真实路径来定位项目根目录，
因此通过软链调用也不会算错目录（常见的坑：用 shell 的 `dirname "$0"`，
在软链场景下会得到软链所在目录而非项目目录）。

### 方式三：什么都不装

```bash
cd litwarden
python3 -m litwarden doctor
```

`python3 -m litwarden` 必须在项目目录内执行，否则报 `No module named litwarden`。

---

## 快速开始

```bash
# 1. 配置：至少把 email 改成一个真实邮箱
#    Crossref / Unpaywall / NCBI 都要求用它标识请求方，
#    Unpaywall 会直接拒绝 example.com 这类占位地址
cp config.example.toml config.toml

# 2. 环境体检：检查数据源连通性、HTTPS 证书、Zotero、等级表
litwarden doctor

# 3. 检索：默认 4 个数据源，都不需要 API key
litwarden search -q "spatial transcriptomics deconvolution" --year-from 2023

# 4. 筛选：期刊等级 + 年均引用 + 关键词匹配度下限
litwarden filter --run 1 --rank sjr:Q1 \
    --min-cites-per-year 2 --min-keyword 0.3 --sort rank \
    --show-reasons --export xlsx,csv,md,bib
```

导出文件写在 `data/exports/` 下。想一条命令跑完全流程，见下面的「工作流串跑」。

### 导出格式

| 格式 | 用途 |
|---|---|
| xlsx | 文献调研表格。14 列（序号/标题/作者/期刊/年份/引用数/年均引用/期刊分区/开放获取/DOI/链接/匹配度/来源/摘要），表头加粗、冻结首行、带自动筛选，可直接用 Excel / WPS 打开 |
| csv | 同上数据，给脚本或统计软件用（UTF-8 BOM，Excel 不乱码） |
| md | 适合贴进笔记或 PR 描述 |
| bib | 直接拖进 Zotero / Overleaf |
| json | 含完整字段与 extra，给下游程序用 |

---

## 命令参考

| 命令 | 作用 |
|---|---|
| `doctor` | 环境体检：配置、数据源、HTTPS 证书、期刊等级表、Zotero 本地 API |
| `doctor --online` | 额外发起真实请求测试每个源的连通性 |
| `sources` | 查看各数据源启用状态与是否需要 key |
| `search` | 多源并行检索 → DOI 归一化去重 → 存候选池并生成一次 run |
| `filter` | 对候选池按多维度筛选排序，写回决策并导出 |
| `runs` | 列出历史运行 |
| `show` | 查看某次运行的论文（可按 include/exclude 过滤） |
| `rank import sjr` | 导入期刊等级表（支持 sjr / ccf / cas / jcr） |
| `rank stats --probe "期刊名"` | 查看等级表规模 / 试查某期刊 |

### search 常用参数

| 参数 | 说明 |
|---|---|
| `-q/--query` | 关键词（自然语言短语即可，各源会各自转成自己的语法） |
| `--author` / `--journal` | 作者 / 期刊 |
| `--year-from` / `--year-to` | 年份区间 |
| `--limit` | 每个源取回的条数 |
| `--sort relevance\|cited\|date` | 排序策略。`cited` 会带回经典文献，但需要配合 `--min-keyword` 使用 |
| `--source` | 只用指定源，逗号分隔 |
| `--preprints-only` | 只要预印本 |
| `--oa-enrich` | 用 Unpaywall 补全合法开放获取全文链接（较慢，需真实邮箱） |
| `--export xlsx,csv,tsv,md,bib,json` | 直接导出。`xlsx` 是可直接用 Excel/WPS 打开的文献表格 |

### filter 常用参数

> 决策语义：`filter` 会为本次运行的全部候选写入 include / exclude 决策，
> 而不是只追加结果。也就是说同一运行的决策始终以最近一次 filter 为准：
> 先按 Q1 筛出 8 篇，再改成不设等级门槛重筛，前一次的 include 标记会被覆盖。
> 若已经按某批结果写过 Zotero，重筛后请重新确认再导入。

| 参数 | 说明 |
|---|---|
| `--run` | 运行编号，默认最近一次 |
| `--min-cites` / `--min-cites-per-year` | 引用量门槛。推荐用年均，否则候选池全是老论文 |
| `--rank sjr:Q2` | 期刊等级门槛，可重复（`--rank sjr:Q1 --rank ccf:B`） |
| `--rank-allow-unknown` | 等级表未收录的论文也保留。等级表不完整时务必加上，否则结果会被大幅清空 |
| `--min-keyword 0.3` | 关键词匹配度下限。按引用量排序时强烈建议加 |
| `--exclude-corrections` | 剔除「Correction to:」这类更正/勘误记录（原论文的附属条目，属噪声） |
| `--title-must deconvolution` | 仅标题必须包含。区分「方法论文」与「应用论文」最有效的旋钮，见下方说明 |
| `--must` / `--exclude` | 标题摘要必须包含 / 必须不含（逗号分隔） |
| `--oa` / `--require-abstract` / `--require-doi` | 硬性字段要求 |
| `--only-preprints` / `--exclude-preprints` | 预印本过滤 |
| `--sort rank\|cites\|cites_per_year\|year\|keyword` | 排序，默认 `rank` |
| `--show-reasons` | 显示剔除原因统计，用于调参 |

`filter` 会打印期刊等级覆盖率。低于 60% 时给出警告：在 `--rank` 条件下
未收录期刊的论文会被剔除，覆盖率低会让结果"看起来没东西"。

### 怎么滤掉「用了这个方法」而不是「提出这个方法」的论文

这是文献筛选里最容易被忽略的一类噪声。BM25 只看词频，无法判断一篇论文是
提出新方法，还是拿现成方法去分析某个生物学问题。搜 "spatial transcriptomics deconvolution"，
会出现大量「Spatial transcriptomics reveals that ...」这类应用论文。

最有效的判别信号是标题：

```bash
# "deconvolution" 出现在标题里，基本就说明它是一篇方法论文
python3 -m litwarden filter --run <N> --title-must deconvolution
```

实测效果：121 篇候选，剔除 68 篇标题不含该方法词的，剩下的全是方法论文。

---

## 数据源

| 源 | 需 key | 定位 |
|---|:--:|---|
| europepmc | 否 | 生信主力。一个接口同时覆盖 PubMed 文献与 bioRxiv/medRxiv 预印本，直接返回被引数与 OA 标记 |
| pubmed | 否 | NCBI 官方，MeSH 检索能力强 |
| arxiv | 否 | 预印本。生命科学用 q-bio.*，神经科学 / 信号处理用 eess.SP / q-bio.NC |
| crossref | 否 | DOI 与期刊元数据校准 |
| openalex | 是 | 被引数 + 期刊信息。2026 年起必须带自己的 key |
| semanticscholar | 是 | 被引数 + TLDR + OA 直链 |

只有 OpenAlex 和 Semantic Scholar 需要 key，且都是免费申请。四个无 key 源已经能覆盖生信场景的绝大部分需求。

---

## 期刊等级

没有任何免费 API 能直接给出 JCR 分区或中科院分区，它们是订阅版权数据。所以做成可插拔的多体系：

| 体系 | 数据来源 | 状态 |
|---|---|---|
| `sjr` | Scimago SJR 官方免费 CSV（含 Q1-Q4） | 内置支持 |
| `ccf` | 中国计算机学会推荐目录（A/B/C） | 内置支持 |
| `cas` | 中科院分区表 | 版权数据，需你自行导出 CSV 导入，本项目不内置不分发 |
| `jcr` | JCR 分区 | 订阅数据，不内置 |

导入方式：把文件放到 `data/journal_rank/`，文件名包含体系名（如 `sjr_2024.csv`），然后

```bash
python3 -m litwarden rank import sjr
```

CSV/JSON 均可，列名自动探测（中英文都认）。SJR 官方导出用分号分隔且表头拆两行，已处理。

- SJR 下载：<https://www.scimagojr.com/journalrank.php>（页面底部 Export）
- 一条记录会同时写 ISSN 和刊名两种匹配键，查询时 ISSN 优先、刊名兜底（预印本通常没 ISSN）

---

## 目录结构

```
文献Agent工作流/
├── config.toml              # 实际配置
├── config.example.toml      # 带注释的模板
├── requirements.txt         # 空（零依赖说明）
├── docs/                    # 调研报告与架构设计
├── litwarden/
│   ├── cli.py               # 命令行入口
│   ├── config.py            # 配置加载与校验
│   ├── models.py            # Paper 模型、标识归一化、跨源去重
│   ├── http.py              # HTTP 客户端（限速 / 重试 / TLS 降级）
│   ├── store.py             # SQLite 存储
│   ├── finder.py            # 多源并行检索编排
│   ├── filterer.py          # BM25 打分 + 过滤 + 排序
│   ├── journalrank.py       # 期刊等级可插拔查表
│   ├── exporters.py         # CSV/TSV/MD/BibTeX/JSON 导出
│   └── adapters/            # 各数据源适配器
└── data/                    # SQLite / 等级表 / 导出文件（本地数据）
```

---

## 已知问题与设计权衡

### 1. TLS 1.3 在部分网络环境下成功率很低（已自动处理）

在特定网络环境下实测（Python 3.14 + OpenSSL 3.5）：走 TLS 1.3 时对
NCBI / Crossref / EBI 的成功率只有约 1/3，报 `SSL: UNEXPECTED_EOF_WHILE_READING`；
强制 TLS 1.2 后升到 8/12 以上。而同一时刻 `curl` 走 TLS 1.3 基本正常，
说明问题出在 Python 侧的 TLS 1.3 握手，被中间设备干扰
（很可能与 OpenSSL 3.5 新增的混合密钥交换令 ClientHello 变大有关）。

默认策略 `tls = "auto"`：第 1 次用 TLS 1.3，失败重试时自动降级到 TLS 1.2，
并把降级结果按域名记住，该域名之后的请求直接走 TLS 1.2。无需手动干预。

> 按域名记住这一点对性能影响很大：不加它，同一次检索耗时 171 秒；
> 加上后是 4 秒。原因是原先每个请求都要先撞一次 TLS 1.3 失败、等退避、再重试。

### 2. HTTPS 证书（已自动处理）

macOS 上用 python.org 安装包装的 Python 默认不含 CA 根证书，所有 HTTPS 会报
`CERTIFICATE_VERIFY_FAILED`。程序会自动兜底加载 `/etc/ssl/cert.pem`。

若仍失败，执行官方的 `Install Certificates.command` 可根治。

### 3. Europe PMC 默认按日期排，不是相关性（已处理）

实测确认：Europe PMC 不带 `sort` 参数时返回的是最新论文，一个领域搜下去只能看到当年新文章，
经典文献完全不出现。因此相关性模式下会自动并取「最新」和「CITED desc（经典）」两批再合并。

### 4. 按引用量排序会把不相关的高被引论文带进来

Crossref / Europe PMC 的 cited 排序是全局的，对模糊检索式会严重漂移：搜 scRNA-seq 会返回
Cell 上的外显子组测序研究。务必配合 `--min-keyword 0.25` 这类相关性下限使用。

### 5. 引用量必须记录来源

Crossref 的 `is-referenced-by-count` 与 OpenAlex 的 `cited_by_count` 差异很大。数据库里每条记录都存了
`cited_by_source` 和 `cited_by_fetched_at`，否则筛选结果无法复现。合并时取最大值。

### 6. PubMed 不返回被引次数

这是有意的字段缺口，由 Europe PMC / Crossref / OpenAlex 在合并阶段补上。所以一个源失败不会导致
引用量全空，这也是「单源失败不中断整体」设计的价值。

---

## 文献管理：写入 Zotero

把筛选结果一键写进 Zotero，并在 Zotero 里管理阅读状态。

```bash
# 体检：确认本地 API 可用、写授权是否已缓存
python3 -m litwarden zotero check

# 列出本机 Zotero 里的集合
python3 -m litwarden zotero collections

# 导入：把某次运行里「已纳入」的论文写进 Zotero
python3 -m litwarden zotero import --run 11 --decision include \
    --collection "空间转录组" --status 未读

# 顺便抓取开放获取 PDF 作为附件（只走合法 OA 链接）
python3 -m litwarden zotero import --run 11 --oa --attach-pdf

# 阅读状态
python3 -m litwarden zotero status
python3 -m litwarden zotero mark 在读 --run 11 --top 5

# 清理上传失败留下的空壳附件
python3 -m litwarden zotero cleanup-empty --yes
```

### Zotero 参数

| 参数 | 说明 |
|---|---|
| `--run N` / `--decision` / `--top N` / `--oa` | 选哪些论文（通常用 `--decision include` 取筛过的） |
| `--collection "名字"` | 目标集合，不存在会自动创建。默认 `litwarden 导入` |
| `--status` | 阅读状态，默认 `未读` |
| `--attach-pdf` | 抓取 OA PDF 并上传。对已导入的论文也生效，可当"补附件"用 |
| `--dry-run` | 只演练不写入 |
| `--reset-key` | 清除缓存的写授权 key（配合 `check` 用） |

### 三个必须知道的设计点

1. 阅读状态用标签，不用集合。`wf:未读 / wf:在读 / wf:已初读 / wf:已精读`（前缀可在 `config.toml` 的 `[butler]` 改）。理由：一篇论文可以属于多个主题集合，但阅读状态是互斥的单一状态。标签在 Zotero 里也能直接筛选。改状态时会替换旧的 `wf:` 状态标签，不会累积。

2. 去重是双保险。每条写入的条目 `extra` 字段里存了 `litwarden-key: <内部键>`，同时比对 DOI。所以重复执行 `import` 不会产生重复条目，实测重跑会全部报"已在 Zotero 中"。

3. 写授权需要你点一次弹窗。Zotero 10+ 的写接口需要本地 API key。首次写入时 Zotero 会弹窗，务必点「Always Allow」（永久授权）。如果点的是「Allow」（一次性），每次写入都会重新弹窗，很快会撞上 Zotero 每分钟 5 次的弹窗限流（HTTP 429）。

授权 key 缓存在本地 SQLite（`data/litwarden.sqlite3` 的 `kv_cache` 表），不会改你的 `config.toml`。要重置：`litwarden zotero check --reset-key`。

### 前置条件

1. Zotero 10+（读接口更低版本也行，写接口需要 10+）
2. 设置 → Advanced → 勾选
   `Allow other applications on this computer to communicate with Zotero`
3. Zotero 必须在运行。注意：进程在 ≠ 本地 API 已就绪，
   刚启动后要等几秒到几十秒才开始监听 `23119` 端口。

---

## 初读提炼：接入 Zotero-AI-Butler

把 Butler 自动精读的笔记回读出来，汇总成一张
「每篇讲了什么方法 / 用了什么数据 / 结论是什么」的对照表。

```bash
# 看进度：多少篇有笔记、多少篇还在排队、多少篇缺 PDF
litwarden digest status

# 回读笔记（纯读请求，可反复执行，已拉过的不会重复拉）
litwarden digest fetch

# 导出汇总表（xlsx + csv + md，默认全出）
litwarden digest export --run 11 --decision include --imported-only

# 看某一篇的笔记原文（按内部键或 DOI）
litwarden digest show --run 11 --key "10.1038/s42003-024-06349-5"
```

导出的三份文件各有用途：

| 格式 | 用途 |
|---|---|
| md | 最适合直接读。每篇一节，按「一句话总结 / 方法 / 数据 / 结论 / 局限」展开 |
| xlsx | 横向对照多篇。13 列，冻结表头 + 自动筛选 |
| csv | 给脚本或统计用 |

### 工作流全貌

```
litwarden search        检索
      ↓
litwarden filter        筛选  →  导出文献调研表（xlsx）
      ↓
litwarden zotero import 写入 Zotero + 抓 OA PDF
      ↓
   （AI Butler 自动扫描新文献 → 自动精读 → 生成 Markdown 笔记）
      ↓
litwarden digest fetch  回读笔记
      ↓
litwarden digest export 导出初读提炼汇总表  ← 最终产物
```

中间那一步是 Butler 自己完成的，需要你先在
AI Butler 仪表盘 → 界面设置 里勾选「自动扫描新文献」（一次设置）。

### 笔记解析器怎么工作

Zotero 的笔记字段是 HTML，而 Butler 生成的是 Markdown。它可能以原样 Markdown
存在 HTML 里，也可能被转成了 HTML 标签。Butler 还会把「文献表格」类型的笔记写成
两列 Markdown 表格，字段全在表格里，一个小节标题都没有。所以解析器对三种形态都容忍：

1. 先把 `<h1>`~`<h6>` 转成 Markdown 的 `#` 标题，再统一按行解析
2. 用一个同义词表把各种标题归并到规范字段，实测能处理：
   - `## 研究方法` / `## 技术方案` / `## Methods` → 方法
   - `## 一句话总结` / `## 📌 核心结论` / `## Summary` → 总结
   - `## 数据集` / `## 实验数据与设置` / `## Datasets` → 数据
3. 没有标题的笔记按 Markdown 表格解析，用 `| 维度 | 内容 |` 这样的键名去归类
4. 归不了类的小节不丢弃，原样保留在「其他小节」里（导出的 md 中也会带上），
   避免因为标题写法特殊而丢信息

同一篇文献可能有多篇笔记（Butler 会分别生成表格与长文两种），解析结果按字段合并，
表格里的值优先。

### 前置条件

- 需要 PDF：没有 PDF 的条目 Butler 无法精读。用 `litwarden zotero audit` 查哪些缺
- Butler 已开启「自动扫描新文献」
- Butler 的 PDF 处理方式必须与所用模型的能力匹配（见下）

#### PDF 处理方式：文字提取 vs Base64 多模态

Butler 读 PDF 有两种方式，选错会直接报错：

| 方式 | 原理 | 能用哪些模型 |
|---|---|---|
| 文字提取 | 先用 pdf.js 把 PDF 转成纯文本再发给模型 | 任意模型，含 DeepSeek 等纯文本模型 |
| Base64 多模态上传 | 把整个 PDF 编码后直接发给模型 | 必须支持 PDF 输入的多模态模型，如 Gemini |

典型报错：用 DeepSeek + Base64 多模态上传时，会得到

```
.messages[1]: file must have a file_id or file_data
```

原因是 DeepSeek 的 Files API 只接受 JPEG / PNG / GIF / WebP 图片，不接受 PDF，
且 file 类型的输入目前绑定 `deepseek-flash` 模型，而 chat 接口的 `deepseek-chat`
并不接受 file 块。

改法：Butler → AI 管家仪表盘 → 模型设置 → 把 PDF 上传方式改为「文字提取」。

代价是丢失版式、图表与公式的视觉信息。方法学论文的细节大多在正文文字里，
影响有限；图表或公式密集的论文会损失一些。若确实需要多模态，
应换用原生支持 PDF 的模型（如 Gemini），而不是让 DeepSeek 承担这件事。

#### 导入与精读之间的时序（一个容易踩的坑）

Zotero 的 API 要求父条目先存在才能上传子附件 PDF，所以「条目已入库、
但 PDF 还没到」的窗口无法完全消除。而 Butler 的「自动扫描新文献」是按
新条目触发的，它可能正好落进这个窗口，于是以

```
PDF 文本提取失败：未找到 PDF 文件路径
```

失败。更麻烦的是 Butler 对这类错误带 `suppressTaskRetry=true`，
它不会自己重试，这篇论文就一直不会有笔记。

litwarden 的对策是把耗时的「下载 PDF」挪到创建条目之前（见 `librarian.prefetch_pdfs`），
让窗口只剩「上传」本身。实测窗口从 17~45 秒压到 7~9 秒，但仍有残留。

如果你在 Butler 的任务队列里看到上面这条错误：

- 在队列里手动重试该任务即可，此时 PDF 已经在库里，会正常精读
  （这也是为什么 `digest status` 会把它算作「待精读」而不是「缺 PDF」）
- 批量导入时用 `--top` 控制规模，窗口会随批量增大而变长
- `litwarden digest status` 与 `litwarden zotero audit` 可以随时核对真实状态

---

## 工作流串跑：Recipe 机制

把整套参数存成一个人可读、可手改的 TOML，之后一条命令重跑。

```bash
litwarden recipe init                          # 生成配方模板（一次即可）
litwarden recipe list                          # 列出所有配方
litwarden recipe show spatial-transcriptomics            # 查看某个配方的完整内容

litwarden run spatial-transcriptomics                     # 跑完整流程（6 个阶段）
litwarden run spatial-transcriptomics --dry-run           # 只打印会执行什么
litwarden run spatial-transcriptomics --list-stages       # 看各阶段启用状态
```

### 配方文件长什么样

放在项目根的 `recipes/<名字>.toml`。故意用 TOML 而不是存在数据库里，
方便你直接编辑、对比、抄给别人。每个字段都有注释说明作用与取舍。

```toml
name = "spatial-transcriptomics"

[search]
query = "spatial transcriptomics deconvolution"
year_from = 2023
limit = 40
sort = "relevance"        # 欧洲 PMC 会同时取「最新」与「高被引」两路合并

[filter]
rank = { sjr = "Q1" }
rank_allow_unknown = true # 等级表收录不全时放宽
title_must = ["deconvolution"]   # ★ 区分「提出方法」与「使用方法」最有效的旋钮
exclude_corrections = true
min_keyword = 0.3
sort = "rank"

[zotero]
enable = true
collection = "空间转录组"
status = "未读"
decision = "include"
attach_pdf = false

[digest]
enable = true
imported_only = true
```

### 六个阶段

| 阶段 | 做什么 | 产出 |
|---|---|---|
| `search` | 多源检索 + 去重 + 引用量补全 | 一次 run |
| `filter` | 按配方条件筛选排序 | include/exclude 决策 |
| `export` | 导出文献调研表 | xlsx / csv / md / bib |
| `zotero` | 写入 Zotero 集合（+ 可选抓 OA PDF） | Zotero 条目 |
| `digest-fetch` | 回读 Butler 精读笔记 | 本地笔记缓存 |
| `digest-export` | 导出初读提炼汇总表 | xlsx / csv / md |

### 只跑到「导入」为止（自读模式）

不是每次都需要 AI 精读。如果你的目的只是筛出该读的、导进 Zotero 自己读，跑前四个阶段就够：

```bash
litwarden run my-topic --only search,filter,export,zotero

# 等价写法
litwarden run my-topic --skip digest-fetch,digest-export
```

拿到的是候选池、一份文献调研表，以及带阅读状态标签的 Zotero 条目（可选附上 PDF）。
想固定成默认行为，就在配方里关掉：

```toml
[digest]
enable = false
```

这里有个容易误解的地方：跳过 litwarden 的后两阶段，并不会阻止 Butler 去生成笔记。
Butler 的自动扫描是独立运行的，只要条目带 PDF、它的「自动扫描新文献」又开着，
它照样会在后台调大模型写总结。要彻底不让 AI 介入，去 Butler 仪表盘关掉
「自动扫描新文献」，或者只关掉「自动扫描时生成 AI 总结」这一项。
如果只是暂时不需要，放着也无妨，笔记会留在 Zotero 里，你不执行 `digest fetch`
就不会进提炼表。

跳过也不等于放弃。候选池和 Zotero 条目都在，哪天想看 AI 总结随时补：

```bash
litwarden digest fetch && litwarden digest export
```

自读时常用的几条：

```bash
litwarden zotero mark 在读 --run 11 --top 5   # 批量改阅读状态，写 wf: 标签
litwarden zotero status                       # 看各状态的分布
litwarden zotero audit                        # 体检哪些条目缺 PDF 附件
```

### 断点续跑

任何一段失败都不会让整个运行崩掉，也不会甩 traceback。失败的阶段会告诉你剩下哪些：

```bash
# 只跑某几段
litwarden run spatial-transcriptomics --only filter,export

# 跳过某几段
litwarden run spatial-transcriptomics --skip zotero,digest-fetch

# 复用已有的检索结果，不重新检索
litwarden run spatial-transcriptomics --run 13 --only filter,export
```

`run_id` 存在本地 SQLite 里（键 `recipe:<名字>:last_run_id`），不回写你的配方文件，
避免污染你手写的注释。

> 两个 scoping 差异（容易看混）：
> - `digest-fetch` 是全局的，拉取所有已导入且有 PDF 的文献的笔记
> - `digest-export` 是按 run 限定的，只导出本次运行中 `decision=include` 的论文
>
> 所以可能看到「fetch 说 30 篇待精读，export 说 0/15 篇有笔记」，这是正常的。

---

## Web 界面

不敲命令，在浏览器里点选操作。

```bash
litwarden web                # 启动后自动打开浏览器（默认 http://127.0.0.1:8765/）
litwarden web --port 8800    # 换端口
litwarden web --no-open      # 不自动开浏览器
```

六个标签页覆盖工作流各环节，外加一个统一的产物出口：

| 标签页 | 做什么 |
|---|---|
| 检索 | 填关键词、选数据源，点一下就开始；带实时日志与进度条 |
| 论文表 | 左侧筛选条件（等级/引用/匹配度/标题词）；表格带「摘要速览」列（首句 + 结论，有结论段就用结论段，并自动跳过数据可用性声明），可在导入之前先读个大概再决定勾选谁；勾选后导出或写入 Zotero |
| Zotero | 本地 API 状态、目标集合、阅读状态、批量体检 PDF 附件、只补 PDF |
| 提炼 | 进度条、回读笔记、汇总表；点标题看笔记解析详情与原文 |
| 导出文件 | 列出 `data/exports/` 下全部产物，按类型/文件名过滤，点名字即下载 |
| 配方 | 列出配方、一键运行、查看配方内容、单跑某阶段 |

产物去哪找：任何导出动作（论文表「导出选中」、提炼「导出汇总表」、配方运行）完成后，
文件清单都会就地显示在当前页并给出下载按钮；历史产物统一在「导出文件」页查看。
底层接口是 `GET /api/exports`（列表）与 `GET /api/download?name=...`（下载）。

> 早先版本有个 bug：文件清单被渲染到了固定的 Zotero 标签页容器里，
> 在「论文表」页点导出后本页看不到任何东西。现已改为每个标签页各有自己的结果容器。

### 技术选择

- 用标准库 `http.server`，没有引入 Flask/FastAPI，与 M1 定下的零依赖原则一致
- 前端是单文件、无框架、无 CDN，可以完全离线使用
- 只绑定 `127.0.0.1`，不对外暴露（本地工具，没有鉴权机制）
- 耗时操作（检索、写 Zotero、补 PDF）在后台线程跑并给前端轮询进度；一次检索可能几十秒，同步阻塞会让界面像卡死

### 两个踩过的坑

1. SQLite 连接是线程绑定的。`ThreadingHTTPServer` 每个请求都在新线程里处理，共用一个 `Store` 实例会报 `SQLite objects created in a thread can only be used in that same thread`。已改成每线程一份独立连接。注意后台任务的 `store` 必须在任务线程里取，提前在请求线程取好再传进去会踩同样的坑。

2. 连接复用会让响应错位。响应体可能上百 KB（论文列表），复用连接一旦长度不一致，客户端会读到错位内容，表现为 JSON 解析报 `Invalid control character`。已显式加 `Connection: close`，本地工具不值得为省一次握手冒这个风险。

---

## 功能范围

| 环节 | 实现方式 |
|---|---|
| ① 文献查找 | 多源并行检索 → DOI/PMID/arXiv 交叉去重 → 引用量与 OA 链接补全 |
| ② 文献筛选 | 年份、引用量、年均引用、期刊等级、关键词匹配度（BM25）多条件组合与排序 |
| ③ 初读提炼 | 投喂 PDF 给 Zotero-AI-Butler 自动精读 → 回读笔记 → 解析成结构化对照表 |
| ④ 文献管理 | 写入 Zotero 条目 + OA PDF 附件 + 阅读状态标签（`wf:` 前缀）+ 附件体检 |
| ⑤ 工作流串跑 | Recipe 机制：一条命令跑完六个阶段，支持断点续跑与单段重跑 |

命令行与 Web 界面共享同一套底层逻辑，两种方式可混用。

### 两种用法

命令行（适合批量、可脚本化）：
```bash
# 先冒烟测试：只导入 2 篇，几分钟走完六个阶段，确认环境配好了、链路是通的
litwarden run smoke-test

# 再换课题：复制模板改一改
cp "recipes/recipe-template.toml" "recipes/my-topic.toml"
# 编辑 [search] query 与 [filter] title_must
litwarden run my-topic
```

Web 界面（适合边看边调）：
```bash
litwarden web
```

六个阶段全跑完约 10 秒到 1 分钟（取决于网络；Zotero 存在时更快）。
产物全部落在 `data/exports/`。

### 已知限制（数据源层面，非代码问题）

- PDF 抓取受数据源限制。2026 年新收录的论文，Europe PMC 往往只有全文 XML，出版商与 NIH 都没提供可程序化下载的 PDF（实测 pmc.ncbi 返回 200 但不是 PDF）。这类论文需要人工通过机构 VPN 取 PDF，`litwarden zotero attach-pdf` 会导出 `未下载到PDF_需手动获取.csv`，里面含 DOI 与 EuropePMC 全文 XML 链接。
- arXiv 预印本没有引用量。Europe PMC 不索引 arXiv，这是数据源本身的限制。

---

## 测试

```bash
python3 -m unittest discover -s tests -v
```

157 个单元测试，全部基于标准库 `unittest`（不引入 pytest），不联网、不触碰真实数据库，
可在完全离线的环境运行。

| 文件 | 覆盖内容 |
|---|---|
| `tests/test_normalize.py` | DOI / PMID / arXiv / ISSN 归一化、`Paper.key` 稳定性、两轮去重与跨源合并 |
| `tests/test_filterer.py` | BM25 打分与长度归一化、全部过滤条件、排序、非法参数处理 |
| `tests/test_journalrank.py` | 各体系分区归一化、CSV 解析（分号分隔 + 表头拆两行）、同一期刊多行取最优分区 |
| `tests/test_exporters.py` | BibTeX key 生成与去重、xlsx 结构合法性、控制字符与 XML 元字符转义 |
| `tests/test_adapters.py` | 标签剥离与实体反转义、检索式构造、适配器解析、配错 key 时的错误提示 |
| `tests/test_security_and_robustness.py` | Host 头校验（防 DNS-rebinding）、CSV 公式注入、并发限速、标识遮蔽 |

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/getting-started.md`](docs/getting-started.md) | 新手上路：不熟 GitHub 也能装好跑起来，下载、装 Python、配置、常见报错 |
| [`docs/architecture.md`](docs/architecture.md) | 架构决策与取舍：为什么坚持零依赖、AI Butler 的松耦合集成方案、期刊等级数据的版权处理 |
| [`docs/ai-research-tools-survey.md`](docs/ai-research-tools-survey.md) | 科研 AI 工具全景调研、完整流程环节划分，以及本项目在其中的定位 |
| [`docs/testing.md`](docs/testing.md) | 测试分层与用例清单、已知问题清单、发布前验收标准 |
| [`docs/migration.md`](docs/migration.md) | 换机器时哪些东西跟仓库走、哪些要另外处理，以及新设备上的完整步骤 |

---

## 贡献

欢迎提 Issue 与 PR。几点约定：

- 保持零第三方依赖。CI 会在 `requirements.txt` 出现真实依赖时直接报错。确有必要引入的依赖，请先在 Issue 里讨论。
- 新功能请带测试。测试放在 `tests/`，用标准库 `unittest`。
- 注释写「为什么」而不是「做了什么」。本项目的注释主要用于记录踩过的坑与非显然的取舍，这是它最有价值的部分。
- 提交前本地跑一遍：

  ```bash
  python3 -m compileall -q litwarden tests && python3 -m unittest discover -s tests
  ```

CI 会在 Python 3.11 / 3.12 / 3.13 / 3.14 与 macOS 上运行同一套测试，
并额外检查是否误提交了本机绝对路径或敏感文件。

---

## 许可证

MIT，全文见 [LICENSE](LICENSE)。

可以自由使用、修改、再分发，包括商用，只需保留版权声明。

---

## 免责声明

本工具只通过各数据源的公开 API 与合法的开放获取链接
（Unpaywall / PMC / arXiv / bioRxiv）获取文献元数据与全文，不绕过任何付费墙。
使用者需自行遵守所在机构与各数据源的使用条款，并留意公共 API 的频率限制
（默认已加入请求间隔）。
