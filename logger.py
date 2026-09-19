import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime

from paths import (
    APP_DIR,
    DATA_DIR,
    ERROR_DIR,
    LOG_FILE,
    IS_FROZEN,
    ensure_writable_dir,
)

# 兼容旧代码引用（注意：冻结后 APP_DIR 是 exe 目录，不再是 library.zip 内部）
SOURCE_DIR = APP_DIR

_active_data_dir = None
_active_error_dir = None


def get_log_file() -> str:
    """返回当前真正生效的日志文件路径"""
    return os.path.join(_active_data_dir or DATA_DIR, os.path.basename(LOG_FILE))


def get_error_dir() -> str:
    """返回当前真正生效的错误日志目录"""
    return _active_error_dir or ERROR_DIR


class ErrorHandler(logging.Handler):
    """当出现 ERROR 及以上级别日志时，自动保存到 error 文件夹"""

    def __init__(self, error_dir: str = None):
        super().__init__(level=logging.ERROR)
        self.error_dir = error_dir or ERROR_DIR
        self._formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    def emit(self, record):
        try:
            directory = ensure_writable_dir(self.error_dir)

            if record.exc_info and not record.exc_text:
                record.exc_text = self._formatter.formatException(record.exc_info)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            error_file = os.path.join(directory, f"error_{timestamp}.log")

            with open(error_file, "a", encoding="utf-8") as f:
                f.write(self._formatter.format(record) + "\n")

                if record.exc_text:
                    f.write(f"--- 堆栈跟踪 ---\n{record.exc_text}\n")

        except Exception:
            self.handleError(record)


def setup_logging():
    """初始化日志系统；无论如何都不抛异常，返回日志文件路径。

    旧实现在这里直接 os.makedirs，冻结打包后路径非法会抛异常，
    而此时 handler 还没装上，导致“报错弹窗 + 零日志”。
    """
    global _active_data_dir, _active_error_dir

    log_file = None
    try:
        _active_data_dir = ensure_writable_dir(DATA_DIR)
        _active_error_dir = ensure_writable_dir(ERROR_DIR)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        if not root_logger.handlers:
            file_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )

            log_file = os.path.join(_active_data_dir, os.path.basename(LOG_FILE))

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)

            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            root_logger.addHandler(console_handler)

            error_handler = ErrorHandler(_active_error_dir)
            error_handler.setFormatter(file_formatter)
            root_logger.addHandler(error_handler)

            logging.info("=" * 50)
            logging.info("ChatSight 启动")
            logging.info(f"程序目录: {APP_DIR} (frozen={IS_FROZEN})")
            logging.info(f"日志文件: {log_file}")
            logging.info("=" * 50)
        else:
            log_file = get_log_file()

    except Exception:
        # 日志系统自己坏了也不能让程序起不来
        try:
            write_crash_log("日志系统初始化失败", traceback.format_exc())
        except Exception:
            pass

    return log_file or get_log_file()


def write_crash_log(title: str, detail: str) -> str:
    """最后一道保险：把一个致命错误写到文件，返回文件路径（绝不抛异常）。"""
    for directory in (_active_error_dir, ERROR_DIR):
        if not directory:
            continue
        try:
            directory = ensure_writable_dir(directory)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            crash_file = os.path.join(directory, f"fatal_{timestamp}.log")
            with open(crash_file, "w", encoding="utf-8") as f:
                f.write(f"致命错误: {title}\n")
                f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"程序目录: {APP_DIR} (frozen={IS_FROZEN})\n")
                f.write(f"可执行文件: {sys.executable}\n")
                f.write(f"描述: {detail}\n\n")

                log_file = get_log_file()
                if os.path.exists(log_file):
                    f.write("=" * 50 + "\n")
                    f.write(f"完整日志 ({log_file}):\n")
                    f.write("=" * 50 + "\n")
                    with open(log_file, "r", encoding="utf-8", errors="replace") as log_f:
                        lines = log_f.read().splitlines()
                        f.write("\n".join(lines[-100:]))
            return crash_file
        except Exception:
            continue
    return ""


def fatal_error(title: str, message: str):
    """记录致命错误、写崩溃日志并弹窗提示"""
    try:
        logging.critical(f"{title}: {message}")
    except Exception:
        pass

    crash_file = write_crash_log(title, message)

    tip = f"错误日志已保存到: {crash_file}" if crash_file else "错误日志保存失败，请检查目录权限"

    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, f"{message}\n\n{tip}")
        root.destroy()
    except Exception:
        # 连弹窗都失败（无 GUI）时，至少把错误打到 stderr
        try:
            sys.stderr.write(f"[{title}] {message}\n{tip}\n")
        except Exception:
            pass


def get_error_files() -> list:
    """获取所有错误日志文件列表"""
    directory = get_error_dir()
    if not os.path.exists(directory):
        return []
    files = [f for f in os.listdir(directory) if f.endswith(".log")]
    files.sort(reverse=True)
    return files


def clear_error_files():
    """清空错误日志文件夹"""
    directory = get_error_dir()
    if not os.path.exists(directory):
        return
    for f in os.listdir(directory):
        try:
            os.remove(os.path.join(directory, f))
        except Exception:
            pass
