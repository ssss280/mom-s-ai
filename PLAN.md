# ChatSight - 桌面聊天识别与智能推荐工具

## 项目概述

ChatSight 是一款桌面端工具，能够实时捕获屏幕上的聊天窗口内容，通过 OCR 识别文字，并借助 AI 大模型生成推荐回复。
当前形态为**网页版**：本地跑一个 Flask 服务，用浏览器作为界面。

## 核心功能

1. **屏幕区域捕获** - 三种截屏方式：全屏立即截、**延时截屏**（给你时间先切到聊天窗口）、
   **直接截取指定窗口**（窗口被浏览器挡住也能截到）；截完在浏览器里拖拽框选识别区域
2. **OCR 文字识别** - 支持本地 Tesseract OCR 和 AI 视觉模型（千问 qwen3-vl / GPT-4o / Claude Vision）
3. **AI 智能回复推荐** - 将识别到的聊天内容发送给 AI 模型，生成多条推荐回复
4. **历史记录（对话 + 识别）** - 使用 SQLite 本地数据库持久化存储：**AI 对话**存成
   「对话记录 + 时间」（保留 user / assistant 角色）、**识别结果**存成「识别记录 + 时间」，
   推荐回复一并保存；**截图文件默认永久保留**，识别结果和历史记录里都能点开查看当时保存的图
5. **多模型支持** - 支持 OpenAI、Anthropic Claude、DeepSeek、通义千问，以及任何 OpenAI 兼容 API（Ollama、LM Studio 等）
6. **联网搜索** - AI 对话页可打开"联网搜索"：先联网检索，再**抓取排名靠前网页的正文**，
   让模型依据正文 + 摘要作答并标注来源编号，回答下方列出可点击的来源链接（**不需要任何搜索 API Key**）

## 技术架构

```
ChatSight/
├── server.py            # Flask 后端 + 全部接口路由（程序入口）
├── version.py           # 版本号 + 更新检测目标仓库（发版时要改这里）
├── update_check.py      # 从 GitHub 检测新版本（后台线程 + 硬超时）
├── local_update.py      # 本地内建更新：从 GitHub 下载文件覆盖到本地（不跳浏览器，防降级）
├── web_search.py        # 免密钥联网搜索（Bing 中文+资讯 RSS 主攻，维基/DDG 补英文，相关性过滤）
├── fair_aliases.py      # 展会别名词典：按「地区 + 种类」推断官方展会名（口语说法搜不到时兜底）
├── eval/                # 联网搜索评测（随机题库 + 判据评分 + 报告，见 eval/REPORT.md）
├── release.py           # 发版助手：按「GitHub 版本 + 1」算出该提交的版本号
├── static/              # 网页前端（黑白极简风格）
│   ├── index.html       # 页面结构
│   ├── app.js           # 前端逻辑（对话、识别、框选、历史、设置）
│   └── style.css        # 样式
├── paths.py             # 运行目录解析（data/、error/、config.json 等）
├── capture.py           # 全屏截图 + 截图目录清理
├── win_capture.py       # 窗口枚举与单窗口截图（ctypes + PrintWindow，被遮挡也能截）
├── ocr.py               # OCR 文字识别引擎
├── models.py            # AI 模型 API 客户端
├── storage.py           # SQLite 数据存储
├── logger.py            # 日志与错误落盘
├── config.json          # 用户配置（含 API Key，已被 .gitignore 排除）
├── config.example.json  # 配置模板
├── requirements.txt     # Python 依赖
├── PLAN.md              # 本计划文件（不可删除）
├── CHANGELOG.md         # 更新记录（每次改动都必须登记，见下）
├── 启动.bat             # 一键启动（py -3 server.py）
├── 安装.bat             # 新电脑一键装环境（自动装 Python + 依赖，再启动）
├── error/               # 运行时报错日志（自动创建）
└── data/
    ├── chatsight.db     # SQLite 数据库（运行时自动创建）
    ├── app.log          # 滚动日志（5MB × 3）
    └── screenshots/     # 截图目录（默认永久保留，可在设置里限制数量）
```

> 路径统一由 `paths.py` 解析（`APP_DIR` 即源码目录）。目录不可写时会自动回退到系统临时目录，保证程序不会因为"写不了日志"而崩溃。

## 版本管理约定

### 三个概念别混

| 东西 | 作用 | 放什么 |
| --- | --- | --- |
| `version.py` 的 `__version__` | 程序当前版本（唯一真相） | `1.1.0` |
| Git **标签** `v1.1.0` | 不可变快照：这个版本号对应哪次提交 | 打完不再改动 |
| GitHub **Release** | 给人看的发布：更新说明、入口 | 从标签生成，说明取自 CHANGELOG |

### 版本号规则

- **语义化版本**：`主.次.修订`。
- **正式版**：`1.2.0`（无后缀）。
- **测试版**：`1.2.0-beta.1`、`1.2.0-rc.1`（带后缀即预发布）。
- **基准是 git 标签**：`release.py` 取标签里的最高版本推算下一个号。
  **不再用"从远端读到的版本号"**——早期就是这么做的，而那个检测会依次读
  version.py → CHANGELOG → releases → tags，谁先返回用谁，
  tags/releases 落后时就会拿到偏低的号，于是同一个版本号发两次
  （之前 CHANGELOG 出现过两组同名版本，根因就在这）。
- **一次提交/推送 = 一个版本**：未发布的改动合并到同一个版本里登记，不要开出多个号。

### 稳定版 / 测试版怎么区分

- 打标签时用后缀区分；GitHub Release 上把测试版勾成 **pre-release**，
  这样 `/releases/latest` 永远只指向正式版。
- 程序侧：配置项 `update_channel`（默认 `stable`）。
  **稳定通道不提示预发布版**——实测 `is_newer("1.7.0-beta.1", "1.6.0")` 为 True，
  没有这道闸的话，一发测试版所有普通用户都会被提示更新。
  想尝鲜的人在设置里切到 `beta`。

### 发版流程

```powershell
py release.py                     # 看下一个版本号该是多少（以标签为基准）
py release.py --apply             # 只写进 version.py
py release.py --release           # 一键：提交 → 打标签 → 推送 → 建 GitHub Release
py release.py --release --beta    # 发测试版（Release 勾 pre-release）
```

建 Release 需要 `GITHUB_TOKEN` 环境变量（`repo` 权限）；没设也能打标签推送，
只是会跳过建 Release 并提示网页链接。

### 分支模型

| 分支 | 用途 | 规则 |
| --- | --- | --- |
| `main` | **始终是可发布的稳定版** | 只接受从 `release/*` 合回来的代码 |
| `dev` | 日常开发 | 平时都推这里 |
| `release/<版本>` | 准备发某个版本时从 `dev` 切出 | 只修 bug；发完合回 `main` 与 `dev` |
| `feature/<名字>` | 单个功能（可选） | 做完合回 `dev` |

```powershell
git switch dev                          # 日常开发
git switch -c release/1.2 dev           # 准备发 1.2.0
py release.py --release                 # 发版（打 tag + 建 Release）
git switch main && git merge release/1.2
git switch dev && git merge release/1.2
```

### 记录要求

- **任何代码或文档改动，都必须在 `CHANGELOG.md` 里登记一条**，没有例外。
- 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
  分类使用 `新增 / 修复 / 变更 / 说明`。
- 校验：`CHANGELOG.md` 顶部版本 == `version.py` 的 `__version__`，不一致算 FAIL。

## 技术选型

| 模块         | 技术方案                      | 说明                                       |
| ------------ | ----------------------------- | ------------------------------------------ |
| 后端服务     | `Flask`                       | 本地 127.0.0.1:5050，线程化运行            |
| 屏幕截图     | `mss` + `Pillow`              | 服务端高性能跨平台截屏                     |
| 窗口截图     | `ctypes` + `PrintWindow`      | 系统 API，窗口被遮挡也能截到，零额外依赖   |
| 区域选择     | 浏览器原生拖拽                | 前端在截图上框选，按缩放比例换算坐标       |
| 本地 OCR     | `pytesseract`                 | 开源免费，需安装 Tesseract-OCR             |
| AI 视觉 OCR  | OpenAI Vision / Claude Vision | 高精度，适合复杂排版                       |
| AI 文本模型  | `openai` Python SDK           | 兼容 OpenAI / Azure / DeepSeek / Ollama 等 |
| 数据存储     | `sqlite3`（标准库）           | 零配置，本地持久化                         |
| 前端         | 原生 HTML/CSS/JS              | 无构建步骤，无框架依赖                     |

## 支持的 AI 模型

### 文本回复模型
- **OpenAI**: GPT-4o, GPT-4o-mini, GPT-4-turbo, GPT-3.5-turbo
- **Anthropic**: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku
- **DeepSeek**: DeepSeek-Chat, DeepSeek-Reasoner
- **通义千问**: qwen-turbo, qwen-plus, qwen-max, qwen3-max
- **本地模型**: 通过 Ollama / LM Studio 等提供的 OpenAI 兼容接口

### 视觉识别模型（可选 OCR）
- **通义千问**: qwen3-vl-plus（默认）、qwen3-vl-flash、qwen-vl-max、qwen-vl-plus、qwen-vl-ocr-latest
- **OpenAI**: GPT-4o Vision
- **Anthropic**: Claude 3.5 Sonnet Vision

## 模块设计

### server.py - Flask 后端
- `load_config()` / `save_config()`：读写 `config.json`
- `run_ocr(image, image_path, session_id)`：OCR 并入库，`session_id` 为 0 时新建**识别**会话
- `/api/chat` 的落库逻辑：每次问答后存「最后一条用户消息 + 本次回复」（`session_id` 为 0 时新建
  **对话**会话），并把 `session_id` 返回给前端续接。只存这两条 —— 前端每次都要把完整历史发给模型，
  全量入库会把同一句话反复记账；落库失败只记日志，不能把已经拿到的回复丢掉
- `parse_port(argv)`：解析 `--port`，缺失或非法时回退 5050
- `port_in_use(port)`：启动前探测端口；**被占用时直接报错退出**，不再允许第二个实例悄悄绑上同一端口
- `MAX_CHAT_MESSAGES`：对话只发送最近 30 条，防止上下文无限增长

接口一览：

| 方法   | 路径                              | 说明                             |
| ------ | --------------------------------- | -------------------------------- |
| GET    | `/`                               | 网页首页                         |
| GET    | `/screenshots/<name>`             | 读取截图文件                     |
| GET    | `/api/config`                     | 读取配置 + 各提供商的默认模型列表 + 当前版本号 |
| POST   | `/api/config`                     | 保存配置                         |
| GET    | `/api/update`                     | 检测 GitHub 上是否有新版本（立即返回，后台联网） |
| POST   | `/api/models`                     | 用表单里的临时配置拉取模型列表   |
| POST   | `/api/chat`                       | AI 对话（`search: true` 时先联网搜索，返回 `sources` 与 `search` 元信息；返回 `session_id`，并把这一问一答存进对话记录） |
| POST   | `/api/ocr/upload`                 | 上传图片识别，返回 `image_url`（保存的图） |
| POST   | `/api/capture`                    | 服务端全屏截图，返回文件名与尺寸 |
| GET    | `/api/windows`                    | 列出可截取的顶层窗口（z 序，最上层在前） |
| POST   | `/api/capture/window`             | 截取指定窗口（被遮挡也能截到）   |
| POST   | `/api/ocr/region`                 | 按框选区域裁剪并识别，返回 `image_url`（裁剪图）与 `source_url`（原图） |
| POST   | `/api/suggestions`                | 生成推荐回复                     |
| POST   | `/api/suggestions/<id>/copied`    | 标记推荐回复已被复制             |
| GET    | `/api/sessions`                   | 历史记录列表（每条带 `type`：`chat` / `recognition`） |
| GET    | `/api/sessions/<id>`              | 单条记录（`session` 含 `type`）+ 消息与推荐（消息带 `role`、`image_url` / `image_exists`） |
| DELETE | `/api/sessions/<id>`              | 删除记录及其消息、推荐           |

### static/ - 网页前端
- `app.js` 里的 `state` 保存当前页、配置、两类会话 id（`sessionId` = 识别、`chatSessionId` = 对话，
  分别落在 localStorage）、待发送对话与截图信息；`loadSession()` 按记录的 `type` 分流 —— 对话记录
  回填到「AI 对话」页（并接上 `state.chat` 可继续对话），识别记录回「识别」页。
  刷新页面时 `restoreChatSession()` 会把上次那段对话接回来，记录被删掉则重置为新对话
- 截屏流程：`POST /api/capture` → 弹层里拖拽框选（坐标按 `截图宽度 / 显示宽度` 缩放）→ `POST /api/ocr/region`
- **三种截屏入口**：「截屏识别」支持选择延时（立即 / 3 秒 / 5 秒，倒计时期间可以切到聊天窗口）；
  「截取窗口」弹出 `GET /api/windows` 的窗口列表，点一行调 `POST /api/capture/window`。
  两种方式返回的结构一致（`name` / `url` / `width` / `height`），共用同一个框选弹层
- `[hidden]` 相关：样式表里有 `[hidden] { display: none !important; }`，否则 `display:flex` 之类的作者样式会盖掉 `hidden` 属性
- **查看截图**：识别结果下方有一条缩略图带（原图 / 裁剪图），加载历史会话时显示该会话保存过的截图；
  点击缩略图打开大图弹层（`#image-modal`），可"在新标签打开"或另存
- **左下角版本标识**（`#version-badge`，位于状态栏最左侧）：页面一打开就显示版本号，
  同时异步调 `/api/update`；检测到新版本时在版本号后面挂一个琥珀色「可更新」标记，点击跳转 GitHub；
  已是最新 / 连不上 GitHub / 还在检测时都只显示版本号（失败原因放在鼠标悬停提示里）。
  注意 `setStatus()` 只写 `#status-text`，不能整块覆盖状态栏，否则会把版本标识冲掉
- **联网搜索开关**（`#chat-search`，输入框下方）：勾选后发送会带上 `search: true`，
  回答里的 `[1] [2]` 编号对应正文下方 `.msg-sources` 里列出的来源；每条来源展示
  `序号 + 网站域名（`.src-site`）+ 文章标题`，点击用新标签打开原文，悬停显示完整 URL。
  开关状态记在 localStorage。「读取网页正文」在设置页（`#cfg-read-pages`），默认开。
  注意 `appendMsg()` 必须先设 `textContent` 再 append 来源节点，顺序反了会被 `textContent` 清掉

### capture.py - 屏幕截图模块
- `capture_full_screen()`：全屏截图（含多显示器），返回 PIL Image
- `capture_region(region)`：截取指定区域
- `prune_screenshots(directory, keep)`：按配置只保留最近 `keep` 张截图；
  **`keep <= 0`（默认）表示永久保留，一张都不删**

### win_capture.py - 窗口截图模块（Windows）
- `list_windows()`：按 z 序枚举可见顶层窗口，过滤工具窗口与 UWP「幽灵窗口」，返回 id / 标题 / 尺寸 / 是否最小化
- `capture_window(hwnd)`：用 `PrintWindow` 把窗口自己画到内存位图再取出像素，**窗口被别的窗口挡住也能截到**；
  失败（窗口已关闭、已最小化、程序不支持后台渲染）时抛 `RuntimeError`，后端转成可读的 400 提示
- 句柄相关的 API 全部显式声明了 `argtypes` / `restype`：ctypes 默认按 `c_int` 处理句柄，64 位下会截断

### version.py + update_check.py - 版本与更新检测
- `version.py`：`__version__`（唯一版本来源）、`UPDATE_REPO`、`UPDATE_BRANCH`
- `update_check.status()`：立即返回已知结果，必要时在**后台线程**里联网检测（页面永不被网络拖住）
- `update_check.check(force)`：同步检测（脚本 / 测试用）
- 检测顺序：`contents/version.py(API)` → `contents/CHANGELOG.md(API)` → `raw version.py`
  → `raw CHANGELOG.md` → `releases/latest` → `tags`
  先走 `api.github.com` 是因为实测它 0.6 秒就返回，而 `raw.githubusercontent.com`
  会"慢慢吐数据"拖到 16 秒
- **硬时间预算**：`urllib` 的 `timeout` 只管单次 socket 读，慢速传输时会失效，
  所以每个地址都放在子线程里 `join(剩余预算)`；整轮上限 `TOTAL_BUDGET`（8 秒）
- 缓存：成功 10 分钟、失败 5 分钟；任何失败都返回 `has_update=False` + 原因，不抛异常、不报 500
- 比较版本号按数字段（`1.10.0 > 1.9.9`），CHANGELOG 只认 `## [x.y.z]` 标题行，
  避免把正文里 Keep a Changelog 的链接版本号当成项目版本

### local_update.py - 本地内建更新（下载覆盖，不跳浏览器）
- 入口：界面点「可更新」→ `POST /api/update/apply`；也可 `GET /api/update/local` 先预演
- **下载通道按实测选**：`raw.githubusercontent.com` 在本机**直接超时**（10 秒读不到数据）→ 不用它；
  优先 `codeload` 整包 zip（一次拿全），失败退到 `api.github.com` 逐文件下载
- **只覆盖仓库里真实存在的文件**，本地多出来的文件一律保留不删
- **不动用户数据**：`config.json`（含 API Key）、`data/`、`error/`、`.git/`、`.bld/`、`build/` 全跳过
- **覆盖前自动备份**到 `data/update_backup/<时间戳>/`；写盘用临时文件 + `os.replace` 原子替换
- **拒绝降级**：远端版本比本地旧时直接拦下（返回 `blocked=downgrade`，接口回 409），
  除非显式 `force=True`。理由：实测远端曾落后本地（`web_search.py` 本地 55KB / 远端 24KB），
  无条件覆盖会静默把新代码冲掉，而日志只会写"更新成功"
- 更新完提示"请重启程序"，**不自动重启**

### 配置校验（server.py 的 sanitize_config）
- 设置页允许用户随便填，所以 `ocr_method` / `reply_style` / `reply_count` / `screenshot_keep`
  **在写入和启动加载时都会收敛**到合法范围（枚举值白名单 + 数值夹取）
- 实测不收敛的后果：`reply_count="abc"` → 用户看到「API 调用失败: slice indices...」；
  `999` → 真的生成 146 条推荐回复（白烧 token）；`-1` → `[:-1]` 静默少给一条

### web_search.py - 免密钥联网搜索
- `search(query, count)`：返回 `{"query", "engine", "results": [{"title","url","snippet","engine"}], "error"}`，
  任何失败都写进 `error`，不抛异常
- `build_context(results, pages=None)`：把结果拼成给模型看的编号上下文
  （`[1] 标题 / 来源网站 / URL / 摘要 / 正文节选`）
- **多源并行查、合并去重后按相关性排序**（引擎清单与分批见下）；任何一个源挂了都不影响整体
  - 早期版本是"谁先出结果用谁"，被真实案例打脸：搜「2026香港秋季照明展」时 Bing 返回一堆
    「2026 年日历/放假安排」，它"有结果"就把搜到了展会时间的搜狗结果挤掉了
  - 本机实测（2026-09）：DuckDuckGo **时通时不通**（不通时直连超时）；搜狗、百度**很容易触发安全验证**
    （连续十几次查询就被拦，页面变成「百度安全验证」）；360 返回「访问异常页面」；
    最稳的是 `cn.bing.com`（带 `mkt`）与 Bing 资讯 RSS
- **相关性过滤分两档**（打分前先用 `term_text()` 剔掉问句成分：什么时候/是什么/帮我查一下/呢/的…）：
  - 不剔的话「香港灯具展是什么时候」9 个 bigram 里有 4 个是噪声，真结果会被压到阈值以下
    （实测真结果 0.29 → 剔除后 **1.00**，日历垃圾仍 0.00）
  - `≥ MIN_RELEVANCE`（0.28）= 可信结果，正常喂给模型
  - `FALLBACK_MIN`（0.15）~ 0.28 = **低相关参考**：仍交给模型，但注入 `SEARCH_LOW_RELEVANCE_PROMPT`
    要求它声明"没检索到很匹配的资料、仅供参考"——模型判语义比 bigram 打分靠谱，也好过直接说"没查到"
  - 低于 0.15 通常丢弃；但**一条都没到 0.15 时**，会挑分数最高的一两条同样按"低相关参考"交给模型
    （实测这样比直接回"没查到"有用）
- **追问要结合上下文**（`is_follow_up()` / `build_query()`）：「2026年的呢」「那地点呢」这类句子自己没检索价值，
  必须拼上上一句（实测日志里出现过直接拿「2026年的呢」去搜）。只有年份/以"呢"结尾/以"那"开头才算追问，
  `北京天气` 这种短查询不会被误拼
- **评测体系（`eval/`）**：改搜索之前先在 `eval/` 上量一遍。25 道随机题库带客观判据，
  `harness.py` 抽题→跑真实搜索→评分→落盘，`compare.py` 对比前后，`check_engines.py` 巡检引擎健康。
  结论见 `eval/REPORT.md`（基线 43.1 → 优化后 85~95，空结果率 60% → 0%）
- **搜索引擎分 4 批打**（`ENGINE_TIERS`，前一批不够数才升级）：
  **顺序不是猜的，由 `eval/engine_eval.py` 顺序实测决定**（看每个源"有贡献的题数 / 被拦次数"）；
  批次只放 2 个源是有意的——并行猛打会把源打爆（并行打 8 个源时 so360 被拦 23/25，顺序时 25/25 全通）。
  当前顺序：`so360`+`bing_cn` → `bing_web`+`bing_news` → `duckduckgo`+`wikipedia` → `sogou`+`baidu`。
  **纯英文查询换一套顺序**（`_ENGLISH_TIERS`）：实测 `cn.bing` 对英文技术问题不看查询词
- **查询记录（`data/search_queries.jsonl`）**：每次搜索落一条，写明用了哪个查询词、打了哪些源、
  每个源什么状态、留下/丢了什么。查看方式：界面「搜索记录」按钮、
  `GET /api/search/log`、`py -3 eval/show_queries.py --detail`。排查"搜出来为什么不对"先看它
- **别名词典兜底（`fair_aliases.py`）**：口语说法（「香港灯展」「香港文具展」）常常一条都搜不到，
  按「地区 + 种类」推断官方名（→「香港国际秋季灯饰展」）再搜一次，并要求模型
  **先说明"没找到关于「用户原话」的内容"、再说"你可能想搜索的是官方名"**
- **诱饵页防御**：不带 `mkt` 的 `www.bing.com` 会给爬虫返回一整页**结构正常但完全跑题**的结果
  （搜灯饰展返回 Lady Gaga），所以主力入口必须是 `cn.bing.com` + `mkt=zh-CN`，
  且任何结果都要过相关性阈值——"解析成功"不等于"搜对了"
- **反爬识别与批次冷却**：没有结果 + 页面异常短 + 出现"安全验证/访问异常"字样 → 判定被拦
  （`EngineBlocked`）。冷却按**批次**算，而且只活在**单次 `search()` 内**（局部状态），
  不跨查询累积——旧版按引擎冷却 5 分钟，一次搜歪就让后面几分钟全部没引擎可用
- **传输层失败短冷却**：HTTP 异常（超时/连不上）会给该引擎记 30 秒短冷却（`_engine_down_until`），
  只对异常生效、"返回 0 条"不触发（实测 DuckDuckGo 不通时整轮被调 10 次全超时）
- **引擎健康统计**（`engine_stats()` / `eval/check_engines.py`）：记录每个源的成功/空手/被拦/失败/耗时，
  "哪个源还能用"应该是量出来的，不是猜出来的
- **跳转链接还原**（`_resolve_redirect`）：Bing 是 `/ck/a?...&u=a1<base64>`（解 base64）；
  360、百度的 `/link?` 必须带 Referer 才跳、返回一个几百字节的 JS 跳转页，真实地址就在页面里，抠出来即可。
  还原后才能按真实域名过滤掉图片/视频垂直页（`MEDIA_HOSTS`），也才能让模型看到域名
- **来源标明网站**（`site_of()`）：结果里带上 `site` 字段（去掉 `www.` 的域名），
  界面按 `[序号] 网站域名 标题` 展示——用户要能一眼看出"这条信息来自哪个网站"；
  喂给模型的上下文里也加一行 `来源网站: xxx.com`
- **查询词清洗与改写**（`clean_query` / `query_variants`）——这条是被"和浏览器搜出来的结果不一样"逼出来的：
  - 聊天里的一句话先砍成第一个分句：`2026香港秋季照明展，什么时候在哪办？` → `2026香港秋季照明展`（问句成分只会稀释关键词）
  - 首轮没有相关结果时**自动换查询词重试**（最多 3 个候选、共用 `TOTAL_BUDGET`）：
    原样 → 去掉 4 位年份 → 再去掉"帮我查一下/怎么样"这类成分
  - 实测依据：搜 `2026香港秋季照明展` 时，**Bing 把 2026 当实体、返回全年日历**；
    去掉年份后 Bing 第一条就变成「展会概览 | 香港贸发局香港国际秋季灯饰展 - HKTDC」；
    而 `2026香港秋季照明展 时间 地点` 同样是日历垃圾。
    加 `mkt=zh-CN`、完整浏览器请求头、先访问首页拿 Cookie、换 `cn.bing.com` **四种方式结果完全一样**，
    所以问题不在请求方式，而在查询词
  - 结果里如实记录 `query`（清洗后的）与 `query_used`（实际生效的），界面会显示"改用「X」"
- **不需要任何搜索 API Key**；结果按 query 缓存 5 分钟
- **抓网页正文**（`fetch_page_text` / `fetch_pages`，`extract_text` 纯正则不用 bs4）：
  摘要常常只是导航文字（实测搜天气时摘要全是"XX天气网为您提供…"），所以搜完后**并行抓前 3 篇的正文**，
  连同摘要一起喂给模型。实测这一招让答案里出现了摘要里没有的细节（展览面积、门票价、完整场馆地址）。
  - 每篇最多取 `PAGE_MAX_CHARS`（2000 字符）、单页最多下载 `PAGE_MAX_BYTES`（800KB）、
    单页超时 `PAGE_TIMEOUT`（5 秒）、总共 `PAGES_BUDGET`（6 秒）——慢了就放弃，退回用摘要
  - 只抓真实地址：搜索引擎的 `/link?` 跳转链接抓不到东西，会白占名额，所以先统一还原（见下）
  - 抽出来少于 `MIN_PAGE_CHARS`（100 字符）就当没抓到：反爬页/纯 JS 页只有寥寥几个字，
    实测出现过"抓正文：1/2 篇成功，共 4 字符"，等于把垃圾当正文喂给模型
  - 非 HTML（PDF/图片）直接跳过；用设置里的「读取网页正文」开关可整体关掉（配置 `search_read_pages`，默认开）
- **跳转链接统一还原**（`_resolve_results`）：在 `search()` 收尾时把结果里所有搜索引擎跳转链接
  一次性并行还原，这样来源显示的是真实域名，抓正文也不会浪费名额
- 对话接口把搜索结果的编号喂给模型并要求用 `[1] [2]` 标注引用；
  **一条都没搜到时也必须注入"没查到"提示**，否则模型会凭记忆自信作答（实测它会把展会地点说错）
- 若文本模型本身走阿里云百炼（DashScope），则改用**模型原生联网搜索**
  （请求里带 `extra_body={"enable_search": True}`），不再自己抓网页

### ocr.py - OCR 识别模块
- `ocr_tesseract(image, lang)`：使用 Tesseract 进行本地 OCR
- `ocr_vision_model(image, client, model)`：使用 AI 视觉模型识别文字
- `extract_chat_text(image, method, client, vision_model, lang)`：统一入口，`auto` 模式先试视觉模型再退回 Tesseract

### models.py - AI 模型客户端
- `ChatSightModel` 类：封装文本客户端与视觉客户端，两者可分别配置
- `get_reply_suggestions(chat_text, count, style)`：获取推荐回复
- `fetch_models_from_api(vision=False)`：拉取模型列表
- `analyze_chat_with_vision(image_base64)`：视觉模型识别
- Base URL 留空时用提供商的默认地址；`provider=custom` 且留空则回退到 OpenAI 默认地址并写告警

### storage.py - 数据存储模块
- `ChatStorage` 类：管理 SQLite 数据库，每次操作独立连接
- 表结构：sessions（记录，`session_type` 区分 `chat` / `recognition`）、messages（消息，
  `role` 区分 `user` / `assistant`，识别消息的 `role` 为空）、suggestions（推荐回复）
- `_migrate()`：启动时给老库补 `sessions.session_type` / `messages.role` 两列
  （SQLite 没有 `ADD COLUMN IF NOT EXISTS`，只能先 `PRAGMA table_info` 查缺再 `ALTER TABLE`），
  并把旧标题「会话 时间」就地改成「识别记录 时间」
- `create_session(title=None, session_type="recognition")`：不传标题时按类型生成
  「对话记录 时间」/「识别记录 时间」
- `save_message()`, `save_suggestions()`, `mark_suggestion_copied()`
- `get_sessions()`, `get_session()`, `get_session_messages()`, `search_messages()`, `delete_session()`

### logger.py - 日志模块
- `setup_logging()`：安装滚动文件日志 + 控制台日志 + `error/` 错误日志（幂等，重复调用无副作用）
- `write_crash_log(title, detail)`：致命错误兜底落盘，绝不抛异常

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
  "reply_count": 3,
  "reply_style": "friendly",
  "screenshot_keep": 0,
  "search_read_pages": 1
}
```

> 文本模型和视觉 OCR 可以使用**不同的服务商**（例如文本用 DeepSeek、OCR 用千问）。
> `vision_provider` / `vision_api_key` / `vision_base_url` 留空时自动回退到上面那套文本配置。
> `ocr_method`：`auto`（先视觉后本地）/ `vision`（只用视觉）/ `tesseract`（只用本地）。
> `screenshot_keep`：截图保留数量，**0 = 永久保留（默认）**，填正数则只保留最近这么多张。
> `search_read_pages`：联网搜索时是否抓网页正文，**1 = 开（默认）**，0 = 只用搜索摘要（更快）。

## 使用流程

1. 启动程序（新电脑双击 `安装.bat`，已装 Python 的双击 `启动.bat`），浏览器打开 http://127.0.0.1:5050
2. 在「设置」里配置 API Key、文本模型、视觉模型，保存
3. 在「识别」页选择截屏方式：
   - **截取窗口**（推荐）：点开后选中聊天窗口，浏览器挡着也能截到
   - **截屏识别 + 延时**：选 3 秒 / 5 秒，点按钮后切到聊天窗口，倒计时结束自动截全屏
   - **截屏识别 + 立即**：直接截当前屏幕
   - 或者点「上传图片」直接上传一张截图
4. 在弹出的截图里拖拽框选聊天区域
5. 程序自动 OCR 识别文字内容（结果可编辑、可复制、可导出）；识别结果**下方会出现原图和裁剪图的缩略图，点开可看大图**
6. 点"生成推荐回复"，AI 根据聊天上下文生成多条回复，一键复制
7. 所有识别结果和推荐自动保存成「识别记录 + 时间」，截图文件默认永久保留在 `data/screenshots/`，
   左侧历史记录可随时回看（含当时的截图）、删除
8. 「AI 对话」页可直接和文本模型聊天，**每轮问答也会自动存成「对话记录 + 时间」**，
   点左侧历史里的对话记录可以回放，并接着往下聊；勾选 **联网搜索** 后，回答会先检索网页、
   带编号引用，并在下方列出可点击来源

## 运行方式

### 新电脑（还没装 Python）
双击 **`安装.bat`**，它会自动：

1. 找 Python（要求 3.9+，会跳过微软商店那个"假 python"别名）；
2. 没找到就下载官方安装包**静默安装**（`InstallAllUsers=0`，**只为当前用户，不需要管理员权限**；
   官方站点不通时自动改用华为云镜像）；
3. 用 pip 安装依赖（默认源失败自动换清华镜像），装完还会 import 一遍确认；
4. 启动程序并打开浏览器。

- 只想装环境、先不启动：`安装.bat --no-start`
- 只想装 Python 本身，直接用一行命令（远程 / 脚本场景）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -useb https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe -OutFile $env:TEMP\py.exe; Start-Process $env:TEMP\py.exe -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_pip=1','Include_launcher=1' -Wait"
```

> 国内网络建议把 URL 换成镜像：
> `https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe`

> `安装.bat` 的三条硬性约束（改它时必须遵守，都是踩过的坑）：
> 1. **内容保持纯 ASCII**：中文 + cmd 代码页切换会让批处理读错位（1.1.0 的 `启动.bat` 就是这么坏的）；
> 2. **必须 CRLF 换行**：LF-only 的批处理会让 `goto` / 标签解析出错；
> 3. **块内引号里不能出现 `)`**，且解释器路径与参数要分开存
>    （`"%PY%"` 里塞 `py -3` 会被 cmd 当成一个不存在的程序名）。
> 用 `py .bld\fix_bat.py` 可以对这三条做体检。

### 已经装过 Python
双击 `启动.bat`（等价于 `py -3 server.py`）。

```bash
pip install -r requirements.txt
py server.py                          # 自动打开浏览器
py server.py --no-browser --port 5050 # 指定端口、不打开浏览器
```

> 如果提示"端口 5050 上已经有一个服务在监听"，说明**上一次的程序窗口没有关掉**。
> Windows 允许两个进程绑同一个端口，此时新实例看起来启动成功，但请求仍会落到旧实例上
> （旧实例没有新加的路由，表现为莫名的 404）。请先关掉旧窗口，或换端口启动。
> 排查：`netstat -ano | findstr :5050`，如果有多个 PID 处于 LISTENING 就是这种情况。

> 注意：使用本地 Tesseract OCR 需要额外安装 Tesseract-OCR 软件。
> 使用 AI 视觉模型则无需安装 Tesseract。
