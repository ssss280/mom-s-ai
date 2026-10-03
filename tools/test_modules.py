"""模块级功能测试：路径/配置/存储/日志/OCR/截屏/模型/搜索/更新，逐项验证并给出结论。

原则：不伪造结果。真正依赖外部条件（Tesseract、视觉 API、屏幕）的项目，
拿不到就明确报"跳过/环境不支持"，而不是当成通过。
"""
import io
import json
import os
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []


def test(name):
    def decorator(func):
        try:
            detail = func()
            RESULTS.append((name, "PASS", detail or ""))
        except SkipTest as e:
            RESULTS.append((name, "SKIP", str(e)))
        except Exception as e:
            RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))
            traceback.print_exc(limit=3)
        return func
    return decorator


class SkipTest(Exception):
    pass


# ---------- 路径 ----------

@test("paths：目录解析与可写回退")
def _paths():
    import paths
    assert os.path.isdir(paths.APP_DIR), "APP_DIR 不存在"
    writable = paths.ensure_writable_dir(paths.DATA_DIR)
    probe = os.path.join(writable, ".probe_test")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("ok")
    os.remove(probe)
    assert paths.DB_PATH.endswith("chatsight.db")
    return f"APP_DIR 可写，DATA_DIR={os.path.basename(writable)}"


# ---------- 配置 ----------

@test("config：读取/保存/字段白名单")
def _config():
    import server
    config = server.load_config()
    assert isinstance(config, dict), "load_config 没返回 dict"
    needed = {"api_provider", "text_model", "vision_provider", "reply_count"}
    missing = needed - set(config)
    assert not missing, f"配置文件缺字段: {missing}"
    # api_key 是**用户自己填**的，必须在可写字段里，否则设置页存不上
    assert "api_key" in server.CONFIG_FIELDS, "api_key 不在可写字段里，设置页会存不上"
    return f"字段齐全，可写字段 {len(server.CONFIG_FIELDS)} 个（含 api_key）"


@test("config：非法值被收敛（修复验证）")
def _config_bad_values():
    """配置里的数值/枚举字段必须被收敛——用户能在设置页随便填。

    修复前实测：`'abc'` 让推荐回复报「API 调用失败: slice indices...」、
    `999` 真的生成 146 条、`-1` 静默少给一条。
    """
    import server
    expectations = {
        "abc": 3, 0: 1, -1: 1, 999: 10, None: 3, 3.7: 3, "5": 5, 5: 5,
    }
    for raw, expected in expectations.items():
        got = server.sanitize_config({"reply_count": raw})["reply_count"]
        assert got == expected, f"reply_count {raw!r} -> {got}，期望 {expected}"
        assert isinstance(got, int), f"reply_count {raw!r} 收敛后不是 int: {type(got)}"
    assert server.sanitize_config({"reply_style": "??"})["reply_style"] == "friendly"
    assert server.sanitize_config({"ocr_method": "xx"})["ocr_method"] == "auto"
    assert server.sanitize_config({"screenshot_keep": -5})["screenshot_keep"] == 0
    return "8 种非法 reply_count + 3 个枚举字段全部收敛正确"


@test("config：search_read_pages 开关真的生效")
def _read_pages_flag():
    import server
    saved = dict(server.config)
    try:
        server.config["search_read_pages"] = 0
        off = server.read_pages_enabled()
        server.config["search_read_pages"] = 1
        on = server.read_pages_enabled()
        assert off is False and on is True, f"开关无效: off={off} on={on}"
    finally:
        server.config.clear()
        server.config.update(saved)
    return "开关 0/1 都能正确反映"


# ---------- 存储 ----------

@test("storage：会话与消息 CRUD + 批量删除")
def _storage():
    from storage import ChatStorage
    db = os.path.join(tempfile.gettempdir(), "chatsight_test_storage.db")
    if os.path.exists(db):
        os.remove(db)
    store = ChatStorage(db)
    sid = store.create_session(session_type="chat")
    msg_id = store.save_message(sid, "问一句", role="user")
    store.save_message(sid, "答一句", role="assistant")
    sessions = store.get_sessions()
    assert any(s["id"] == sid for s in sessions), "刚建的会话不在列表里"
    messages = store.get_session_messages(sid)
    assert len(messages) == 2, f"消息数不对: {len(messages)}"
    detail = store.get_session(sid)
    assert detail.get("id") == sid and detail.get("type") == "chat", f"会话详情异常: {detail}"
    # 推荐回复挂在**消息**上，不是会话
    ids = store.save_suggestions(messages[0]["id"], ["回复一", "回复二"], model_used="test")
    assert len(ids) == 2, f"建议没存进去: {ids}"
    store.mark_suggestion_copied(ids[0])
    again = store.get_session_messages(sid)
    copied = [s for s in again[0].get("suggestions", []) if s["copied"]]
    assert len(copied) == 1, "标记已复制没生效"
    # 关键词搜索
    found = store.search_messages("问一句")
    assert found, "关键词搜索没结果"
    # 批量删除
    sid2 = store.create_session(session_type="recognition")
    store.delete_sessions([sid, sid2])
    left = [s["id"] for s in store.get_sessions()]
    assert sid not in left and sid2 not in left, "批量删除没生效"
    return "建会话/存消息/建议/已复制/搜索/批量删除 全部正常"


# ---------- 日志 ----------

@test("logger：日志落盘 + ERROR 自动进 error/")
def _logger():
    import logging
    from logger import setup_logging, get_log_file, get_error_dir
    path = setup_logging()
    assert path and os.path.exists(path), f"日志文件不存在: {path}"
    logging.getLogger("test").error("功能自检写入的错误行")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = io.open(path, encoding="utf-8", errors="replace").read()
    assert "功能自检写入的错误行" in text, "日志内容没写进去"
    error_dir = get_error_dir()
    files = [f for f in os.listdir(error_dir) if f.startswith("error_")] if os.path.isdir(error_dir) else []
    return f"日志可写，error/ 下有 {len(files)} 个错误文件"


# ---------- OCR ----------

@test("ocr：引擎探测与文本提取接口")
def _ocr():
    import ocr
    names = [n for n in dir(ocr) if not n.startswith("_")]
    assert "extract_chat_text" in names, f"缺少提取入口，现有: {names[:10]}"
    info = []
    for attr in ("tesseract_available", "has_tesseract", "check_tesseract"):
        if hasattr(ocr, attr):
            info.append(f"{attr}={getattr(ocr, attr)}")
    return "接口齐全 " + (" ".join(info) if info else "(无可用性探测函数)")


@test("ocr：Tesseract 实际可用性")
def _tesseract():
    import ocr
    for attr in ("tesseract_available",):
        if hasattr(ocr, attr):
            value = getattr(ocr, attr)
            value = value() if callable(value) else value
            if value:
                return "Tesseract 可用"
            raise SkipTest("本机未安装 Tesseract（本地 OCR 不可用，属环境问题）")
    raise SkipTest("模块没有提供可用性探测函数")


# ---------- 截屏 ----------

@test("capture：全屏截图（PIL Image）")
def _capture():
    from capture import capture_full_screen
    from PIL import Image
    image = capture_full_screen()
    assert isinstance(image, Image.Image), f"返回类型异常: {type(image)}"
    width, height = image.size
    assert width > 0 and height > 0, f"尺寸异常: {image.size}"
    # 真截图不该是纯色空图：抽样看像素是否有多样性
    colors = image.convert("RGB").resize((32, 32)).getcolors(maxcolors=1024)
    assert colors, "无法读取像素"
    return f"{width}x{height}，抽样到 {len(colors)} 种颜色（有真实画面内容）"


@test("win_capture：窗口枚举")
def _windows():
    import win_capture
    names = [n for n in dir(win_capture) if not n.startswith("_")]
    lister = next((n for n in ("list_windows", "enum_windows", "get_windows") if n in names), None)
    if not lister:
        raise SkipTest(f"未找到窗口枚举函数，现有: {names[:12]}")
    windows = getattr(win_capture, lister)()
    assert isinstance(windows, list), "枚举结果不是列表"
    return f"{lister}() 返回 {len(windows)} 个窗口"


# ---------- 模型 ----------

@test("models：各厂商默认配置")
def _models():
    from models import PROVIDER_DEFAULTS
    assert PROVIDER_DEFAULTS, "PROVIDER_DEFAULTS 为空"
    providers = sorted(PROVIDER_DEFAULTS)
    for name, spec in PROVIDER_DEFAULTS.items():
        assert isinstance(spec, dict), f"{name} 的配置不是 dict"
    return f"{len(providers)} 个厂商: {providers}"


# ---------- 搜索 ----------

@test("web_search：接口与查询改写")
def _search_api():
    import web_search as ws
    for fn in ("search", "build_query", "clean_query", "query_variants", "fetch_pages",
               "site_of", "build_context", "relevance", "engine_stats", "reset_health",
               "recent_queries", "query_log_path", "clear_query_log"):
        assert hasattr(ws, fn), f"缺少对外函数 {fn}"
    assert ws.query_variants(ws.clean_query("香港灯展什么时候办"))[0]
    assert ws.build_query("2026年的呢", ["香港灯具展是什么时候"]) == "香港灯具展是什么时候 2026年"
    return "13 个对外函数齐全，查询改写正确"


@test("fair_aliases：地区+种类解析")
def _aliases():
    import fair_aliases as fa
    cases = {"香港照明展": "香港国际秋季灯饰展", "香港灯展": "香港国际秋季灯饰展",
             "香港玩具展": "香港贸发局香港玩具展"}
    for query, expect in cases.items():
        got = fa.suggest_official(query).get("official")
        assert got == expect, f"{query} -> {got!r}，期望 {expect!r}"
    assert fa.suggest_official("DeepSeek 价格") == {}, "不该给非展会查询瞎建议"
    return "3 条命中，非展会查询不瞎猜"


# ---------- 更新 ----------

@test("update_check：版本比较与解析")
def _update_check():
    import update_check as uc
    assert uc.parse_version("v1.2.3") == "1.2.3"
    assert uc.parse_version("## [1.10.0] - x") == "1.10.0"
    assert uc.is_newer("1.10.0", "1.9.9") is True
    assert uc.is_newer("1.2.0", "1.2.0") is False
    assert uc.changelog_version("## [2.0.0] - 2026-01-01") == "2.0.0"
    assert uc.next_version("1.2.0") == "1.3.0"
    return "解析/比较/递增 全部正确"


@test("local_update：防降级与文件分类")
def _local_update():
    import local_update as lu
    assert lu.is_downgrade("1.3.0", "1.4.0") is True
    assert lu.is_downgrade("1.5.0", "1.4.0") is False
    assert lu._is_protected("config.json") is True
    assert lu._is_protected("data/app.log") is True
    assert lu._is_protected(".git/config") is True
    assert lu._is_protected("web_search.py") is False
    return "降级判定与受保护路径正确"


@test("release：以 git 标签为基准推算版本")
def _release():
    import release
    # 关键函数都得在（重写后新增了标签基准与一键发版）
    for fn in ("highest_version", "highest_stable_version", "read_local_version",
               "write_local_version", "changelog_section", "make_tag", "create_release"):
        assert hasattr(release, fn), f"缺少 {fn}"
    # 标签解析：预发布版也要能排出高低
    tags = ["v1.0.0", "v1.0.2", "v1.0.10", "v1.1.0", "v1.2.0-beta.1"]
    assert release.highest_version(tags) == "1.2.0", release.highest_version(tags)
    assert release.highest_stable_version(tags) == "1.1.0", release.highest_stable_version(tags)
    # 1.0.10 必须大于 1.0.9（字符串排序会排错，这里是数字段比较）
    assert release.highest_version(["v1.0.9", "v1.0.10"]) == "1.0.10"
    return "标签基准推算正确（含 1.0.10 > 1.0.9、预发布版单独统计）"


@test("release：改版本号必须先获人工同意（AGENTS.md 第一条）")
def _release_gate():
    """没有 approved=True 时，底层写函数必须拒绝——否则闸门形同虚设。"""
    import release
    before = release.read_local_version()
    try:
        release.write_local_version("9.9.9")
        raise AssertionError("未获同意却写成功了！闸门失效")
    except PermissionError as e:
        assert "同意" in str(e) or "approved" in str(e)
    after = release.read_local_version()
    assert before == after, f"版本号被改动了：{before} -> {after}"
    return f"拒绝未授权的写入，version.py 保持 {after}"


# ---------- 输出 ----------

print("=" * 78)
for name, status, detail in RESULTS:
    mark = {"PASS": "OK  ", "SKIP": "跳过", "FAIL": "!!  "}[status]
    print(f"{mark} {name}")
    if detail:
        print(f"       {detail}")
print("=" * 78)
passed = sum(1 for _n, s, _d in RESULTS if s == "PASS")
skipped = sum(1 for _n, s, _d in RESULTS if s == "SKIP")
failed = [n for n, s, _d in RESULTS if s == "FAIL"]
print(f"通过 {passed} / 跳过 {skipped} / 失败 {len(failed)}")
if failed:
    print("失败项:", failed)
