"""统一的运行目录解析。

为什么需要这个模块：
    源码运行时 `__file__` 就是脚本所在目录，一切正常；
    但用 cx_Freeze 打包后，模块被塞进 `lib\\library.zip`，
    `__file__` 变成 `...\\lib\\library.zip\\logger.pyc`，
    于是 `DATA_DIR = 源码目录/data` 会算成 `library.zip\\data`，
    `os.makedirs()` 直接抛 FileNotFoundError [WinError 3]，
    而这时日志 handler 还没装上 —— 报错弹窗有了，日志一个都没写。

目录优先级：
    1. 环境变量 CHATSIGHT_HOME（启动.bat 会设成项目源目录）
    2. 冻结运行时：exe 所在目录
    3. 源码运行时：本文件所在目录
"""

import os
import sys
import tempfile

IS_FROZEN = bool(getattr(sys, "frozen", False))


def _resolve_app_dir() -> str:
    env_home = os.environ.get("CHATSIGHT_HOME", "").strip().strip('"')
    if env_home:
        return os.path.abspath(env_home)

    if IS_FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))

    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _resolve_app_dir()
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
