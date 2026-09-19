import base64
import shutil
import logging
from io import BytesIO
from PIL import Image

logger = logging.getLogger(__name__)

try:
    import pytesseract
    _tesseract_bin = shutil.which("tesseract")
    HAS_TESSERACT = _tesseract_bin is not None
except ImportError:
    HAS_TESSERACT = False


def image_to_base64(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def ocr_tesseract(image: Image.Image, lang: str = "chi_sim+eng") -> str:
    if not HAS_TESSERACT:
        raise RuntimeError(
            "Tesseract-OCR 未安装或未添加到系统 PATH。\n"
            "下载地址: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "安装后重启程序即可使用本地 OCR。"
        )
    try:
        text = pytesseract.image_to_string(image, lang=lang)
        logger.info(f"Tesseract OCR 完成，识别 {len(text)} 个字符")
        return text.strip()
    except Exception as e:
        logger.exception(f"Tesseract 识别失败: {e}")
        raise RuntimeError(f"Tesseract 识别失败: {e}")


def ocr_vision_model(image: Image.Image, client, model: str = "qwen3-vl-plus") -> str:
    img_b64 = image_to_base64(image)
    logger.info(f"使用视觉模型 {model} 进行 OCR，图片尺寸 {image.size}")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一个聊天截图 OCR 助手。请把图片里的文字**完整、逐行**地转写出来，"
                    "按对话顺序输出，格式为：\n"
                    "发送者: 消息内容\n"
                    "每行一条消息。要求：\n"
                    "1. 不要遗漏任何一行，包括图片最右侧、最下方的文字；\n"
                    "2. 不要总结、不要翻译、不要补全，逐字照抄；\n"
                    "3. 看不清的字用 □ 代替，不要猜；\n"
                    "4. 分不清发送者时，只输出消息内容本身；\n"
                    "5. 只输出识别结果，不要添加任何说明或 Markdown 代码块。"
                )
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请把这张聊天截图里的所有文字完整转写出来，逐行输出，不要漏字。"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}"
                        }
                    }
                ]
            }
        ],
        max_tokens=4000,
        temperature=0.1
    )
    content = (response.choices[0].message.content or "").strip()
    return _strip_code_fence(content)


def _strip_code_fence(text: str) -> str:
    """有些模型会把结果包在 ``` 代码块里，去掉它"""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_chat_text(image: Image.Image, method: str = "auto",
                      client=None, vision_model: str = "qwen3-vl-plus",
                      lang: str = "chi_sim+eng") -> str:
    logger.info(f"OCR 方式: {method}")
    if method == "tesseract":
        return ocr_tesseract(image, lang=lang)
    elif method == "vision":
        if client is None:
            raise ValueError("使用视觉模型 OCR 需要先配置视觉模型 API（设置 → 视觉 API Key）")
        return ocr_vision_model(image, client, model=vision_model)
    elif method == "auto":
        vision_error = None
        if client is not None:
            try:
                return ocr_vision_model(image, client, model=vision_model)
            except Exception as e:
                vision_error = e
                logger.warning(f"视觉模型 OCR 失败，尝试 Tesseract: {e}")
        if HAS_TESSERACT:
            return ocr_tesseract(image, lang=lang)
        message = (
            "没有可用的 OCR 引擎。\n"
            "方案1: 在「设置」里填好视觉模型的 API Key（推荐，例如千问 qwen3-vl-plus）\n"
            "方案2: 安装 Tesseract-OCR (https://github.com/UB-Mannheim/tesseract/wiki)"
        )
        if vision_error is not None:
            message += f"\n\n视觉模型调用失败原因：{vision_error}"
        raise RuntimeError(message)
    else:
        raise ValueError(f"未知的 OCR 方法: {method}")
