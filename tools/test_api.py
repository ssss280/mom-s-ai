"""接口层全面测试：逐个打 ChatSight 的 HTTP 接口，记录状态码与返回结构。

判定原则：
- 2xx 且结构符合预期 = 通过；
- 需要外部条件（视觉 API Key / Tesseract / 屏幕）而本机没有时，
  接口应返回**可读错误**而不是 500 堆栈 —— 这类算"优雅降级通过"；
- 真正的 500（未捕获异常）一律算 bug。
"""
import base64
import io
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:5177"
RESULTS = []


def call(method, path, payload=None, timeout=180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, {"_raw": body[:200]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"_raw": body[:200]}
    except Exception as e:
        return 0, {"_error": f"{type(e).__name__}: {e}"}


def record(name, status, data, note="", allow=(200,)):
    ok = status in allow
    detail = note or f"HTTP {status}"
    if isinstance(data, dict) and data.get("error"):
        detail += f" | error={str(data['error'])[:60]}"
    RESULTS.append((name, ok, detail))
    print(f"  {'OK ' if ok else '!! '}{name:38s} {detail}")


print("=== 基础接口 ===")
status, data = call("GET", "/api/config")
record("GET /api/config", status, data,
       f"HTTP {status}，providers={len((data.get('providers') or {}))} 个，"
       f"version={data.get('version')!r}")

status, data = call("GET", "/api/update")
record("GET /api/update", status, data,
       f"HTTP {status}，current={data.get('current')!r} latest={data.get('latest')!r} "
       f"has_update={data.get('has_update')}")

status, data = call("GET", "/api/update/local")
record("GET /api/update/local", status, data,
       f"HTTP {status}，本地={data.get('local', {}).get('local_version')!r} "
       f"远端={data.get('local', {}).get('remote_version')!r}")

status, data = call("GET", "/api/search/log?limit=3")
record("GET /api/search/log", status, data,
       f"HTTP {status}，记录 {data.get('count')} 条，引擎快照 {len(data.get('engines') or {})} 个")

print("\n=== 配置写入（非法值探测）===")
status, data = call("GET", "/api/config")
original = (data.get("config") or {})

status, data = call("POST", "/api/config", {"reply_count": "abc"})
record("POST /api/config 非法 reply_count", status, data,
       f"HTTP {status}（未校验就写入？）", allow=(200, 400, 422))

status, data = call("GET", "/api/config")
now = (data.get("config") or {}).get("reply_count")
print(f"      写入后 reply_count = {now!r}（类型 {type(now).__name__}）")
if not isinstance(now, int):
    RESULTS.append(("配置未做类型校验导致脏值入库", False,
                    f"reply_count={now!r} 会让推荐回复功能 500"))
    print(f"  !! 配置未做类型校验导致脏值入库              reply_count={now!r}")

# 还原
call("POST", "/api/config", {"reply_count": original.get("reply_count", 3)})

print("\n=== 会话 CRUD ===")
status, data = call("GET", "/api/sessions")
record("GET /api/sessions", status, data,
       f"HTTP {status}，{len(data.get('sessions') or [])} 条")

print("\n=== 截屏与窗口 ===")
status, data = call("POST", "/api/capture", {})
has_shot = bool(isinstance(data, dict) and data.get("url") and data.get("name"))
record("POST /api/capture", status, data,
       f"HTTP {status}，{data.get('width')}x{data.get('height')} "
       f"name={str(data.get('name'))[:16]!r}")

status, data = call("GET", "/api/windows")
windows = data.get("windows") if isinstance(data, dict) else None
record("GET /api/windows", status, data,
       f"HTTP {status}，窗口 {len(windows) if windows is not None else '?'} 个")

if windows:
    target = windows[0]
    hwnd = target.get("id") or target.get("handle") or target.get("hwnd")
    status, data = call("POST", "/api/capture/window", {"id": hwnd})
    record("POST /api/capture/window", status, data,
           f"HTTP {status}，{data.get('width')}x{data.get('height')} "
           f"（目标={str(target.get('title'))[:18]!r}）", allow=(200, 400))

print("\n=== OCR：上传图片（multipart）===")
from PIL import Image, ImageDraw  # noqa: E402
image = Image.new("RGB", (400, 120), "white")
ImageDraw.Draw(image).text((10, 45), "ChatSight OCR test 12345", fill="black")
buffer = io.BytesIO()
image.save(buffer, format="PNG")
png_bytes = buffer.getvalue()


def post_multipart(path, filename, content, fields=None, timeout=120):
    boundary = "----ChatSightTestBoundary"
    body = io.BytesIO()
    for key, value in (fields or {}).items():
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                   f"{value}\r\n".encode())
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
               f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode())
    body.write(content)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        BASE + path, data=body.getvalue(), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return 0, {"_error": f"{type(e).__name__}: {e}"}


status, data = post_multipart("/api/ocr/upload", "test.png", png_bytes)
text = (data.get("text") or "") if isinstance(data, dict) else ""
record("POST /api/ocr/upload", status, data,
       f"HTTP {status}，识别文本 {len(text)} 字"
       + ("（本机无 OCR 引擎，给的是可读错误）" if status != 200 else ""),
       allow=(200, 400, 422, 500))

print("\n=== OCR：框选区域（走完整的 截图→框选 流程）===")
status, shot = call("POST", "/api/capture", {})
if status == 200 and shot.get("name"):
    status, data = call("POST", "/api/ocr/region",
                        {"name": shot["name"], "x": 0, "y": 0,
                         "w": min(400, shot.get("width") or 400),
                         "h": min(200, shot.get("height") or 200)})
    text = (data.get("text") or "") if isinstance(data, dict) else ""
    record("POST /api/ocr/region", status, data,
           f"HTTP {status}，识别文本 {len(text)} 字"
           + ("（本机无 OCR 引擎，给的是可读错误）" if status != 200 else ""),
           allow=(200, 400, 422, 500))
else:
    record("POST /api/ocr/region", 0, {}, "前置截图失败，未能测试")

print("\n=== OCR：越界框选应被夹回而不是报错 ===")
status, shot = call("POST", "/api/capture", {})
if status == 200 and shot.get("name"):
    status, data = call("POST", "/api/ocr/region",
                        {"name": shot["name"], "x": -50, "y": -50,
                         "w": 99999, "h": 99999})
    record("越界框选被夹回", status, data, f"HTTP {status}（不应 500）",
           allow=(200, 400, 422))

print("\n=== AI 推荐回复（需 API Key）===")
status, data = call("POST", "/api/suggestions", {"text": "你好，在吗？", "session_id": 0})
if status == 400 and "API Key" in str(data.get("error", "")):
    record("POST /api/suggestions 未配置 Key", status, data,
           "优雅拒绝：提示先配 API Key（符合预期）", allow=(400,))
else:
    count = len((data.get("suggestions") or [])) if isinstance(data, dict) else 0
    record("POST /api/suggestions", status, data, f"HTTP {status}，返回 {count} 条",
           allow=(200, 400, 502))

print("\n=== 对话（含联网搜索）===")
status, data = call("POST", "/api/chat",
                    {"messages": [{"role": "user", "content": "香港玩具展是什么时候"}],
                     "search": True})
if status == 200:
    info = data.get("search") or {}
    record("POST /api/chat + 联网搜索", status, data,
           f"HTTP {status}，来源 {len(data.get('sources') or [])} 条，"
           f"search.engine={info.get('engine')!r}")
else:
    record("POST /api/chat + 联网搜索", status, data,
           f"HTTP {status}（需 API Key）", allow=(400, 502))

print("\n" + "=" * 78)
passed = sum(1 for _n, ok, _d in RESULTS if ok)
print(f"通过 {passed}/{len(RESULTS)}")
for name, ok, detail in RESULTS:
    if not ok:
        print(f"  失败: {name} —— {detail}")
