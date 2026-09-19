import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)


PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "text_models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
        "vision_models": ["gpt-4o", "gpt-4-turbo"],
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "text_models": ["deepseek-chat", "deepseek-reasoner"],
        "vision_models": [],
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text_models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-max"],
        "vision_models": [
            "qwen3-vl-plus",
            "qwen3-vl-flash",
            "qwen-vl-max",
            "qwen-vl-plus",
            "qwen-vl-ocr-latest",
        ],
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "text_models": ["llama3", "qwen2", "gemma2"],
        "vision_models": ["llava"],
    },
    "custom": {
        "base_url": "",
        "text_models": [],
        "vision_models": [],
    },
}


class ChatSightModel:
    def __init__(self, config: dict):
        self.config = config
        self.client = None          # 文本模型客户端
        self.vision_client = None   # 视觉（OCR）客户端，可独立配置
        self._init_clients()

    def _build_client(self, provider: str, api_key: str, base_url: str):
        provider = provider or "openai"
        if not base_url:
            base_url = PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "")

        if not api_key:
            if provider == "ollama":
                api_key = "ollama"
            else:
                return None

        return OpenAI(api_key=api_key, base_url=base_url)

    def _init_clients(self):
        self.client = self._build_client(
            self.config.get("api_provider", "openai"),
            self.config.get("api_key", ""),
            self.config.get("api_base_url", ""),
        )

        # 视觉 OCR 支持独立的提供商 / Key / 地址（例如文本用 DeepSeek、
        # 视觉用千问），留空则自动回退到上面的文本配置
        self.vision_client = self._build_client(
            self.config.get("vision_provider") or self.config.get("api_provider", "openai"),
            self.config.get("vision_api_key") or self.config.get("api_key", ""),
            self.config.get("vision_base_url") or self.config.get("api_base_url", ""),
        )

    def update_config(self, config: dict):
        self.config = config
        self._init_clients()

    @property
    def is_configured(self) -> bool:
        return self.client is not None

    @property
    def is_vision_configured(self) -> bool:
        return self.vision_client is not None

    def get_available_models(self, model_type: str = "text") -> list:
        provider = self.config.get("api_provider", "openai")
        if model_type == "vision":
            provider = self.config.get("vision_provider") or provider
        defaults = PROVIDER_DEFAULTS.get(provider, {})
        if model_type == "text":
            return defaults.get("text_models", [])
        return defaults.get("vision_models", [])

    def fetch_models_from_api(self, vision: bool = False) -> list[str]:
        client = self.vision_client if vision else self.client
        if client is None:
            return []
        try:
            models = client.models.list()
            result = sorted([m.id for m in models.data])
            logger.info(f"从 API 获取到 {len(result)} 个模型")
            return result
        except Exception as e:
            logger.exception(f"获取模型列表失败: {e}")
            return []

    def get_reply_suggestions(self, chat_text: str, count: int = 3,
                               style: str = "friendly") -> list[str]:
        if not self.is_configured:
            return ["请先在设置中配置 API Key"]

        model = self.config.get("text_model", "gpt-4o-mini")

        style_prompts = {
            "friendly": "友好、亲切、自然的语气",
            "professional": "专业、礼貌、正式",
            "humorous": "幽默、轻松、有趣",
            "concise": "简洁、直接、高效",
            "empathetic": "共情、理解、温暖",
        }
        style_desc = style_prompts.get(style, style_prompts["friendly"])

        system_prompt = f"""你是一个聊天助手。根据下面的聊天对话内容，生成 {count} 条不同风格的推荐回复。
要求：
1. 回复语气：{style_desc}
2. 每条回复要自然、符合聊天上下文
3. 回复要简短，像真实聊天消息一样
4. 直接输出回复内容，不要加编号或引号
5. 每条回复之间用 "---" 分隔"""

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"以下是聊天内容：\n\n{chat_text}"}
                ],
                max_tokens=1500,
                temperature=0.8,
            )
            content = response.choices[0].message.content.strip()
            suggestions = [s.strip() for s in content.split("---") if s.strip()]
            return suggestions[:count]
        except Exception as e:
            logger.exception(f"生成推荐回复失败: {e}")
            return [f"API 调用失败: {str(e)}"]

    def analyze_chat_with_vision(self, image_base64: str) -> str:
        if not self.is_configured:
            return "请先在设置中配置 API Key"

        model = self.config.get("vision_model", "gpt-4o")

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个聊天截图识别助手。请识别图片中的所有聊天内容，按对话顺序输出，格式为 '发送者: 消息内容'。"
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请识别这张聊天截图中的文字内容。"},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                            }
                        ]
                    }
                ],
                max_tokens=2000,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.exception(f"视觉模型调用失败: {e}")
            return f"视觉模型调用失败: {str(e)}"
