# 与 Zotero-AI-Butler 的集成细节（一处已修复的竞态）

本文记录 litwarden 写 Zotero、Butler 自动精读这条链路上的一处竞态，以及它是怎么被定位和修掉的。

## 现象

litwarden 导入的论文，Butler 的「自动扫描新文献」有时会把任务判为失败：

```
PDF 文本提取失败：未找到 PDF 文件路径
```

关键在于任务记录里带 `suppressTaskRetry: true`，Butler 此后不再重试。这篇论文就永久没有笔记，只能靠人工在仪表盘上重试或重扫。

## 触发条件

Butler 的 AutoScan 挂在 Zotero 的 `item` `add` 事件上：

```js
// aiButler.js —— AutoScan.handleNotify
if (event === "add" && this.shouldProcess(item)) {
    await this.delay(2e3);          // 条目入库后只等 2 秒
    await this.enqueueIfReady(item);
}
```

`enqueueIfReady` 调用 `hasUsableAnalyzableAttachment` 判断附件是否就绪。这个判断和真正提取 PDF 时的判断口径不一致：

| 函数 | 用在哪 | 是否查磁盘 |
|---|---|---|
| `isAttachmentFileAvailable` | 入队前 | 否，`getFile()` 有值就通过 |
| `ensurePdfAttachmentAvailable` | 提取前 | 是，用 `getFilePathAsync()` 加 `pathExists()` |

后者是这样写的：

```js
const existingPath = await pdfAttachment.getFilePathAsync();
if (existingPath && await this.pathExists(existingPath)) return existingPath;
// 查不到 → 尝试从 Zotero 云端重下（本地上传的附件没有云端副本）→ 失败
return false;   // → 抛「未找到 PDF 文件路径」
```

于是出现一个必然窗口。Zotero 的附件协议要求先建附件条目、再上传文件本体，在「条目已存在、文件未落盘」这段时间里，入队检查会通过，提取却必然失败。

## 实测证据

一次真实导入（2026-10-08，itemID 205）：

| 事件 | 时刻 |
|---|---|
| 父条目创建（Butler 开始 2 秒倒计时） | 16:49:19 |
| Butler 判定附件可用，入队并启动任务 | 16:49:21.207 |
| 任务失败（耗时 84 ms） | 16:49:21.291 |
| PDF 文件真正落盘 | 16:49:21.437 |

文件比判定晚了 230 毫秒。这就说明入队检查没有查磁盘。否则它当时会判定为不可用，转而进入自带的退避重试队列（2s、4s、8s、16s、30s，最多 5 次），那次上传本来能赶上。

同一批导入的另外 4 篇因为没有可用的开放获取链接（403 / 404）没有附件，不在讨论范围。

## 三层处理

1. 把 PDF 下载提前到建条目之前（`prefetch_pdfs`）。原本「建条目后再下载」的窗口实测可长达 37 秒（一篇 57 MB 的 PDF），现在窗口只剩上传本身。
2. 逐篇交错：建一篇条目就立刻处理它的附件，再建下一篇。原先是先把所有条目批量建好、再统一传附件，排在后面的论文要等前面几篇传完，2 秒早已过去。
3. 先上传、后认亲，这一步根治了问题。先把附件传成独立条目，它没有父条目，Butler 不处理独立附件的 `add` 事件，所以这个中间态不会误触发扫描；再创建父条目，此时 Butler 才开始 2 秒倒计时，文件已经躺在磁盘上；最后立刻把附件认亲到父条目下（`set_parent`，实测约 0.05 秒）。

第 2 步单独用是不够的。实测一次 3.4 MB 的上传要约 2 秒，正好压在判定线上，以 230 毫秒之差落败。靠提速赢不了这场竞争，得让文件在父条目出现之前就位。

## 实测验证

单篇对照（同一个 3.4 MB 的 PDF，同一台机器）：

| 走法 | 父条目出现到附件就绪 | Butler 结果 |
|---|---|---|
| 先建条目，再传附件 | 2.0 s（文件晚 230 ms 落盘） | failed |
| 先上传，再建条目，最后认亲 | 0.1 s | completed |

三篇连测，走生产代码路径，Butler 逐篇串行处理：

```
itemId=213  completed  57s
itemId=215  completed  42s
itemId=217  completed  56s
```

作为对照，同一批走老路子的条目是 4/5 失败。

顺序由 `tests/test_import_race.py` 钉死，免得将来有人图省事改回直觉顺序。

## 兜底入口

Butler 仪表盘上有两个批量按钮。「扫描未总结论文」重扫全库，把缺总结笔记的文献重新入队；「扫描未精读论文」针对精读笔记。

导入完发现有条目没笔记时，点一次就能把受影响的条目全部捞回来，不必逐条重试。
