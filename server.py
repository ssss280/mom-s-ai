"""ChatSight 网页版后端（Flask）。

启动: py server.py  （自动打开浏览器）
      py server.py --no-browser --port 5050
"""

import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser

from flask import Flask, jsonify, request, send_from_directory
from PIL import Image

from capture import capture_full_screen, prune_screenshots
from logger import setup_logging, write_crash_log
from models import ChatSightModel, PROVIDER_DEFAULTS
from ocr import extract_chat_text
from paths import APP_DIR, CONFIG_PATH, SCREENSHOT_DIR, ensure_writable_dir
from storage import ChatStorage
import update_check
import web_search
import win_capture
from version import __version__

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", static_url_path="")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# 先把日志装好：下面的配置/模型/数据库初始化一旦抛异常，
# 也需要有 app.log 和 error/ 记录可查（之前这几行跑在 setup_logging 之前，日志是空的）
setup_logging()

config = load_config()
model = ChatSightModel(config)
storage = ChatStorage()

CONFIG_FIELDS = (
    "api_provider", "api_key", "api_base_url", "text_model",
    "vision_provider", "vision_api_key", "vision_base_url", "vision_model",
    "ocr_method", "reply_count", "reply_style", "screenshot_keep",
    "search_read_pages",
)

# 每次对话最多带上最近多少条消息，避免上下文无限增长
MAX_CHAT_MESSAGES = 30
DEFAULT_PORT = 5050
SEARCH_COUNT = 5          # 联网搜索取几条结果喂给模型

SEARCH_SYSTEM_PROMPT = """你是一个可以联网搜索的中文助手。下面会给你一份刚搜到的网页结果，请：
1. 优先依据这些结果回答，不要编造结果里没有的事实；
2. 引用某条结果时在句子后面用 [1] [2] 这样的编号标注；
3. 如果结果里没有相关信息，直接说明"搜索结果中没有找到相关信息"，再给出你自己的判断；
4. 每条结果都标了「来源网站」，界面上会把网站和链接一并展示给用户，
   所以你只要标编号即可，**不要在正文里输出 URL**；
5. 用中文简洁作答，不要复述这份说明。"""

# 搜不到结果时也必须注入这段：否则模型会"凭记忆"自信作答，
# 实测它会把 2026 香港秋季灯饰展的地点说成别的地方（没有来源支撑）
SEARCH_EMPTY_SYSTEM_PROMPT = """用户希望你联网查最新信息，但这次联网搜索没有取到相关结果。请：
1. 开头明确说明"这次联网搜索没有查到相关信息"，不要假装查到了；
2. 如果你要凭已有知识回答，必须说明这可能不是最新信息、建议以官方来源为准；
3. 绝对不要编造具体的日期、价格、地点、人名等事实；
4. 用中文简洁作答，不要复述这份说明。"""

# 找到了结果但相关性都不高时用这段：让模型把这些当线索而不是事实
SEARCH_LOW_RELEVANCE_PROMPT = """重要提醒：这次联网搜索**没有找到高度相关的资料**，下面给到的几条相关性较低，只能当线索。请：
1. 开头说明"没有检索到很匹配的资料，以下仅供参考"；
2. 不要把这几条当成确凿事实，凡涉及日期、价格、地点、人名都要提醒用户自行核对；
3. 宁可说"不确定"，也不要替它们圆场；
4. 用中文简洁作答，不要复述这份说明。"""


def err(e: Exception, code: int = 500):
    logger.exception(f"接口错误: {e}")
    return jsonify({"error": str(e)}), code


def screenshot_keep() -> int:
    """截图保留数量；0（默认）= 永久保留，正数 = 只留最近这么多张。"""
    try:
        return int(config.get("screenshot_keep", 0) or 0)
    except (TypeError, ValueError):
        return 0


def read_pages_enabled() -> bool:
    """联网搜索时是否抓取网页正文（默认开）。配置里可以关掉。"""
    value = config.get("search_read_pages", 1)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


def image_url(path: str) -> str:
    """把磁盘路径转成前端可访问的 URL（截图目录是扁平的，用文件名即可）"""
    return f"/screenshots/{os.path.basename(path)}" if path else ""


def cleanup_screenshots(directory: str):
    prune_screenshots(directory, screenshot_keep())


def run_ocr(image, image_path: str, session_id: int = 0):
    """OCR 并入库；session_id 为空时新建会话。返回 (text, session_id)"""
    client = model.vision_client if config.get("ocr_method") in ("auto", "vision") else None
    text = extract_chat_text(
        image,
        method=config.get("ocr_method", "auto"),
        client=client,
        vision_model=config.get("vision_model", "qwen3-vl-plus"),
    )
    if not session_id:
        session_id = storage.create_session(session_type="recognition")
    storage.save_message(session_id, text, image_path)
    return text, session_id


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/screenshots/<path:name>")
def screenshots(name):
    return send_from_directory(ensure_writable_dir(SCREENSHOT_DIR), name)


# ---------- 配置 ----------

@app.get("/api/config")
def get_config():
    return jsonify({"config": config, "providers": PROVIDER_DEFAULTS, "version": __version__})


@app.post("/api/config")
def update_config():
    global config
    try:
        body = request.get_json(force=True)
        new_config = dict(config)
        for field in CONFIG_FIELDS:
            if field in body:
                new_config[field] = body[field]
        save_config(new_config)
        config = new_config
        model.update_config(config)
        return jsonify({"ok": True})
    except Exception as e:
        return err(e)


@app.post("/api/models")
def fetch_models():
    """用表单里（可能未保存的）配置拉取模型列表"""
    try:
        body = request.get_json(force=True)
        tmp_config = dict(config)
        for field in CONFIG_FIELDS:
            if field in body:
                tmp_config[field] = body[field]
        tmp_model = ChatSightModel(tmp_config)
        vision = bool(body.get("vision"))
        models = tmp_model.fetch_models_from_api(vision=vision)
        return jsonify({"models": models})
    except Exception as e:
        return err(e)


# ---------- AI 对话 ----------

def is_dashscope() -> bool:
    """文本模型是否走阿里云百炼（DashScope 自带联网搜索，可以直接开开关）。"""
    base = config.get("api_base_url") or ""
    if not base:
        provider = config.get("api_provider", "openai")
        base = PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "")
    return "dashscope" in base


@app.post("/api/chat")
def chat():
    try:
        if not model.is_configured:
            return jsonify({"error": "请先在「设置」中配置 API Key"}), 400

        body = request.get_json(force=True)
        raw = body.get("messages", []) if isinstance(body, dict) else []
        if not isinstance(raw, list):
            return jsonify({"error": "messages 必须是数组"}), 400
        messages = [
            {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
            for m in raw
            if isinstance(m, dict) and str(m.get("content") or "").strip()
        ][-MAX_CHAT_MESSAGES:]
        if not messages:
            return jsonify({"error": "消息不能为空"}), 400

        use_search = bool(body.get("search"))
        session_id = int(body.get("session_id") or 0)
        # 前端每次都会带上完整历史（模型需要上下文），所以只把「最后一条用户消息 + 本次回复」
        # 写进记录，否则同一句话会被反复保存
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        sources = []
        search_info = {"requested": use_search, "mode": "", "engine": "", "error": ""}

        if use_search:
            if is_dashscope():
                # 百炼自带联网搜索：让模型自己检索，不用我们抓网页
                search_info["mode"] = "native"
            else:
                # 追问句（「2026年的呢」）自己没检索价值，要拼上上一句用户消息才有意义
                user_texts = [m["content"] for m in messages if m["role"] == "user"]
                query = web_search.build_query(user_texts[-1] if user_texts else "", user_texts[:-1])
                found = web_search.search(query, count=SEARCH_COUNT)
                search_info["engine"] = found["engine"]
                search_info["error"] = found["error"]
                search_info["mode"] = "local"
                search_info["low_relevance"] = bool(found.get("low_relevance"))
                # 让前端能显示"实际用了哪个查询词"（搜索里会把问句成分和年份洗掉/改写）
                search_info["query"] = found["query"]
                search_info["query_used"] = found.get("query_used") or found["query"]
                if found["results"]:
                    sources = [{"title": r["title"], "url": r["url"], "site": web_search.site_of(r["url"])}
                               for r in found["results"]]
                    pages = {}
                    if read_pages_enabled():
                        # 摘要常常只有导航文字，抓正文能显著提高答案准确性（代价是多 1~3 秒）
                        pages = web_search.fetch_pages([r["url"] for r in found["results"]],
                                                       count=web_search.READ_PAGES)
                        search_info["pages"] = len(pages)
                    injected = [{"role": "system", "content": SEARCH_SYSTEM_PROMPT}]
                    if found.get("low_relevance"):
                        # 没找到高相关结果时，明确告诉模型这是弱证据，别当事实用
                        injected.insert(0, {"role": "system", "content": SEARCH_LOW_RELEVANCE_PROMPT})
                    injected.append({
                        "role": "system",
                        "content": "以下是刚刚联网搜索到的网页结果（含正文节选）：\n\n"
                                   + web_search.build_context(found["results"], pages),
                    })
                    messages = injected + messages
                else:
                    search_info["error"] = found["error"] or "没有搜到结果"
                    # 关键：搜不到也要明确告诉模型"没查到"，否则它会凭记忆自信作答
                    messages = [{"role": "system", "content": SEARCH_EMPTY_SYSTEM_PROMPT}] + messages

        kwargs = {
            "model": config.get("text_model", "gpt-4o-mini"),
            "messages": messages,
            "max_tokens": 2000,
            "temperature": 0.7 if not use_search else 0.3,
        }
        if use_search and search_info["mode"] == "native":
            kwargs["extra_body"] = {"enable_search": True}

        response = model.client.chat.completions.create(**kwargs)
        reply = (response.choices[0].message.content or "").strip()
        if not reply:
            return jsonify({"error": "模型没有返回任何内容"}), 502

        # 落库成「对话记录」。存库失败不该把已经拿到的回复丢掉，所以只记日志
        try:
            if not session_id:
                session_id = storage.create_session(session_type="chat")
            if last_user:
                storage.save_message(session_id, last_user, role="user")
            storage.save_message(session_id, reply, role="assistant")
        except Exception as db_error:
            logger.warning(f"对话记录保存失败: {db_error}")

        payload = {"reply": reply, "session_id": session_id}
        if use_search:
            payload["search"] = search_info
            payload["sources"] = sources
            # 百炼原生搜索时，引用信息在响应的 search_info 里（有就一并返回，没有就算了）
            if search_info["mode"] == "native":
                extra = getattr(response, "search_info", None)
                items = getattr(extra, "search_results", None) if extra else None
                if isinstance(items, list):
                    payload["sources"] = [
                        {"title": getattr(i, "title", "") or "",
                         "url": getattr(i, "url", "") or "",
                         "site": web_search.site_of(getattr(i, "url", "") or "")}
                        for i in items
                    ]
        return jsonify(payload)
    except Exception as e:
        return err(e)


# ---------- 识别（上传 / 截屏 / OCR） ----------

@app.post("/api/ocr/upload")
def ocr_upload():
    try:
        file = request.files.get("image")
        if file is None:
            return jsonify({"error": "缺少图片文件"}), 400
        session_id = int(request.form.get("session_id") or 0)

        image = Image.open(file.stream).convert("RGB")
        screenshots_dir = ensure_writable_dir(SCREENSHOT_DIR)
        img_path = os.path.join(screenshots_dir, f"up_{int(time.time() * 1000)}.png")
        image.save(img_path)
        cleanup_screenshots(screenshots_dir)

        text, session_id = run_ocr(image, img_path, session_id)
        return jsonify({"text": text, "session_id": session_id,
                        "image_path": img_path, "image_url": image_url(img_path)})
    except Exception as e:
        return err(e)


@app.post("/api/capture")
def capture():
    """服务端全屏截图，前端在图片上框选后再调 /api/ocr/region"""
    try:
        image = capture_full_screen()
        screenshots_dir = ensure_writable_dir(SCREENSHOT_DIR)
        name = f"cap_{int(time.time() * 1000)}.png"
        image.save(os.path.join(screenshots_dir, name))
        cleanup_screenshots(screenshots_dir)
        return jsonify({
            "name": name,
            "url": f"/screenshots/{name}",
            "width": image.width,
            "height": image.height,
        })
    except Exception as e:
        return err(e)


# ---------- 窗口截屏 ----------

@app.get("/api/windows")
def windows():
    """列出当前可见的顶层窗口，供「截取窗口」使用"""
    try:
        if not win_capture.IS_WINDOWS:
            return jsonify({"supported": False, "windows": []})
        return jsonify({"supported": True, "windows": win_capture.list_windows()})
    except Exception as e:
        return err(e)


@app.post("/api/capture/window")
def capture_window_api():
    """截取指定窗口：窗口被浏览器挡住也能截到（返回结构同 /api/capture）"""
    try:
        body = request.get_json(force=True)
        hwnd = int(body.get("id") or 0)
        if not hwnd:
            return jsonify({"error": "缺少窗口 id，请重新选择窗口"}), 400

        image = win_capture.capture_window(hwnd)
        screenshots_dir = ensure_writable_dir(SCREENSHOT_DIR)
        name = f"win_{int(time.time() * 1000)}.png"
        image.save(os.path.join(screenshots_dir, name))
        cleanup_screenshots(screenshots_dir)
        return jsonify({
            "name": name,
            "url": f"/screenshots/{name}",
            "width": image.width,
            "height": image.height,
        })
    except RuntimeError as e:
        # 窗口已关闭 / 已最小化 / 系统拒绝后台截图：这是可预期的失败，给出可读提示
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return err(e)


@app.post("/api/ocr/region")
def ocr_region():
    try:
        body = request.get_json(force=True)
        name = os.path.basename(body.get("name", ""))
        if not name:
            return jsonify({"error": "缺少截图文件名，请重新截屏"}), 400
        session_id = int(body.get("session_id") or 0)
        x, y = int(body["x"]), int(body["y"])
        w, h = int(body["w"]), int(body["h"])
        if w <= 0 or h <= 0:
            return jsonify({"error": "框选区域无效"}), 400

        img_path = os.path.join(ensure_writable_dir(SCREENSHOT_DIR), name)
        if not os.path.isfile(img_path):
            return jsonify({"error": "截图文件不存在，请重新截屏"}), 400
        image = Image.open(img_path).convert("RGB")

        # 前端按缩放比例换算后可能有 1~2 像素越界，先把选区夹回图片范围，
        # 否则 PIL 会用黑色补齐越界部分，反而干扰 OCR
        x = max(0, min(x, image.width - 1))
        y = max(0, min(y, image.height - 1))
        w = max(1, min(w, image.width - x))
        h = max(1, min(h, image.height - y))
        crop = image.crop((x, y, x + w, y + h))

        screenshots_dir = ensure_writable_dir(SCREENSHOT_DIR)
        crop_path = os.path.join(screenshots_dir, f"crop_{int(time.time() * 1000)}.png")
        crop.save(crop_path)
        cleanup_screenshots(screenshots_dir)

        text, session_id = run_ocr(crop, crop_path, session_id)
        return jsonify({
            "text": text,
            "session_id": session_id,
            "image_path": crop_path,
            "image_url": image_url(crop_path),
            # 原图（未裁剪的截图）也一并返回，界面上可以对照查看
            "source_path": img_path,
            "source_url": image_url(img_path),
        })
    except Exception as e:
        return err(e)


# ---------- 推荐回复 ----------

@app.post("/api/suggestions")
def suggestions():
    try:
        if not model.is_configured:
            return jsonify({"error": "请先在「设置」中配置 API Key"}), 400
        body = request.get_json(force=True)
        chat_text = body.get("text", "").strip()
        session_id = int(body.get("session_id") or 0)
        if not chat_text:
            return jsonify({"error": "请先识别聊天内容"}), 400

        items = model.get_reply_suggestions(
            chat_text,
            count=config.get("reply_count", 3),
            style=config.get("reply_style", "friendly"),
        )

        ids = [None] * len(items)
        if session_id:
            msgs = storage.get_session_messages(session_id)
            if msgs:
                saved = storage.save_suggestions(
                    msgs[-1]["id"], items,
                    model_used=config.get("text_model", "unknown"),
                )
                ids = saved
        return jsonify({
            "suggestions": [{"id": sid, "text": t} for sid, t in zip(ids, items)]
        })
    except Exception as e:
        return err(e)


@app.post("/api/suggestions/<int:sug_id>/copied")
def suggestion_copied(sug_id):
    try:
        storage.mark_suggestion_copied(sug_id)
        return jsonify({"ok": True})
    except Exception as e:
        return err(e)


# ---------- 历史会话 ----------

@app.get("/api/sessions")
def sessions():
    try:
        return jsonify({"sessions": storage.get_sessions()})
    except Exception as e:
        return err(e)


# ---------- 版本与更新 ----------

@app.get("/api/update")
def update_status():
    """检测 GitHub 上是否有新版本。

    立即返回已知结果（没结果就 pending=True），真正的联网检测在后台线程里做，
    所以永远不会把页面卡住；连不上 GitHub 时返回 has_update=False + 原因，不报 500。
    """
    try:
        return jsonify(update_check.status())
    except Exception as e:
        logger.warning(f"更新检测异常: {e}")
        return jsonify({
            "current": __version__, "latest": "", "has_update": False,
            "url": update_check.REPO_URL, "source": "", "error": str(e), "pending": False,
        })


@app.get("/api/sessions/<int:session_id>")
def session_detail(session_id):
    try:
        messages = storage.get_session_messages(session_id)
        for m in messages:
            # 顺手给出可访问的图片 URL，前端历史记录里可以直接查看当时保存的截图
            m["image_url"] = image_url(m.get("image_path"))
            m["image_exists"] = bool(m.get("image_path")) and os.path.isfile(m["image_path"])
        # session 带上 type，前端据此决定用「AI 对话」页还是「识别」页展示
        return jsonify({"session": storage.get_session(session_id), "messages": messages})
    except Exception as e:
        return err(e)


@app.delete("/api/sessions/<int:session_id>")
def session_delete(session_id):
    try:
        storage.delete_session(session_id)
        return jsonify({"ok": True})
    except Exception as e:
        return err(e)


def parse_port(argv: list) -> int:
    """解析 --port 参数；缺失或不是数字时回退到默认端口（以前会直接 IndexError）。"""
    if "--port" in argv:
        idx = argv.index("--port") + 1
        if idx < len(argv):
            try:
                return int(argv[idx])
            except ValueError:
                pass
        logger.warning("--port 后面缺少有效端口号，改用默认端口 %s", DEFAULT_PORT)
    return DEFAULT_PORT


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """端口上是否已经有服务在监听。

    Windows 的 SO_REUSEADDR 允许两个进程绑同一个端口，新进程会"看起来启动成功"，
    但浏览器的请求可能仍然落到旧进程上（旧进程没有新加的路由，表现为莫名其妙的 404）。
    所以启动前先探一下，避免这种"改了代码却不生效"的假象。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main():
    setup_logging()
    port = parse_port(sys.argv)

    if port_in_use(port):
        message = (
            f"端口 {port} 上已经有一个服务在监听，很可能已经有一个 ChatSight 在运行。\n"
            f"请先关掉那个命令行窗口（旧实例），再启动本程序；\n"
            f"或者换个端口启动：py server.py --port {port + 1}"
        )
        logger.error(message.replace("\n", " "))
        sys.stderr.write(message + "\n")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    logger.info(f"ChatSight 网页版启动: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        crash = write_crash_log("启动失败", traceback.format_exc())
        sys.stderr.write(traceback.format_exc())
        if crash:
            sys.stderr.write(f"错误日志: {crash}\n")
        sys.exit(1)
