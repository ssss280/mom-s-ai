const $ = (sel) => document.querySelector(sel);

const state = {
  page: "chat",
  config: {},
  providers: {},
  version: "",
  sessionId: Number(localStorage.getItem("chatsight_session") || 0),
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

function setSession(id) {
  state.sessionId = id;
  localStorage.setItem("chatsight_session", String(id));
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
  el.addEventListener("click", () => showPage(el.dataset.page))
);

// ---------- AI 对话 ----------

function appendMsg(role, text, isError = false, sources = null) {
  $("#chat-empty").style.display = "none";
  const div = document.createElement("div");
  div.className = `msg ${role}` + (isError ? " error" : "");
  div.textContent = text;
  // 联网搜索的来源要挂在正文后面（顺序不能反，否则会被 textContent 清掉）
  if (sources && sources.length) {
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
  $("#chat-messages").appendChild(div);
  const area = $("#chat-area");
  area.scrollTop = area.scrollHeight;
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  const useSearch = $("#chat-search").checked;

  input.value = "";
  state.chat.push({ role: "user", content: text });
  appendMsg("user", text);
  setStatus(useSearch ? "正在联网搜索..." : "AI 正在思考...");
  $("#btn-chat-send").disabled = true;

  try {
    const data = await postJSON("/api/chat", { messages: state.chat, search: useSearch });
    state.chat.push({ role: "assistant", content: data.reply });
    appendMsg("ai", data.reply, false, data.sources);
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
  } catch (e) {
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
  state.chat = [];
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
    data.sessions.forEach((s) => {
      const item = document.createElement("div");
      item.className = "history-item" + (s.id === state.selectedSession ? " selected" : "");
      item.textContent = s.title;
      item.title = `${s.title}\n${(s.updated_at || "").slice(0, 16)}`;
      item.addEventListener("click", () => loadSession(s.id));
      list.appendChild(item);
    });
  } catch (e) {
    showError(e);
  }
}

async function loadSession(id) {
  try {
    const data = await api(`/api/sessions/${id}`);
    if (!data.messages.length) return;

    state.selectedSession = id;
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

$("#btn-history-delete").addEventListener("click", async () => {
  if (!state.selectedSession) {
    toast("请先在左侧选择一个会话");
    return;
  }
  if (!confirm("确定要删除此会话记录吗？")) return;
  try {
    await api(`/api/sessions/${state.selectedSession}`, { method: "DELETE" });
    if (state.sessionId === state.selectedSession) setSession(0);
    state.selectedSession = null;
    await refreshHistory();
    setStatus("会话已删除");
  } catch (e) {
    showError(e);
  }
});

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
    badge.title = `发现新版本 v${String(info.latest).replace(/^v/i, "")}（当前 v${name}），点击查看`;
    badge.onclick = () => window.open(info.url || "", "_blank", "noopener");
  } else {
    // 已是最新 / 连不上 GitHub / 还在检测：只显示版本名称
    badge.classList.remove("updatable");
    badge.onclick = null;
    badge.title = info && info.error
      ? `ChatSight v${name}（更新检测：${info.error}）`
      : `ChatSight v${name}${info && info.pending ? "（正在检测更新…）" : "，已是最新版本"}`;
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
