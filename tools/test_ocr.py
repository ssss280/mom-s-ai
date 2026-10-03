"""OCR 功能专项测试：用合成图片验证编码、编码往返、引擎选择与错误提示。

不依赖外部服务：视觉模型那一路用假 client 验证"调用失败会退回 Tesseract / 给出清晰错误"。
"""
import io
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PIL import Image, ImageDraw  # noqa: E402
import ocr  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'OK ' if ok else '!! '}{name}" + (f"  —— {detail}" if detail else ""))


def make_image(text="Hello 123"):
    image = Image.new("RGB", (420, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.text((12, 40), text, fill="black")
    return image


print("=== 1) 引擎可用性标志 ===")
print(f"  HAS_TESSERACT = {ocr.HAS_TESSERACT}")

print("\n=== 2) image_to_base64 往返 ===")
image = make_image()
encoded = ocr.image_to_base64(image)
check("返回非空字符串", isinstance(encoded, str) and len(encoded) > 100,
      f"{len(encoded)} 字符")
import base64
try:
    raw = base64.b64decode(encoded)
    decoded = Image.open(io.BytesIO(raw))
    check("能解码回图片且尺寸一致", decoded.size == image.size,
          f"{decoded.size} vs {image.size}")
except Exception as e:
    check("能解码回图片且尺寸一致", False, f"{type(e).__name__}: {e}")

print("\n=== 3) 未知 OCR 方法要给清晰错误 ===")
try:
    ocr.extract_chat_text(make_image(), method="不存在的引擎")
    check("未知方法抛 ValueError", False, "居然没报错")
except ValueError as e:
    check("未知方法抛 ValueError", "未知的 OCR 方法" in str(e), str(e)[:50])
except Exception as e:
    check("未知方法抛 ValueError", False, f"抛的是 {type(e).__name__}: {e}")

print("\n=== 4) method=vision 但没配 client ===")
try:
    ocr.extract_chat_text(make_image(), method="vision", client=None)
    check("缺 client 时给出可读提示", False, "居然没报错")
except ValueError as e:
    check("缺 client 时给出可读提示", "视觉" in str(e), str(e)[:60])
except Exception as e:
    check("缺 client 时给出可读提示", False, f"抛的是 {type(e).__name__}: {e}")


print("\n=== 5) auto 模式：视觉失败要能退回 Tesseract ===")


class BrokenClient:
    """模拟视觉 API 调用失败（网络/额度问题）。"""
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("模拟视觉 API 失败")


try:
    text = ocr.extract_chat_text(make_image(), method="auto",
                                 client=BrokenClient())
    check("视觉失败后退回 Tesseract 并返回文本", isinstance(text, str),
          f"识别结果 {text[:40]!r}")
except RuntimeError as e:
    if ocr.HAS_TESSERACT:
        check("视觉失败后退回 Tesseract 并返回文本", False, f"没退回，报错: {str(e)[:60]}")
    else:
        check("无 Tesseract 时给出安装指引", "Tesseract" in str(e) or "OCR" in str(e),
              str(e)[:60])
except Exception as e:
    check("auto 模式行为", False, f"{type(e).__name__}: {e}")

print("\n=== 6) Tesseract 实际识别（有则验证，无则跳过）===")
if ocr.HAS_TESSERACT:
    try:
        text = ocr.ocr_tesseract(make_image("TEST 888"))
        check("Tesseract 能识别出内容", bool(text.strip()), f"识别到 {text.strip()[:40]!r}")
    except Exception as e:
        check("Tesseract 能识别出内容", False, f"{type(e).__name__}: {str(e)[:70]}")
else:
    print("  跳过：本机未检测到 Tesseract")

print("\n" + "=" * 60)
passed = sum(1 for _n, ok, _d in RESULTS if ok)
print(f"通过 {passed}/{len(RESULTS)}")
for name, ok, detail in RESULTS:
    if not ok:
        print(f"  失败: {name} —— {detail}")
