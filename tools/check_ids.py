"""检查 app.js 里引用的所有 $("#id") 是否都存在于 index.html。"""
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

html_ids = set(re.findall(r'id="([^"]+)"', html))
js_ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))

missing = sorted(js_ids - html_ids)
unused = sorted(html_ids - js_ids)

print(f"index.html 里定义的 id: {len(html_ids)} 个")
print(f"app.js 里引用的 id  : {len(js_ids)} 个")
print("JS 引用但 HTML 里不存在:", missing if missing else "无")
print("HTML 里未被 JS 直接引用(可能由 CSS 使用):", unused if unused else "无")

# CSS 里引用的 #id 也确认一下
css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
css_ids = set(re.findall(r'#([A-Za-z][A-Za-z0-9_-]+)\s*[,{:.\[]', css))
ghost = sorted(css_ids - html_ids)
print("CSS 里引用但 HTML 中不存在的 id:", ghost if ghost else "无")

sys.exit(1 if (missing or ghost) else 0)
