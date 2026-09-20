"""Windows 窗口枚举与单窗口截图。

网页版默认截的是"整个屏幕"，而浏览器窗口通常压在最前面，结果截到的就是浏览器自己。
这里通过 PrintWindow 让系统把指定窗口的画面单独画出来——**窗口被浏览器挡住也能截到**，
并且不会把浏览器窗口拍进去。

只用 ctypes（标准库），不引入新依赖；非 Windows 平台全部返回空/报错。
"""

import ctypes
import logging
import sys
from ctypes import wintypes

from PIL import Image

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

PW_RENDERFULLCONTENT = 0x00000002
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
DIB_RGB_COLORS = 0
BI_RGB = 0

# 太小的窗口基本都是系统杂项窗口，不作为识别目标
MIN_WINDOW_SIZE = 80

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:  # 极老的系统没有 dwmapi
        dwmapi = None

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    # 显式声明参数/返回类型：ctypes 默认按 c_int 处理整数句柄，64 位下会截断
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int

    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


def _is_cloaked(hwnd: int) -> bool:
    """UWP 等应用会留下不可见的"幽灵窗口"，这里过滤掉。"""
    if not IS_WINDOWS or dwmapi is None:
        return False
    value = ctypes.c_int(0)
    try:
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except OSError:
        return False
    return hr == 0 and value.value != 0


def list_windows() -> list[dict]:
    """按 z 序（最上层在前）返回可截图的顶层窗口。"""
    if not IS_WINDOWS:
        return []

    windows: list[dict] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _is_cloaked(hwnd):
            return True

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < MIN_WINDOW_SIZE or height < MIN_WINDOW_SIZE:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        windows.append({
            "id": int(hwnd),
            "title": buffer.value,
            "width": width,
            "height": height,
            "minimized": bool(user32.IsIconic(hwnd)),
        })
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_callback), 0)
    except OSError as e:
        logger.warning(f"枚举窗口失败: {e}")
        return []

    logger.info(f"枚举到 {len(windows)} 个可截图的窗口")
    return windows


def capture_window(hwnd: int) -> Image.Image:
    """截取指定窗口，返回 PIL Image（窗口被遮挡也能截到）。"""
    if not IS_WINDOWS:
        raise RuntimeError("窗口截图目前只支持 Windows")

    hwnd = int(hwnd)
    if not user32.IsWindow(hwnd):
        raise RuntimeError("窗口不存在或已关闭，请点「刷新」重新选择")

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("无法获取窗口尺寸")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("窗口尺寸无效，可能已最小化，请先还原窗口再截取")

    logger.info(f"截取窗口 {hwnd}: {width}x{height}")
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        raise RuntimeError("无法获取窗口绘图上下文")
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    if not mem_dc or not bitmap:
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
        raise RuntimeError("创建绘图缓冲区失败")

    old_obj = gdi32.SelectObject(mem_dc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not ok:
            # 老系统 / 部分程序不认 PW_RENDERFULLCONTENT，退回默认行为再试一次
            ok = user32.PrintWindow(hwnd, mem_dc, 0)
        if not ok:
            raise RuntimeError(
                "系统拒绝了该窗口的后台截图（部分程序不支持），请改用「延时截屏」并切换到该窗口"
            )

        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # 负数 = 自上而下，省得再翻转
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB
        info = BITMAPINFO()
        info.bmiHeader = header

        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(
            mem_dc, bitmap, 0, height, ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(info), DIB_RGB_COLORS,
        )
        if not lines:
            raise RuntimeError("读取窗口像素失败")
        # 必须 copy()：buffer 是局部变量，出了函数就释放了
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1).copy()
    finally:
        if old_obj:
            gdi32.SelectObject(mem_dc, old_obj)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
