"""接口契约核对：前端调用的每个 /api 路径，后端是否真的有；反之是否有孤儿接口。"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return io.open(os.path.join(ROOT, name), encoding="utf-8", errors="replace").read()


server = read("server.py")
app_js = read("static/app.js")
index_html = read("static/index.html")

backend = set(re.findall(r'@app\.(?:get|post|put|delete)\("([^"]+)"', server))
print(f"=== 后端接口 {len(backend)} 个 ===")
for path in sorted(backend):
    print(f"  {path}")

print("\n=== 前端调用的路径 ===")
frontend = set()
for match in re.finditer(r'api\(\s*[`"\']([^`"\']+)', app_js):
    frontend.add(match.group(1))
for match in re.finditer(r'fetch\(\s*[`"\']([^`"\']+)', app_js):
    frontend.add(match.group(1))
for path in sorted(frontend):
    print(f"  {path}")


def normalize(path):
    """把 /api/sessions/${id} 这种模板归一成 /api/sessions/<int:session_id> 形式的键。"""
    path = re.sub(r"\$\{[^}]+\}", "*", path)
    path = re.sub(r"<[^>]+>", "*", path)
    return path.split("?")[0].rstrip("/")


backend_norm = {normalize(p): p for p in backend}
frontend_norm = {normalize(p): p for p in frontend}

print("\n=== 核对结果 ===")
missing = []
for norm, original in sorted(frontend_norm.items()):
    candidates = [b for b in backend_norm if b == norm]
    if not candidates:
        # 静态资源不算接口
        if original.startswith("/screenshots") or original.startswith("/static"):
            continue
        missing.append(original)
print(f"  前端调用但后端没有: {missing or '无 ✓'}")

unused = sorted(backend - set(frontend_norm.values()))
print(f"  后端有但前端没调: {unused or '无'}")

print("\n=== 静态资源挂载 ===")
for keyword in ("/screenshots", "/static", "send_from_directory"):
    print(f"  {keyword}: {'有' if keyword in server else '无'}")
