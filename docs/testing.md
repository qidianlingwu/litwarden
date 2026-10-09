# 测试方案

> 本文说明 litflow 的测试分层、用例清单与验收标准。
> 分层的目的是把「不需要联网的」和「必须打真实 API 的」分开：前者进 CI 长期守护，后者在发布前手动跑一次。

---

## 一、当前覆盖情况

| 项 | 数值 |
|---|---|
| 源码 | 10107 行 / 26 个模块 |
| 单元测试 | 201 个用例 / 10 个文件 |
| 已覆盖模块 | `models` `filterer` `journalrank` `exporters` `digester` `store` `http` `adapters/*` `web` `librarian`（导入编排）`zotero`（删除接口） |

### 架构分层（依赖方向严格单向，无循环）

```
叶子层（零内部依赖）   config / http / models / zotero / webui
                         ↓
数据与算法层           store / exporters / filterer / journalrank / adapters
                         ↓
编排层                 finder / digester / librarian
                         ↓
顶层                   workflow / cli / web
```

这张图画的是模块之间的依赖层次，下面那张表说的是测试覆盖，两回事。

### 尚未覆盖的模块

排序依据是风险，不是代码行数：

| 模块 | 行数 | 风险 | 为什么危险 |
|---|---|---|---|
| `zotero.py` | 779 | 高 | 写操作。错了会改坏使用者的真实文献库，部分操作不可逆。目前只测了删除接口的参数格式与结果核对 |
| `librarian.py` | 810 | 高 | 批量写入加作者名解析（三种写法），错位会污染 Zotero 条目。目前只测了导入的调用顺序 |
| `workflow.py` | 589 | 中 | 六阶段编排与断点续跑语义，错了会重复写入或漏跑 |
| `config.py` | 140 | 中 | 配置校验，纯逻辑，容易测 |
| `webui.py` | 1354 | 低 | 前端内联字符串，已有静态校验 |

`finder.py`（多源并发编排，失败隔离与去重都在这一层）与 `cli.py`（子命令分发）也没有单测，它们依赖真实网络，靠 L2 契约测试和 L3 端到端覆盖。

---

## 二、已知问题清单

### 已修复

| 问题 | 影响 |
|---|---|
| 期刊等级表「取最优分区」把 Q1 当成最差（`rank_index` 返回 0，被 `or 99` 兜底误判） | 导入后保留 Q2/Q3/Q4、丢掉 Q1，按 `--rank sjr:Q1` 筛选会漏整批期刊。真实数据实测 254 个期刊受影响 |
| 导入时先批量建条目、再统一传附件 | 附件没传完就被 Butler 判定为可分析，任务失败且不重试。改为先上传附件、再建条目、最后认亲 |
| Butler 的「文献表格」笔记整篇解析失败 | 这类笔记没有 `#` 标题，字段全在两列表格里，原来的兜底条件不成立，一个字段都抽不出来 |
| `delete_items` 批量删除只删第一个 | `itemKey` 写成了重复参数，服务端返回 204 却只删一个；且按请求数记账会虚报成功数 |
| Web 服务无 Host 头校验 | 可被 DNS-rebinding 利用，绕过 CORS 调用 `/api/zotero/import` 改写文献库 |
| 并发共享 `HttpClient` 无锁 | 每域名限速失效，易触发对方 429 |
| `run_papers.keyword_score` 写入时序错误 | 纯 search 的 run 该字段恒为 NULL |
| CSV 公式注入 | 标题/摘要来自第三方数据源，前导 `=`/`+`/`-`/`@` 会被表格软件当公式执行 |
| `doctor` / `zotero check` 明文打印 Zotero Server-ID | 诊断输出常被贴进 issue，泄漏本机实例标识 |
| `--sort` 拼错时静默降级 | 使用者拿到另一种排序却毫无提示 |
| `finder` 假设数据源返回 dict | 源返回数组时整次检索在最后一步失败 |

### 有意不修

| 项 | 原因 |
|---|---|
| 未引入 `defusedxml` | 与「零第三方依赖」冲突。CPython 的 ElementTree 默认不解析外部实体，XXE 风险低；实体膨胀 DoS 需权衡 |
| `/api/*` 的 `int(run_id)` 未校验 | 只影响错误码是 500 还是 400，已被外层 except 兜住 |
| `Store` 未统一 `close()` | 单进程短命命令，进程退出即释放 |
| 停用词表在 `adapters/base.py` 与 `filterer.py` 各有一份 | 用途不同（构造检索式 vs BM25 打分），语义本就不同 |

---

## 三、测试分层

| 层 | 内容 | 需要联网 | 需要 key | 可进 CI |
|---|---|---|---|---|
| L1 | 单元测试 | 否 | 否 | 是 |
| L2 | 适配器契约测试（真实 API） | 是 | 部分 | 否 |
| L3 | 端到端主链路（六阶段全跑） | 是 | 是 | 否 |
| L4 | 异常与边界 | 部分 | 否 | 部分 |
| L5 | 数据一致性与可复现性 | 否 | 否 | 是 |

---

## 四、用例清单

### L1 单元测试

| # | 目标 | 用例要点 |
|---|---|---|
| L1-1 | `digester.parse_note` | HTML、Markdown 标题、两列表格三种形态各一组；小节标题的多种写法（`## 研究方法` / `## Methods` / `## 📌 核心结论`）；归不了类的小节必须原样保留在「其他小节」而非丢弃 |
| L1-2 | `digester.html_to_text` | 标签剥离、实体反转义、`<br>` / `<li>` 转行、空白压缩 |
| L1-3 | `digester.classify` | 已知标题前缀映射到正确字段；未知标题不误判 |
| L1-4 | `librarian.split_creator` | 三种作者写法：`Satterstrom, Frank` / `Satterstrom FK` / `Frank Satterstrom`；单名、中间名缩写、含连字符的姓 |
| L1-5 | `store` 基本读写 | 临时 DB：`upsert_papers` 幂等；`to_row`/`from_row` 往返字段不丢；`ON CONFLICT` 更新语义 |
| L1-6 | `store.attach_run_papers` | 回归：传入已算好 `keyword_score` 的论文，写库后必须非 NULL |
| L1-7 | `config` 校验 | 缺 `email` / 占位 `email` 抛 `ConfigError`；`data_dir` 相对路径解析到配置所在目录；非法 `tls` 被兜底 |
| L1-8 | `unpaywall` / `semanticscholar` 解析 | 固定 JSON 样本，不联网 |
| L1-9 | `workflow` 配方解析 | 缺字段时的默认值；非法 stage 名报错；`--only` / `--skip` 组合语义 |
| L1-10 | `zotero.delete_items` | 回归：`itemKey` 必须是逗号分隔的单个参数；返回值要按读回结果核对，不能按请求数记账 |
| L1-11 | `librarian.import_papers` 调用顺序 | 回归：上传附件、建条目、认亲三者的先后顺序不能变 |

### L2 适配器契约测试

这里验证的是契约，不是具体数据。论文会变，契约不会：

| # | 用例 | 判定标准 |
|---|---|---|
| L2-1 | 单源返回结构 | 每个源都返回 `list[Paper]`，`title` 非空，`key` 稳定 |
| L2-2 | `sort` 参数生效 | `cited` 下引用量单调不增；`date` 下年份单调不增 |
| L2-3 | 年份过滤真正下发 | 结果年份全部 >= `year_from` |
| L2-4 | 分页与限流 | `limit=100` 不报错；连续请求不触发 429 |
| L2-5 | OpenAlex key 生效 | 带 key 时不再返回 `429 Insufficient budget` |
| L2-6 | S2 key 生效 | 带 key 时返回 `citationCount` 与 `openAccessPdf` |
| L2-7 | 缺 key 时降级 | 不改 `enabled` 时不构建该适配器，检索不中断 |
| L2-8 | 单源故障隔离 | 人为给一个源错误 URL，其余源仍正常返回并在 `errors` 中上报 |

### L3 端到端主链路

| # | 用例 | 判定标准 |
|---|---|---|
| L3-1 | 多源并行检索 | 各源均参与，`per_source` 有计数 |
| L3-2 | 跨源去重 | 同一论文被多源命中时合并为 1 条，`sources` 含多个源名 |
| L3-3 | 筛选与排序 | `--rank sjr:Q1 --min-keyword 0.3` 的结果全部满足条件 |
| L3-4 | 四种导出格式 | xlsx 可解析、csv 带 BOM、bib 的 key 唯一、md 结构完整 |
| L3-5 | `--oa-enrich` | 命中 OA 链接的篇数 > 0，链接域名合法 |
| L3-6 | 写入 Zotero | 先 `--top 3` 建条目，核对标题/作者/DOI/年份，再放量 |
| L3-7 | 阅读状态标签 | Zotero 中出现 `wf:未读`；重复导入不产生重复标签 |
| L3-8 | 幂等重跑 | 同一 run 连续写入两次，条目数不翻倍 |
| L3-9 | `digest fetch` → `export` | 有笔记的正确解析；无笔记的标为「尚无精读笔记」而非报错 |
| L3-10 | Recipe 全跑 | 六阶段全部成功，产物齐备 |
| L3-11 | Butler 自动精读回路 | 导入后条目在 Butler 侧任务为 completed，`digest fetch` 能读回笔记 |

### L4 异常与边界

| # | 用例 | 预期行为 |
|---|---|---|
| L4-1 | 检索式为空 | 明确错误，不是堆栈 |
| L4-2 | 关键词无命中 | 返回 0 条，不崩 |
| L4-3 | 年份区间无结果 | 同上 |
| L4-4 | `--run 99999` | 报错清晰 |
| L4-5 | `--sort` 拼错 | 抛错而非静默降级 |
| L4-6 | `--config` 指向不存在文件 | `ConfigError` 而非 `FileNotFoundError` 堆栈 |
| L4-7 | 等级表未导入时用 `--rank` | 提示去导入，而不是筛出 0 条后沉默 |
| L4-8 | Zotero 未启动 | 明确提示并给出设置路径 |
| L4-9 | Web 端伪造 Host | 返回 403 |
| L4-10 | 断网重试 | 重试到上限后报错并带排查提示，不无限挂起 |

### L5 数据一致性与可复现性

PRISMA 流程图要求计数严格对齐，这一层就是守护它。

| # | 用例 | 判定标准 |
|---|---|---|
| L5-1 | 计数一致性 | `raw_count` = 各源计数之和；去重后数量 <= raw |
| L5-2 | PRISMA 对齐 | 「识别数 = 去重后数 + 各阶段排除数」严格成立 |
| L5-3 | 引用量溯源 | 每条有引用量的记录都带 `cited_by_source` 与 `cited_by_fetched_at` |
| L5-4 | 同配方重跑 | 相同配方与时间窗口下两次运行的候选集高度重合（允许数据源有新增） |
| L5-5 | `run_papers.keyword_score` 非空 | 纯 search 的 run 也必须有分数 |

---

## 五、执行顺序与验收标准

```
① 单元测试            python3 -m unittest discover -s tests
② L2 契约测试          litflow doctor --online
③ L1 补测 → 全绿并进 CI
④ L5 一致性
⑤ L3 端到端           先限量（--top 3 写 Zotero），核对无误后放量
⑥ L4 异常
⑦ 复跑 ① 确认无回归
```

放行标准：

- L1 全绿
- L2 / L5 无失败项
- L3 六阶段全部成功，Zotero 条目经人工核对无误
- L4 无「应该报错却静默通过」的项

---

## 六、不做自动化测试的部分

| 项 | 原因 |
|---|---|
| `--oa-enrich` 大批量压测 | 逐篇请求 Unpaywall，会触发对方限流，对公共 API 不礼貌 |
| Butler 自动精读的完整回路 | 依赖插件行为与外部模型服务，不属本工程可控范围；只测「回读笔记」这一侧 |
| Web UI 的浏览器交互 | 需额外安装 Node/Playwright 运行时；改用静态校验 + 接口级测试覆盖 |
| Windows 平台 | 入口脚本是 POSIX shell，当前不支持（README 已声明仅 macOS / Linux） |
