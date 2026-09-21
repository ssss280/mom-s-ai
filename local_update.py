"""本地内建更新：从 GitHub 把更新内容**下载到本地并覆盖**，不跳转浏览器。

为什么不跳 GitHub 网页：用户点"可更新"是想让本机代码变成新版，而不是自己去看网页、
再手工下载解压。这里直接把文件取回来写到程序目录。

实测（决定用哪条下载通道）：
- `raw.githubusercontent.com` 在本机**直接超时**（10 秒读不到数据）→ 不能用它逐文件下载；
- `api.github.com` 很快（tree 0.3s、contents 0.6s）→ 用 contents API 逐文件下载可行；
- `codeload.github.com` 的 zip 最快（整包 79KB / 1.7s）→ **优先走它**，一次拿全。

安全约定（自动覆盖别人的工作目录，必须守规矩）：
1. **只覆盖"仓库里存在的文件"**，绝不删除本地多出来的文件——用户自己的脚本不会被清掉；
2. 写盘前**先备份**被覆盖的文件到 `data/update_backup/<时间戳>/`，可以手动回滚；
3. **不动用户数据**：`config.json`（含 API Key）、`data/`、`error/`、`.git/` 一律跳过；
4. 先写临时文件再 `os.replace` 原子替换，避免写一半断电留下半个文件；
5. 更新完告诉用户"需要重启程序"，**不自动重启**（重启由用户决定）。
"""

import io
import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
import zipfile

from version import UPDATE_BRANCH, UPDATE_REPO

logger = logging.getLogger(__name__)

TIMEOUT = 20            # 单次请求超时（zip 整包实测 1.7s，留足余量）
REPO_URL = f"https://github.com/{UPDATE_REPO}"
ZIP_URL = f"https://codeload.github.com/{UPDATE_REPO}/zip/refs/heads/{UPDATE_BRANCH}"
_RAW = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}"
_API = f"https://api.github.com/repos/{UPDATE_REPO}"

# 绝不被更新覆盖的路径（用户数据 / 版本控制 / 运行产物）
PROTECTED_DIRS = {".git", "data", "error", "build", ".bld", "__pycache__", ".vscode", ".idea"}
PROTECTED_FILES = {"config.json"}
# 本地多出来、但仓库里没有的文件：一律保留（不删）
BACKUP_DIR_NAME = "update_backup"


def app_dir() -> str:
    """程序目录（更新写入的目标）。"""
    import paths
    return paths.APP_DIR


def _is_protected(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if any(part in PROTECTED_DIRS for part in parts[:-1]) or parts[0] in PROTECTED_DIRS:
        return True
    return os.path.basename(rel_path) in PROTECTED_FILES


def _fetch(url: str, timeout: int = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "ChatSight-LocalUpdate",
        "Accept": "application/vnd.github+json, application/octet-stream, */*",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ---------- 远端文件清单 ----------

def remote_manifest() -> list:
    """远端仓库里所有文件（相对路径 + 大小）。只用 api.github.com，实测最快最稳。"""
    data = json.loads(_fetch(f"{_API}/git/trees/{UPDATE_BRANCH}?recursive=1").decode("utf-8"))
    files = [{"path": item["path"], "size": item.get("size", 0)}
             for item in data.get("tree", []) if item.get("type") == "blob"]
    if not files:
        raise RuntimeError("远端仓库没有返回任何文件")
    return files


def _local_files() -> set:
    """本地程序目录里现有的文件（相对路径），同样跳过受保护目录。"""
    found = set()
    root = app_dir()
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in PROTECTED_DIRS]
        for name in names:
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if not _is_protected(rel):
                found.add(rel)
    return found


def plan() -> dict:
    """算出这次更新会做什么：新增哪些、覆盖哪些、远端删了哪些（本地保留）。

    只做"预演"不写盘，让用户/界面先看清楚再决定。
    """
    remote = remote_manifest()
    local = _local_files()
    remote_paths = {item["path"] for item in remote}
    protected = {item["path"] for item in remote if _is_protected(item["path"])}

    return {
        "repo": UPDATE_REPO,
        "branch": UPDATE_BRANCH,
        "remote_count": len(remote_paths),
        "local_count": len(local),
        "add": sorted(remote_paths - local),
        "overwrite": sorted(remote_paths & local),
        "keep_only_local": sorted(local - remote_paths),
        "skip_protected": sorted(protected),
        "remote_size": sum(item["size"] for item in remote),
    }


# ---------- 下载 ----------

def download_zip() -> bytes:
    """下载整包 zip（最快通道：实测 79KB/1.7s）。"""
    return _fetch(ZIP_URL, timeout=TIMEOUT * 3)


def _extract(zip_bytes: bytes) -> dict:
    """把 zip 解成 {相对路径: 内容}，并去掉 GitHub 自动加的那层根目录。"""
    files = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            parts = name.split("/", 1)
            if len(parts) != 2:
                continue                    # 根目录条目本身
            rel = parts[1]
            if _is_protected(rel):
                logger.info(f"更新跳过受保护文件：{rel}")
                continue
            files[rel] = archive.read(name)
    if not files:
        raise RuntimeError("下载到的压缩包是空的")
    return files


def _download_each(manifest: list) -> dict:
    """退路：整包下不动时，用 contents API 逐文件下载（实测 0.6s/个）。"""
    files = {}
    for item in manifest:
        rel = item["path"]
        if _is_protected(rel):
            continue
        data = json.loads(_fetch(f"{_API}/contents/{rel}?ref={UPDATE_BRANCH}").decode("utf-8"))
        content = data.get("content") or ""
        if data.get("encoding") == "base64":
            import base64
            files[rel] = base64.b64decode(content)
        else:
            files[rel] = content.encode("utf-8")
    return files


def fetch_remote_files(manifest: list = None) -> tuple:
    """取回远端所有需要写入的文件，返回 (文件字典, 用了哪条通道)。

    优先 zip：一次拿全，实测 79KB/1.5s。zip 里天然只含"仓库真实存在的文件"，
    所以**计划的依据就是解包结果本身**，不会出现"计划里列了、实际下不到"的错位
    （用 contents API 逐文件下载时才会需要 manifest）。
    """
    try:
        return _extract(download_zip()), "zip"
    except Exception as e:
        logger.warning(f"整包下载失败（{type(e).__name__}: {e}），改用逐文件下载")
        return _download_each(manifest or remote_manifest()), "contents-api"


def _plan_from(files: dict) -> dict:
    """基于**真实下载到的文件**算计划（而不是另外去问一次远端清单）。

    之前 plan() 走 api.github.com 的 tree、下载走 codeload zip，两者是两次独立请求；
    如果远端在这两次之间变了（或退路通道只拿到部分文件），就会出现
    "计划里 20 个新增、实际一个都没写进去"这种自相矛盾的报告。
    """
    local = _local_files()
    remote_paths = set(files)
    return {
        "add": sorted(remote_paths - local),
        "overwrite": sorted(remote_paths & local),
        "keep_only_local": sorted(local - remote_paths),
    }


# ---------- 写盘 ----------

def _backup(root: str, rel_paths: list) -> str:
    """把即将被覆盖的文件复制一份到 data/update_backup/<时间戳>/。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = os.path.join(root, "data", BACKUP_DIR_NAME, stamp)
    for rel in rel_paths:
        source = os.path.join(root, rel)
        if not os.path.exists(source):
            continue
        target = os.path.join(backup_root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    return backup_root if os.path.isdir(backup_root) else ""


def apply_update(files: dict, dry_run: bool = False) -> dict:
    """把下载好的文件写进程序目录（原子替换 + 先备份）。

    返回 {written, added, overwritten, backup, skipped, remote_version, local_version}
    """
    root = app_dir()
    added, overwritten = [], []
    for rel in sorted(files):
        (overwritten if os.path.exists(os.path.join(root, rel)) else added).append(rel)

    result = {"written": [], "added": added, "overwritten": overwritten,
              "backup": "", "dry_run": dry_run}
    if dry_run:
        return result

    result["backup"] = _backup(root, overwritten)

    for rel, content in files.items():
        target = os.path.join(root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temp = target + ".update_tmp"
        with open(temp, "wb") as fh:
            fh.write(content)
        os.replace(temp, target)          # 原子替换：不会出现写了一半的文件
        result["written"].append(rel)

    result["remote_version"] = _version_in(files.get("version.py", b""))
    logger.info(f"本地更新完成：新增 {len(added)} 个、覆盖 {len(overwritten)} 个"
                f"（备份在 {result['backup'] or '无'}）")
    return result


def _version_in(raw: bytes) -> str:
    import re
    match = re.search(rb'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', raw or b"")
    return match.group(1).decode("utf-8", "replace") if match else ""


def _version_tuple(text: str) -> tuple:
    import re
    numbers = tuple(int(x) for x in re.findall(r"\d+", text or ""))
    return numbers or (0,)


def is_downgrade(remote: str, local: str) -> bool:
    """远端版本是否**比本地旧**。

    必须有这道闸：实测远端仓库当时停在 1.3.0，而本地已经到 1.4.0
    （`web_search.py` 本地 55KB、远端只有 24KB）。无条件覆盖会**把本地新代码降级冲掉**，
    而且日志里只会写"更新成功"——这种静默倒退比更新失败危险得多。
    """
    if not remote or not local:
        return False
    a, b = _version_tuple(remote), _version_tuple(local)
    size = max(len(a), len(b))
    a += (0,) * (size - len(a))
    b += (0,) * (size - len(b))
    return a < b


def run(dry_run: bool = False, force: bool = False) -> dict:
    """检查 → 下载 → 覆盖。任何一步失败都返回带 error 的结果，不抛给调用方。

    默认**拒绝降级**：远端版本比本地旧时直接返回 blocked，除非 force=True。
    """
    from version import __version__ as local_version

    started = time.time()
    out = {"ok": False, "error": "", "channel": "", "remote_version": "",
           "local_version": local_version, "add": [], "overwrite": [],
           "keep_only_local": [], "backup": "", "blocked": "", "elapsed": 0.0}
    try:
        files, channel = fetch_remote_files()
        out["channel"] = channel
        out["remote_version"] = _version_in(files.get("version.py", b""))

        if is_downgrade(out["remote_version"], local_version) and not force:
            out["ok"] = False
            out["blocked"] = "downgrade"
            out["error"] = (f"远端版本 {out['remote_version']} 比本地 {local_version} 旧，"
                            f"已拒绝覆盖（避免把本地新代码降级冲掉）。"
                            f"如确实要强制覆盖，请用 force=True")
            logger.warning(f"本地更新被拦下：{out['error']}")
            out["elapsed"] = round(time.time() - started, 2)
            return out

        plan_info = _plan_from(files)
        out["add"] = plan_info["add"]
        out["overwrite"] = plan_info["overwrite"]
        out["keep_only_local"] = plan_info["keep_only_local"]

        applied = apply_update(files, dry_run=dry_run)
        out["backup"] = applied["backup"]
        out["written"] = applied["written"]
        out["ok"] = True
    except Exception as e:      # noqa: BLE001 —— 更新失败不能把服务搞挂
        out["error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"本地更新失败：{out['error']}")
    out["elapsed"] = round(time.time() - started, 2)
    return out


def status() -> dict:
    """给界面看的简述：本地版本 + 远端版本（不下载文件，只读远端 version.py）。"""
    import re
    from version import __version__
    info = {"local_version": __version__, "remote_version": "", "error": ""}
    try:
        text = _fetch(f"{_API}/contents/version.py?ref={UPDATE_BRANCH}").decode("utf-8")
        data = json.loads(text)
        import base64
        raw = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
        info["remote_version"] = _version_in(raw.encode("utf-8"))
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    return info
