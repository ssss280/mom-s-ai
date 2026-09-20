"""ChatSight 网页版后端（Flask）。

启动: py server.py  （自动打开浏览器）
      py server.py --no-browser --port 5050
"""

import json
import logging
import os
import sys
import threading
import time
import webbrowser

from flask import Flask, jsonify, request, send_from_directory
from PIL import Image

from capture import capture_full_screen
from logger import setup_logging, write_crash_log
from models import ChatSightModel, PROVIDER_DEFAULTS
from ocr import extract_chat_text
from paths import APP_DIR, CONFIG_PATH, SCREENSHOT_DIR, ensure_writable_dir
from storage import ChatStorage

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


config = load_config()
model = ChatSightModel(config)
storage = ChatStorage()

CONFIG_FIELDS = (
    "api_provider", "api_key", "api_base_url", "text_model",
    "vision_provider", "vision_api_key", "vision_base_url", "vision_model",
    "ocr_method", "reply_count", "reply_style",
)


def err(e: Exception, code: int = 500):
    logger.exception(f"接口错误: {e}")
    return jsonify({"error": str(e)}), code


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
        session_id = storage.create_session()
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
    return jsonify({"config": config, "providers": PROVIDER_DEFAULTS})


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

@app.post("/api/chat")
def chat():
    try:
        if not model.is_configured:
            return jsonify({"error": "请先在「设置」中配置 API Key"}), 400
        messages = request.get_json(force=True).get("messages", [])
        response = model.client.chat.completions.create(
            model=config.get("text_model", "gpt-4o-mini"),
            messages=messages,
            max_tokens=2000,
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply})
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

        text, session_id = run_ocr(image, img_path, session_id)
        return jsonify({"text": text, "session_id": session_id})
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
        return jsonify({
            "name": name,
            "url": f"/screenshots/{name}",
            "width": image.width,
            "height": image.height,
        })
    except Exception as e:
        return err(e)


@app.post("/api/ocr/region")
def ocr_region():
    try:
        body = request.get_json(force=True)
        name = os.path.basename(body.get("name", ""))
        session_id = int(body.get("session_id") or 0)
        x, y = int(body["x"]), int(body["y"])
        w, h = int(body["w"]), int(body["h"])
        if w <= 0 or h <= 0:
            return jsonify({"error": "框选区域无效"}), 400

        img_path = os.path.join(ensure_writable_dir(SCREENSHOT_DIR), name)
        image = Image.open(img_path).convert("RGB")
        crop = image.crop((x, y, x + w, y + h))

        screenshots_dir = ensure_writable_dir(SCREENSHOT_DIR)
        crop_path = os.path.join(screenshots_dir, f"crop_{int(time.time() * 1000)}.png")
        crop.save(crop_path)

        text, session_id = run_ocr(crop, crop_path, session_id)
        return jsonify({"text": text, "session_id": session_id})
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


@app.get("/api/sessions/<int:session_id>")
def session_detail(session_id):
    try:
        return jsonify({"messages": storage.get_session_messages(session_id)})
    except Exception as e:
        return err(e)


@app.delete("/api/sessions/<int:session_id>")
def session_delete(session_id):
    try:
        storage.delete_session(session_id)
        return jsonify({"ok": True})
    except Exception as e:
        return err(e)


def main():
    setup_logging()
    port = 5050
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

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
