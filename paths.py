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


_writable_cache: dict = {}


def ensure_writable_dir(path: str) -> str:
    """创建并返回一个确实可写的目录。

    目标目录不可用时（只读盘、路径非法、无权限），
    回退到用户临时目录，保证程序不因为“写不了日志”而启动失败。

    结果按目录缓存：旧实现每次调用都 create+delete 一个 .write_test 探测文件，
    而 query_log_path() 每搜一次就调一次——一次 29 题评测就是上百次无意义的
    文件创建/删除。目录可写性不会在进程生命周期内变化，探测一次就够。
    """
    cached = _writable_cache.get(path)
    if cached:
        return cached
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        _writable_cache[path] = path
        return path
    except OSError:
        fallback = os.path.join(
            tempfile.gettempdir(), "ChatSight", os.path.basename(path.rstrip("\\/")) or "data"
        )
        os.makedirs(fallback, exist_ok=True)
        _writable_cache[path] = fallback
        return fallback
