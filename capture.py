import mss
import logging
from PIL import Image

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
