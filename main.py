import sys
import os
import traceback
import logging
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logger import setup_logging, fatal_error, write_crash_log


def main():
    # 先装日志系统：setup_logging 内部已保证不会抛异常
    setup_logging()
    logger = logging.getLogger(__name__)

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        logger.critical(f"未捕获的异常:\n{error_msg}")
        write_crash_log("程序错误", error_msg)
        fatal_error("程序错误", f"发生未处理的异常:\n\n{exc_value}")

    sys.excepthook = handle_exception

    def handle_thread_exception(args):
        handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = handle_thread_exception

    try:
        from gui import ChatSightApp
        logger.info("正在初始化界面...")
        app = ChatSightApp()
        logger.info("界面初始化完成，启动主循环")
        app.run()
        logger.info("程序正常退出")
    except Exception as e:
        error_msg = traceback.format_exc()
        logger.critical(f"启动失败:\n{error_msg}")
        write_crash_log("启动失败", error_msg)
        fatal_error("启动失败", f"程序无法启动:\n\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 连 main() 的兜底都失效时，直接落盘
        crash = write_crash_log("入口异常", traceback.format_exc())
        sys.stderr.write(traceback.format_exc())
        if crash:
            sys.stderr.write(f"错误日志: {crash}\n")
        sys.exit(1)
