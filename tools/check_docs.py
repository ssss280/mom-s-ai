"""文档一致性门禁（对应 PROJECT.md 的 P4 / G4 门禁）。

把 PROJECT.md 里「同一事实只写一处」「改了规则要清掉旧说法」这两条约定，
变成可运行的检查——**光写文档约束不住 AI，这是本项目已经验证过的结论**。

检查项：
    1. 版本一致   version.py 的 __version__ == CHANGELOG.md 顶部版本
    2. 发版命令   所有 .md 代码块里会改版本号的命令必须带 --approved
    3. 过时规则   version.py 里不得残留"以远端版本为准"的旧基准说法
    4. 架构树     PLAN.md 架构树列出的仓库文件必须真实存在
    5. 主线入口   PROJECT.md 存在，且 AGENTS.md / README.md 都链接到它

用法：
    py -3 tools\\check_docs.py
    py -3 tools\\check_docs.py -v      # 额外打印通过项与提示

退出码：0 = 全绿（可能有 WARN），1 = 有 FAIL，2 = 脚本自身出错。
"""
from __future__ import annotations

import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILS: list = []
WARNS: list = []
PASSES: list = []

# 运行时数据目录：架构树里列了，但换台机器不一定存在，跳过
RUNTIME_PREFIXES = ("data/", "error/")

# 旧版基准说法——正是导致同号重发的那条规则
STALE_RULES = (
    "以 GitHub 仓库上的版本为准",
    "GitHub 版本 + 1",
    "GitHub 上当前是",
)

SKIP_DIRS = {".git", "__pycache__", "data", "error", ".bld", "build", "node_modules", ".vscode", ".idea"}


def read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def fail(check: str, msg: str) -> None:
    FAILS.append((check, msg))


def warn(check: str, msg: str) -> None:
    WARNS.append((check, msg))


def okay(check: str, msg: str) -> None:
    PASSES.append((check, msg))


def markdown_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".md"):
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, ROOT).replace("\\", "/")


def fenced_blocks(text: str):
    """切出所有 ``` 围栏块的内容（不含围栏行）。"""
    blocks, cur, in_fence = [], None, False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_fence and cur is not None:
                blocks.append(cur)
            cur = [] if not in_fence else None
            in_fence = not in_fence
            continue
        if in_fence and cur is not None:
            cur.append(line)
    if in_fence and cur is not None:
        blocks.append(cur)
    return blocks


# --------------------------------------------------------------------------- 1
def check_version_sync() -> None:
    name = "版本一致"
    src = read("version.py")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
    if not m:
        fail(name, "version.py 里找不到 __version__")
        return
    ver = m.group(1)

    log = read("CHANGELOG.md")
    m2 = re.search(r"^##\s*\[([^\]]+)\]", log, re.M)
    if not m2:
        fail(name, "CHANGELOG.md 里找不到任何 '## [版本]' 标题")
        return
    top = m2.group(1).strip()

    if top != ver:
        fail(name, f"CHANGELOG 顶部版本 {top!r} != version.py 的 {ver!r}（AGENTS.md 第二条要求必须相等）")
    else:
        okay(name, f"CHANGELOG 顶部 = version.py = {ver}")


# --------------------------------------------------------------------------- 2
def check_release_cmd() -> None:
    name = "发版命令"
    hits = 0
    for rel in markdown_files():
        in_fence = False
        for lineno, line in enumerate(read(rel).splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence or "release.py" not in line:
                continue
            if "--publish-only" in line:          # 不改版本号，不需要同意凭据
                continue
            if "--apply" not in line and "--release" not in line:
                continue                           # 只读预览
            hits += 1
            if "--approved" not in line:
                fail(name, f"{rel}:{lineno} 会改版本号却没带 --approved，照做会被拒绝："
                           f"{line.strip()}")
    if hits:
        okay(name, f"扫到 {hits} 条会改版本号的命令示例，均已检查 --approved")
    else:
        warn(name, "一条会改版本号的命令示例都没扫到，检查可能失效了")


# --------------------------------------------------------------------------- 3
def check_stale_rule() -> None:
    name = "过时规则"
    src = read("version.py")
    found = [s for s in STALE_RULES if s in src]
    if found:
        fail(name, f"version.py 仍残留旧版基准说法（{found}）——这正是导致同号重发的规则，"
                   f"它躺在改版本号时第一眼就会看到的位置")
    else:
        okay(name, "version.py 无旧版基准说法")


# --------------------------------------------------------------------------- 4
ARCH_ENTRY = re.compile(r"^([\u2502\s]*)(?:\u251c\u2500\u2500|\u2514\u2500\u2500)\s+(\S+)")


def check_arch_tree() -> None:
    name = "架构树"
    tree = None
    for block in fenced_blocks(read("PLAN.md")):
        if any("ChatSight/" in line for line in block):
            tree = block
            break
    if tree is None:
        fail(name, "PLAN.md 里找不到以 'ChatSight/' 开头的架构树代码块")
        return

    stack: list = []
    listed_top: set = set()
    checked = skipped = 0
    for line in tree[1:]:
        m = ARCH_ENTRY.match(line)
        if not m:
            continue
        prefix, entry = m.group(1), m.group(2)
        depth = len(prefix) // 4
        del stack[depth:]
        stack.append(entry.rstrip("/"))
        if depth == 0:
            listed_top.add(entry.rstrip("/"))
        path = "/".join(stack)
        if path.startswith(RUNTIME_PREFIXES) or path in ("data", "error"):
            skipped += 1
            continue
        if os.path.exists(os.path.join(ROOT, path)):
            checked += 1
        else:
            fail(name, f"PLAN.md 架构树列了 {path}，但仓库里不存在")

    okay(name, f"核对 {checked} 项存在（跳过 {skipped} 项运行时数据）")

    # 反向：仓库顶层有、架构树没列的（只提示，不阻塞）
    for entry in sorted(os.listdir(ROOT)):
        if entry in SKIP_DIRS or entry.startswith("."):
            continue
        if entry.endswith(".py") or entry.endswith(".md") or entry.endswith(".bat"):
            if entry not in listed_top:
                warn(name, f"仓库顶层有 {entry}，PLAN.md 架构树没列")


# --------------------------------------------------------------------------- 5
def check_entry_link() -> None:
    name = "主线入口"
    if not os.path.exists(os.path.join(ROOT, "PROJECT.md")):
        fail(name, "PROJECT.md 不存在——AI 找不到主线流程")
        return
    missing = [f for f in ("AGENTS.md", "README.md") if "PROJECT.md" not in read(f)]
    if missing:
        fail(name, f"{'、'.join(missing)} 没有链接到 PROJECT.md，AI 会看不到主流程")
    else:
        okay(name, "AGENTS.md 与 README.md 都指向 PROJECT.md")


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    for fn in (check_version_sync, check_release_cmd, check_stale_rule,
               check_arch_tree, check_entry_link):
        try:
            fn()
        except Exception as exc:  # 单项炸了不能拖垮整轮
            fail(fn.__name__, f"检查本身抛异常：{exc!r}")

    if verbose:
        for check, msg in PASSES:
            print(f"  [ OK ] {check}：{msg}")
    for check, msg in WARNS:
        print(f"  [WARN] {check}：{msg}")
    for check, msg in FAILS:
        print(f"  [FAIL] {check}：{msg}")

    total = len(FAILS)
    print(f"\n文档一致性：{len(PASSES)} 项通过，{len(WARNS)} 项提示，{total} 项失败")
    if total:
        print("（规则见 PROJECT.md §2 单一事实源地图；修完再跑一遍）")
        return 1
    print("（门禁通过，可以进入 P4 登记 / P5 发版）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
