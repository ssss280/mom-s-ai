"""发版助手：按「GitHub 上的版本 + 1」确定本次该提交的版本号。

用法：
    py release.py                 # 联网查 GitHub 版本，打印本次应提交的版本号
    py release.py --apply         # 直接把版本号写进 version.py，并打印 CHANGELOG 模板
    py release.py --patch         # 升修订号（1.2.0 → 1.2.1），默认升次版本号（→ 1.3.0）
    py release.py --offline       # 不联网，以本地 version.py 为基准（没网时用）

流程（写进了 PLAN.md「更新记录约定」）：
    1. py release.py --apply            → version.py 更新成该用的版本号
    2. 在 CHANGELOG.md 顶部加一条同名版本记录
    3. git commit / git push
"""

import argparse
import logging
import pathlib
import re
import sys
from datetime import date

import update_check
from version import __version__

VERSION_FILE = pathlib.Path(__file__).with_name("version.py")


def read_local_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text)
    return match.group(1) if match else ""


def write_local_version(version: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'(__version__\s*=\s*)[\'"][^\'"]+[\'"]',
        lambda m: f'{m.group(1)}"{version}"',
        text,
    )
    if count != 1:
        raise RuntimeError(f"version.py 里没有找到唯一的 __version__（匹配到 {count} 处）")
    VERSION_FILE.write_text(new_text, encoding="utf-8")


def changelog_top_version() -> str:
    path = VERSION_FILE.with_name("CHANGELOG.md")
    if not path.exists():
        return ""
    match = re.search(r"^##\s*\[?v?(\d+(?:\.\d+)+)\]?", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="按 GitHub 上的版本号推算本次要提交的版本")
    parser.add_argument("--apply", action="store_true", help="把算出的版本号写进 version.py")
    parser.add_argument("--patch", action="store_true", help="升修订号而不是次版本号")
    parser.add_argument("--offline", action="store_true", help="不联网，以本地版本为基准")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    local = read_local_version()
    if args.offline:
        remote = local
        print(f"[离线模式] 以本地版本 {local} 为基准")
    else:
        print("正在查询 GitHub 上的版本 …")
        info = update_check.status()
        # 后台线程在跑，等一下结果（最多 10 秒）
        import time
        for _ in range(20):
            if not info.get("pending"):
                break
            time.sleep(0.5)
            info = update_check.status()
        remote = info.get("latest") or ""
        if info.get("pending"):
            print("查询超时。可以重试，或用 --offline 以本地版本为基准。")
            return 2
        if not remote:
            print(f"没能从 GitHub 取到版本号：{info.get('error') or '未知原因'}")
            print("可以检查网络，或用 --offline 以本地版本为基准。")
            return 2
        print(f"GitHub 版本：{remote}（来源 {info.get('source')}）")

    part = "patch" if args.patch else "minor"
    target = update_check.next_version(remote, part)

    print(f"本地版本  ：{local}")
    print(f"本次应提交：{target}")
    if update_check.is_newer(local, target):
        print("提示：本地版本比目标还新，检查一下是不是漏提交了？")

    if not args.apply:
        print("\n（加 --apply 可把它写进 version.py）")
        return 0

    write_local_version(target)
    print(f"\n已写入 {VERSION_FILE}：__version__ = \"{target}\"")
    top = changelog_top_version()
    if top and top != target:
        print(f"注意：CHANGELOG.md 顶部还写着 {top}，请合并或新增一条 [{target}] 记录。")
    print(f"""
下一步：
  1. 在 CHANGELOG.md 顶部加/合并一条记录，标题为：
       ## [{target}] - {date.today().isoformat()}
  2. git add -A && git commit && git push
  3. 推送完成后，已发布版本的用户打开页面就会在左下角看到「可更新」""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
