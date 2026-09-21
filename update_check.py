"""从 GitHub 检测是否有新版本。

设计要点（都是实测踩出来的）：

1. **绝不拖慢页面**：`/api/update` 立即返回当前已知结果，联网检测在后台线程里做。
   实测 raw.githubusercontent.com 在国内能"慢慢吐数据"拖到 16 秒，而 api.github.com 只要 0.6 秒，
   所以优先走 API，其次才退回 raw CDN。
2. **硬性时间预算**：`urllib` 的 `timeout` 只约束单次 socket 读，慢速传输时会失效，
   因此每个地址都放在子线程里跑并用 `join(剩余预算)` 卡死；整轮上限 `TOTAL_BUDGET` 秒。
3. **结果缓存**：成功缓存 10 分钟、失败缓存 5 分钟，刷新页面不会反复打 GitHub。
4. **失败不影响任何功能**：拿不到就 `has_update=False` + 原因，界面只显示版本号。

检测顺序：
    API contents/version.py  →  API contents/CHANGELOG.md  →  raw version.py
      →  raw CHANGELOG.md  →  API releases/latest  →  API tags
"""

import base64
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

from version import UPDATE_BRANCH, UPDATE_REPO, __version__

logger = logging.getLogger(__name__)

TIMEOUT = 4           # 单次 HTTP 请求超时（秒，仅作参考，真正的上限由线程 join 控制）
TOTAL_BUDGET = 8      # 整轮检测的总预算（秒）
CACHE_TTL = 600       # 成功结果缓存 10 分钟
FAIL_CACHE_TTL = 300  # 失败结果缓存 5 分钟

_RAW = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}"
_API = f"https://api.github.com/repos/{UPDATE_REPO}"
REPO_URL = f"https://github.com/{UPDATE_REPO}"

_TIMEOUT = object()  # 哨兵：子线程没在预算内跑完

_state = {"at": 0.0, "data": None, "running": False}
_lock = threading.Lock()


# ---------- 版本号处理 ----------

def parse_version(text: str) -> str:
    """从 'v1.2.3' / 'refs/tags/1.2.3' / '## [1.2.3] - 2026-01-01' 里取出纯版本号。"""
    match = re.search(r"(\d+(?:\.\d+)+)", text or "")
    return match.group(1) if match else ""


def version_tuple(text: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新。按数字段比较，所以 1.10.0 > 1.9.9。"""
    a, b = version_tuple(latest), version_tuple(current)
    size = max(len(a), len(b))
    a += (0,) * (size - len(a))
    b += (0,) * (size - len(b))
    return a > b


# 预发布标记：1.7.0-beta.1 / 1.7.0-rc.2 / 2.0.0-alpha
PRERELEASE_RE = re.compile(r"-(alpha|beta|rc|pre|dev)", re.I)


def is_prerelease(version: str) -> bool:
    """这个版本号是不是预发布（测试版）。

    为什么必须能识别：实测 `is_newer("1.7.0-beta.1", "1.6.0")` 返回 True，
    也就是**只要发了测试版，所有稳定版用户都会看到「可更新」并被拽去更新**。
    有了这个判断，稳定通道就能把它过滤掉。
    """
    return bool(PRERELEASE_RE.search(version or ""))


def should_notify(latest: str, current: str, channel: str = "stable") -> bool:
    """按通道判断"该不该提示用户更新"。

    - `stable`（默认）：忽略预发布版。日常用户不该被测试版打扰。
    - `beta`：预发布版也提示，方便自己/愿意尝鲜的人拿到测试版。
    """
    if not is_newer(latest, current):
        return False
    if channel == "beta":
        return True
    return not is_prerelease(latest)


def next_version(remote: str, part: str = "minor") -> str:
    """按「GitHub 上的版本 + 1」算出本次要提交的版本号。

    默认升次版本号（1.2.0 → 1.3.0）；`part="patch"` 升修订号（1.2.0 → 1.2.1），
    `part="major"` 升主版本号（1.2.0 → 2.0.0）。
    """
    numbers = [int(x) for x in re.findall(r"\d+", remote or "")] or [0]
    while len(numbers) < 3:
        numbers.append(0)
    major, minor, patch = numbers[0], numbers[1], numbers[2]
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor + 1}.0"


def changelog_version(text: str) -> str:
    """取 CHANGELOG 里第一个版本标题行的版本号。

    只认 `## [1.2.0]` 这种标题：正文里有 Keep a Changelog 的链接 "1.1.0"，
    全文搜索版本号会把它当成项目版本（实测踩过）。
    """
    match = re.search(r"^##\s*\[?v?(\d+(?:\.\d+)+)\]?", text or "", re.MULTILINE)
    return match.group(1) if match else ""


# ---------- 硬超时执行 ----------

def _run_with_deadline(func, deadline: float):
    """在子线程里执行 func，超过 deadline 就放弃（返回 _TIMEOUT）。

    不能只用 socket 超时：连接建立后如果对端"慢慢吐数据"，每次读都会重置超时，
    实测能拖到 16 秒。子线程是 daemon，被放弃后随进程一起结束。
    """
    box = {}

    def worker():
        try:
            box["value"] = func()
        except Exception as e:  # noqa: BLE001 - 交给主线程重新抛
            box["error"] = e

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(max(0.1, deadline - time.time()))
    if thread.is_alive():
        return _TIMEOUT
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------- HTTP ----------

def _fetch(url: str) -> tuple:
    """返回 (状态码, 文本)。HTTP 错误码照常返回；网络类错误抛出去。"""
    request = urllib.request.Request(url, headers={
        "User-Agent": "ChatSight-UpdateCheck",
        "Accept": "application/vnd.github+json, text/plain, */*",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _need(status_url: str):
    """取文本；404 返回 None（可以试下一个地址），限流/其它错误抛出去。"""
    code, body = _fetch(status_url)
    if code == 200:
        return body
    if code == 404:
        return None
    if code in (403, 429):
        raise RuntimeError(f"GitHub 接口限流（{code}）")
    raise RuntimeError(f"GitHub 返回 {code}")


def _api_file_text(path: str):
    """通过 contents API 读仓库里的文件（走 api.github.com，比 raw CDN 快很多）。"""
    body = _need(f"{_API}/contents/{path}?ref={UPDATE_BRANCH}")
    if body is None:
        return None
    data = json.loads(body)
    content = data.get("content")
    if not content:
        return None
    if data.get("encoding") == "base64":
        return base64.b64decode(content).decode("utf-8", "replace")
    return content


# ---------- 各种检测方式 ----------

def _from_version_api():
    text = _api_file_text("version.py")
    if text is None:
        return None
    match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text)
    version = parse_version(match.group(1)) if match else ""
    return (version, "version.py(API)", f"{REPO_URL}/blob/{UPDATE_BRANCH}/version.py") if version else None


def _from_changelog_api():
    text = _api_file_text("CHANGELOG.md")
    if text is None:
        return None
    version = changelog_version(text)
    return (version, "CHANGELOG.md(API)", f"{REPO_URL}/blob/{UPDATE_BRANCH}/CHANGELOG.md") if version else None


def _from_version_raw():
    body = _need(f"{_RAW}/version.py")
    if body is None:
        return None
    match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', body)
    version = parse_version(match.group(1)) if match else ""
    return (version, "version.py(raw)", f"{REPO_URL}/blob/{UPDATE_BRANCH}/version.py") if version else None


def _from_changelog_raw():
    body = _need(f"{_RAW}/CHANGELOG.md")
    if body is None:
        return None
    version = changelog_version(body)
    return (version, "CHANGELOG.md(raw)", f"{REPO_URL}/blob/{UPDATE_BRANCH}/CHANGELOG.md") if version else None


def _from_releases():
    code, body = _fetch(f"{_API}/releases/latest")
    if code == 200:
        data = json.loads(body)
        version = parse_version(data.get("tag_name", ""))
        if version:
            return version, "release", data.get("html_url") or f"{REPO_URL}/releases"
        return None
    if code in (403, 429):
        raise RuntimeError(f"GitHub 接口限流（{code}）")
    return None


def _from_tags():
    code, body = _fetch(f"{_API}/tags")
    if code == 200:
        tags = json.loads(body)
        if tags:
            version = parse_version(tags[0].get("name", ""))
            if version:
                return version, "tag", f"{REPO_URL}/releases"
        return None
    if code in (403, 429):
        raise RuntimeError(f"GitHub 接口限流（{code}）")
    return None


STRATEGIES = (
    _from_version_api,
    _from_changelog_api,
    _from_version_raw,
    _from_changelog_raw,
    _from_releases,
    _from_tags,
)


def update_channel() -> str:
    """当前更新通道：stable（默认，只提示正式版）/ beta（预发布版也提示）。

    从 config.json 读，避免模块之间互相 import。读不到就按 stable 处理——
    默认更保守：宁可少提示，也不要把测试版推给日常用户。
    """
    try:
        from paths import CONFIG_PATH
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            value = (json.load(fh) or {}).get("update_channel", "stable")
        return "beta" if str(value).strip().lower() == "beta" else "stable"
    except Exception:
        return "stable"


def _detect(deadline: float) -> dict:
    info = {
        "current": __version__,
        "latest": "",
        "has_update": False,
        "url": REPO_URL,
        "source": "",
        "channel": update_channel(),
        "latest_prerelease": False,
        "error": "",
    }
    found = None
    try:
        for strategy in STRATEGIES:
            if time.time() >= deadline:
                break
            result = _run_with_deadline(strategy, deadline)
            if result is _TIMEOUT:
                info["error"] = "更新检测超时（网络太慢）"
                logger.warning("更新检测超时，已放弃")
                return info
            if result:
                found = result
                break
    except Exception as e:
        info["error"] = f"无法连接 GitHub：{e}"
        logger.warning(f"更新检测失败：{e}")
        return info

    if not found:
        info["error"] = "未能从 GitHub 获取版本信息"
        logger.warning("更新检测失败：所有地址都没有版本信息")
        return info

    latest, source, url = found
    channel = info["channel"]
    info.update(
        latest=latest, source=source, url=url,
        latest_prerelease=is_prerelease(latest),
        # 关键：稳定通道**不提示预发布版**，否则一发 beta 所有用户都会被拽去更新
        has_update=should_notify(latest, __version__, channel),
    )
    logger.info(f"更新检测：本地 {__version__} / 远端 {latest}（{source}）"
                f"通道 {channel}{'，预发布' if info['latest_prerelease'] else ''} → "
                f"{'有新版本' if info['has_update'] else '已是最新（或按通道不提示）'}")
    return info


# ---------- 对外接口 ----------

def _is_fresh() -> bool:
    data = _state["data"]
    if not data:
        return False
    ttl = CACHE_TTL if data.get("latest") else FAIL_CACHE_TTL
    return (time.time() - _state["at"]) < ttl


def _run_detection():
    try:
        result = _detect(time.time() + TOTAL_BUDGET)
        _state["at"] = time.time()
        _state["data"] = result
    except Exception as e:  # 后台线程里绝不能抛出去
        logger.warning(f"后台更新检测异常：{e}")
        _state["at"] = time.time()
        _state["data"] = {
            "current": __version__, "latest": "", "has_update": False,
            "url": REPO_URL, "source": "", "error": str(e),
        }
    finally:
        _state["running"] = False


def _start_background():
    with _lock:
        if _state["running"]:
            return
        _state["running"] = True
    threading.Thread(target=_run_detection, name="update-check", daemon=True).start()


def status() -> dict:
    """立即返回已知状态；过期就在后台刷新（绝不阻塞请求线程）。

    `pending=True` 表示这一轮还没有结果，前端可以稍后再问一次。
    """
    if _is_fresh():
        return dict(_state["data"], cached=True, pending=False)

    _start_background()
    if _state["data"]:
        return dict(_state["data"], cached=True, pending=True)
    return {
        "current": __version__, "latest": "", "has_update": False,
        "url": REPO_URL, "source": "", "error": "", "pending": True,
    }


def check(force: bool = False) -> dict:
    """同步检测（脚本 / 测试用），带缓存；任何情况下都返回同结构 dict。"""
    if not force and _is_fresh():
        return dict(_state["data"], cached=True, pending=False)

    result = _detect(time.time() + TOTAL_BUDGET)
    _state["at"] = time.time()
    _state["data"] = result
    return dict(result, cached=False, pending=False)


def reset_cache():
    """测试用：清掉缓存。"""
    _state["at"] = 0.0
    _state["data"] = None
    _state["running"] = False
