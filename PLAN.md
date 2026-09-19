# ChatSight - 桌面聊天识别与智能推荐工具

## 项目概述

ChatSight 是一款桌面端工具，能够实时捕获屏幕上的聊天窗口内容，通过 OCR 识别文字，并借助 AI 大模型生成推荐回复。

## 核心功能

1. **屏幕区域捕获** - 用户框选屏幕上的聊天窗口区域
2. **OCR 文字识别** - 支持本地 Tesseract OCR 和 AI 视觉模型（GPT-4o/Claude Vision）
3. **AI 智能回复推荐** - 将识别到的聊天内容发送给 AI 模型，生成多条推荐回复
4. **聊天记录保存** - 使用 SQLite 本地数据库持久化存储所有识别到的对话和推荐
5. **多模型支持** - 支持 OpenAI、Anthropic Claude、以及任何 OpenAI 兼容 API（如 DeepSeek、通义千问、Ollama 本地模型等）

## 技术架构

```
ChatSight/
├── main.py              # 程序入口
├── gui.py               # GUI 界面（tkinter）
├── paths.py             # 运行目录解析（源码/exe 冻结两种模式）
├── capture.py           # 屏幕截图与区域选择
├── ocr.py               # OCR 文字识别引擎
├── models.py            # AI 模型 API 客户端
├── storage.py           # SQLite 数据存储
├── logger.py            # 日志与错误落盘
├── config.json          # 用户配置文件（API Key、模型选择等）
├── requirements.txt     # Python 依赖
├── PLAN.md              # 本计划文件（不可删除）
├── error/               # 运行时报错日志（自动创建）
└── data/
    └── chatsight.db     # SQLite 数据库（运行时自动创建）
```

> 打包说明：cx_Freeze 冻结后 `__file__` 指向 `lib\library.zip` 内部，
> 因此 data/、error/、config.json 的路径一律通过 `paths.py` 解析：
> 优先环境变量 `CHATSIGHT_HOME`，其次 exe 所在目录，最后源码目录。

## 技术选型

| 模块         | 技术方案                          | 说明                                       |
| ------------ | --------------------------------- | ------------------------------------------ |
| 屏幕截图     | `mss` + `Pillow`                  | 高性能跨平台截屏                           |
| 区域选择     | `tkinter` Canvas                  | 原生实现，无额外依赖                       |
| 本地 OCR     | `pytesseract`                     | 开源免费，需安装 Tesseract-OCR             |
| AI 视觉 OCR  | OpenAI Vision / Claude Vision     | 高精度，适合复杂排版                       |
| AI 文本模型  | `openai` Python SDK               | 兼容 OpenAI / Azure / DeepSeek / Ollama 等 |
| 数据存储     | `sqlite3`（标准库）               | 零配置，本地持久化                         |
| GUI          | `tkinter` + `tkinter.ttk`         | Python 内置，无需安装                      |
| 全局热键     | `pynput`                          | 监听键盘事件，快速触发截屏                 |

## 支持的 AI 模型

### 文本回复模型
- **OpenAI**: GPT-4o, GPT-4o-mini, GPT-4-turbo, GPT-3.5-turbo
- **Anthropic**: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku
- **DeepSeek**: DeepSeek-Chat, DeepSeek-Reasoner
- **通义千问**: qwen-turbo, qwen-plus, qwen-max
- **本地模型**: 通过 Ollama / LM Studio 等提供的 OpenAI 兼容接口

### 视觉识别模型（可选 OCR）
- **通义千问**: qwen3-vl-plus（默认）、qwen3-vl-flash、qwen-vl-max、qwen-vl-ocr-latest
- **OpenAI**: GPT-4o Vision
- **Anthropic**: Claude 3.5 Sonnet Vision

## 模块设计

### capture.py - 屏幕截图模块
- `capture_screen()`: 全屏截图，返回 PIL Image
- `select_region()`: 弹出全屏覆盖层，用户拖拽框选区域
- `capture_region(region)`: 截取指定区域

### ocr.py - OCR 识别模块
- `ocr_tesseract(image)`: 使用 Tesseract 进行本地 OCR
- `ocr_vision_model(image, client)`: 使用 AI 视觉模型识别文字
- `extract_chat_text(image, method, client)`: 统一入口，返回结构化聊天内容

### models.py - AI 模型客户端
- `ChatSightModel` 类：封装所有模型调用
- 支持配置多个模型 provider，运行时切换
- `get_reply_suggestions(chat_text)`: 获取推荐回复
- `analyze_with_vision(image_base64)`: 视觉模型分析截图

### storage.py - 数据存储模块
- `ChatStorage` 类：管理 SQLite 数据库
- 表结构：sessions（会话）, messages（消息）, suggestions（推荐回复）
- `save_session()`, `save_message()`, `save_suggestion()`
- `get_history()`, `search_messages()`

### gui.py - 图形界面
- 主窗口：显示识别到的聊天内容 + 推荐回复列表
- 设置面板：配置 API Key、选择模型、OCR 方式
- 截屏按钮 / 全局热键触发
- 历史记录浏览器

### config.json - 配置文件结构
```json
{
  "api_provider": "deepseek",
  "api_key": "",
  "api_base_url": "",
  "text_model": "deepseek-chat",
  "vision_provider": "qwen",
  "vision_api_key": "",
  "vision_base_url": "",
  "vision_model": "qwen3-vl-plus",
  "ocr_method": "auto",
  "language": "zh",
  "hotkey": "ctrl+shift+s",
  "reply_count": 3,
  "reply_style": "friendly"
}
```

> 文本模型和视觉 OCR 可以使用**不同的服务商**（例如文本用 DeepSeek、OCR 用千问）。
> `vision_provider` / `vision_api_key` / `vision_base_url` 留空时自动回退到上面那套文本配置。

## 使用流程

1. 启动程序，配置 API Key 和模型
2. 点击"截屏识别"或按全局热键 Ctrl+Shift+S
3. 在屏幕上框选聊天窗口区域
4. 程序自动 OCR 识别文字内容
5. AI 模型分析聊天上下文，生成推荐回复
6. 用户可点击复制推荐回复，或自行编辑后发送
7. 所有对话和推荐自动保存到本地数据库

## 运行方式

```bash
pip install -r requirements.txt
python main.py
```

> 注意：使用本地 Tesseract OCR 需要额外安装 Tesseract-OCR 软件。
> 使用 AI 视觉模型则无需安装 Tesseract。
