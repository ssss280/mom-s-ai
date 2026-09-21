const $ = (sel) => document.querySelector(sel);

const state = {
  page: "chat",
  config: {},
  providers: {},
  version: "",
  sessionId: Number(localStorage.getItem("chatsight_session") || 0),
  chatSessionId: Number(localStorage.getItem("chatsight_chat_session") || 0),
  chat: [],
  capture: null,
  selectedSession: null,
};

// ---------- 基础 ----------

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
  return data;
}

function postJSON(path, body, method = "POST") {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

function setStatus(text) {
  $("#status-text").textContent = text;
}

let toastTimer = null;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3000);
}

function showError(e) {
  const msg = e instanceof Error ? e.message : String(e);
  setStatus(`错误: ${msg}`);
  toast(msg);
}

// 搜索结果里的标题/摘要是**外部网页内容**，直接拼进 innerHTML 会被注入脚本。
// 任何来自搜索结果的字符串都必须先过这个函数。
function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setSession(id) {
  state.sessionId = id;
  localStorage.setItem("chatsight_session", String(id));
}

function setChatSession(id) {
  state.chatSessionId = id;
  localStorage.setItem("chatsight_chat_session", String(id));
}

// ---------- 页面导航 ----------

function showPage(key) {
  state.page = key;
  document.querySelectorAll(".nav-item").forEach((el) =>
    el.classList.toggle("active", el.dataset.page === key)
  );
  document.querySelectorAll(".page").forEach((el) =>
    el.classList.toggle("active", el.id === `page-${key}`)
  );
}

document.querySelectorAll(".nav-item").forEach((el) =>
  el.addEventListener("click", () => {
    if (el.dataset.page === "chat") resetNewChat();
    showPage(el.dataset.page);
  })
);

// ---------- AI 对话 ----------

function appendMsg(role, text, isError = false, sources = null) {
  $("#chat-empty").style.display = "none";
  const div = document.createElement("div");
  div.className = `msg ${role}` + (isError ? " error" : "");
  div.textContent = text;
  // 联网搜索的来源要挂在正文后面（顺序不能反，否则会被 textContent 清掉）
  if (sources && sources.length) {
    appendSources(div, sources);
  }
  $("#chat-messages").appendChild(div);
  const area = $("#chat-area");
  area.scrollTop = area.scrollHeight;
  return div;
}

function appendSources(div, sources) {
  const box = document.createElement("div");
  box.className = "msg-sources";

  const head = document.createElement("div");
  head.className = "msg-sources-head";
  head.textContent = "信息来源";
  box.appendChild(head);

  sources.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "msg-source";

    const a = document.createElement("a");
    a.href = s.url || "#";
    a.target = "_blank";
    a.rel = "noopener";
    a.title = s.url || "";

    const idx = document.createElement("span");
    idx.className = "src-index";
    idx.textContent = `[${i + 1}]`;

    // 明确标出是哪个网站
    const site = document.createElement("span");
    site.className = "src-site";
    site.textContent = s.site || s.url || "";

    const title = document.createElement("span");
    title.className = "src-title";
    title.textContent = s.title || s.url || "";

    a.append(idx, site, title);
    row.appendChild(a);
    box.appendChild(row);
  });
  div.appendChild(box);
}

// ---------- 打字机效果 ----------

let typewriterActive = false;

async function typewriterMsg(div, text) {
  typewriterActive = true;
  const cursor = document.createElement("span");
  cursor.className = "typing-cursor";
  div.appendChild(cursor);
  const area = $("#chat-area");
  for (let i = 0; i < text.length; i++) {
    if (!typewriterActive) break;
    div.insertBefore(document.createTextNode(text[i]), cursor);
    // 每打几个字就滚动一次，避免长文本卡住滚动
    if (i % 3 === 0) area.scrollTop = area.scrollHeight;
    // 标点处稍微停顿，让节奏更像人在打字
    const ch = text[i];
    let delay = 18;
    if (/[，。！？；：、,.!?;:]/.test(ch)) delay = 120;
    else if (/[\n\r]/.test(ch)) delay = 80;
    await sleep(delay);
  }
  typewriterActive = false;
  cursor.remove();
  area.scrollTop = area.scrollHeight;
}

function skipTypewriter() {
  typewriterActive = false;
}

// ---------- 加载动画 ----------

function showLoading() {
  $("#chat-empty").style.display = "none";
  const div = document.createElement("div");
  div.className = "msg ai msg-loading";
  div.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  $("#chat-messages").appendChild(div);
  const area = $("#chat-area");
  area.scrollTop = area.scrollHeight;
  return div;
}

function removeLoading(div) {
  if (div && div.parentNode) div.remove();
}

// ---------- 推荐追问 ----------

async function showSuggestions(context) {
  const box = $("#chat-suggestions");
  box.innerHTML = "";
  box.hidden = true;
  if (!context || !context.trim()) return;

  try {
    const data = await postJSON("/api/chat/suggest-questions", {
      messages: state.chat.slice(-6), // 最近几条对话作为上下文
    });
    const questions = (data.questions || []).filter((q) => q && q.trim());
    if (!questions.length) return;
    box.hidden = false;
    questions.forEach((q) => {
      const chip = document.createElement("button");
      chip.className = "chat-sug-chip";
      chip.textContent = q.trim();
      chip.title = "点击直接发送";
      chip.addEventListener("click", () => {
        box.innerHTML = "";
        box.hidden = true;
        $("#chat-input").value = q.trim();
        sendChat();
      });
      box.appendChild(chip);
    });
    const area = $("#chat-area");
    area.scrollTop = area.scrollHeight;
  } catch (e) {
    // 推荐追问失败不打扰用户，静默处理
    console.warn("推荐追问失败:", e);
  }
}

function hideSuggestions() {
  const box = $("#chat-suggestions");
  box.innerHTML = "";
  box.hidden = true;
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  const useSearch = $("#chat-search").checked;

  input.value = "";
  state.chat.push({ role: "user", content: text });
  appendMsg("user", text);
  hideSuggestions();
  setStatus(useSearch ? "正在联网搜索..." : "AI 正在思考...");
  $("#btn-chat-send").disabled = true;

  const loadingDiv = showLoading();

  try {
    const data = await postJSON("/api/chat", {
      messages: state.chat,
      search: useSearch,
      session_id: state.chatSessionId || 0,
    });
    removeLoading(loadingDiv);

    if (data.session_id) setChatSession(data.session_id);
    state.chat.push({ role: "assistant", content: data.reply });

    // 先创建消息 div，再用打字机逐字填入，来源追加在打字完成后
    const msgDiv = document.createElement("div");
    msgDiv.className = "msg ai";
    $("#chat-messages").appendChild(msgDiv);
    const area = $("#chat-area");
    area.scrollTop = area.scrollHeight;

    await typewriterMsg(msgDiv, data.reply);

    if (data.sources && data.sources.length) {
      appendSources(msgDiv, data.sources);
      area.scrollTop = area.scrollHeight;
    }

    refreshHistory();
    if (useSearch && data.search) {
      const count = (data.sources || []).length;
      if (count) {
        const pages = data.search.pages || 0;
        const rewritten = data.search.query_used && data.search.query_used !== data.search.query
          ? `，改用「${data.search.query_used}」` : "";
        setStatus(`已联网搜索（${data.search.engine || "模型原生"}${rewritten}）`
          + (pages ? `，读取 ${pages} 篇正文` : "")
          + `，引用 ${count} 条结果`);
      } else {
        setStatus(`联网搜索没有取到结果：${data.search.error || "未知原因"}`);
      }
    } else {
      setStatus("就绪");
    }

    // 回答完成后生成推荐追问
    showSuggestions(data.reply);
  } catch (e) {
    removeLoading(loadingDiv);
    // 失败时不要把这条消息留在待发送历史里，否则重试会发出两条连续的 user 消息
    const last = state.chat[state.chat.length - 1];
    if (last && last.role === "user") state.chat.pop();
    appendMsg("ai", `错误: ${e.message}`, true);
    setStatus("就绪");
  } finally {
    $("#btn-chat-send").disabled = false;
  }
}

// 联网搜索开关（记住上次的选择）
const searchToggle = $("#chat-search");
searchToggle.checked = localStorage.getItem("chatsight_chat_search") === "1";
searchToggle.addEventListener("change", () => {
  localStorage.setItem("chatsight_chat_search", searchToggle.checked ? "1" : "0");
  setStatus(searchToggle.checked ? "联网搜索已开启" : "联网搜索已关闭");
});

$("#btn-chat-send").addEventListener("click", sendChat);
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

$("#btn-chat-clear").addEventListener("click", () => {
  // 只清空当前这段对话、下次发送新建一条记录；已保存的记录留在左侧历史里
  state.chat = [];
  setChatSession(0);
  $("#chat-messages").innerHTML = "";
  $("#chat-empty").style.display = "";
  setStatus("对话已清空");
});

// ---------- 识别：上传 ----------

$("#btn-upload").addEventListener("click", () => $("#file-input").click());

$("#file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;

  setStatus("正在识别图片中的文字...");
  const form = new FormData();
  form.append("image", file);
  form.append("session_id", String(state.sessionId || 0));

  try {
    const data = await api("/api/ocr/upload", { method: "POST", body: form });
    onRecognized(data);
  } catch (err) {
    showError(err);
  }
});

function onRecognized(data) {
  $("#recog-text").value = data.text;
  setSession(data.session_id);

  // 本次识别用到的图片（原图 / 裁剪图）都留了文件，这里给出可点开的缩略图
  const shots = [];
  if (data.source_url) shots.push({ url: data.source_url, label: "原图" });
  if (data.image_url) shots.push({ url: data.image_url, label: data.source_url ? "裁剪图" : "上传图" });
  renderShots(shots);

  refreshHistory();
  setStatus(`识别完成，共 ${data.text.length} 个字符`);
}

// ---------- 截图查看 ----------

function renderShots(items) {
  const strip = $("#shot-strip");
  strip.innerHTML = "";
  const list = (items || []).filter((s) => s && s.url);
  if (!list.length) {
    strip.hidden = true;
    return;
  }
  list.forEach((s) => {
    const box = document.createElement("div");
    box.className = "shot";
    box.title = `${s.label}（点击查看大图）`;

    const img = document.createElement("img");
    img.src = s.url;
    img.alt = s.label;
    img.loading = "lazy";

    const label = document.createElement("span");
    label.className = "shot-label";
    label.textContent = s.label;

    box.append(img, label);
    box.addEventListener("click", () => openImage(s.url, s.label));
    strip.appendChild(box);
  });
  strip.hidden = false;
}

function openImage(url, label = "截图") {
  $("#image-view").src = url;
  $("#image-title").textContent = label;
  $("#image-open").href = url;
  $("#image-modal").hidden = false;
}

function closeImage() {
  $("#image-modal").hidden = true;
  $("#image-view").src = "";
}

$("#btn-image-close").addEventListener("click", closeImage);
$("#image-modal").addEventListener("click", (e) => {
  // 点空白处关闭，点图片本身不关
  if (e.target.id === "image-modal") closeImage();
});

// ---------- 识别：截屏框选 ----------

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function captureFullScreen() {
  const delay = Number($("#capture-delay").value) || 0;
  for (let i = delay; i > 0; i--) {
    // 浏览器窗口在最前面时，服务端截到的只会是浏览器自己，
    // 所以给一个倒计时让用户先切到要识别的聊天窗口
    setStatus(`将在 ${i} 秒后截屏，请先切换到要识别的聊天窗口`);
    toast(`${i} 秒后截屏，请切换到目标窗口`);
    await sleep(1000);
  }
  setStatus("正在截屏...");
  const data = await postJSON("/api/capture");
  state.capture = data;
  openCropModal(data);
  setStatus("请拖拽框选要识别的区域");
}

$("#btn-capture").addEventListener("click", async () => {
  try {
    await captureFullScreen();
  } catch (e) {
    showError(e);
  }
});

// ---------- 识别：截取指定窗口 ----------

async function openWindowPicker() {
  $("#window-modal").hidden = false;
  $("#window-list").innerHTML = '<div class="win-empty">正在读取窗口列表...</div>';
  try {
    const data = await api("/api/windows");
    if (!data.supported) {
      $("#window-list").innerHTML =
        '<div class="win-empty">窗口截图目前只支持 Windows，请改用「截屏识别」的延时模式。</div>';
      return;
    }
    renderWindows(data.windows);
  } catch (e) {
    $("#window-list").innerHTML = `<div class="win-empty">读取窗口列表失败：${e.message}</div>`;
  }
}

function renderWindows(windows) {
  const list = $("#window-list");
  list.innerHTML = "";
  if (!windows.length) {
    list.innerHTML = '<div class="win-empty">没有找到可截取的窗口</div>';
    return;
  }
  windows.forEach((w) => {
    const item = document.createElement("div");
    item.className = "win-item";

    const title = document.createElement("div");
    title.className = "win-title";
    title.textContent = w.title;
    title.title = w.title;

    const size = document.createElement("div");
    size.className = "win-size";
    size.textContent = `${w.width}×${w.height}`;

    item.append(title, size);
    if (w.minimized) {
      const tag = document.createElement("div");
      tag.className = "win-tag";
      tag.textContent = "已最小化";
      item.appendChild(tag);
    }
    item.addEventListener("click", () => pickWindow(w));
    list.appendChild(item);
  });
}

async function pickWindow(w) {
  closeWindowPicker();
  setStatus(`正在截取窗口：${w.title}`);
  try {
    const data = await postJSON("/api/capture/window", { id: w.id });
    state.capture = data;
    openCropModal(data);
    setStatus("请拖拽框选要识别的区域");
  } catch (e) {
    showError(e);
  }
}

function closeWindowPicker() {
  $("#window-modal").hidden = true;
}

$("#btn-window").addEventListener("click", openWindowPicker);
$("#btn-window-refresh").addEventListener("click", openWindowPicker);
$("#btn-window-cancel").addEventListener("click", closeWindowPicker);

// ---------- 联网搜索记录（排查"搜出来为什么不对"）----------
// 数据来自 /api/search/log：每次搜索用了哪个查询词、打了哪些源、每个源什么状态、
// 留下了什么、丢了什么。比翻 data/app.log 直观得多。
let searchLogOnlyProblems = false;

function searchLogStateLabel(state) {
  return {
    ok: "正常", empty: "无结果", blocked: "被拦", error: "失败",
    cooling: "冷却跳过", cached: "缓存命中",
  }[state] || state;
}

function renderSearchLog(data) {
  const list = $("#searchlog-list");
  const records = data.records || [];
  const shown = searchLogOnlyProblems
    ? records.filter(r => !r.returned || r.status === "low_relevance")
    : records;
  $("#searchlog-hint").textContent =
    `共 ${data.count} 条记录（${data.problem_count} 条有问题）` +
    `${searchLogOnlyProblems ? `，当前只显示 ${shown.length} 条有问题的` : ""} · ` +
    `文件：${data.path}`;
  if (!shown.length) {
    list.innerHTML = `<p class="win-hint">暂无记录。开启「联网搜索」发一条消息后这里就有内容了。</p>`;
    return;
  }
  list.innerHTML = shown.map(record => {
    const engines = (record.engines || []).map(e =>
      `<span class="sl-chip ${e.state}">${escapeHtml(e.engine)}=${searchLogStateLabel(e.state)}` +
      `${e.found ? ` ${e.found}条` : ""}${e.ms ? ` ${e.ms}ms` : ""}</span>`).join("");
    const results = (record.results || []).map(item =>
      `<li><b>${item.score.toFixed(2)}</b> [${escapeHtml(item.engine)}] ` +
      `<span class="sl-site">${escapeHtml(item.site)}</span> ${escapeHtml(item.title)}</li>`).join("");
    const dropped = (record.dropped || []).slice(0, 3).map(item =>
      `<li class="sl-dropped">分低丢弃 ${item.score.toFixed(2)} ${escapeHtml(item.title)}</li>`).join("");
    const overflow = (record.overflow || []).slice(0, 3).map(item =>
      `<li class="sl-overflow">超出名额 ${item.score.toFixed(2)} ${escapeHtml(item.title)}</li>`).join("");
    const bad = !record.returned || record.status === "low_relevance";
    return `<div class="sl-item ${bad ? "sl-bad" : ""}">
      <div class="sl-head">
        <span class="sl-time">${escapeHtml(record.time || "")}</span>
        <span class="sl-query">${escapeHtml(record.query || "")}</span>
        <span class="sl-meta">→ 「${escapeHtml(record.query_used || record.query || "")}」 ` +
        `返回 ${record.returned} 条 · ${record.elapsed}s</span>
      </div>
      <div class="sl-engines">${engines || "(未调用引擎)"}</div>
      ${record.error ? `<div class="sl-error">${escapeHtml(record.error)}</div>` : ""}
      ${results ? `<ul class="sl-results">${results}</ul>` : ""}
      ${dropped || overflow ? `<ul class="sl-extra">${dropped}${overflow}</ul>` : ""}
    </div>`;
  }).join("");
}

async function openSearchLog() {
  $("#searchlog-modal").hidden = false;
  try {
    const response = await fetch(`/api/search/log?limit=30`);
    const data = await response.json();
    if (data.error) { toast(data.error); return; }
    renderSearchLog(data);
  } catch (e) {
    toast("读取搜索记录失败：" + e.message);
  }
}

function closeSearchLog() {
  $("#searchlog-modal").hidden = true;
}

$("#btn-search-log").addEventListener("click", openSearchLog);
$("#btn-searchlog-close").addEventListener("click", closeSearchLog);
$("#btn-searchlog-refresh").addEventListener("click", openSearchLog);
$("#btn-searchlog-only").addEventListener("click", () => {
  searchLogOnlyProblems = !searchLogOnlyProblems;
  $("#btn-searchlog-only").textContent = searchLogOnlyProblems ? "看全部" : "只看有问题";
  openSearchLog();
});
$("#btn-searchlog-clear").addEventListener("click", async () => {
  try {
    await fetch("/api/search/log/clear", { method: "POST" });
    toast("已清空搜索记录");
    openSearchLog();
  } catch (e) {
    toast("清空失败：" + e.message);
  }
});

const crop = { dragging: false, x1: 0, y1: 0, x2: 0, y2: 0 };

function openCropModal(data) {
  const img = $("#crop-image");
  img.src = data.url;
  $("#crop-rect").hidden = true;
  $("#crop-info").textContent = "拖拽框选要识别的区域";
  $("#crop-modal").hidden = false;
}

function closeCropModal() {
  $("#crop-modal").hidden = true;
  $("#crop-image").src = "";
  state.capture = null;
  setStatus("就绪");
}

function cropPoint(e) {
  const rect = $("#crop-image").getBoundingClientRect();
  return {
    x: Math.min(Math.max(e.clientX - rect.left, 0), rect.width),
    y: Math.min(Math.max(e.clientY - rect.top, 0), rect.height),
    offsetX: rect.left - $("#crop-stage").getBoundingClientRect().left,
    offsetY: rect.top - $("#crop-stage").getBoundingClientRect().top,
  };
}

$("#crop-stage").addEventListener("mousedown", (e) => {
  if (!state.capture) return;
  const p = cropPoint(e);
  crop.dragging = true;
  crop.x1 = crop.x2 = p.x;
  crop.y1 = crop.y2 = p.y;
  $("#crop-rect").hidden = true;
});

// 拖拽过程挂在 document 上：鼠标移到舞台外（工具栏、窗口外）也不会丢失事件
document.addEventListener("mousemove", (e) => {
  if (!crop.dragging) return;
  const p = cropPoint(e);
  crop.x2 = p.x;
  crop.y2 = p.y;
  const x = Math.min(crop.x1, crop.x2);
  const y = Math.min(crop.y1, crop.y2);
  const w = Math.abs(crop.x2 - crop.x1);
  const h = Math.abs(crop.y2 - crop.y1);
  const rectEl = $("#crop-rect");
  rectEl.hidden = false;
  rectEl.style.left = `${p.offsetX + x}px`;
  rectEl.style.top = `${p.offsetY + y}px`;
  rectEl.style.width = `${w}px`;
  rectEl.style.height = `${h}px`;
  const scale = state.capture ? state.capture.width / $("#crop-image").getBoundingClientRect().width : 1;
  $("#crop-info").textContent = `区域: ${Math.round(w * scale)} x ${Math.round(h * scale)}`;
});

document.addEventListener("mouseup", async () => {
  if (!crop.dragging) return;
  crop.dragging = false;
  if (!state.capture) return;

  const img = $("#crop-image").getBoundingClientRect();
  const w = Math.abs(crop.x2 - crop.x1);
  const h = Math.abs(crop.y2 - crop.y1);
  if (w < 10 || h < 10) {
    $("#crop-rect").hidden = true;
    $("#crop-info").textContent = "选区太小，请重新拖拽框选";
    return;
  }

  const scale = state.capture.width / img.width;
  const body = {
    name: state.capture.name,
    session_id: state.sessionId || 0,
    x: Math.round(Math.min(crop.x1, crop.x2) * scale),
    y: Math.round(Math.min(crop.y1, crop.y2) * scale),
    w: Math.round(w * scale),
    h: Math.round(h * scale),
  };

  $("#crop-modal").hidden = true;
  state.capture = null;
  setStatus("正在识别文字...");

  try {
    const data = await postJSON("/api/ocr/region", body);
    onRecognized(data);
  } catch (err) {
    showError(err);
  }
});

$("#btn-crop-cancel").addEventListener("click", closeCropModal);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#image-modal").hidden) closeImage();
  else if (!$("#crop-modal").hidden) closeCropModal();
  else if (!$("#window-modal").hidden) closeWindowPicker();
});

// ---------- 识别：复制 / 导出 ----------

$("#btn-copy").addEventListener("click", async () => {
  const text = $("#recog-text").value.trim();
  if (!text) return;
  await navigator.clipboard.writeText(text);
  setStatus("文本已复制到剪贴板");
});

$("#btn-export").addEventListener("click", () => {
  const text = $("#recog-text").value.trim();
  if (!text) {
    toast("没有可导出的文本");
    return;
  }
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `识别文本_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-")}.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus("文本已导出");
});

// ---------- 推荐回复 ----------

function renderSuggestions(items) {
  const box = $("#suggestions");
  box.innerHTML = "";
  items.forEach((s, i) => {
    const card = document.createElement("div");
    card.className = "sug-card";

    const title = document.createElement("div");
    title.className = "sug-title";
    title.textContent = `推荐 ${i + 1}`;

    const text = document.createElement("div");
    text.className = "sug-text";
    text.textContent = s.text;

    const actions = document.createElement("div");
    actions.className = "sug-actions";
    const btn = document.createElement("button");
    btn.className = "btn btn-mini";
    btn.textContent = "复制";
    btn.addEventListener("click", async () => {
      await navigator.clipboard.writeText(s.text);
      if (s.id) postJSON(`/api/suggestions/${s.id}/copied`).catch(() => {});
      setStatus("已复制推荐回复到剪贴板");
    });
    actions.appendChild(btn);

    card.append(title, text, actions);
    box.appendChild(card);
  });
}

$("#btn-suggest").addEventListener("click", async () => {
  const text = $("#recog-text").value.trim();
  if (!text) {
    toast("请先识别聊天内容");
    return;
  }

  setStatus("正在生成推荐回复...");
  $("#suggestions").innerHTML = "";
  try {
    const data = await postJSON("/api/suggestions", {
      text,
      session_id: state.sessionId || 0,
    });
    renderSuggestions(data.suggestions);
    setStatus(`已生成 ${data.suggestions.length} 条推荐回复`);
  } catch (e) {
    showError(e);
  }
});

// ---------- 历史会话 ----------

async function refreshHistory() {
  try {
    const data = await api("/api/sessions");
    const list = $("#history-list");
    list.innerHTML = "";
    if (!data.sessions.length) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "暂无会话";
      list.appendChild(empty);
      return;
    }
    const groups = { chat: [], recognition: [] };
    data.sessions.forEach((s) => {
      const type = s.type === "chat" ? "chat" : "recognition";
      groups[type].push(s);
    });
    renderHistoryGroup(list, "对话记录", groups.chat);
    renderHistoryGroup(list, "识别记录", groups.recognition);
  } catch (e) {
    showError(e);
  }
}

function renderHistoryGroup(container, label, sessions) {
  if (!sessions.length) return;
  const header = document.createElement("div");
  header.className = "history-group-header";
  header.textContent = label;
  container.appendChild(header);
  sessions.forEach((s) => {
    const row = document.createElement("div");
    row.className = "history-row";
    row.dataset.id = s.id;

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "history-check";
    cb.addEventListener("change", updateBatchCount);

    const item = document.createElement("div");
    item.className = "history-item" + (s.id === state.selectedSession ? " selected" : "");
    item.textContent = s.title;
    item.title = `${s.title}\n${(s.updated_at || "").slice(0, 16)}`;
    item.addEventListener("click", () => {
      if (state.batchMode) {
        cb.checked = !cb.checked;
        updateBatchCount();
      } else {
        loadSession(s.id);
      }
    });

    row.append(cb, item);
    container.appendChild(row);
  });
}

function resetNewChat() {
  skipTypewriter();
  state.chat = [];
  setChatSession(0);
  $("#chat-messages").innerHTML = "";
  $("#chat-empty").style.display = "";
  hideSuggestions();
}

function toggleBatchMode() {
  state.batchMode = !state.batchMode;
  document.getElementById("history-list").classList.toggle("batch-mode", state.batchMode);
  $("#history-batch").hidden = !state.batchMode;
  $("#btn-batch-select-all").textContent = "全选";
  updateBatchCount();
}

function selectAllBatch() {
  const checks = document.querySelectorAll(".history-check");
  const allChecked = [...checks].every((c) => c.checked);
  checks.forEach((c) => (c.checked = !allChecked));
  $("#btn-batch-select-all").textContent = allChecked ? "全选" : "取消全选";
  updateBatchCount();
}

function updateBatchCount() {
  const count = document.querySelectorAll(".history-check:checked").length;
  $("#btn-batch-delete").textContent = count ? `删除 (${count})` : "批量删除";
  $("#btn-batch-delete").disabled = count === 0;
}

async function batchDelete() {
  const ids = [...document.querySelectorAll(".history-check:checked")]
    .map((cb) => Number(cb.closest(".history-row").dataset.id));
  if (!ids.length) return;
  if (!confirm(`确定要删除选中的 ${ids.length} 条记录吗？`)) return;
  try {
    await postJSON("/api/sessions/batch-delete", { ids });
    if (ids.includes(state.sessionId)) setSession(0);
    if (ids.includes(state.chatSessionId)) {
      setChatSession(0);
      resetNewChat();
    }
    if (ids.includes(state.selectedSession)) state.selectedSession = null;
    state.batchMode = false;
    document.getElementById("history-list").classList.remove("batch-mode");
    $("#history-batch").hidden = true;
    await refreshHistory();
    setStatus(`已删除 ${ids.length} 条记录`);
  } catch (e) {
    showError(e);
  }
}

function loadChatSession(id, messages) {
  skipTypewriter();
  hideSuggestions();
  state.chat = messages.map((m) => ({
    role: m.role === "assistant" ? "assistant" : "user",
    content: m.raw_text,
  }));
  setChatSession(id);

  $("#chat-messages").innerHTML = "";
  state.chat.forEach((m) => appendMsg(m.role === "assistant" ? "ai" : "user", m.content));
  showPage("chat");
  setStatus(`已加载对话记录，共 ${messages.length} 条消息`);
}

async function loadSession(id) {
  try {
    const data = await api(`/api/sessions/${id}`);
    if (!data.messages.length) return;

    state.selectedSession = id;

    // 对话记录在「AI 对话」页回放，识别记录在「识别」页回放
    if ((data.session || {}).type === "chat") {
      loadChatSession(id, data.messages);
      await refreshHistory();
      return;
    }

    setSession(id);

    $("#recog-text").value = data.messages.map((m) => m.raw_text).join("\n\n---\n\n");

    // 历史里保存过的截图（文件还在的才显示，最多 12 张）
    renderShots(
      data.messages
        .filter((m) => m.image_url && m.image_exists)
        .slice(-12)
        .map((m) => ({ url: m.image_url, label: (m.created_at || "").slice(11, 16) || "截图" }))
    );

    const lastMsg = data.messages[data.messages.length - 1];
    renderSuggestions(
      (lastMsg.suggestions || []).map((s) => ({ id: s.id, text: s.text }))
    );

    showPage("recognition");
    setStatus(`已加载会话，共 ${data.messages.length} 条消息`);
    await refreshHistory(); // 重渲染历史列表，让当前会话保持高亮
  } catch (e) {
    showError(e);
  }
}

$("#btn-history-refresh").addEventListener("click", refreshHistory);
$("#btn-batch-toggle").addEventListener("click", toggleBatchMode);
$("#btn-batch-select-all").addEventListener("click", selectAllBatch);
$("#btn-batch-delete").addEventListener("click", batchDelete);

// ---------- 设置 ----------

function collectFormConfig() {
  return {
    api_provider: $("#cfg-provider").value,
    api_key: $("#cfg-api-key").value,
    api_base_url: $("#cfg-base-url").value,
    text_model: $("#cfg-text-model").value,
    vision_provider: $("#cfg-vision-provider").value,
    vision_api_key: $("#cfg-vision-api-key").value,
    vision_base_url: $("#cfg-vision-base-url").value,
    vision_model: $("#cfg-vision-model").value,
    ocr_method: $("#cfg-ocr-method").value,
    reply_count: Number($("#cfg-reply-count").value) || 3,
    reply_style: $("#cfg-reply-style").value,
    screenshot_keep: Number($("#cfg-screenshot-keep").value) || 0,
    search_read_pages: $("#cfg-read-pages").checked ? 1 : 0,
    update_channel: $("#cfg-update-channel").value,
  };
}

function fillDatalist(id, items) {
  const dl = $(id);
  dl.innerHTML = "";
  (items || []).forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    dl.appendChild(opt);
  });
}

function refreshModelDatalists() {
  const provider = $("#cfg-provider").value;
  const visionProvider = $("#cfg-vision-provider").value || provider;
  const defaults = state.providers[provider] || {};
  const visionDefaults = state.providers[visionProvider] || {};
  fillDatalist("#dl-text-models", defaults.text_models);
  fillDatalist("#dl-vision-models", visionDefaults.vision_models);
  if (defaults.text_models && defaults.text_models.length) {
    $("#cfg-text-model").value = defaults.text_models[0];
  }
  const visionModels = visionDefaults.vision_models || [];
  if (visionModels.length && !visionModels.includes($("#cfg-vision-model").value)) {
    $("#cfg-vision-model").value = visionModels[0];
  }
}

async function loadSettings() {
  const data = await api("/api/config");
  state.config = data.config;
  state.providers = data.providers;
  state.version = data.version || "";
  renderVersionBadge({ current: state.version, pending: true });

  const providerSel = $("#cfg-provider");
  providerSel.innerHTML = "";
  Object.keys(state.providers).forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    providerSel.appendChild(opt);
  });

  const visionProviderSel = $("#cfg-vision-provider");
  visionProviderSel.innerHTML = "";
  ["", ...Object.keys(state.providers)].forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p === "" ? "（与文本模型相同）" : p;
    visionProviderSel.appendChild(opt);
  });

  const c = state.config;
  providerSel.value = c.api_provider || "openai";
  $("#cfg-api-key").value = c.api_key || "";
  $("#cfg-base-url").value = c.api_base_url || "";
  $("#cfg-text-model").value = c.text_model || "";
  visionProviderSel.value = c.vision_provider || "";
  $("#cfg-vision-api-key").value = c.vision_api_key || "";
  $("#cfg-vision-base-url").value = c.vision_base_url || "";
  $("#cfg-vision-model").value = c.vision_model || "";
  $("#cfg-ocr-method").value = c.ocr_method || "auto";
  $("#cfg-reply-count").value = c.reply_count || 3;
  $("#cfg-reply-style").value = c.reply_style || "friendly";
  $("#cfg-screenshot-keep").value = c.screenshot_keep ?? 0;
  // 没配置过时默认开启（和后端 config.get("search_read_pages", 1) 保持一致）
  $("#cfg-read-pages").checked = c.search_read_pages === undefined ? true : !!c.search_read_pages;
  // 更新通道默认 stable（只提示正式版）
  $("#cfg-update-channel").value = c.update_channel === "beta" ? "beta" : "stable";

  refreshModelDatalists();
  $("#cfg-text-model").value = c.text_model || $("#cfg-text-model").value;
  $("#cfg-vision-model").value = c.vision_model || $("#cfg-vision-model").value;
}

$("#cfg-provider").addEventListener("change", refreshModelDatalists);
$("#cfg-vision-provider").addEventListener("change", refreshModelDatalists);

async function fetchModels(vision) {
  const body = collectFormConfig();
  body.vision = vision;
  setStatus("正在获取模型列表...");
  try {
    const data = await postJSON("/api/models", body);
    if (!data.models.length) {
      toast("未能获取到模型列表，请检查 API Key");
      setStatus("就绪");
      return;
    }
    fillDatalist(vision ? "#dl-vision-models" : "#dl-text-models", data.models);
    setStatus(`已获取 ${data.models.length} 个模型，请从下拉提示中选择`);
    toast(`已获取 ${data.models.length} 个模型`);
  } catch (e) {
    showError(e);
  }
}

$("#btn-fetch-text").addEventListener("click", () => fetchModels(false));
$("#btn-fetch-vision").addEventListener("click", () => fetchModels(true));

$("#btn-save-settings").addEventListener("click", async () => {
  try {
    await postJSON("/api/config", collectFormConfig());
    setStatus("设置已保存");
    toast("设置已保存");
  } catch (e) {
    showError(e);
  }
});

// ---------- 版本与更新检测 ----------

function renderVersionBadge(info) {
  const badge = $("#version-badge");
  const current = (info && info.current) || state.version || "";
  if (!current) {
    badge.hidden = true;
    return;
  }
  const name = current.replace(/^v/i, "");

  badge.innerHTML = "";
  const versionEl = document.createElement("span");
  versionEl.textContent = `v${name}`;
  badge.appendChild(versionEl);

  if (info && info.has_update && info.latest) {
    // 检测到新版本：版本号后面挂一个"可更新"标记
    const tag = document.createElement("span");
    tag.className = "badge-update";
    tag.textContent = "可更新";
    badge.appendChild(tag);
    badge.classList.add("updatable");
    const suffix = info.latest_prerelease ? "（预发布版）" : "";
    badge.title = `发现新版本 v${String(info.latest).replace(/^v/i, "")}${suffix}` +
      `（当前 v${name}），点击直接下载更新`;
    // 点击**直接下载并覆盖到本地**，不再跳转 GitHub（用户要求）
    badge.onclick = applyLocalUpdate;
  } else {
    // 已是最新 / 连不上 GitHub / 还在检测：只显示版本名称
    badge.classList.remove("updatable");
    badge.onclick = null;
    // 稳定通道下若远端有更新的预发布版，如实说明"按通道不提示"，而不是假装已是最新
    const held = info && info.latest && info.latest_prerelease && !info.has_update;
    if (held) {
      badge.title = `ChatSight v${name}（远端有预发布版 v${info.latest}，` +
        `当前 stable 通道不提示；想尝鲜可在设置里切到 beta）`;
    } else if (info && info.error) {
      badge.title = `ChatSight v${name}（更新检测：${info.error}）`;
    } else if (info && info.pending) {
      badge.title = `ChatSight v${name}（正在检测更新…）`;
    } else {
      badge.title = `ChatSight v${name}（已是最新版本）`;
    }
  }
  badge.hidden = false;
}

async function checkUpdate(attempt = 0) {
  try {
    const info = await api("/api/update");
    renderVersionBadge({ ...info, current: info.current || state.version });
    // 后端把联网检测放后台做，没结果时会先回 pending，这里再问几次
    if (info.pending && attempt < 8) {
      setTimeout(() => checkUpdate(attempt + 1), 1500);
    }
  } catch (e) {
    // 接口异常 / 连不上：只显示版本名称
    renderVersionBadge({ current: state.version, error: e.message });
  }
}

// 本地内建更新：把 GitHub 上的文件下载回本地覆盖，不跳浏览器。
// 后端会拒绝"远端比本地旧"的降级覆盖，并把原因返回（HTTP 409），这里如实显示。
async function applyLocalUpdate() {
  const badge = $("#version-badge");
  const previous = badge ? badge.textContent : "";
  if (badge) { badge.textContent = "正在下载更新…"; badge.onclick = null; }
  toast("正在从 GitHub 下载更新…");
  try {
    const response = await fetch("/api/update/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const result = await response.json();
    if (result.blocked === "downgrade") {
      toast("远端版本比本地旧，已拒绝覆盖（避免降级）");
      alert("没有下载覆盖：\n\n" + (result.error || "远端版本比本地旧"));
    } else if (result.ok) {
      const added = (result.add || []).length;
      const overwritten = (result.overwrite || []).length;
      alert(
        `更新完成：新增 ${added} 个文件、覆盖 ${overwritten} 个文件。\n` +
        `远端版本：${result.remote_version}（本地原为 ${result.local_version}）\n` +
        (result.backup ? `被覆盖的文件已备份到：\n${result.backup}\n` : "") +
        `\n请重启程序（关掉窗口重新运行 启动.bat）让新代码生效。`
      );
      toast("更新完成，请重启程序");
    } else {
      toast("更新失败：" + (result.error || "未知原因"));
      alert("更新失败：\n\n" + (result.error || "未知原因"));
    }
  } catch (e) {
    toast("更新失败：" + e.message);
    alert("更新失败：" + e.message);
  } finally {
    if (badge && previous) badge.textContent = previous;
    checkUpdate();
  }
}

// ---------- 启动 ----------

(async function init() {
  try {
    await loadSettings();
    await refreshHistory();
  } catch (e) {
    showError(e);
  }
  setStatus("就绪");
  if (state.version) renderVersionBadge({ current: state.version, pending: true });
  checkUpdate();
})();
