import mss
import os
import logging
from PIL import Image

logger = logging.getLogger(__name__)

# 截图目录保留策略：0 = 不清理（永久保留），正数 = 只保留最近这么多张。
# 实际取值来自配置里的 screenshot_keep，默认永久保留。
DEFAULT_SCREENSHOT_KEEP = 0


def capture_full_screen():
    logger.info("截取全屏")
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        logger.info(f"全屏截图完成: {img.size}")
        return img


# 说明：这里原本还有一个 capture_region()，但**全仓库没有任何地方调用它**。
# 框选识别走的是「服务端截全屏 → 前端拖拽 → /api/ocr/region 在服务端裁剪」这条路
# （见 server.py 的 ocr_region），所以那个函数是死代码，已删除，避免两份裁剪逻辑。


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def prune_screenshots(directory: str, keep: int = DEFAULT_SCREENSHOT_KEEP) -> int:
    """只保留最近的 keep 张截图，返回删除数量。

    keep <= 0 表示**永久保留**，一张都不删（默认行为）。
    只有用户在设置里填了正数才会清理，避免长期使用堆积几个 GB。
    新截的图一定是最新的，所以不会被自己删掉。
    """
    if not keep or keep <= 0:
        return 0
    try:
        names = [n for n in os.listdir(directory) if n.lower().endswith(".png")]
    except OSError:
        return 0
    if len(names) <= keep:
        return 0

    paths = [os.path.join(directory, n) for n in names]
    paths.sort(key=_mtime)  # 旧的在前
    removed = 0
    for path in paths[: len(paths) - keep]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"清理旧截图 {removed} 张（保留最近 {keep} 张）")
    return removed
