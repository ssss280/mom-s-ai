"""截图目录清理工具 —— **默认只预览，不删任何东西**。

背景：配置项 `screenshot_keep` 默认 `0` = 永久保留，长期运行会一直涨
（本机实测 52 张 / 45.2 MB，其中好几张是约 4 MB 的全屏截图，可能含屏幕隐私内容）。

用法
    py tools/prune_screenshots.py                       # 只报告占用，不删
    py tools/prune_screenshots.py --keep 100            # 预览：保留最近 100 张，会删掉哪些
    py tools/prune_screenshots.py --keep 100 --apply    # 真正执行删除

安全约定（这是删用户数据的工具，闸门要硬）
    1. 没有 `--apply` 就**绝不删除**任何文件，只打印计划；
    2. 执行前把完整清单和可释放空间打出来；
    3. 只处理 `data/screenshots/` 下的图片，不碰任何其它路径；
    4. `--keep 0` 被拒绝——「全删」请用系统的删除，不要用这个工具。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "screenshots"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def collect() -> list:
    if not SHOTS.is_dir():
        return []
    files = [p for p in SHOTS.iterdir()
             if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    files.sort(key=lambda p: p.stat().st_mtime)   # 旧 → 新
    return files


def read_keep_setting():
    """顺手读一下配置里的 screenshot_keep，只用于提示，不修改。"""
    import json
    cfg = ROOT / "config.json"
    if not cfg.exists():
        return None
    try:
        value = json.loads(cfg.read_text(encoding="utf-8")).get("screenshot_keep")
        return value if isinstance(value, int) else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="截图目录清理（默认只预览）")
    parser.add_argument("--keep", type=int, default=None,
                        help="保留最近 N 张（最旧的先删）；不给就只报告占用")
    parser.add_argument("--apply", action="store_true",
                        help="真正执行删除；不给则只打印计划")
    args = parser.parse_args()

    files = collect()
    total = sum(p.stat().st_size for p in files)
    keep_setting = read_keep_setting()

    print(f"截图目录: {SHOTS}")
    print(f"  文件数: {len(files)}")
    print(f"  总大小: {human(total)}")
    if files:
        biggest = max(files, key=lambda p: p.stat().st_size)
        print(f"  最大单张: {human(biggest.stat().st_size)}  {biggest.name}")
    print(f"  配置 screenshot_keep = {keep_setting}"
          f"{'（0 = 永久保留，这就是它会一直涨的原因）' if keep_setting == 0 else ''}")
    print()

    if args.keep is None:
        print("没有给 --keep，只报告占用。")
        print("想看清理计划：py tools/prune_screenshots.py --keep 100")
        return 0

    if args.keep <= 0:
        print("!! --keep 必须 >= 1。想清空请直接用系统的删除，不要用这个工具。")
        return 2

    if len(files) <= args.keep:
        print(f"当前 {len(files)} 张 <= keep {args.keep}，没有可清理的。")
        return 0

    doomed = files[: len(files) - args.keep]
    freed = sum(p.stat().st_size for p in doomed)
    print(f"保留最近 {args.keep} 张，将删除最旧的 {len(doomed)} 张，释放 {human(freed)}：")
    for p in doomed[:20]:
        print(f"  - {p.name}  {human(p.stat().st_size)}")
    if len(doomed) > 20:
        print(f"  ... 另有 {len(doomed) - 20} 张")
    print()

    if not args.apply:
        print("这是预览。要真正删除，请加 --apply 再跑一次：")
        print(f"  py tools/prune_screenshots.py --keep {args.keep} --apply")
        return 0

    removed = 0
    for p in doomed:
        try:
            os.remove(p)
            removed += 1
        except OSError as exc:
            print(f"  !! 删除失败 {p.name}: {exc}")
    print(f"已删除 {removed} 张，释放 {human(freed)}。")
    print("注意：历史记录里引用这些图片的条目会打不开图（记录本身不受影响）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
