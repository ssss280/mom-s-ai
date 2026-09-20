"""统一的运行目录解析（网页版：始终以项目源目录为准）。"""

import os
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
ERROR_DIR = os.path.join(APP_DIR, "error")
SCREENSHOT_DIR = os.path.join(DATA_DIR, "screenshots")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "chatsight.db")
LOG_FILE = os.path.join(DATA_DIR, "app.log")


def ensure_writable_dir(path: str) -> str:
    """创建并返回一个确实可写的目录。

    目标目录不可用时（只读盘、路径非法、无权限），
    回退到用户临时目录，保证程序不因为“写不了日志”而启动失败。
    """
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return path
    except OSError:
        fallback = os.path.join(
            tempfile.gettempdir(), "ChatSight", os.path.basename(path.rstrip("\\/")) or "data"
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback
