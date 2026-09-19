import tkinter as tk
from tkinter import ttk
import mss
import logging
from PIL import Image, ImageTk
import threading

logger = logging.getLogger(__name__)


def capture_full_screen():
    logger.info("截取全屏")
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        logger.info(f"全屏截图完成: {img.size}")
        return img


def capture_region(region):
    x1, y1, x2, y2 = region
    logger.info(f"截取区域: ({x1}, {y1}, {x2}, {y2})")
    with mss.mss() as sct:
        monitor = {"top": y1, "left": x1, "width": x2 - x1, "height": y2 - y1}
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        logger.info(f"区域截图完成: {img.size}")
        return img


def select_region(callback, master=None):
    """全屏覆盖层，拖拽框选区域。

    callback 收到 (region, image)：
        region 为 (x1, y1, x2, y2)，按 ESC 取消时为 None；
        image  为框选区域的截图（PIL.Image），取消时为 None。

    image 直接从覆盖层显示的那张全屏截图里裁剪，而不是选完再重新抓一次屏。
    因为选完之后主窗口会重新显示并置顶，那时抓屏的话自家窗口正好盖住框选区域，
    结果只有左边一条缝能识别到（表现就是"后面的字识别不出来"）。
    顺带好处：所见即所得，用户框的就是最终识别的内容。

    master 给定时用主窗口同一个 Tcl 解释器的 Toplevel，并立即返回（不阻塞）。
    注意：绝不能在已有主窗口时再开第二个 Tk() 并调用它的 mainloop()。
    Tcl 的 Tk_GetNumMainWindows() 统计的是整个线程的主窗口数量，主界面窗口
    一直存在，嵌套的 mainloop 就永远退不出来（实测卡死），调用方后面恢复
    主窗口的代码再也不会执行 —— 表现就是"点了截屏程序就消失（其实没退出）"。
    """
    # 先在主窗口已隐藏、覆盖层还没出现时抓一张干净的屏幕
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        sct_img = sct.grab(monitor)
        bg_image = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

    owns_root = master is None
    root = tk.Tk() if owns_root else tk.Toplevel(master)
    try:
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(cursor="cross")
        try:
            root.focus_force()
        except tk.TclError:
            pass

        # 画布尺寸与截图一致，保证画布坐标 == 截图像素坐标（1:1）
        canvas = tk.Canvas(root, width=bg_image.width, height=bg_image.height, highlightthickness=0)
        canvas.pack()

        # 显式传 master=root：图片名是按 Tcl 解释器隔离的。
        # 不传 master 时 PhotoImage 会注册到默认根窗口那个解释器，
        # 而 canvas 属于本函数创建的窗口（独立模式下是另一个解释器），
        # 于是报 TclError: image "pyimage1" doesn't exist
        bg_photo = ImageTk.PhotoImage(bg_image, master=root)
        canvas.create_image(0, 0, anchor=tk.NW, image=bg_photo)
        canvas.image = bg_photo  # 留住引用，防止被 Python GC 回收

        overlay = canvas.create_rectangle(0, 0, 0, 0, outline="#00ff00", width=2, fill="", stipple="gray25")
        info_label = canvas.create_text(10, 15, anchor=tk.NW, text="拖拽选择聊天窗口区域 | ESC 取消",
                                         fill="#00ff00", font=("Microsoft YaHei", 12, "bold"))

        start_x = tk.IntVar(value=0)
        start_y = tk.IntVar(value=0)
        selection_made = tk.BooleanVar(value=False)

        def finish(region):
            """关闭覆盖层，回传框选区域 + 对应的截图"""
            root.destroy()
            if region is None:
                callback(None, None)
            else:
                x1, y1, x2, y2 = region
                callback(region, bg_image.crop((x1, y1, x2, y2)))

        def on_press(event):
            start_x.set(event.x)
            start_y.set(event.y)
            canvas.coords(overlay, event.x, event.y, event.x, event.y)

        def on_drag(event):
            canvas.coords(overlay, start_x.get(), start_y.get(), event.x, event.y)
            w = abs(event.x - start_x.get())
            h = abs(event.y - start_y.get())
            canvas.itemconfig(info_label, text=f"区域: {w} x {h} | ESC 取消 | 回车确认")

        def on_release(event):
            x1 = min(start_x.get(), event.x)
            y1 = min(start_y.get(), event.y)
            x2 = max(start_x.get(), event.x)
            y2 = max(start_y.get(), event.y)
            if (x2 - x1) > 10 and (y2 - y1) > 10:
                selection_made.set(True)
                finish((x1, y1, x2, y2))

        def on_escape(event):
            finish(None)

        def on_enter(event):
            coords = canvas.coords(overlay)
            if coords and coords[2] > 0 and coords[3] > 0:
                x1, y1, x2, y2 = coords[0], coords[1], coords[2], coords[3]
                if x1 > x2:
                    x1, x2 = x2, x1
                if y1 > y2:
                    y1, y2 = y2, y1
                if (x2 - x1) > 10 and (y2 - y1) > 10:
                    selection_made.set(True)
                    finish((int(x1), int(y1), int(x2), int(y2)))

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", on_escape)
        root.bind("<Return>", on_enter)

        if owns_root:
            # 只有独立使用（线程里没有别的 Tk 主窗口）时才可以阻塞等待
            root.mainloop()
        # master 模式：立即返回，用户选完区域后由回调继续处理
    except Exception:
        # 覆盖层出错时必须销毁，否则会留下一个看不见的全屏窗口卡住界面
        try:
            root.destroy()
        except Exception:
            pass
        raise


def select_region_async(callback):
    thread = threading.Thread(target=select_region, args=(callback,), daemon=True)
    thread.start()
