"""体检 .bat 的三条硬性约束（**只读，不改文件**）：

1. 必须纯 ASCII（cmd 在代码页切换时读中文会错位，项目在 启动.bat 上踩过）
2. 必须全部 CRLF（LF-only 的批处理会让 goto/标签解析出错）
3. 语句块内部不能出现引号包裹的 ")"（cmd 会把它当成块结束符）

> 这里原本会在体检前**把文件写回成 CRLF** —— 一个叫"体检"的脚本悄悄改文件是危险的，
> 已经去掉那次写回。需要转行尾请显式用别的工具，不要让体检顺手改东西。
"""
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

path = pathlib.Path(__file__).resolve().parent.parent / "安装.bat"
text = path.read_bytes().decode("utf-8")

normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")

raw = path.read_bytes()
crlf = raw.count(b"\r\n")
lf_only = raw.count(b"\n") - crlf
ok = True

print(f"CRLF={crlf}  裸 LF={lf_only}  {'OK' if lf_only == 0 else 'FAIL'}")
ok &= lf_only == 0

bad_bytes = [(i, b) for i, b in enumerate(raw) if b > 127]
if bad_bytes:
    line = normalized[:bad_bytes[0][0]].count("\r\n") + 1
    print(f"FAIL 非 ASCII 字节 {len(bad_bytes)} 个，第一个在第 {line} 行："
          f"{normalized.splitlines()[line - 1].strip()!r}")
    ok = False
else:
    print("OK 纯 ASCII")

# 块深度扫描：块内出现引号里的 ")" 就会提前结束块
depth = 0
problems = []
for no, line in enumerate(normalized.split("\r\n"), 1):
    stripped = line.strip()
    if stripped.lower().startswith(("rem", "::")):
        continue
    inside = depth > 0
    # 找引号内的 ")"
    if inside:
        for chunk in line.split('"')[1::2]:
            if ")" in chunk:
                problems.append((no, stripped))
                break
    opens = sum(1 for c in line if c == "(")
    closes = sum(1 for c in line if c == ")")
    depth += opens - closes
    if depth < 0:
        problems.append((no, f"块深度变负: {stripped}"))
        depth = 0

if problems:
    print("FAIL 可疑行（块内引号含右括号）：")
    for no, line in problems:
        print(f"  第 {no} 行: {line}")
    ok = False
else:
    print("OK 没有块内引号右括号问题")
print(f"最终块深度 = {depth} {'OK' if depth == 0 else 'FAIL 括号不配平'}")

sys.exit(0 if ok and depth == 0 else 1)
