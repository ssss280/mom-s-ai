# ChatSight

桌面聊天识别与 AI 智能推荐工具（网页版）：本地跑一个 Flask 服务，用浏览器当界面。

- **截图识别**：全屏截屏 / 延时截屏 / 指定窗口截屏（窗口被遮挡也能截），拖拽框选后 OCR
- **AI 推荐回复**：把识别到的聊天内容交给大模型，生成多条可直接用的回复
- **联网搜索**：AI 对话可开启联网搜索（免密钥），结果会标注来源链接
- **本地内建更新**：发现新版本时点一下，程序自己从 GitHub 下载覆盖，不跳网页
- **历史记录**：对话与识别结果存本地 SQLite，截图默认永久保留
- **多模型支持**：OpenAI / DeepSeek / 通义千问 / Claude / 任意 OpenAI 兼容接口（Ollama、LM Studio 等）

## 快速开始

```powershell
安装.bat        # 新电脑：自动装 Python 与依赖，然后启动
启动.bat        # 已装好环境：直接启动
```

浏览器打开 <http://127.0.0.1:5050>，在「设置」里填 API Key 即可使用。

## ⚠️ 给 AI 助手 / 贡献者

**动手前先读 [AGENTS.md](AGENTS.md)**——那是本项目的强制工作流，其中第一条是：

> **改动版本号前必须先询问用户，得到明确同意后才能改。**
> （`release.py` 缺 `--approved <版本号>` 会直接拒绝执行，这是刻意设计的摩擦。）

其他约定见 [PLAN.md](PLAN.md)（架构、各模块设计取舍、版本管理约定）与
[CHANGELOG.md](CHANGELOG.md)（每次改动都必须登记）。

## 版本与更新

- **稳定版**：形如 `1.1.0`；**测试版**：形如 `1.2.0-beta.1`
  （GitHub 上标记为 pre-release；程序默认 `stable` 通道**不会**提示测试版）
- 全部版本与更新说明：<https://github.com/ssss280/mom-s-ai/releases>
- 查看某个版本的代码快照：<https://github.com/ssss280/mom-s-ai/tags>

## 发版（仅在用户同意版本号后执行）

```powershell
py release.py                              # 只读预览：以 git 标签为基准的下一个版本号
py release.py --apply --approved 1.1.1     # 写版本号
py release.py --release --approved 1.1.1   # 一键：提交 → 打标签 → 推送 → 建 Release
py release.py --release --beta --approved 1.2.0-beta.1   # 发测试版
```

> 建 GitHub Release 需要 `GITHUB_TOKEN` 环境变量（`repo` 权限）；没有则跳过该步并给出网页链接。

## 分支

| 分支 | 用途 |
| --- | --- |
| `main` | 始终是可发布的稳定版 |
| `dev` | 日常开发 |
| `release/<版本>` | 准备发版时的冻结线，只修 bug |

## 自检脚本

`eval/` 下是联网搜索评测；`.bld/` 下是功能自检（未纳入版本控制）：

```powershell
py -3 .bld\test_modules.py    # 模块功能（配置/存储/日志/OCR/搜索/更新）
py -3 .bld\test_api.py        # 接口（需先启动服务）
py -3 .bld\test_channels.py   # 稳定/测试双通道
py -3 .bld\test_ocr.py        # OCR
py -3 eval\harness.py --all --seed 20260101   # 联网搜索评测
```
