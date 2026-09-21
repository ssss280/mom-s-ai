"""发版助手（重写版）：以 **git tag 为权威基准**，一条命令完成发版全流程。

为什么要改成"以 tag 为基准"：
旧版调用 `update_check.status()` 取"远端版本"，而那个检测会依次去读
version.py → CHANGELOG → releases → tags，**谁先返回就用谁**。
一旦 tags/releases 落后于 version.py，就会拿到偏低的版本号，
于是同一个版本号被发两次——这正是之前 CHANGELOG 出现两组同名版本的根源。
tag 是发布时打的、不可变，用它当基准才稳。

用法：
    py release.py                        # 打印本地/远端(tag)最高版本，以及本次建议的号
    py release.py --apply                # 只把版本号写进 version.py
    py release.py --release              # 一键发版：升版本 → 提交 → 打 tag → 推送 → 建 Release
    py release.py --release --beta       # 发测试版（1.7.0-beta.1，Release 勾 pre-release）
    py release.py --patch                # 升修订号（1.6.0 → 1.6.1），默认升次版本号
    py release.py --offline              # 不联网，只用本地 tag 作基准

一键发版依赖：
  - `git`（必需）
  - GitHub Release 需要 `GITHUB_TOKEN` 环境变量（有 `repo` 权限）。没有也能打 tag 并推送，
    只是会跳过"建 Release"这一步并明确提示你。
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

import update_check
from version import UPDATE_REPO, __version__

VERSION_FILE = "version.py"
CHANGELOG_FILE = "CHANGELOG.md"
ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------- git 基础 ----------

def git(*args, check=True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def local_tags(fetch: bool = False) -> list:
    """取标签列表；fetch=True 时先同步远端标签。"""
    if fetch:
        try:
            git("fetch", "--tags", "--quiet", "origin")
        except RuntimeError as e:
            print(f"  （同步远端标签失败，用本地标签：{e}）")
    return [t for t in git("tag", "-l").split() if t.strip()]


def highest_version(tags: list, include_prerelease: bool = True) -> str:
    """从标签里取最高版本号。默认连预发布版一起比，用于判断"整体最高"。"""
    best = ""
    for tag in tags:
        version = update_check.parse_version(tag)
        if not version:
            continue
        if not include_prerelease and update_check.is_prerelease(tag):
            continue
        if not best or update_check.is_newer(version, best):
            best = version
    return best


def highest_stable_version(tags: list) -> str:
    """只比正式版（不带 -beta/-rc）。"""
    best = ""
    for tag in tags:
        if update_check.is_prerelease(tag):
            continue
        version = update_check.parse_version(tag)
        if version and (not best or update_check.is_newer(version, best)):
            best = version
    return best


# ---------- version.py / CHANGELOG ----------

def read_local_version() -> str:
    text = open(os.path.join(ROOT, VERSION_FILE), encoding="utf-8").read()
    match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text)
    return match.group(1) if match else ""


def write_local_version(version: str) -> None:
    path = os.path.join(ROOT, VERSION_FILE)
    text = open(path, encoding="utf-8").read()
    new_text, count = re.subn(
        r'(__version__\s*=\s*)[\'"][^\'"]+[\'"]',
        lambda m: f'{m.group(1)}"{version}"', text)
    if count != 1:
        raise RuntimeError(f"version.py 里没有唯一的 __version__（匹配到 {count} 处）")
    open(path, "w", encoding="utf-8", newline="\n").write(new_text)


def changelog_top_version() -> str:
    path = os.path.join(ROOT, CHANGELOG_FILE)
    if not os.path.exists(path):
        return ""
    match = re.search(r"^##\s*\[?v?(\d+(?:\.\d+)+[^\]]*)\]?", 
                      open(path, encoding="utf-8").read(), re.MULTILINE)
    return match.group(1) if match else ""


def changelog_section(version: str) -> str:
    """把 CHANGELOG 里某个版本的正文抽出来，用作 Release 说明。"""
    path = os.path.join(ROOT, CHANGELOG_FILE)
    if not os.path.exists(path):
        return ""
    text = open(path, encoding="utf-8").read()
    pattern = re.compile(rf"^##\s*\[{re.escape(version)}\][^\n]*\n(.*?)(?=^##\s*\[|\Z)",
                         re.M | re.S)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


# ---------- 发版动作 ----------

def make_tag(version: str) -> None:
    tag = f"v{version}"
    if tag in git("tag", "-l").split():
        raise RuntimeError(f"标签 {tag} 已存在（版本号重复？可用 git tag -d {tag} 删掉重打）")
    git("tag", "-a", tag, "-m", f"ChatSight {version}")
    print(f"  已打标签 {tag}")


def push(tag: str = "") -> None:
    git("push", "origin", "HEAD")
    print("  已推送提交")
    if tag:
        git("push", "origin", tag)
        print(f"  已推送标签 {tag}")


def create_release(version: str, prerelease: bool, notes: str) -> bool:
    """用 GitHub API 建 Release；没有 token 就跳过并提示。"""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("  跳过建 Release：没有设置 GITHUB_TOKEN 环境变量。")
        print(f"  可以稍后在网页上手动建：https://github.com/{UPDATE_REPO}/releases/new?tag=v{version}")
        return False

    payload = json.dumps({
        "tag_name": f"v{version}",
        "name": f"v{version}",
        "body": notes or f"ChatSight {version}",
        "prerelease": bool(prerelease),
        "draft": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{UPDATE_REPO}/releases",
        data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "ChatSight-Release",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        print(f"  已建 Release：{data.get('html_url')}")
        return True
    except urllib.error.HTTPError as e:
        print(f"  建 Release 失败（HTTP {e.code}）：{e.read().decode('utf-8', 'replace')[:160]}")
    except Exception as e:
        print(f"  建 Release 失败：{type(e).__name__}: {e}")
    return False


# ---------- 主流程 ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="以 git tag 为基准的发版助手")
    parser.add_argument("--apply", action="store_true", help="把版本号写进 version.py")
    parser.add_argument("--release", action="store_true",
                        help="一键发版：升版本 → 写 CHANGELOG 模板 → 提交 → 打 tag → 推送 → 建 Release")
    parser.add_argument("--beta", action="store_true", help="发预发布版（1.7.0-beta.1）")
    parser.add_argument("--patch", action="store_true", help="升修订号而不是次版本号")
    parser.add_argument("--offline", action="store_true", help="不联网，只用本地标签作基准")
    parser.add_argument("--force", action="store_true", help="允许版本号不比基准高（谨慎）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    tags = local_tags(fetch=not args.offline)
    stable = highest_stable_version(tags)
    overall = highest_version(tags)
    local = read_local_version()
    print(f"github 标签共 {len(tags)} 个")
    print(f"  最高正式版 : {stable or '（无）'}")
    print(f"  最高版本   : {overall or '（无）'}")
    print(f"  version.py : {local}")
    print(f"  CHANGELOG  : {changelog_top_version()}")

    # 基准 = 标签里的最高版本，取不到就退回本地 version.py
    base = overall or local
    if args.beta:
        # 测试版：在"最高正式版"上继续加 beta 序号，例如 1.6.0 -> 1.7.0-beta.1
        stable_base = stable or local
        target = f"{update_check.next_version(stable_base, 'patch' if args.patch else 'minor')}-beta.1"
        # 同一正式版下已经有 beta.N 时递增
        existing = [t for t in tags if t.startswith(f"v{target.rsplit('-beta', 1)[0]}-beta.")]
        if existing:
            numbers = [int(m.group(1)) for t in existing
                       if (m := re.search(r"-beta\.(\d+)$", t))]
            target = f"{target.rsplit('-beta', 1)[0]}-beta.{max(numbers) + 1 if numbers else 2}"
    else:
        target = update_check.next_version(base, "patch" if args.patch else "minor")
        # 正式版不能和已有测试版撞号：1.7.0-beta.1 存在时，正式版仍是 1.7.0（对的）

    print(f"\n本次目标版本：{target}")
    if not args.force and not update_check.is_newer(target, base):
        print(f"提示：目标 {target} 不比基准 {base} 新——很可能已经发过这个号了。")
        print("      确认要发就加 --force。")
        return 2

    if not (args.apply or args.release):
        print("\n（加 --apply 只写版本号；加 --release 走完整发版流程）")
        return 0

    write_local_version(target)
    print(f"已写入 {VERSION_FILE}：__version__ = {target!r}")

    top = changelog_top_version()
    if top != target:
        print(f"注意：CHANGELOG 顶部还是 {top or '（空）'}，请新增一条 [{target}] 记录"
              f"（--release 会把它当作 Release 说明）")

    if not args.release:
        return 0

    print("\n=== 开始一键发版 ===")
    if not args.beta:
        section = changelog_section(target)
        if not section:
            print(f"警告：CHANGELOG 里没有 [{target}] 段落，Release 说明会是空的。")
            print("      建议先补上再来发版（已写入 version.py，可以直接重跑 --release）。")
            return 3

    make_tag(target)
    push(f"v{target}")
    if not args.beta:
        create_release(target, prerelease=False, notes=changelog_section(target))
    else:
        create_release(target, prerelease=True,
                       notes=changelog_section(target) or f"ChatSight {target} 测试版")

    print(f"""
发版完成：{target}
  - 稳定版用户：{'' if args.beta else '会看到「可更新」'}
  - 测试版：{'已标记为 pre-release，稳定通道用户不会收到提示' if args.beta else '（本次是正式版）'}
  - 查看历史：https://github.com/{UPDATE_REPO}/releases""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
