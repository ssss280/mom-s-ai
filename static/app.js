const $ = (sel) => document.querySelector(sel);

const state = {
  page: "chat",
  config: {},
  providers: {},
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
  $("#statusbar").textContent = text;
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

function appendMsg(role, text, isError = false) {
  $("#chat-empty").style.display = "none";
  const div = document.createElement("div");
  div.className = `msg ${role}` + (isError ? " error" : "");
  div.textContent = text;
  $("#chat-messages").appendChild(div);
  const area = $("#chat-area");
  area.scrollTop = area.scrollHeight;
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  state.chat.push({ role: "user", content: text });
  appendMsg("user", text);
  setStatus("AI 正在思考...");
  $("#btn-chat-send").disabled = true;

  try {
    const data = await postJSON("/api/chat", { messages: state.chat });
    state.chat.push({ role: "assistant", content: data.reply });
    appendMsg("ai", data.reply);
    setStatus("就绪");
  } catch (e) {
    appendMsg("ai", `错误: ${e.message}`, true);
    setStatus("就绪");
  } finally {
    $("#btn-chat-send").disabled = false;
  }
}

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
  refreshHistory();
  setStatus(`识别完成，共 ${data.text.length} 个字符`);
}

// ---------- 识别：截屏框选 ----------

$("#btn-capture").addEventListener("click", async () => {
  setStatus("正在截屏...");
  try {
    const data = await postJSON("/api/capture");
    state.capture = data;
    openCropModal(data);
    setStatus("请拖拽框选要识别的区域");
  } catch (e) {
    showError(e);
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
  const p = cropPoint(e);
  crop.dragging = true;
  crop.x1 = crop.x2 = p.x;
  crop.y1 = crop.y2 = p.y;
});

$("#crop-stage").addEventListener("mousemove", (e) => {
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

$("#crop-stage").addEventListener("mouseup", async (e) => {
  if (!crop.dragging) return;
  crop.dragging = false;
  const img = $("#crop-image").getBoundingClientRect();
  const w = Math.abs(crop.x2 - crop.x1);
  const h = Math.abs(crop.y2 - crop.y1);
  if (w < 10 || h < 10 || !state.capture) return;

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
  if (e.key === "Escape" && !$("#crop-modal").hidden) closeCropModal();
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

    const lastMsg = data.messages[data.messages.length - 1];
    renderSuggestions(
      (lastMsg.suggestions || []).map((s) => ({ id: s.id, text: s.text }))
    );

    showPage("recognition");
    setStatus(`已加载会话，共 ${data.messages.length} 条消息`);
    document.querySelectorAll(".history-item").forEach((el) => el.classList.remove("selected"));
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

// ---------- 启动 ----------

(async function init() {
  try {
    await loadSettings();
    await refreshHistory();
  } catch (e) {
    showError(e);
  }
  setStatus("就绪");
})();
