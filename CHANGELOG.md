# 更新日志

本文件记录 ChatSight 的所有重要变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-09-19

本次集中修复了打包运行、日志落盘与截屏识别链路上的多个阻断性问题，并新增了视觉 OCR 的独立配置能力。

### 修复

- **打包后一启动就崩溃**：cx_Freeze 冻结后 `__file__` 指向 `lib\library.zip\logger.pyc`，导致 `data/`、`error/` 目录被算到压缩包内部，`os.makedirs()` 抛
  `FileNotFoundError [WinError 3]`。由于崩溃发生在日志 handler 安装之前，表现为"弹窗报错但一个日志都没有"。
  新增 `paths.py` 统一解析运行目录（`CHATSIGHT_HOME` 环境变量 → exe 所在目录 → 源码目录），所有模块改用它。
- **错误日志不落盘**：`setup_logging()` 加异常保护并返回实际日志路径；新增 `write_crash_log()` 作为最后兜底；
  `main.py` 补上 `threading.excepthook` 与入口级保护；`fatal_error()` 弹窗现在显示真实日志路径。
- **界面回调异常被吞掉**：tkinter 按钮等回调抛出的异常不走 `sys.excepthook`，此前只在 stderr 闪一下。
  现已通过 `report_callback_exception` 写入 `error/` 并提示用户。
- **10 处 `lambda` 闭包引用 `except ... as e` 的 `e`**：Python 会在 `except` 块结束时删除该变量，
  延迟执行的 lambda 抛 `NameError: cannot access free variable 'e'`，把真正的错误信息吃掉了。
- **截屏报 `TclError: image "pyimage1" doesn't exist`**：`ImageTk.PhotoImage` 未指定 `master`，
  图片被注册到主界面的 Tcl 解释器，而选区 canvas 属于另一个解释器。现已显式传入 `master`。
- **点截图后程序"消失"（其实没退出）**：`select_region()` 用第二个 `Tk()` 开嵌套 `mainloop()`，
  而 Tcl 的 `Tk_GetNumMainWindows()` 统计的是**整个线程**的主窗口数，主界面窗口一直存在，
  嵌套 mainloop 永不返回，后面恢复主窗口的代码永远执行不到。改为使用主窗口同一解释器的 `Toplevel` 并不再阻塞调用方。
- **框选区域不准确、后面的字识别不出来**：原先先恢复主窗口、再启动抓屏线程，导致自家窗口弹回置顶后盖住框选区域，
  只有窗口左边缘外的一条缝能识别到。现改为从覆盖层显示的那张全屏截图裁剪（抓屏在覆盖层出现前完成），
  既消除竞态，也做到所见即所得。
- **`启动.bat` 被 cmd 解析错位**：UTF-8 中文文本 + 中途 `chcp 65001` 会让 cmd 读错位，
  导致 `start` 那一行不执行。现已改为纯 ASCII，并通过 `CHATSIGHT_HOME` 让 `data/`、`error/`、`config.json` 落在项目目录。

### 新增

- **文本模型与视觉 OCR 可分别配置**：新增 `vision_provider` / `vision_api_key` / `vision_base_url`，
  留空自动回退到文本配置。设置面板新增"视觉 OCR 独立配置"区块，可让文本走 DeepSeek、OCR 走千问。
- **支持通义千问视觉模型 OCR**（默认 `qwen3-vl-plus`），模型列表补充 `qwen3-vl-flash`、
  `qwen-vl-max`、`qwen-vl-plus`、`qwen-vl-ocr-latest`。
- `build_safe.py`：构建入口，规避 cx_Freeze 在 Python 3.14 上扫描字节码时的随机崩溃
  （`TypeError: unsupported operand type(s) for -: 'range_iterator' and 'int'` / `0xC0000005` 访问违例）。
- `config.example.json`：配置模板（`config.json` 含 API Key，已被 `.gitignore` 排除）。
- `.gitignore`：排除运行期数据、打包产物与本地配置。

### 变更

- OCR 提示词改为强调"完整、逐行、不遗漏最右/最下、不总结不翻译"，`max_tokens` 由 2000 提到 4000，
  并自动去除模型返回的 ``` 代码块包裹。
- `auto` 模式在视觉模型失败时会带上真实失败原因，不再只报"没有可用的 OCR 引擎"。
- `build.bat` 改为纯 ASCII 并调用 `build_safe.py`。
- 数据库路径、截图目录统一由 `paths.py` 解析。

### 说明

- Python 3.14 + cx_Freeze 的字节码扫描崩坏在 `dis._get_cache_size()`（会返回非 int），
  `build_safe.py` 在导入 cx_Freeze 之前对其做了保护；该崩溃具有随机性，构建失败时重试通常即可通过。

## [1.0.0] - 2026-09-19

首个可用版本。

### 新增

- **屏幕区域捕获**：全屏覆盖层拖拽框选（`mss` + `tkinter` Canvas）。
- **OCR 文字识别**：本地 Tesseract 与 AI 视觉模型两种方式，支持 `auto` 自动选择。
- **AI 智能回复推荐**：根据识别到的聊天内容生成多条推荐回复，可一键复制。
- **聊天记录保存**：SQLite 本地持久化会话、消息与推荐回复。
- **多模型支持**：OpenAI、Anthropic、DeepSeek、通义千问、Ollama 及任意 OpenAI 兼容接口。
- **图形界面**：AI 对话、聊天识别、截图 OCR、历史记录、设置五个标签页。
- **日志系统**：`data/app.log` 滚动日志 + `error/` 错误日志目录。
- **打包与启动**：`build.bat` 构建 cx_Freeze 单文件目录版，`启动.bat` 一键运行。
