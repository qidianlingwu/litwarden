"""Web UI 的前端资源。

整个界面是**一个字符串常量**，不依赖任何前端框架、不加载任何 CDN 资源——
因为这是个本地工具，必须能离线工作，也要守住「零第三方依赖」这条线。

界面分五个标签页，对应工作流的五个环节：
    检索 → 论文表（筛选）→ Zotero → 提炼 → 配方
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>litflow · 文献工作流</title>
<style>
:root{
  --bg:#f7f8fa; --panel:#fff; --line:#e3e6ec; --line2:#eef0f4;
  --ink:#1b1f26; --ink2:#5b6472; --ink3:#8b94a3;
  --accent:#2f6feb; --accent-soft:#eaf1fe;
  --ok:#0f8a5f; --warn:#b4761a; --err:#c0392b;
  --radius:10px;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif;
  color:var(--ink); background:var(--bg); display:flex; flex-direction:column;
}
header{
  background:var(--panel); border-bottom:1px solid var(--line);
  padding:10px 20px; display:flex; align-items:center; gap:16px; flex-wrap:wrap;
}
header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
header .pills{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
.pill{
  font-size:12px;padding:3px 10px;border-radius:999px;
  background:var(--line2);color:var(--ink2);white-space:nowrap;
}
.pill.ok{background:#e6f6ef;color:var(--ok)}
.pill.warn{background:#fdf3e3;color:var(--warn)}
.pill.err{background:#fdecea;color:var(--err)}

main{flex:1;display:flex;min-height:0}
nav{
  width:132px;flex:none;background:var(--panel);border-right:1px solid var(--line);
  padding:12px 8px;display:flex;flex-direction:column;gap:2px;
}
nav button{
  all:unset;cursor:pointer;padding:9px 12px;border-radius:8px;color:var(--ink2);
  font-size:13.5px;display:flex;align-items:center;gap:8px;
}
nav button:hover{background:var(--line2)}
nav button.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
nav .num{font-size:11px;opacity:.6;margin-left:auto}

#view{flex:1;overflow:auto;padding:18px 20px 40px}
section{display:none}
section.on{display:block}
h2{font-size:15px;margin:0 0 4px;font-weight:650}
.sub{color:var(--ink3);font-size:12.5px;margin-bottom:14px}

.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px 14px}
label.f{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--ink2)}
input,select,textarea{
  font:inherit;font-size:13px;padding:6px 9px;border:1px solid var(--line);
  border-radius:7px;background:#fff;color:var(--ink);width:100%;
}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
textarea{resize:vertical;min-height:34px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row.wrap-top{align-items:flex-start}
button.btn{
  font:inherit;font-size:13px;padding:7px 15px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:#fff;color:var(--ink);
}
button.btn:hover{border-color:#c9cfda;background:#fbfcfe}
button.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
button.btn.primary:hover{filter:brightness(1.07)}
button.btn:disabled{opacity:.45;cursor:not-allowed}
button.btn.sm{font-size:12px;padding:4px 10px}
button.btn.danger{color:var(--err);border-color:#f0c9c4}

.chk{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink2);cursor:pointer}
.chk input{width:auto}

table{border-collapse:separate;border-spacing:0;width:100%;font-size:12.5px;background:#fff}
th,td{padding:7px 9px;border-bottom:1px solid var(--line2);text-align:left;vertical-align:top}
th{
  position:sticky;top:0;z-index:2;background:#f3f5f9;font-weight:600;color:var(--ink2);
  white-space:nowrap;font-size:12px;
}
th.sortable{cursor:pointer;user-select:none}
th.sortable:hover{color:var(--accent)}
tbody tr:hover{background:#fafbfd}
td.nowrap{white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.title-cell{max-width:560px}
.title-cell b{font-weight:600}
/* 摘要速览：够读个大概，又不让行高失控。加在内部元素上而不是 td 上——
   td 改 display 会破坏表格布局。完整速览挂在 td 的 title 属性上可悬停查看 */
.abs-cell{max-width:380px;font-size:12px;line-height:1.5;color:var(--ink3);
  display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.muted{color:var(--ink3)}
.tag{
  display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;
  background:var(--line2);color:var(--ink2);margin-right:4px;
}
.tag.q1{background:#e6f6ef;color:var(--ok)}
.tag.q2{background:#eef4ff;color:var(--accent)}
.tag.q3,.tag.q4{background:#f3f4f6;color:var(--ink3)}
.oa-y{color:var(--ok);font-weight:600}
.oa-n{color:var(--ink3)}
.oa-u{color:var(--ink3)}

.tablewrap{border:1px solid var(--line);border-radius:var(--radius);overflow:auto;max-height:62vh;background:#fff}
pre.log{
  background:#1e2229;color:#d6dae1;border-radius:8px;padding:12px 14px;font-size:12px;
  line-height:1.6;overflow:auto;max-height:280px;white-space:pre-wrap;margin:0;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
}
.bar{height:6px;border-radius:99px;background:var(--line2);overflow:hidden;margin:10px 0}
.bar>i{display:block;height:100%;background:var(--accent);width:0;transition:width .3s}
.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:13px}
.kv dt{color:var(--ink3)}
.kv dd{margin:0;font-variant-numeric:tabular-nums}
.hint{font-size:12px;color:var(--ink3);margin-top:8px}
.empty{color:var(--ink3);padding:28px;text-align:center;font-size:13px}
.recipe{border:1px solid var(--line);border-radius:8px;padding:11px 13px;margin-bottom:9px;background:#fff}
.recipe .nm{font-weight:600}
.recipe .desc{color:var(--ink3);font-size:12.5px;margin-top:2px}
details.note{margin-top:6px}
details.note summary{cursor:pointer;color:var(--accent);font-size:12.5px}
details.note pre{
  background:#f8f9fb;border:1px solid var(--line2);border-radius:7px;padding:11px;
  white-space:pre-wrap;font-size:12px;max-height:420px;overflow:auto;margin:8px 0 0;
  font-family:ui-monospace,Menlo,monospace;
}
.toast{
  position:fixed;right:20px;bottom:20px;background:#1e2229;color:#fff;padding:11px 16px;
  border-radius:9px;font-size:13px;box-shadow:0 8px 26px rgba(0,0,0,.22);opacity:0;
  transform:translateY(8px);transition:.22s;pointer-events:none;max-width:min(520px,80vw);
}
.toast.on{opacity:1;transform:none}
.toast.err{background:#7f2318}
</style>
</head>
<body>

<header>
  <h1>litflow</h1>
  <span class="pill" id="p-ver">…</span>
  <div class="pills" id="p-overview"></div>
</header>

<main>
  <nav>
    <button data-tab="search" class="on">检索<span class="num" id="n-search"></span></button>
    <button data-tab="papers">论文表<span class="num" id="n-papers"></span></button>
    <button data-tab="zotero">Zotero</button>
    <button data-tab="digest">提炼</button>
    <button data-tab="exports">导出文件</button>
    <button data-tab="recipe">配方</button>
  </nav>

  <div id="view">

  <!-- ================= 检索 ================= -->
  <section id="s-search" class="on">
    <h2>检索</h2>
    <div class="sub">四个免 key 数据源并行检索，自动按 DOI / PMID / arXiv 去重</div>
    <div class="card">
      <div class="grid">
        <label class="f" style="grid-column:span 2">
          关键词（英文）
          <input id="q" placeholder="spatial transcriptomics deconvolution">
        </label>
        <label class="f">作者<input id="author" placeholder="可选"></label>
        <label class="f">期刊<input id="journal" placeholder="可选"></label>
        <label class="f">起始年份<input id="year_from" type="number" placeholder="2023"></label>
        <label class="f">结束年份<input id="year_to" type="number" placeholder="可选"></label>
        <label class="f">每源条数<input id="limit" type="number" value="40"></label>
        <label class="f">排序
          <select id="sort">
            <option value="relevance">相关性（兼顾最新与经典）</option>
            <option value="cited">引用量（偏经典）</option>
            <option value="date">日期（偏最新）</option>
          </select>
        </label>
      </div>
      <div class="row" style="margin-top:12px">
        <span class="muted" style="font-size:12px">数据源</span>
        <div id="sources" class="row"></div>
      </div>
      <div class="row" style="margin-top:12px">
        <label class="chk"><input type="checkbox" id="oa_enrich"> 用 Unpaywall 补全开放获取链接（较慢）</label>
        <label class="chk"><input type="checkbox" id="preprints_only"> 只要预印本</label>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn primary" id="btn-search">开始检索</button>
        <span class="muted" id="search-hint" style="font-size:12px"></span>
      </div>
    </div>
    <div id="search-progress" style="display:none" class="card">
      <div class="bar"><i id="search-bar"></i></div>
      <pre class="log" id="search-log"></pre>
    </div>
    <div class="card" id="search-result" style="display:none">
      <div class="row"><b id="search-summary"></b></div>
      <div class="tablewrap" style="margin-top:10px"><table id="search-table"></table></div>
      <div class="hint">检索结果按关键词匹配度排序。要按期刊等级/引用量筛，去「论文表」标签页。</div>
    </div>
  </section>

  <!-- ================= 论文表 ================= -->
  <section id="s-papers">
    <h2>论文表</h2>
    <div class="sub">按期刊等级、引用量、关键词匹配度筛选排序，勾选后可写入 Zotero 或导出</div>

    <div class="card">
      <div class="row" style="margin-bottom:12px">
        <label class="f" style="max-width:190px">数据来源
          <select id="run-select"></select>
        </label>
        <span class="muted" id="run-info" style="font-size:12px;margin-top:16px"></span>
      </div>
      <div class="grid">
        <label class="f">最低年均引用<input id="f_mincpy" type="number" step="0.5" placeholder="如 2"></label>
        <label class="f">最低总引用<input id="f_mincites" type="number" placeholder="如 20"></label>
        <label class="f">关键词匹配度下限<input id="f_minkw" type="number" step="0.05" placeholder="如 0.3"></label>
        <label class="f">期刊等级
          <select id="f_ranksys">
            <option value="">不限</option>
            <option value="sjr">SJR</option>
            <option value="ccf">CCF</option>
            <option value="cas">中科院</option>
            <option value="jcr">JCR</option>
          </select>
        </label>
        <label class="f">等级门槛
          <select id="f_ranklevel">
            <option value="Q1">Q1（最高）</option>
            <option value="Q2">Q2 及以上</option>
            <option value="Q3">Q3 及以上</option>
            <option value="A">A</option>
            <option value="B">B 及以上</option>
            <option value="1区">1 区</option>
            <option value="2区">2 区及以上</option>
          </select>
        </label>
        <label class="f">排序
          <select id="f_sort">
            <option value="rank">期刊等级优先</option>
            <option value="cites_per_year">年均引用</option>
            <option value="cites">总引用</option>
            <option value="year">年份</option>
            <option value="keyword">关键词匹配度</option>
          </select>
        </label>
        <label class="f" style="grid-column:span 2">标题必须包含（逗号分隔）
          <input id="f_titlemust" placeholder="deconvolution —— 区分「提出方法」与「使用方法」最有效">
        </label>
        <label class="f">必须包含（标题或摘要）<input id="f_must" placeholder="可选"></label>
        <label class="f">排除词（标题或摘要）<input id="f_exclude" placeholder="可选"></label>
      </div>
      <div class="row" style="margin-top:12px">
        <label class="chk"><input type="checkbox" id="f_oa"> 只要开放获取</label>
        <label class="chk"><input type="checkbox" id="f_onlypre"> 只要预印本</label>
        <label class="chk"><input type="checkbox" id="f_expre"> 排除预印本</label>
        <label class="chk"><input type="checkbox" id="f_corr" checked> 剔除勘误记录</label>
        <label class="chk"><input type="checkbox" id="f_allowunknown" checked> 等级未收录也保留</label>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn primary" id="btn-filter">应用筛选</button>
        <button class="btn" id="btn-filter-reset">重置条件</button>
        <span class="muted" id="filter-info" style="font-size:12px"></span>
      </div>
    </div>

    <div class="card">
      <div class="row">
        <b id="papers-count">—</b>
        <button class="btn sm" id="btn-all">全选</button>
        <button class="btn sm" id="btn-none">清空选择</button>
        <button class="btn sm" id="btn-invert">反选</button>
        <span style="flex:1"></span>
        <button class="btn" id="btn-export">导出选中</button>
        <button class="btn primary" id="btn-import">写入 Zotero</button>
      </div>
    </div>

    <div class="tablewrap"><table id="papers-table"></table></div>
    <div class="hint">
      点击列头排序（仅对当前已加载的数据排序）。写进 Zotero 前请先确认下方 Zotero 区的账号与集合设置。
    </div>

    <!-- 导出结果就地显示。原先这个清单被画到了 Zotero 标签页里，
         用户在「论文表」页点完导出只看到一闪而过的提示，以为没导出成功。 -->
    <div class="card" id="p-export-result" style="display:none">
      <div class="row">
        <b>已生成的文件</b>
        <span style="flex:1"></span>
        <button class="btn sm goto-exports">查看全部导出文件</button>
      </div>
      <div id="p-export-body" style="margin-top:10px"></div>
    </div>
  </section>

  <!-- ================= Zotero ================= -->
  <section id="s-zotero">
    <h2>Zotero</h2>
    <div class="sub">把论文写入 Zotero 集合，管理阅读状态，批量体检 PDF 附件</div>

    <div class="card">
      <div class="row"><b>本地 API 状态</b><button class="btn sm" id="btn-zcheck">重新检测</button></div>
      <div id="z-status" style="margin-top:10px"></div>
    </div>

    <div class="card">
      <h2 style="font-size:13.5px">写入设置</h2>
      <div class="grid" style="margin-top:10px">
        <label class="f">目标集合<input id="z-collection" value="litflow 导入"></label>
        <label class="f">阅读状态
          <select id="z-status-sel">
            <option value="未读">未读</option>
            <option value="在读">在读</option>
            <option value="已初读">已初读</option>
            <option value="已精读">已精读</option>
          </select>
        </label>
        <label class="f">写入哪些
          <select id="z-decision">
            <option value="include">筛过的（推荐）</option>
            <option value="candidate">全部候选</option>
          </select>
        </label>
      </div>
      <div class="row" style="margin-top:12px">
        <label class="chk"><input type="checkbox" id="z-attach"> 抓取开放获取 PDF（较慢，只走合法 OA 链接）</label>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn primary" id="btn-zimport">写入 Zotero</button>
        <button class="btn" id="btn-zaudit">批量体检附件</button>
        <button class="btn" id="btn-zsync">重建本地关联</button>
        <button class="btn" id="btn-zapdf" style="display:none">只补 PDF</button>
      </div>
    </div>

    <div id="z-progress" style="display:none" class="card">
      <div class="bar"><i id="z-bar"></i></div>
      <pre class="log" id="z-log"></pre>
    </div>

    <div class="card" id="z-result" style="display:none">
      <div id="z-result-body"></div>
    </div>
  </section>

  <!-- ================= 提炼 ================= -->
  <section id="s-digest">
    <h2>初读提炼</h2>
    <div class="sub">回读 Zotero-AI-Butler 生成的精读笔记，汇总成方法 / 数据 / 结论对照表</div>

    <div class="card">
      <div class="row"><b>进度</b>
        <button class="btn sm" id="btn-dstatus">刷新</button>
        <button class="btn sm primary" id="btn-dfetch">回读笔记</button>
      </div>
      <div id="d-status" style="margin-top:12px"></div>
      <div id="d-progress" style="display:none;margin-top:12px">
        <div class="bar"><i id="d-bar"></i></div>
        <pre class="log" id="d-log"></pre>
      </div>
    </div>

    <div class="card">
      <div class="row">
        <b id="d-count">—</b>
        <button class="btn" id="btn-dexport">导出汇总表</button>
      </div>
      <div class="hint" id="d-hint" style="margin-top:8px"></div>
    </div>

    <div class="tablewrap"><table id="digest-table"></table></div>

    <div class="card" id="d-note" style="display:none">
      <div class="row">
        <b id="d-note-title">笔记详情</b>
        <span style="flex:1"></span>
        <button class="btn sm" id="btn-dnote-close">关闭</button>
      </div>
      <div id="d-note-body" style="margin-top:10px"></div>
    </div>

    <div class="card" id="d-export-result" style="display:none">
      <div class="row">
        <b>已生成的文件</b>
        <span style="flex:1"></span>
        <button class="btn sm goto-exports">查看全部导出文件</button>
      </div>
      <div id="d-export-body" style="margin-top:10px"></div>
    </div>
  </section>

  <!-- ================= 导出文件 ================= -->
  <section id="s-exports">
    <h2>导出文件</h2>
    <div class="sub">检索候选表、筛选结果表、初读提炼汇总表都落在这里，点文件名即可下载</div>

    <div class="card">
      <div class="row">
        <b id="exp-count">—</b>
        <button class="btn sm" id="btn-exp-refresh">刷新</button>
        <span style="flex:1"></span>
        <span class="muted" id="exp-dir" style="font-size:12px"></span>
      </div>
      <div class="row" style="margin-top:10px">
        <label class="f" style="max-width:240px">只看某类
          <select id="exp-filter">
            <option value="">全部</option>
            <option value="xlsx">Excel (.xlsx)</option>
            <option value="csv">表格 (.csv)</option>
            <option value="md">Markdown (.md)</option>
            <option value="bib">BibTeX (.bib)</option>
          </select>
        </label>
        <label class="f" style="max-width:280px;flex:1">按文件名过滤
          <input id="exp-search" placeholder="如 文献调研 / 初读提炼 / 选中论文">
        </label>
      </div>
      <div id="exp-list" style="margin-top:12px"></div>
      <div class="hint">
        文件同时保存在本机导出目录，可在 Finder 里直接打开（地址见上方）。
        想重新生成，去「论文表」页勾选后点「导出选中」，或在「提炼」页点「导出汇总表」。
      </div>
    </div>
  </section>

  <!-- ================= 配方 ================= -->
  <section id="s-recipe">
    <h2>工作流配方</h2>
    <div class="sub">一条命令跑完整流程。配方是项目根 recipes/ 下的 TOML 文件，可直接编辑</div>

    <div class="card">
      <div class="row">
        <button class="btn" id="btn-rlist">刷新列表</button>
        <button class="btn" id="btn-rinit">生成模板</button>
        <span class="muted" style="font-size:12px">运行时可指定只跑某几段</span>
      </div>
    </div>

    <div id="recipe-list"></div>

    <div id="r-progress" style="display:none" class="card">
      <div class="bar"><i id="r-bar"></i></div>
      <pre class="log" id="r-log"></pre>
    </div>

    <!-- 配方跑完后产物落在 data/exports/，这里给一个直达入口，
         否则用户只看到一句「全部阶段执行成功」，不知道文件在哪。 -->
    <div class="card" id="r-export-result" style="display:none">
      <div class="row">
        <b>本次生成的文件</b>
        <span style="flex:1"></span>
        <button class="btn sm goto-exports">查看全部导出文件</button>
      </div>
      <div id="r-export-body" style="margin-top:10px"></div>
    </div>
  </section>

  </div>
</main>

<div class="toast" id="toast"></div>

<script>
"use strict";

/* ---------------------------------------------------------------- 基础工具 */

const $ = id => document.getElementById(id);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

let toastTimer = null;
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.toggle("err", !!isErr);
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), isErr ? 6000 : 2600);
}

async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
  if (!r.ok && data && data.error) throw new Error(data.error);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return data;
}

/* ---------------------------------------------------------------- 标签页 */

document.querySelectorAll("nav button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
    const tab = b.dataset.tab;
    document.querySelectorAll("section").forEach(s =>
      s.classList.toggle("on", s.id === "s-" + tab));
    if (tab === "papers") loadRuns();
    if (tab === "digest") digestStatus();
    if (tab === "exports") loadExports();
    if (tab === "recipe") listRecipes();
    if (tab === "zotero") zoteroCheck();
  };
});

/* ---------------------------------------------------------------- 任务轮询 */

async function pollJob(jobId, logEl, barEl, intervalMs) {
  const started = Date.now();
  for (;;) {
    const j = await api("/api/job/" + jobId);
    if (logEl) {
      logEl.textContent = (j.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (barEl) {
      const pct = j.progress != null ? j.progress : Math.min(92, (Date.now() - started) / 300);
      barEl.style.width = Math.max(3, Math.min(99, pct)) + "%";
    }
    if (j.status === "done") { if (barEl) barEl.style.width = "100%"; return j.result; }
    if (j.status === "error") throw new Error(j.error || "任务失败");
    await new Promise(r => setTimeout(r, intervalMs || 600));
  }
}

/* ---------------------------------------------------------------- 表格渲染 */

const COLS = {
  search: [
    { k: "_i", t: "#", cls: "num", w: 40 },
    { k: "title", t: "标题", cls: "title-cell" },
    { k: "journal", t: "期刊" },
    { k: "year", t: "年", cls: "num" },
    { k: "cited_by", t: "引用", cls: "num" },
    { k: "cites_per_year", t: "年均", cls: "num" },
    { k: "keyword_score", t: "匹配", cls: "num" },
    { k: "rank_str", t: "等级" },
    { k: "is_oa", t: "OA" },
    { k: "doi", t: "DOI", cls: "nowrap" },
  ],
  papers: [
    { k: "_chk", t: "", w: 30 },
    { k: "_i", t: "#", cls: "num", w: 40 },
    { k: "title", t: "标题", cls: "title-cell" },
    { k: "abstract_preview", t: "摘要速览", nosort: true },
    { k: "journal", t: "期刊" },
    { k: "year", t: "年", cls: "num" },
    { k: "cited_by", t: "引用", cls: "num" },
    { k: "cites_per_year", t: "年均", cls: "num" },
    { k: "keyword_score", t: "匹配", cls: "num" },
    { k: "rank_str", t: "等级" },
    { k: "is_oa", t: "OA" },
    { k: "doi", t: "DOI", cls: "nowrap" },
  ],
  digest: [
    { k: "_i", t: "#", cls: "num", w: 40 },
    { k: "title", t: "标题", cls: "title-cell" },
    { k: "journal", t: "期刊" },
    { k: "year", t: "年", cls: "num" },
    { k: "has_note", t: "有笔记" },
    { k: "has_pdf", t: "PDF" },
    { k: "summary", t: "一句话总结" },
    { k: "method", t: "方法" },
    { k: "data", t: "数据" },
    { k: "conclusion", t: "结论/创新" },
    { k: "limitation", t: "局限" },
  ],
};

let lastRows = {};
let sortState = {};

function rankTag(s) {
  if (!s) return "";
  const span = el("span", "tag");
  span.textContent = s;
  const low = s.toLowerCase();
  if (low.includes("q1") || low.includes("a") && low.length <= 3 || low.includes("1区")) span.className = "tag q1";
  else if (low.includes("q2") || low.includes("2区")) span.className = "tag q2";
  else span.className = "tag q3";
  return span;
}

function cellVal(row, col) {
  const k = col.k;
  if (k === "_i") return row.__i;
  if (k === "rank_str") return row.rank_str || "";
  if (k === "is_oa") return row.is_oa;
  if (k === "cited_by") return row.cited_by == null ? "—" : row.cited_by;
  if (k === "cites_per_year") return row.cites_per_year == null ? "—" : Number(row.cites_per_year).toFixed(1);
  if (k === "keyword_score") return row.keyword_score == null ? "—" : Number(row.keyword_score).toFixed(2);
  if (k === "year") return row.year || "—";
  return row[k] == null ? "" : row[k];
}

function renderTable(tableId, colKey, rows, opts) {
  opts = opts || {};
  const table = $(tableId);
  table.innerHTML = "";
  const cols = COLS[colKey];

  const thead = el("thead"), trh = el("tr");
  cols.forEach(c => {
    // 摘要速览是给人读的，不是排序键：按它排序既没意义又会把顺序弄乱
    const th = el("th", c.nosort ? "" : "sortable", c.t);
    if (c.w) th.style.width = c.w + "px";
    if (c.nosort) { trh.appendChild(th); return; }
    th.onclick = () => {
      const cur = sortState[tableId];
      const dir = cur && cur.k === c.k && cur.dir === -1 ? 1 : -1;
      sortState[tableId] = { k: c.k, dir };
      const sorted = rows.slice().sort((a, b) => {
        let x = a[c.k], y = b[c.k];
        if (c.k === "title" || c.k === "journal") { x = x || ""; y = y || ""; return dir * String(x).localeCompare(String(y)); }
        x = (x == null || x === "") ? -Infinity : Number(x) || 0;
        y = (y == null || y === "") ? -Infinity : Number(y) || 0;
        return dir * (x - y);
      });
      renderTable(tableId, colKey, sorted, opts);
    };
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  table.appendChild(thead);

  const tb = el("tbody");
  if (!rows.length) {
    const tr = el("tr"), td = el("td", "empty", opts.emptyText || "没有数据");
    td.colSpan = cols.length; tr.appendChild(td); tb.appendChild(tr);
  }

  rows.forEach(row => {
    const tr = el("tr");

    if (opts.selectable) {
      const td = el("td");
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = !!row.__checked;
      cb.onchange = () => { row.__checked = cb.checked; if (opts.onSelect) opts.onSelect(); };
      td.appendChild(cb);
      tr.appendChild(td);
    }

    cols.forEach(c => {
      if (c.k === "_chk") return;
      const td = el("td", c.cls || "");
      const v = cellVal(row, c);

      if (c.k === "rank_str") {
        const box = el("td");
        (row.rank_str || "").split(";").filter(Boolean).forEach((s, i, arr) => {
          box.appendChild(rankTag(s.trim()));
          if (i < arr.length - 1) box.appendChild(document.createTextNode(" "));
        });
        tr.appendChild(box);
        return;
      }
      if (c.k === "is_oa") {
        td.textContent = row.is_oa === true ? "✓" : (row.is_oa === false ? "✗" : "·");
        td.className = row.is_oa === true ? "oa-y" : (row.is_oa === false ? "oa-n" : "oa-u");
        tr.appendChild(td);
        return;
      }
      if (c.k === "title") {
        const b = el("b", null, row.title || "");
        td.appendChild(b);
        if (row.oa_url) {
          const a = el("a", "tag", "PDF");
          a.href = row.oa_url; a.target = "_blank"; a.rel = "noreferrer";
          a.style.textDecoration = "none";
          td.appendChild(document.createTextNode(" "));
          td.appendChild(a);
        }
        if (opts.onTitleClick) {
          td.style.cursor = "pointer";
          td.onclick = () => opts.onTitleClick(row);
        }
        tr.appendChild(td);
        return;
      }
      if (c.k === "summary" && !row.has_note) {
        td.innerHTML = '<span class="muted">尚无笔记</span>';
        tr.appendChild(td);
        return;
      }
      if (c.k === "abstract_preview") {
        // 空摘要要显式标出来，否则容易被误当成加载失败
        if (!row.abstract_preview) {
          td.innerHTML = '<span class="muted">无摘要</span>';
        } else {
          td.title = row.abstract_preview;   // 悬停可看完整速览
          td.appendChild(el("div", "abs-cell", row.abstract_preview));
        }
        tr.appendChild(td);
        return;
      }
      td.textContent = v;
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  lastRows[tableId] = rows;
}

/* ---------------------------------------------------------------- 概览 */

async function loadOverview() {
  try {
    const o = await api("/api/overview");
    $("p-ver").textContent = "v" + o.version;
    const box = $("p-overview");
    box.innerHTML = "";
    const add = (txt, cls) => {
      const s = el("span", "pill" + (cls ? " " + cls : ""), txt);
      box.appendChild(s);
    };
    add(`数据源 ${o.sources_enabled}/${o.sources_total}`, o.sources_enabled ? "ok" : "warn");
    add(`等级表 ${o.journal_rank_total.toLocaleString()} 条`, o.journal_rank_total ? "ok" : "warn");
    add(o.zotero.ok ? `Zotero ${o.zotero.version || ""}` : "Zotero 未连接",
        o.zotero.ok ? "ok" : "err");
    if (o.zotero.linked) add(`已导入 ${o.zotero.linked} 篇`);
    add(`候选池 ${o.papers_total.toLocaleString()} 篇`);

    const sbox = $("sources");
    sbox.innerHTML = "";
    (o.sources || []).forEach(s => {
      const lab = el("label", "chk");
      const cb = el("input");
      cb.type = "checkbox"; cb.checked = s.enabled; cb.value = s.name; cb.dataset.src = "1";
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(s.name));
      if (s.requires_key && !s.has_key) {
        const t = el("span", "pill warn", "缺 key");
        t.style.fontSize = "10px";
        lab.appendChild(t);
      }
      sbox.appendChild(lab);
    });
    $("n-search").textContent = o.runs.length ? "" : "";
    $("btn-export").disabled = !o.runs.length;
  } catch (e) { toast("读取概览失败：" + e.message, true); }
}

/* ---------------------------------------------------------------- 检索 */

$("btn-search").onclick = async () => {
  const q = $("q").value.trim();
  const author = $("author").value.trim();
  const journal = $("journal").value.trim();
  if (!q && !author && !journal) { toast("至少填关键词、作者或期刊之一", true); return; }

  const sources = Array.from(document.querySelectorAll('#sources input[data-src]'))
    .filter(c => c.checked).map(c => c.value);

  $("search-progress").style.display = "block";
  $("search-result").style.display = "none";
  $("search-log").textContent = "开始检索…";
  $("search-bar").style.width = "4%";
  $("btn-search").disabled = true;
  $("search-hint").textContent = "";

  try {
    const j = await api("/api/search", "POST", {
      query: q, author, journal,
      year_from: $("year_from").value ? +$("year_from").value : null,
      year_to: $("year_to").value ? +$("year_to").value : null,
      limit: +$("limit").value || 40,
      sort: $("sort").value,
      sources,
      oa_enrich: $("oa_enrich").checked,
      preprints_only: $("preprints_only").checked,
    });
    const res = await pollJob(j.job_id, $("search-log"), $("search-bar"));
    renderSearchResult(res);
    $("run-select").value = res.run_id;
    loadRuns();
  } catch (e) {
    toast("检索失败：" + e.message, true);
    $("search-log").textContent += "\n\n[失败] " + e.message;
  } finally {
    $("btn-search").disabled = false;
  }
};

function renderSearchResult(res) {
  $("search-result").style.display = "block";
  const errs = Object.entries(res.errors || {});
  $("search-summary").textContent =
    `运行 #${res.run_id}｜原始 ${res.raw} 条 → 去重 ${res.papers.length} 篇`
    + (errs.length ? `｜${errs.length} 个源失败` : "");
  const rows = res.papers.map((p, i) => ({ ...p, __i: i + 1 }));
  renderTable("search-table", "search", rows);
  $("search-hint").textContent = "去「论文表」标签页做筛选";
  $("n-search").textContent = res.papers.length;
}

/* ---------------------------------------------------------------- 论文表 */

async function loadRuns() {
  try {
    const o = await api("/api/overview");
    const sel = $("run-select");
    const keep = sel.value;
    sel.innerHTML = "";
    o.runs.forEach(r => {
      const op = el("option", null, `#${r.id} · ${r.papers} 篇 · ${(r.label || "").slice(0, 40)}`);
      op.value = r.id;
      sel.appendChild(op);
    });
    if (keep && o.runs.some(r => String(r.id) === keep)) sel.value = keep;
    else if (o.runs.length) sel.value = o.runs[0].id;
    if (o.runs.length) loadPapers();
    else renderTable("papers-table", "papers", [], { emptyText: "还没有检索记录，先去「检索」跑一次" });
  } catch (e) { toast("读取运行列表失败：" + e.message, true); }
}

$("run-select").onchange = loadPapers;

function buildFilters() {
  const f = {};
  const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
  f.min_cites_per_year = num("f_mincpy");
  f.min_cites = num("f_mincites");
  f.min_keyword = num("f_minkw");
  const rs = $("f_ranksys").value;
  if (rs) f.rank = { [rs]: $("f_ranklevel").value };
  f.sort = $("f_sort").value;
  f.title_must = $("f_titlemust").value.split(",").map(s => s.trim()).filter(Boolean);
  f.must = $("f_must").value.split(",").map(s => s.trim()).filter(Boolean);
  f.exclude = $("f_exclude").value.split(",").map(s => s.trim()).filter(Boolean);
  f.oa_only = $("f_oa").checked;
  f.only_preprints = $("f_onlypre").checked;
  f.exclude_preprints = $("f_expre").checked;
  f.exclude_corrections = $("f_corr").checked;
  f.rank_allow_unknown = $("f_allowunknown").checked;
  return f;
}

async function loadPapers() {
  const runId = +$("run-select").value;
  if (!runId) return;
  try {
    const res = await api("/api/filter", "POST", { run_id: runId, filters: buildFilters() });
    const rows = res.papers.map((p, i) => ({ ...p, __i: i + 1, __checked: true }));
    $("papers-count").textContent = `纳入 ${rows.length} 篇`;
    $("filter-info").textContent =
      `候选 ${res.stats.input} 篇，剔除 ${res.stats.dropped} 篇`
      + (res.rank_coverage ? `｜等级覆盖 ${res.rank_coverage}` : "");
    renderTable("papers-table", "papers", rows, {
      selectable: true,
      onSelect: updateSelCount,
      emptyText: "当前条件下没有论文。放宽条件试试（例如去掉等级门槛）",
    });
    updateSelCount();
  } catch (e) { toast("筛选失败：" + e.message, true); }
}

function selectedRows() {
  return (lastRows["papers-table"] || []).filter(r => r.__checked);
}
function updateSelCount() {
  const n = selectedRows().length;
  $("papers-count").textContent =
    `共 ${(lastRows["papers-table"] || []).length} 篇，已选 ${n} 篇`;
  $("btn-import").disabled = n === 0;
  $("btn-export").disabled = n === 0;
}
$("btn-filter").onclick = loadPapers;
$("btn-filter-reset").onclick = () => {
  ["f_mincpy", "f_mincites", "f_minkw", "f_titlemust", "f_must", "f_exclude"].forEach(i => $(i).value = "");
  $("f_ranksys").value = ""; $("f_ranklevel").value = "Q1"; $("f_sort").value = "rank";
  $("f_oa").checked = false; $("f_onlypre").checked = false; $("f_expre").checked = false;
  $("f_corr").checked = true; $("f_allowunknown").checked = true;
  loadPapers();
};
$("btn-all").onclick = () => { (lastRows["papers-table"] || []).forEach(r => r.__checked = true); loadPapers(); };
$("btn-none").onclick = () => { (lastRows["papers-table"] || []).forEach(r => r.__checked = false); renderTable("papers-table", "papers", lastRows["papers-table"], { selectable: true, onSelect: updateSelCount }); updateSelCount(); };
$("btn-invert").onclick = () => { (lastRows["papers-table"] || []).forEach(r => r.__checked = !r.__checked); renderTable("papers-table", "papers", lastRows["papers-table"], { selectable: true, onSelect: updateSelCount }); updateSelCount(); };

$("btn-export").onclick = async () => {
  const keys = selectedRows().map(r => r.key);
  if (!keys.length) return;
  try {
    const res = await api("/api/export", "POST", { run_id: +$("run-select").value, keys });
    toast(`已导出 ${res.files.length} 个文件`);
    showFiles(res.files, "p-export-result", "p-export-body");
  } catch (e) { toast("导出失败：" + e.message, true); }
};

/* ---------------------------------------------------------------- 导出文件 */

const EXP_MIME_ICON = { xlsx: "▦", csv: "▤", md: "▤", bib: "❝" };

function baseName(p) {
  return String(p).split(/[\\/]/).pop();
}

// 下载链接。服务端固定返回 attachment，所以直接给 <a href> 即可。
function downloadHref(name) {
  return "/api/download?name=" + encodeURIComponent(name);
}

function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(2) + " MB";
}

function fmtTime(sec) {
  if (!sec) return "";
  const d = new Date(sec * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 生成「文件清单」DOM。files 可以是绝对路径字符串数组，也可以是 {name,size,mtime} 对象数组
function fileListDom(files) {
  const wrap = el("div");
  files.forEach(f => {
    const name = typeof f === "string" ? baseName(f) : f.name;
    const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
    const row = el("div");
    row.style.cssText = "display:flex;align-items:center;gap:10px;padding:7px 10px;"
      + "border:1px solid var(--line);border-radius:8px;margin-bottom:6px;background:#fff";
    const icon = el("span", null, EXP_MIME_ICON[ext] || "▢");
    icon.style.cssText = "font-size:15px;color:var(--accent)";

    const a = el("a", null, name);
    a.href = downloadHref(name);
    a.style.cssText = "font-size:13px;flex:1;color:var(--accent);text-decoration:none;"
      + "overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    a.title = "点击下载";
    row.appendChild(icon);
    row.appendChild(a);

    const meta = [];
    if (f && f.size != null) meta.push(fmtSize(f.size));
    if (f && f.mtime) meta.push(fmtTime(f.mtime));
    if (meta.length) {
      const m = el("span", "muted", meta.join(" · "));
      m.style.fontSize = "11.5px";
      row.appendChild(m);
    }
    const dl = el("a", "btn sm", "下载");
    dl.href = downloadHref(name);
    dl.style.textDecoration = "none";
    row.appendChild(dl);
    wrap.appendChild(row);
  });
  return wrap;
}

// 导出完成后就地显示结果。
// 注意 boxId/bodyId 必须传当前标签页里的容器——早先写死在 Zotero 页，
// 导致在「论文表」页导出后用户看不到任何文件清单。
function showFiles(files, boxId, bodyId) {
  if (!files || !files.length) return;
  const box = $(boxId);
  const body = $(bodyId);
  if (!box || !body) return;
  box.style.display = "block";
  body.innerHTML = "";
  const tip = el("div", "muted", `共 ${files.length} 个文件，点击文件名或右侧「下载」保存。`);
  tip.style.cssText = "font-size:12px;margin-bottom:8px";
  body.appendChild(tip);
  body.appendChild(fileListDom(files));
}

async function loadExports() {
  const box = $("exp-list");
  box.innerHTML = "<div class='muted' style='font-size:12.5px'>读取中…</div>";
  try {
    const res = await api("/api/exports");
    const all = res.files || [];
    $("exp-dir").textContent = res.dir || "";
    $("exp-filter").dataset.dir = res.dir || "";
    renderExports(all);
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", null, "读取失败：" + e.message));
  }
}

let expAll = [];

function renderExports(all) {
  expAll = all;
  const ext = $("exp-filter").value;
  const kw = ($("exp-search").value || "").trim().toLowerCase();
  let rows = all;
  if (ext) rows = rows.filter(f => f.ext === ext);
  if (kw) rows = rows.filter(f => f.name.toLowerCase().includes(kw));

  $("exp-count").textContent = `共 ${rows.length} 个文件`
    + (rows.length !== all.length ? `（已从 ${all.length} 个中过滤）` : "");

  const box = $("exp-list");
  box.innerHTML = "";
  if (!rows.length) {
    box.appendChild(el("div", "muted", all.length ? "没有匹配的文件。" : "还没有导出过任何文件。去「论文表」页勾选后点「导出选中」。"));
    return;
  }
  box.appendChild(fileListDom(rows));
}

$("btn-exp-refresh").onclick = loadExports;
$("exp-filter").onchange = () => renderExports(expAll);
$("exp-search").oninput = () => renderExports(expAll);
document.querySelectorAll(".goto-exports").forEach(b => {
  b.onclick = () => document.querySelector('nav button[data-tab="exports"]').click();
});

/* ---------------------------------------------------------------- Zotero */

async function zoteroCheck() {
  const box = $("z-status");
  box.textContent = "检测中…";
  try {
    const z = await api("/api/zotero/status");
    box.innerHTML = "";
    const dl = el("dl", "kv");
    const add = (k, v, cls) => {
      dl.appendChild(el("dt", null, k));
      const dd = el("dd", cls || null, v);
      dl.appendChild(dd);
    };
    add("本地 API", z.ok ? "可用" : "不可用", z.ok ? "oa-y" : "oa-n");
    if (z.ok) {
      add("Zotero 版本", z.version || "—");
      add("写授权", z.has_key ? "已缓存（不再弹窗）" : "未授权（首次写入会弹窗）");
      add("集合数", z.collections);
      add("已导入文献", z.linked + " 篇");
    } else {
      add("原因", z.error || "未知");
    }
    box.appendChild(dl);
    if (!z.ok) {
      const h = el("div", "hint");
      h.innerHTML = "请确认 Zotero 正在运行，且 <b>设置 → Advanced</b> 勾选了 "
        + "「Allow other applications on this computer to communicate with Zotero」。";
      box.appendChild(h);
    }
  } catch (e) { box.textContent = "检测失败：" + e.message; }
}
$("btn-zcheck").onclick = zoteroCheck;

async function runZoteroJob(path, body, okMsg) {
  $("z-progress").style.display = "block";
  $("z-log").textContent = "执行中…";
  $("z-bar").style.width = "5%";
  try {
    const j = await api(path, "POST", body);
    const res = await pollJob(j.job_id, $("z-log"), $("z-bar"), 800);
    if (okMsg) toast(okMsg);
    return res;
  } catch (e) {
    toast("失败：" + e.message, true);
    $("z-log").textContent += "\n\n[失败] " + e.message;
    throw e;
  }
}

$("btn-zimport").onclick = async () => {
  const keys = selectedRows().map(r => r.key);
  if (!keys.length) { toast("先在「论文表」里勾选要写入的论文", true); return; }
  try {
    const res = await runZoteroJob("/api/zotero/import", {
      run_id: +$("run-select").value, keys,
      collection: $("z-collection").value.trim() || "litflow 导入",
      status: $("z-status-sel").value,
      attach_pdf: $("z-attach").checked,
    }, "写入完成");
    renderZoteroResult(res);
    zoteroCheck();
  } catch (e) { /* 已提示 */ }
};

$("btn-zaudit").onclick = async () => {
  try {
    const res = await api("/api/zotero/audit");
    renderZoteroResult(res, true);
  } catch (e) { toast("体检失败：" + e.message, true); }
};

$("btn-zsync").onclick = async () => {
  try {
    const res = await api("/api/zotero/sync", "POST", {});
    toast(`重建完成：新增 ${res.matched} 条关联`);
    zoteroCheck();
  } catch (e) { toast("重建失败：" + e.message, true); }
};

function renderZoteroResult(res, isAudit) {
  const box = $("z-result"), body = $("z-result-body");
  box.style.display = "block";
  body.innerHTML = "";

  if (isAudit) {
    body.appendChild(el("b", null, `已导入 ${res.total} 篇`));
    const dl = el("dl", "kv");
    dl.style.marginTop = "10px";
    const add = (k, v) => { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); };
    add("有 PDF 附件", res.ok + " 篇");
    add("没有 PDF", res.no_pdf + " 篇" + (res.no_pdf_fixable ? `（其中 ${res.no_pdf_fixable} 篇可补抓）` : ""));
    if (res.missing) add("Zotero 里已找不到", res.missing + " 篇");
    body.appendChild(dl);
    if (res.no_pdf) {
      $("btn-zapdf").style.display = res.no_pdf_fixable ? "inline-block" : "none";
      const hint = el("div", "hint");
      hint.textContent = "没有 PDF 的条目 Butler 无法精读。可点「只补 PDF」批量补抓，"
        + "或用 `litflow zotero attach-pdf` 并查看导出的失败清单。";
      body.appendChild(hint);
    }
    return;
  }

  body.appendChild(el("b", null, res.summary || "完成"));
  const ul = el("div");
  ul.style.marginTop = "8px";
  (res.skipped || []).slice(0, 6).forEach(s => {
    const d = el("div", "hint"); d.textContent = "跳过：" + s[1]; ul.appendChild(d);
  });
  (res.failed || []).slice(0, 6).forEach(f => {
    const d = el("div", "hint"); d.style.color = "var(--err)";
    d.textContent = "失败：" + f[0] + " —— " + f[1]; ul.appendChild(d);
  });
  (res.pdf_problems || []).slice(0, 6).forEach(f => {
    const d = el("div", "hint"); d.textContent = "PDF 未抓取：" + f[0] + " —— " + f[1]; ul.appendChild(d);
  });
  body.appendChild(ul);
}

$("btn-zapdf").onclick = async () => {
  try {
    const res = await runZoteroJob("/api/zotero/attach-pdf", {}, "补抓完成");
    toast(`补抓成功 ${res.attached} 篇，失败 ${res.problems}` + (res.manifest ? "（失败清单已导出）" : ""));
  } catch (e) { /* 已提示 */ }
};

/* ---------------------------------------------------------------- 提炼 */

async function digestStatus() {
  const box = $("d-status");
  box.textContent = "读取中…";
  try {
    const s = await api("/api/digest/status");
    box.innerHTML = "";
    if (!s.total) {
      box.innerHTML = '<span class="muted">还没有文献导入 Zotero。先去「Zotero」标签页写入。</span>';
      renderTable("digest-table", "digest", [], { emptyText: "无数据" });
      $("d-count").textContent = "—";
      return;
    }
    const dl = el("dl", "kv");
    const add = (k, v, cls) => { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", cls || null, v)); };
    add("已导入 Zotero", s.total + " 篇");
    add("已有精读笔记", s.done + " 篇", s.done ? "oa-y" : null);
    add("待 Butler 精读", s.pending + " 篇");
    add("缺 PDF（读不了）", s.no_pdf + " 篇", s.no_pdf ? "oa-n" : null);
    box.appendChild(dl);
    const pct = Math.round(100 * s.done / s.total);
    const bar = el("div", "bar");
    const i = el("i"); i.style.width = pct + "%";
    bar.appendChild(i);
    box.appendChild(bar);
    const hint = el("div", "hint");
    hint.textContent = `完成度 ${pct}%`;
    box.appendChild(hint);
    if (s.no_pdf) {
      const h2 = el("div", "hint");
      h2.textContent = "缺 PDF 的条目 Butler 无法精读，可用「Zotero」页的「只补 PDF」批量补抓。";
      box.appendChild(h2);
    }
  } catch (e) { box.textContent = "读取失败：" + e.message; }
}
$("btn-dstatus").onclick = digestStatus;

$("btn-dfetch").onclick = async () => {
  $("d-progress").style.display = "block";
  $("d-log").textContent = "回读笔记中…";
  $("d-bar").style.width = "5%";
  try {
    const j = await api("/api/digest/fetch", "POST", {});
    await pollJob(j.job_id, $("d-log"), $("d-bar"), 700);
    toast("回读完成");
    digestStatus();
    loadDigest();
  } catch (e) {
    toast("回读失败：" + e.message, true);
    $("d-log").textContent += "\n\n[失败] " + e.message;
  }
};

async function loadDigest() {
  const rid = +$("run-select").value || null;
  try {
    const res = await api("/api/digest/rows" + (rid ? "?run_id=" + rid : ""));
    const rows = res.rows.map((r, i) => ({ ...r, __i: i + 1 }));
    $("d-count").textContent = `${res.with_note}/${rows.length} 篇已有笔记`;
    $("d-hint").textContent = rows.length
      ? "点击标题可查看笔记解析详情"
      : "先去「Zotero」写入文献，再等 Butler 生成笔记";
    renderTable("digest-table", "digest", rows, {
      onTitleClick: row => showNoteDetail(row.paper_key),
      emptyText: "还没有可汇总的论文",
    });
  } catch (e) { toast("读取汇总失败：" + e.message, true); }
}

async function showNoteDetail(key) {
  try {
    const d = await api("/api/digest/note?key=" + encodeURIComponent(key));
    // 必须渲染在「提炼」页自己的容器里。原来写死成 z-result，
    // 点标题后详情跑到 Zotero 标签页去了，当前页看不到任何反应。
    const box = $("d-note"), body = $("d-note-body");
    box.style.display = "block";
    body.innerHTML = "";
    $("d-note-title").textContent = d.title || key;
    const dl = el("dl", "kv"); dl.style.marginTop = "10px";
    Object.entries(d.fields || {}).forEach(([k, v]) => {
      if (!v) return;
      dl.appendChild(el("dt", null, d.labels[k] || k));
      const dd = el("dd", null, v); dd.style.maxHeight = "160px"; dd.style.overflow = "auto";
      dl.appendChild(dd);
    });
    if (!Object.keys(d.fields || {}).length) {
      body.appendChild(el("div", "hint", "还没有解析出字段。可能 Butler 尚未生成笔记。"));
    } else {
      body.appendChild(dl);
    }
    if (d.raw) {
      const det = el("details", "note");
      det.appendChild(el("summary", null, "查看笔记原文（解析不对时用它排查）"));
      const pre = el("pre", null, d.raw);
      det.appendChild(pre);
      body.appendChild(det);
    }
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { toast("读取笔记失败：" + e.message, true); }
}
$("btn-dnote-close").onclick = () => { $("d-note").style.display = "none"; };

$("btn-dexport").onclick = async () => {
  try {
    const res = await api("/api/digest/export", "POST", { run_id: +$("run-select").value || null });
    toast(`已导出 ${res.files.length} 个文件`);
    showFiles(res.files, "d-export-result", "d-export-body");
  } catch (e) { toast("导出失败：" + e.message, true); }
};

/* ---------------------------------------------------------------- 配方 */

async function listRecipes() {
  const box = $("recipe-list");
  box.textContent = "读取中…";
  try {
    const res = await api("/api/recipes");
    box.innerHTML = "";
    if (!res.recipes.length) {
      box.appendChild(el("div", "empty", "还没有配方。点「生成模板」再复制一份改名。"));
      return;
    }
    res.recipes.forEach(r => {
      const card = el("div", "recipe");
      card.appendChild(el("div", "nm", r.name));
      if (r.description) card.appendChild(el("div", "desc", r.description));
      const m = el("div", "desc");
      m.textContent = `检索：${r.query || "—"}　|　阶段：${r.stages.join(" → ")}`;
      card.appendChild(m);

      const row = el("div", "row");
      row.style.marginTop = "9px";
      const b = el("button", "btn primary sm", "运行完整流程");
      b.onclick = () => runRecipe(r.name, []);
      row.appendChild(b);

      ["search", "filter", "zotero", "digest-export"].forEach(s => {
        const lb = el("button", "btn sm", "只跑 " + s);
        lb.onclick = () => runRecipe(r.name, [s]);
        row.appendChild(lb);
      });

      const det = el("details", "note");
      det.appendChild(el("summary", null, "查看配方内容"));
      det.appendChild(el("pre", null, r.text));
      row.appendChild(det);
      card.appendChild(row);
      box.appendChild(card);
    });
  } catch (e) { box.textContent = "读取失败：" + e.message; }
}
$("btn-rlist").onclick = listRecipes;
$("btn-rinit").onclick = async () => {
  try {
    const r = await api("/api/recipes/init", "POST", {});
    toast(r.created.length ? "模板已生成" : "模板已存在");
    listRecipes();
  } catch (e) { toast("生成失败：" + e.message, true); }
};

async function runRecipe(name, only) {
  $("r-progress").style.display = "block";
  $("r-log").textContent = `执行配方「${name}」…`;
  $("r-bar").style.width = "4%";
  // 记下开始时间，跑完后据此挑出「本次新生成」的文件
  const startedAt = Date.now() / 1000 - 2;
  try {
    const j = await api("/api/recipe/run", "POST", { name, only });
    const res = await pollJob(j.job_id, $("r-log"), $("r-bar"), 800);
    toast(res.ok === res.total ? "全部阶段执行成功" : `${res.ok}/${res.total} 个阶段成功`,
          res.ok !== res.total);
    await showNewExports(startedAt, "r-export-result", "r-export-body");
  } catch (e) {
    toast("执行失败：" + e.message, true);
    $("r-log").textContent += "\n\n[失败] " + e.message;
  }
}

// 跑完流程后列出这一次新生成的文件（导出目录里 mtime 晚于 startTs 的）
async function showNewExports(startTs, boxId, bodyId) {
  try {
    const res = await api("/api/exports");
    const fresh = (res.files || []).filter(f => f.mtime >= startTs);
    if (!fresh.length) return;
    showFiles(fresh, boxId, bodyId);
  } catch (e) { /* 列表拉不到不影响主流程 */ }
}

/* ---------------------------------------------------------------- 启动 */

(async function init() {
  await loadOverview();
  // 从 URL 里读默认检索词，方便从命令行跳过来
  const p = new URLSearchParams(location.search);
  if (p.get("q")) $("q").value = p.get("q");
  if (p.get("tab")) {
    const b = document.querySelector(`nav button[data-tab="${p.get("tab")}"]`);
    if (b) b.click();
  }
})();
</script>
</body>
</html>
"""
