# 新手上路：没用过 GitHub 也能跑起来

这份说明写给没接触过 GitHub、也不熟悉命令行的人。全程不需要注册账号，不需要编程基础。照着做，大约 10 分钟能跑出第一份结果。

只想看看它是什么，跳到最后一节。

## 目录

- [第 0 步：你需要准备什么](#第-0-步你需要准备什么)
- [第 1 步：把代码下载下来](#第-1-步把代码下载下来)
- [第 2 步：确认装了 Python](#第-2-步确认装了-python)
- [第 3 步：打开终端，走进项目目录](#第-3-步打开终端走进项目目录)
- [第 4 步：填一个配置文件](#第-4-步填一个配置文件)
- [第 5 步：跑起来](#第-5-步跑起来)
- [想要 PDF 和 AI 精读笔记？](#想要-pdf-和-ai-精读笔记)
- [常见问题](#常见问题)
- [这东西到底能干什么](#这东西到底能干什么)

## 第 0 步：你需要准备什么

| | 要求 | 怎么理解 |
|---|---|---|
| 电脑 | macOS 或 Linux | Windows 不能直接用，需要先装 WSL，见[常见问题](#常见问题) |
| Python | 3.11 或更高 | 第 2 步会带你确认 |
| 邮箱 | 一个真实的邮箱地址 | 检索时用它标识请求方，填假的会被拒绝 |
| Zotero | 可选 | 只有想自动收文献、抓 PDF 才需要 |

不需要 GitHub 账号，不需要 pip 安装任何东西（这个项目不用第三方库），也不需要联网账号或云端服务。

## 第 1 步：把代码下载下来

有两种方式，新手请用第一种。

### 方式 A：下载压缩包

1. 打开项目主页：<https://github.com/qidianlingwu/litflow>
2. 找到文件列表右上方那个绿色的 `Code` 按钮，点它
3. 在弹出的菜单里点最下面的 `Download ZIP`
4. 下载得到一个 `litflow-main.zip`，双击解压，会得到一个叫 `litflow-main` 的文件夹

就这样，不需要注册也不需要登录。GitHub 页面上那些文件是逐个浏览的源码视图，直接复制容易漏文件；ZIP 是打包好的完整一份，拿它就对了。

### 方式 B：用 git 命令

```bash
git clone https://github.com/qidianlingwu/litflow.git
```

ZIP 是一次性快照，以后想更新得重新下载一次；`git clone` 之后可以 `git pull` 拉取更新。功能上完全一样，新手用 ZIP 没有任何问题。

### 关于文件夹名字

用 ZIP 下载解压后文件夹叫 `litflow-main`，用 `git clone` 得到的叫 `litflow`。后面所有命令里的路径，按你实际的文件夹名来，本文统一写作 `litflow`。

## 第 2 步：确认装了 Python

打开终端。macOS 上按 `Command + 空格`，输入 `终端` 或 `Terminal`，回车。

敲这一行然后回车：

```bash
python3 --version
```

看到 `Python 3.11.x` 或更高的版本号，就可以跳到第 3 步了。`3.12`、`3.13`、`3.14` 都可以，只要不低于 3.11。

看到 `Python 3.9.x` 之类的低版本，需要装新版。提示 `command not found: python3` 说明这台电脑没装 Python。两种情况都看下面。

### 怎么装新版 Python

macOS 用户去 <https://www.python.org/downloads/macos/> 下载安装包，选 3.12 或更新，双击一路点继续。装完关掉终端再重开，重新执行 `python3 --version` 确认。

为什么必须 3.11 以上：项目用到了 Python 3.11 才进标准库的 `tomllib` 来读配置文件。版本低了会在启动时报找不到模块。

## 第 3 步：打开终端，走进项目目录

命令行有个「当前目录」的概念，后面所有命令都必须在项目文件夹里面执行，否则会报 `No module named litflow`。

在终端里输入 `cd `（cd 后面有一个空格），然后把第 1 步解压出来的 `litflow-main` 文件夹从访达直接拖到终端窗口里，路径会自动补全。回车。

```bash
cd /Users/你的用户名/Downloads/litflow-main      # 拖进去后大致长这样
```

确认自己站对地方了。敲这两行，应该能看到 `README.md` 和 `litflow`：

```bash
pwd      # 显示当前目录
ls       # 列出目录里的东西
```

## 第 4 步：填一个配置文件

程序需要知道你的邮箱（向公开数据库请求时的礼貌标识）。复制一份模板：

```bash
cp config.example.toml config.toml
```

然后用任意文本编辑器打开 `config.toml`（macOS 上可以右键，选打开方式，用文本编辑），找到这一行：

```toml
email = "you@example.com"
```

把引号里换成你自己的真实邮箱：

```toml
email = "你的邮箱@example.com"
```

保存。其余选项都不用改，默认值可以直接用。

这个 `config.toml` 里有你的邮箱。它不会被上传到任何地方，项目也已经在 `.gitignore` 里把它排除在外。不过如果你要把整个文件夹发给别人，记得先把这一份删掉。

## 第 5 步：跑起来

### 先做个环境体检

```bash
python3 -m litflow doctor
```

它会检查数据源连通性、HTTPS 证书、Zotero 是否在运行等等，逐项打勾或打叉。这里报错最常见的原因是没有 `cd` 进项目目录，见第 3 步。

### 检索一批文献

```bash
python3 -m litflow search -q "spatial transcriptomics deconvolution" --year-from 2023
```

`-q` 后面换成你自己的检索词。这一步会同时查 Europe PMC、PubMed、arXiv、Crossref，自动去重合并，都不需要 API key。

### 筛选并导出表格

```bash
python3 -m litflow filter --run 1 --rank sjr:Q1 \
    --min-cites-per-year 2 --min-keyword 0.3 --sort rank \
    --show-reasons --export xlsx,csv,md,bib
```

导出的文件在 `data/exports/` 目录里，用 Excel 或 Numbers 打开 `.xlsx` 即可。

### 更喜欢网页界面

```bash
python3 -m litflow web
```

会在浏览器里打开一个本地网页（地址 `http://127.0.0.1:8765`），可以点选、勾选、导出，不用记命令。这个网页只在你自己的电脑上，别人访问不到。

想装一个 `litflow` 短命令、省得每次敲 `python3 -m litflow`，见 [`README.md`](../README.md) 的「安装」一节，三种方式任选。

## 想要 PDF 和 AI 精读笔记？

这两件事需要额外装 [Zotero](https://www.zotero.org/)（免费的文献管理软件）。

装好之后，筛选出的论文可以写进你的 Zotero 库，并自动从合法的开放获取链接下载 PDF 作为附件。拿不到的就跳过，不会绕过付费墙。

再装上 [Zotero-AI-Butler](https://github.com/steven-jianhao-li/zotero-AI-Butler) 插件，就能让它替你精读，然后 litflow 把笔记汇总成结构化对照表。

这两步都是可选的。不装 Zotero 的话，前面的检索、筛选、导出表格全都正常可用。具体命令见 [`README.md`](../README.md) 的「文献管理：写入 Zotero」一节。

## 常见问题

### 报错 `command not found: litflow`

你没有装成短命令。改用完整写法即可，效果一样：

```bash
python3 -m litflow doctor
```

### 报错 `No module named litflow`

你不在项目目录里。回到第 3 步，用 `cd` 进入解压出来的文件夹再执行。

### 报错 `python3: command not found`

没装 Python，或者装完没重开终端。见第 2 步。

### 体检时提示邮箱无效，或者 Unpaywall 报错

`config.toml` 里填的是占位邮箱（`example.com` 之类）。换成真实邮箱。

### Windows 能用吗？

目前不能直接跑。入口脚本 `bin/litflow` 是 POSIX shell 脚本，只支持 macOS 与 Linux。

Windows 用户可以装 WSL（Windows 里的 Linux 环境，微软官方支持，网上教程很多），在 WSL 里按本文操作。或者先用 `python3 -m litflow` 这条路径，它在 Windows 上多数子命令可用，但尚未正式验证过。

### 它会把我的数据传到网上吗？

不会。数据库、导出的表格、笔记全部只存在你本机。只有在你主动执行检索时，程序才会向欧洲 PMC、PubMed、arXiv、Crossref 这些公开文献数据库发起查询请求。

### 下载的 ZIP 版本，以后怎么更新？

重新去项目主页点一次 `Download ZIP` 覆盖即可。你的 `config.toml` 和 `data/` 目录是独立的，覆盖代码不会丢。更新前建议先备份 `config.toml`。

### 怎么知道项目能不能正常跑？

项目自带 240 个自动化测试，每次改动都会在云端跑一遍（macOS 与 Linux，Python 3.11 到 3.14）。想在本机自测：

```bash
python3 -m unittest discover -s tests
```

### 遇到本文没写的问题怎么办？

到项目主页的 [Issues](https://github.com/qidianlingwu/litflow/issues) 页面点 `New issue`，把你执行的命令和完整报错贴上去。提 issue 需要 GitHub 账号，如果没有，也可以直接把报错发给分享给你这个项目的人。

## 这东西到底能干什么

一句话：把读文献时那些机械重复的活儿自动化掉，判断留给你。

它做的事一共六步，一条命令可以串起来跑：

| 步骤 | 做什么 |
|---|---|
| 1 检索 | 一次查四个公开文献库，自动去重合并 |
| 2 筛选 | 按期刊等级、年均引用、关键词匹配度排序，并给出筛选理由 |
| 3 导出 | 生成 Excel / CSV / Markdown / BibTeX 表格 |
| 4 入库 | 把选中的论文写进你的 Zotero，自动抓开放获取 PDF |
| 5 回读 | 读回 AI 管家写的笔记 |
| 6 提炼 | 把笔记汇总成结构化对照表（研究问题 / 方法 / 数据 / 结论 / 局限） |

第 4 步和第 5 步之间那一步「精读」是 AI 管家插件干的，不属于本工具。litflow 只负责把论文交出去、再把笔记取回来。

它不帮你判断论文好坏，不绕过付费墙，也不上传你的任何数据。学术判断始终由你来做。

## 还想了解更多

| 文档 | 内容 |
|---|---|
| [`README.md`](../README.md) | 完整功能、全部命令与参数 |
| [`docs/architecture.md`](architecture.md) | 为什么这么设计、做了哪些取舍 |
| [`docs/testing.md`](testing.md) | 测试覆盖与验收标准 |
| [`docs/migration.md`](migration.md) | 换电脑时怎么迁移 |

本项目采用 MIT 许可证，可自由使用与修改。
