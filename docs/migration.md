# 迁移到另一台设备

本文说明怎么把 litflow 搬到新机器上，以及哪些东西跟着仓库走、哪些必须另外处理。

## 仓库里有什么

仓库只有代码和文档，一共 49 个文件：

```
litflow/           26 个模块（全部标准库实现，无第三方依赖）
tests/             10 个文件，201 个用例
docs/              4 份文档
recipes/           模板、示例、冒烟测试三个配方
config.example.toml
pyproject.toml     pip 安装元数据与命令行入口
requirements.txt   空文件，用来说明零依赖
bin/litflow        POSIX shell 入口
.github/workflows/ci.yml
```

## 仓库里没有什么

`.gitignore` 明确排除了下面这些，它们不会随仓库过去：

| 排除项 | 里面是什么 | 大概体积 |
|---|---|---|
| `data/` | 候选池 SQLite、期刊等级表、导出产物 | 20 MB 以上 |
| `config.toml` | 你的邮箱、数据源开关、Zotero 设置 | 几 KB |

其中 `data/litflow.sqlite3` 装着候选池、运行记录、论文与 Zotero 条目的对应关系、
Butler 笔记缓存，以及 Zotero 的写授权 key。这些是源头数据，导出文件都是从它生成的。

所以新机器上不能只 clone 就完事，下面分开说。

## 新设备上的步骤

### 1. 装代码

需要 Python 3.11 以上（用到了标准库 `tomllib`）。

```bash
git clone <仓库地址>
cd litflow
pip install .
litflow --version
```

不想装到环境里也可以软链：

```bash
ln -sf "$(pwd)/bin/litflow" ~/.local/bin/litflow
```

### 2. 配置

```bash
cp config.example.toml config.toml
```

至少要改一项：`[general] email`。Crossref、Unpaywall、NCBI 都要求用它标识请求方，
Unpaywall 会直接拒绝 `example.com` 这类占位地址，影响开放获取链接的补全和 PDF 抓取。

其余按需：`[sources.openalex]` 与 `[sources.semanticscholar]` 默认关闭，需要自备免费 key；
`[zotero] base_url` 默认 `http://127.0.0.1:23119`，一般不用改。

### 3. 期刊等级表

这一步最容易被忽略。等级表是第三方数据，不进仓库，得自己下：

1. 到 <https://www.scimagojr.com/journalrank.php> 页面底部 Export，下载 SJR 的 CSV
2. 放进 `data/journal_rank/`，文件名里带上体系名，例如 `sjr_2025.csv`
3. 执行 `litflow rank import sjr`

SJR 官方导出用分号分隔、表头还拆成两行，litflow 已经处理，列名中英文都能识别。
一份完整的 SJR 表大约 11 MB、三万两千行，导入后是六万多条查表记录（一个期刊会按
ISSN 和刊名各写一条匹配键）。

中科院分区（`cas`）和 JCR（`jcr`）是版权数据，本项目不内置也不分发，需要你自己导出 CSV 再导入。

不导入等级表也能用，只是 `--rank` 这个筛选条件会失效。`litflow rank stats` 可以查看当前表里有多少条。

### 4. Zotero

装 Zotero 10 以上，在「设置 → Advanced」勾选
`Allow other applications on this computer to communicate with Zotero`。

写入时 Zotero 会弹一次授权窗，这里必须点「Always Allow」。点「Always」（一次性授权）
的话每次写都会重新弹窗，很快就会撞上 Zotero 每分钟 5 次的弹窗限流（HTTP 429）。

授权 key 缓存在 `data/litflow.sqlite3` 的 `kv_cache` 表里，不会写进 `config.toml`。
换机器后首次写入会重新弹窗，属正常现象。要主动重置用 `litflow zotero check --reset-key`。

注意 Zotero 进程在运行不等于本地 API 已就绪，刚启动后要等几秒到几十秒才开始监听 23119 端口。

### 5. Zotero-AI-Butler（可选）

只有需要「初读提炼」时才装。装完在它的仪表盘里确认三件事：

| 开关 | 默认值 | 说明 |
|---|---|---|
| 自动扫描新文献 | 开 | 关掉则新导入的论文不会自动精读 |
| 自动扫描时生成 AI 总结 | 开 | 关掉可省大模型额度 |
| 自动扫描时生成 AI 精读 | 关 | |

如果你只想用前四个阶段、论文自己读，把前两项关掉，AI 就完全不介入。
保持开着也不会出错，只是会有笔记躺在 Zotero 里而你并不回读。

### 6. 体检

```bash
litflow doctor          # 配置、证书、数据源、等级表、Zotero 本地 API
litflow doctor --online # 额外发真实请求测试每个源
litflow run smoke-test  # 只导入 2 篇，几分钟走完六个阶段
```

## 数据怎么迁移

### 方案一：从零重建

新机器上直接跑配方，候选池重新检索出来。Zotero 那边用 Zotero 自带的同步。

优点是没有残留、状态干净。代价是要重跑一遍检索，历史运行记录和笔记缓存也没有了。

### 方案二：整体搬迁

把整个 `data/` 目录和 `config.toml` 一并拷过去，用 rsync、网盘或移动硬盘都行。
这里走文件复制，不要走 git。

这样候选池、运行记录、论文与 Zotero 的对应关系、笔记缓存都会带过去，接手就能用。

如果 Zotero 库在、但本地对应关系丢了（或者换的是 Zotero 账号），用这个从 Zotero 反向重建：

```bash
litflow zotero sync
```

它靠条目 `extra` 字段里的 `litflow-key:` 匹配，找不到就退回匹配 DOI。

一个提醒：`sync` 目前不记录附件的状态，重建出来的关联在 litflow 眼里等于「还没有 PDF」。
所以重建之后别急着跑 `litflow zotero attach-pdf`，先 `litflow zotero audit` 看看实际
附件情况，否则可能给已有附件的条目重复上传一份。

## 换机器后常见的几个问题

### 报 `No module named litflow`

用 `python3 -m litflow` 的形式时必须在项目目录内执行，配置也是从当前目录向上查找
`config.toml` 的。想在任何目录调用，用 `pip install .` 装成命令，或者软链 `bin/litflow`。

### 所有 HTTPS 请求报 `CERTIFICATE_VERIFY_FAILED`

macOS 上用 python.org 安装包装的 Python 默认不带 CA 根证书。程序会自动兜底加载
`/etc/ssl/cert.pem`；仍然失败就执行官方的 `Install Certificates.command` 根治。

### 检索很慢，几十秒到几分钟

多半是 TLS 握手的兼容问题，见 README「已知问题」第 1 条。默认策略会在失败后按域名降级，
第一次检索慢，之后访问同一个域名会快很多。

### PDF 抓取失败率高

先确认 `config.toml` 里的邮箱是真实地址。另外 2026 年新收录的论文常常没有可程序化下载的
PDF，这类会汇总进 `未下载到PDF_需手动获取.csv`，需要人工通过机构 VPN 取。

## 平台限制

入口脚本 `bin/litflow` 是 POSIX shell，只支持 macOS 和 Linux，Windows 上用不了。
`pyproject.toml` 提供的 `pip install` 入口是跨平台的，但脚本形式的入口不行。
