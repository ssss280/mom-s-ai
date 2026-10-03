"""前端逻辑静态检查：版本徽标、搜索记录面板、更新按钮的事件绑定是否齐全。"""
import io
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
html = io.open(ROOT / "static" / "index.html", encoding="utf-8").read()
js = io.open(ROOT / "static" / "app.js", encoding="utf-8").read()
css = io.open(ROOT / "static" / "style.css", encoding="utf-8").read()

# 1) 每个 addEventListener 的 id 在 HTML 里是否存在
ids_in_html = set(re.findall(r'id="([^"]+)"', html))
selectors = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
missing = sorted(s for s in selectors if s not in ids_in_html)
print("=== 1. JS 里引用的元素 id 是否都在 HTML 中 ===")
print(f"  HTML 定义 {len(ids_in_html)} 个 id，JS 引用 {len(selectors)} 个")
print(f"  引用了但不存在的 id: {missing or '无 ✓'}")

# 2) 关键功能的事件绑定
print("\n=== 2. 关键功能是否有事件绑定 ===")
features = {
    "联网搜索开关": "chat-search",
    "搜索记录按钮": "btn-search-log",
    "版本徽标点击（本地更新）": "applyLocalUpdate",
    "更新弹窗打开": "openUpdateModal",
    "更新弹窗立即更新按钮": "btn-update-apply",
    "发送按钮": "btn-chat-send",
    "截屏按钮": "btn-capture",
    "窗口截屏": "btn-window",
    "设置保存": "cfg-",
}
for label, token in features.items():
    print(f"  {'OK ' if token in js or token in html else '!! '}{label} ({token})")

# 3) CSS 类是否都被用到（找完全没引用的自定义类，可能是残留）
print("\n=== 3. CSS 里定义了但 JS/HTML 从未使用的类（疑似残留）===")
css_classes = set(re.findall(r"\.([a-z][a-z0-9_-]{2,})\s*[{,]", css, re.I))
used = html + js
unused = sorted(c for c in css_classes if c not in used)
print(f"  CSS 自定义类 {len(css_classes)} 个，未被引用 {len(unused)} 个")
for name in unused[:15]:
    print(f"    .{name}")

# 4) 更新相关：确认不再跳 GitHub
print("\n=== 4. 更新入口是否还跳 GitHub ===")
print(f"  window.open(info.url: {'仍存在（应去掉）' if 'window.open(info.url' in js else '已移除 ✓'}")
print(f"  调用本地更新接口: {'有 ✓' if '/api/update/apply' in js else '没有'}")

# 5) 语法粗检：括号配平
print("\n=== 5. app.js 括号配平 ===")
for pair in ("()", "{}", "[]"):
    opened = js.count(pair[0])
    closed = js.count(pair[1])
    print(f"  {pair}: {opened} / {closed}  {'✓' if opened == closed else '!! 不配平'}")
