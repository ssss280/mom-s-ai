"""验证"改版本号必须先获同意"这道闸门真的拦得住。"""
import io
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_version():
    return re.search(r'__version__\s*=\s*"([^"]+)"',
                     io.open(ROOT / "version.py", encoding="utf-8").read()).group(1)


def run(*args):
    result = subprocess.run(["py", "-3", "release.py", *args], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


before = read_version()
print(f"当前版本: {before}")
print()

CASES = [
    ("只读预览（不带 --apply/--release）", ["--offline"], 0,
     "应正常预览，不改版本号"),
    ("--apply 但没给 --approved", ["--apply", "--offline"], 4,
     "必须被拒绝"),
    ("--release 但没给 --approved", ["--release", "--offline"], 4,
     "必须被拒绝"),
    ("--approved 给了错误版本号", ["--apply", "--offline", "--approved", "9.9.9"], 4,
     "必须被拒绝（与算出的号不一致）"),
]

results = []
for label, args, expect_code, expect_note in CASES:
    code, output = run(*args)
    ok = code == expect_code
    results.append((label, ok))
    print(f"{'OK ' if ok else '!! '}{label}")
    print(f"     退出码 {code}（期望 {expect_code}）—— {expect_note}")
    tail = [line for line in output.splitlines() if line.strip()][-4:]
    for line in tail:
        print(f"       | {line[:88]}")
    print()

print("=== 底层写函数是否也拦住（防绕过）===")
probe = subprocess.run(
    ["py", "-3", "-c",
     f"import sys; sys.path.insert(0, r'{ROOT}'); import release; "
     "print('BEFORE-WRITE'); release.write_local_version('9.9.9'); print('AFTER-WRITE')"],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
combined = probe.stdout + probe.stderr
blocked = "PermissionError" in combined and "AFTER-WRITE" not in probe.stdout
print(f"  {'OK ' if blocked else '!! '}直接调用 write_local_version() 会抛 PermissionError")
print(f"      stdout={probe.stdout.strip()[:60]!r}")
print(f"      异常类型={'PermissionError' if 'PermissionError' in combined else '无'}")
results.append(("底层函数防绕过", blocked))

after = read_version()
print(f"\n=== 版本号是否被改动 ===")
print(f"  执行前 {before} → 执行后 {after}  {'未被改动 ✓' if before == after else '!! 被改了'}")
results.append(("版本号未被改动", before == after))

passed = sum(1 for _l, ok in results if ok)
print(f"\n{passed}/{len(results)} 通过")
