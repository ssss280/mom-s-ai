"""免密钥联网搜索：Bing / 搜狗 / 百度 并行查，DuckDuckGo 兜底。

为什么不"谁先出结果用谁"：实测搜「2026香港秋季照明展」时，Bing 返回的是一堆
「2026 年日历/放假安排」——和照明展毫无关系，但它"有结果"，就把真正搜到了
展会时间的搜狗结果挤掉了。所以必须**并行问多个引擎、合并去重、按相关性排序**。

相关性打分（不引入分词库）：
- 中文按 2 字滑窗（bigram）拆，"2026香港秋季照明展" → 香港/港秋/秋季/季照/照明/明展
- 英文/数字按单词拆
- 命中数 / 总项数，标题命中额外加权、整体命中再加分
- 低于 MIN_RELEVANCE 的结果直接丢掉（宁可返回空，也不把无关网页喂给模型）

其他要点：
- 结果按 query 缓存 5 分钟；
- Bing 的结果链接常是 /ck/a?...&u=a1<base64> 跳转，还原成真实 URL；
- 百度优先取块里的 mu="真实地址"；
- 任何失败都不抛异常，写进返回的 error 里。
"""

import base64
import html
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 8
DEFAULT_COUNT = 6
CACHE_TTL = 300
MAX_SNIPPET = 300
MIN_RELEVANCE = 0.3      # 相关性达到这个值才算"可信结果"
FALLBACK_MIN = 0.2       # 达不到上面但到了这个值：作为"低相关参考"喂给模型，并提示它自行判断
TOTAL_BUDGET = 12        # 一轮搜索的总预算（秒）
READ_PAGES = 3           # 联网搜索后抓前几篇的正文
PAGE_MAX_CHARS = 2000    # 每篇正文最多取多少字符喂给模型
PAGE_MAX_BYTES = 800_000  # 单页最多下载多少字节（防大页面拖时间）
MIN_PAGE_CHARS = 100     # 抽出来不到这么多字符的正文当没抓到（反爬页/纯 JS 页只有几个字）
PAGES_BUDGET = 6         # 抓正文的总预算（秒）——实测放到 8 秒会让整轮偏慢
PAGE_TIMEOUT = 5         # 单页抓取超时（秒）

_cache: dict = {}


# ---------- 基础工具 ----------

def _get(url: str, timeout: int = TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _text(fragment: str) -> str:
    """去掉标签、还原实体、压缩空白。"""
    text = re.sub(r"<[^>]+>", "", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_url(url: str) -> str:
    url = html.unescape(url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def _unwrap_bing(url: str) -> str:
    """还原 Bing 的 /ck/a?...&u=a1<base64url> 跳转链接。"""
    if "bing.com/ck/a" not in url:
        return url
    match = re.search(r"[?&]u=a1([^&]+)", url)
    if not match:
        return url
    token = match.group(1).replace("-", "+").replace("_", "/")
    token += "=" * (-len(token) % 4)
    try:
        return base64.b64decode(token).decode("utf-8", "replace")
    except Exception:
        return url


# 图片/视频/AI 聚合这类垂直页面对"给模型提供事实"没有价值，直接排除
MEDIA_HOSTS = (
    "image.baidu.com", "v.baidu.com", "image.so.com", "ai.so.com", "360kan.com",
    "pic.sogou.com", "images.google.", "gstatic.com",
)

# 被反爬拦下时页面里会出现这些字样（配合"一条结果都没有 + 页面异常短"判断）
BLOCK_MARKERS = re.compile(r"安全验证|验证码|请输入验证|访问过于频繁|wappass|seccode|captcha|滑动验证", re.I)
COOLDOWN = 300          # 被拦后 5 分钟内不再问这个引擎（实测拦一会儿就放，10 分钟太长会把自己饿死）

_blocked_until: dict = {}


class EngineBlocked(RuntimeError):
    """引擎返回了安全验证页，短时间内别再打它。"""


def _looks_blocked(page: str, result_count: int) -> bool:
    """没有结果、页面还异常短、又带验证字样 → 判定被拦。"""
    return result_count == 0 and len(page) < 30000 and bool(BLOCK_MARKERS.search(page))


def _acceptable(url: str, blocked_hosts: tuple, allow_paths: tuple = ()) -> bool:
    if not url.startswith("http"):
        return False
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if not host:
        return False
    if any(bad in host for bad in MEDIA_HOSTS):
        return False
    if any(bad in host for bad in blocked_hosts):
        # 搜索结果页自己的跳转链接（例如搜狗的 /link?url=）要放行，
        # 否则会把该引擎的结果整批丢掉
        return any(parsed.path.startswith(path) for path in allow_paths)
    return True


# ---------- 查询词清洗与改写 ----------

_CLAUSE_SPLIT = re.compile(r"[，,。.；;！!？?\n]")


def clean_query(text: str, max_len: int = 60) -> str:
    """把聊天里的一句话变成合适的搜索词。

    「2026香港秋季照明展，什么时候在哪办？」->「2026香港秋季照明展」
    搜索词里带上"什么时候/在哪办"这类问句成分只会稀释关键词。
    """
    query = re.sub(r"\s+", " ", (text or "").strip())
    head = _CLAUSE_SPLIT.split(query)[0].strip()
    if len(head) >= 2:
        query = head
    return query[:max_len].strip(" \t，,。.！!？?、;；:：")


# 追问句：自己几乎没有检索价值，必须结合上一句才有意义
_FOLLOW_UP_TAIL = re.compile(r"(呢|那|那么|还有|继续|再来|然后)\s*[？?。.]?$")
_FOLLOW_UP_HEAD = re.compile(r"^(那|那么|还有|再|继续|然后|它|这个|那个|这些|那些)")
_ONLY_YEARISH = re.compile(r"^[\d年月日\s，,。.？?、]{0,8}$")


def is_follow_up(query: str) -> bool:
    """判断是不是"追问"（如「2026年的呢」「那地点呢」）。

    这类句子必须拼上上一句才能搜索——实测日志里出现过直接拿「2026年的呢」去搜的情况，
    结果当然是一堆无关网页。
    """
    query = (query or "").strip()
    if not query:
        return False
    if _FOLLOW_UP_TAIL.search(query) or _FOLLOW_UP_HEAD.match(query):
        return True
    # 只有年份/日期这种，也算追问（"2026年"）
    return bool(_ONLY_YEARISH.match(query))


def build_query(message: str, history: list = None) -> str:
    """结合上下文生成搜索词。

    history 是之前的用户消息（从旧到新），当前这句如果是追问就拼上最近一条有信息量的上一句。
    """
    cleaned = clean_query(message)
    if not cleaned:
        return ""
    if not is_follow_up(cleaned):
        return cleaned
    for previous in reversed(history or []):
        prev = clean_query(previous)
        # 上一句也得有信息量，否则继续往前找
        if prev and len(term_text(prev).strip()) >= 2 and not is_follow_up(prev):
            # 追问句里的新约束（年份等）要保留，但"是什么时候/呢"这类噪声去掉：
            # 「香港灯具展是什么时候」+「2026年的呢」->「香港灯具展是什么时候 2026年」
            tail = term_text(cleaned).strip() or cleaned
            return f"{prev} {tail}".strip()
    return cleaned


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" \t，,。.！!？?、;；:：")


# 去掉年份（连着的"年"一起去掉，否则会剩下一个孤零零的"年"）
_YEAR_RE = re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}\s*年?(?![0-9])")
# 只削句尾的疑问成分，不要句中乱削（否则「今天北京天气怎么样」会变成「今天北京天气 样」）
_TAIL_QUESTION_RE = re.compile(
    r"(?:怎么样|怎么|如何|是多少|多少钱|在哪[里儿]?|什么时候|何时|是什么|有哪些|几点|多久|的时间|时间|是|吗|呢|啊|呀|吧|的)\s*$"
)
_LEAD_COMMAND_RE = re.compile(
    r"^(?:(?:帮我|请|麻烦|帮忙)\s*)?(?:(?:查一查|查查|查一下|查询|搜索一下|搜索|搜一下|搜一搜|找一下|了解一下|看看)\s*)?"
)


def query_variants(query: str) -> list:
    """按"先原样、再改写"的顺序给出候选查询词。

    实测：搜「2026香港秋季照明展」时 Bing 把 2026 当实体，返回全年日历；
    去掉 2026 后第一条就是「展会概览 | 香港贸发局香港国际秋季灯饰展 - HKTDC」。
    所以第一个候选没有相关结果时，必须换个说法再试。
    """
    query = (query or "").strip()
    variants = [query]

    # 去掉 4 位年份（只有首轮没结果时才会用到，不怕误伤"2026年放假安排"这类查询）
    no_year = _tidy(_YEAR_RE.sub(" ", query))
    if no_year and no_year != query:
        variants.append(no_year)

    # 再去掉"帮我查一下/怎么样"这类问句成分，只留核心实体
    core = no_year or query
    for _ in range(3):   # 反复削：`香港灯具展是什么时候` -> `香港灯具展是` -> `香港灯具展`
        stripped = _tidy(_TAIL_QUESTION_RE.sub("", _LEAD_COMMAND_RE.sub("", core)))
        if stripped == core:
            break
        core = stripped
    if len(core) >= 2 and core not in variants:
        variants.append(core)

    return variants[:3]


def _score_and_filter(query: str, merged: list) -> list:
    """打分并按相关性排序。

    这里**不做阈值过滤**：可信（≥MIN_RELEVANCE）和低相关（≥FALLBACK_MIN）由调用方分层，
    否则"低相关兜底"那一档永远是空的。
    """
    for item in merged:
        item["score"] = relevance(query, item)
    return sorted(merged, key=lambda x: x["score"], reverse=True)


# ---------- 还原跳转链接 ----------

REDIRECT_PATTERNS = (
    r'window\.location\.(?:replace|href)\s*[=(]\s*["\']([^"\']+)',
    r'<meta[^>]+http-equiv=["\']?refresh[^>]+url=([^"\'>\s]+)',
)
_INTERNAL_HOSTS = ("so.com", "baidu.com", "sogou.com", "bing.com")


def _is_internal(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(bad in host for bad in _INTERNAL_HOSTS)


def _resolve_redirect(url: str, timeout: int = 6) -> str:
    """把搜索页的跳转链接还原成真实地址。

    360 的 /link?m=... 必须带 Referer 才会跳，拿到的是一个 376 字节的 JS 跳转页；
    真实地址就写在页面里，抠出来即可。失败就原样返回（浏览器里点还是能跳）。
    """
    if "/link?" not in url or not _is_internal(url):
        return url
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.so.com/",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl()
            body = response.read().decode("utf-8", "replace")
    except Exception as e:
        logger.debug(f"还原跳转失败 {url[:60]}: {e}")
        return url

    if final != url and not _is_internal(final):
        return _clean_url(final)
    for pattern in REDIRECT_PATTERNS:
        match = re.search(pattern, body, re.I)
        if match:
            target = _clean_url(html.unescape(match.group(1)))
            if target.startswith("http") and not _is_internal(target):
                return target
    for candidate in re.findall(r'https?://[^\s"\'<>]{10,200}', body):
        if not _is_internal(candidate):
            return _clean_url(candidate)
    return url


def _resolve_many(urls: list, timeout: int = 6) -> dict:
    """并行还原一批跳转链接。"""
    resolved: dict = {}
    lock = threading.Lock()

    def worker(target):
        real = _resolve_redirect(target, timeout)
        with lock:
            resolved[target] = real

    threads = [threading.Thread(target=worker, args=(u,), daemon=True) for u in urls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout + 1)
    return resolved


def _resolve_results(results: list, timeout: int = 6) -> list:
    """统一把结果里的搜索引擎跳转链接还原成真实地址。

    搜狗/百度/360 的结果链接都可能是 /link? 跳转；不还原的话，
    既看不出真实域名，后面"抓正文"那一步也会因为跳转链接抓不到东西而白白浪费名额。
    还原失败就保留原链接（浏览器里点通常还是能跳）。
    """
    pending = [item["url"] for item in results if _is_internal(item.get("url", ""))]
    if pending:
        resolved = _resolve_many(pending, timeout)
        for item in results:
            item["url"] = resolved.get(item["url"], item["url"])
    return results


# ---------- 相关性打分 ----------

# 问句成分：它们不是检索关键词，留在打分里只会稀释命中率
# （实测「香港灯具展是什么时候」9 个 bigram 里 4 个是问句噪声，把真结果压到阈值以下）
_QUESTION_WORDS = re.compile(
    r"(什么时候|多久|几点|什么样|怎么样|怎么办|怎么|如何|是什么|哪些|哪个|哪里|在哪[里儿]?|"
    r"多少钱|多少|为何|为什么|是不是|有没有|能不能|可不可以|"
    r"请问|帮我|帮忙|查一查|查查|查一下|查询|搜索一下|搜索|搜一下|搜一搜|找一下|了解一下|看看|告诉我|"
    r"是|的|吗|呢|啊|呀|吧|了|一下|时候)"
)


def term_text(text: str) -> str:
    """只保留有检索价值的词，用于相关性打分。"""
    text = re.sub(r"^(?:那|那么|还有|再|继续|然后|它|这个|那个)\s*", "", (text or "").strip())
    return _QUESTION_WORDS.sub(" ", text)


def relevance(query: str, item: dict) -> float:
    """查询词与结果标题/摘要的匹配程度，0~1。"""
    source = term_text(query) or query
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", source or "")
    grams = {cjk[i:i + 2] for i in range(len(cjk) - 1)} or ({cjk} if cjk else set())
    words = set(re.findall(r"[a-z0-9]{2,}", (source or "").lower()))
    terms = list(grams) + list(words)
    if not terms:
        return 0.0

    title = (item.get("title") or "").lower()
    body = title + " " + (item.get("snippet") or "").lower()

    hits = sum(1 for term in terms if term in body)
    score = hits / len(terms)

    title_hits = sum(1 for term in terms if term in title)
    if title_hits:
        score += 0.15 * (title_hits / len(terms))
    if query and query.lower() in body:
        score += 0.2
    return min(1.0, round(score, 3))


# ---------- 各引擎 ----------

def _search_bing(query: str, count: int) -> list:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=zh-CN&count=20"
    page = _get(url)
    results = []
    for block in re.split(r'<li class="b_algo', page)[1:]:
        title_match = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_match:
            continue
        link = _unwrap_bing(_clean_url(title_match.group(1)))
        title = _text(title_match.group(2))
        if not title or not _acceptable(link, ("bing.com", "microsoft.com", "msn.com")):
            continue
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        results.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet_match.group(1)) if snippet_match else "",
            "engine": "bing",
        })
    if _looks_blocked(page, len(results)):
        raise EngineBlocked("bing 需要安全验证")
    return results


def _search_sogou(query: str, count: int) -> list:
    url = "https://www.sogou.com/web?query=" + urllib.parse.quote(query)
    page = _get(url)
    results = []
    for block in re.split(r'<div class="vrwrap', page)[1:]:
        title_match = re.search(r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_match:
            continue
        link = _clean_url(title_match.group(1))
        if link.startswith("/"):
            link = "https://www.sogou.com" + link
        title = _text(title_match.group(2))
        if not title or not _acceptable(link, ("sogou.com", "sogoucdn.com"), allow_paths=("/link",)):
            continue
        snippet_match = re.search(r'class="(?:fz-mid|space-txt|str_info|text-lh-24)[^"]*"[^>]*>(.*?)</div>',
                                  block, re.S)
        if not snippet_match:
            snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        results.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet_match.group(1)) if snippet_match else "",
            "engine": "sogou",
        })
    if _looks_blocked(page, len(results)):
        raise EngineBlocked("sogou 需要安全验证")
    return results


def _search_baidu(query: str, count: int) -> list:
    url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(query) + "&rn=20"
    page = _get(url)
    results = []
    for block in re.split(r'<div[^>]+class="result[^"]*c-container', page)[1:]:
        title_match = re.search(r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_match:
            continue
        # 百度结果通常是 /link?url=... 跳转；能拿到 mu="真实地址" 时优先用它
        real_match = re.search(r'\bmu="(https?://[^"]+)"', block)
        link = _clean_url(real_match.group(1)) if real_match else _clean_url(title_match.group(1))
        title = _text(title_match.group(2))
        if not title or not _acceptable(link, ()):
            continue
        snippet_match = re.search(r'class="[^"]*(?:content-right|c-abstract|content-right_)[^"]*"[^>]*>(.*?)</',
                                  block, re.S)
        results.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet_match.group(1)) if snippet_match else "",
            "engine": "baidu",
        })
    if _looks_blocked(page, len(results)):
        raise EngineBlocked("baidu 需要安全验证")
    return results


def _search_so360(query: str, count: int) -> list:
    """360 搜索。实测它比搜狗/百度更不容易被拦，而且摘要里常带具体信息。"""
    url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
    page = _get(url)
    raw = []
    for block in re.split(r'<li class="res-list', page)[1:]:
        title_match = re.search(r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_match:
            continue
        link = _clean_url(title_match.group(1))
        if link.startswith("/"):
            link = "https://www.so.com" + link
        title = _text(title_match.group(2))
        if not title:
            continue
        snippet_match = re.search(r'class="res-desc"[^>]*>(.*?)</p>', block, re.S) or \
            re.search(r'class="res-rich[^"]*"[^>]*>(.*?)</div>', block, re.S) or \
            re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        raw.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet_match.group(1)) if snippet_match else "",
            "engine": "so360",
        })
    if _looks_blocked(page, len(raw)):
        raise EngineBlocked("so360 需要安全验证")

    # 360 的链接几乎都是 /link?m= 跳转，先还原成真实地址，才能按域名过滤图片/视频页
    to_resolve = [item["url"] for item in raw if "/link?" in item["url"]][:count * 2]
    resolved = _resolve_many(to_resolve)
    results = []
    for item in raw:
        item["url"] = resolved.get(item["url"], item["url"])
        if _acceptable(item["url"], ("so.com",), allow_paths=("/link",)):
            results.append(item)
    return results


def _search_duckduckgo(query: str, count: int) -> list:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    page = _get(url, timeout=TIMEOUT)
    results = []
    for block in re.split(r'<div class="result results_links', page)[1:]:
        title_match = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not title_match:
            continue
        link = _clean_url(title_match.group(1))
        if "duckduckgo.com/l/" in link:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
            link = _clean_url((params.get("uddg") or [""])[0])
        title = _text(title_match.group(2))
        if not title or not _acceptable(link, ("duckduckgo.com",)):
            continue
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        results.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet_match.group(1)) if snippet_match else "",
            "engine": "duckduckgo",
        })
    return results


ENGINES = (
    ("bing", _search_bing),
    ("so360", _search_so360),
    ("sogou", _search_sogou),
    ("baidu", _search_baidu),
    ("duckduckgo", _search_duckduckgo),
)
# 分两批打：搜狗/百度很容易触发安全验证，只在第一批没结果时才用它们，
# 免得每次搜索都同时打 4 个引擎、用不了几次就被集体拦下
PRIMARY_ENGINES = ENGINES[:2]     # bing + so360，最抗压
SECONDARY_ENGINES = ENGINES[2:4]  # sogou + baidu，中文覆盖好但容易拦
FALLBACK_ENGINE = ENGINES[4]      # DuckDuckGo，挂了代理/在国外才用得上


def _gather(query: str, engines, count: int, deadline: float):
    """并行跑多个引擎，收集结果与错误；被安全验证拦下的引擎进入冷却期。"""
    merged: list = []
    errors: list = []
    lock = threading.Lock()
    now = time.time()

    def worker(name, engine):
        if now < _blocked_until.get(name, 0):
            with lock:
                errors.append(f"{name}: 冷却中")
            return
        try:
            found = engine(query, count)
        except EngineBlocked as e:
            with lock:
                _blocked_until[name] = time.time() + COOLDOWN
                errors.append(f"{name}: 被安全验证拦下，冷却 {COOLDOWN // 60} 分钟")
            logger.warning(f"搜索源 {name} 被拦下（{e}），{COOLDOWN // 60} 分钟内不再使用")
            return
        except Exception as e:
            with lock:
                errors.append(f"{name}: {type(e).__name__}")
            logger.warning(f"搜索源 {name} 失败: {e}")
            return
        with lock:
            if found:
                merged.extend(found)
            else:
                errors.append(f"{name}: 无结果")

    threads = []
    for name, engine in engines:
        thread = threading.Thread(target=worker, args=(name, engine), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(max(0.1, deadline - time.time()))
    return merged, errors


def _dedupe(items: list) -> list:
    seen_urls, seen_titles, final = set(), set(), []
    for item in items:
        url = item.get("url", "")
        title = re.sub(r"\s+", "", item.get("title", ""))
        if not url or url in seen_urls or (title and title in seen_titles):
            continue
        seen_urls.add(url)
        if title:
            seen_titles.add(title)
        item["snippet"] = (item.get("snippet") or "")[:MAX_SNIPPET]
        final.append(item)
    return final


# ---------- 对外接口 ----------

def search(query: str, count: int = DEFAULT_COUNT, use_cache: bool = True) -> dict:
    """搜索并返回统一结构，任何情况下都不抛异常。

    返回: {"query", "engine", "results": [{"title","url","snippet","engine","score"}], "error"}
    `engine` 是贡献结果最多的引擎；结果已按相关性排序并过滤掉不相关的。
    """
    query = clean_query(query)
    info = {"query": query, "query_used": "", "engine": "", "results": [], "error": "",
            "low_relevance": False}
    if not query:
        info["error"] = "搜索内容为空"
        return info

    if use_cache:
        cached = _cache.get(query)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return dict(cached[1], cached=True)

    deadline = time.time() + TOTAL_BUDGET
    merged_all, errors, results = [], [], []
    weak: list = []          # 相关性不够"可信"但也不是垃圾的结果，最后兜底用

    def _try(variant: str, engines, need_secondary: bool):
        """跑一批引擎，返回 (可信结果, 低相关候选)。"""
        merged, variant_errors = _gather(variant, engines, count * 2, deadline)
        merged_all.extend(merged)
        errors.extend(variant_errors)
        scored = _score_and_filter(variant, merged)
        good = [item for item in scored if item["score"] >= MIN_RELEVANCE]
        low = [item for item in scored if FALLBACK_MIN <= item["score"] < MIN_RELEVANCE]
        return good, low

    # 一轮一轮换查询词试：原样 -> 去掉年份 -> 只留核心实体
    for variant in query_variants(query):
        if time.time() >= deadline:
            break
        good, low = _try(variant, PRIMARY_ENGINES, False)
        weak.extend(low)
        if not good and time.time() < deadline:
            # 主力两个引擎没结果，再拉搜狗/百度（它们更容易被反爬，所以放第二批）
            more_good, more_low = _try(variant, SECONDARY_ENGINES, True)
            good, weak = more_good, weak + more_low
        if good:
            results = _resolve_results(_dedupe(good)[:count])
            if results:
                info["query_used"] = variant
                if variant != query:
                    logger.info(f"原查询「{query}」没有相关结果，换用「{variant}」搜到 {len(results)} 条")
                break

    # 换了词也没有，再用 DuckDuckGo 兜底（挂了代理的话它最准）
    if not results and time.time() < deadline:
        logger.info("国内引擎没有相关结果，尝试 DuckDuckGo")
        extra, extra_errors = _gather(query, (FALLBACK_ENGINE,), count * 2, deadline)
        errors.extend(extra_errors)
        scored = _score_and_filter(query, extra)
        good = [item for item in scored if item["score"] >= MIN_RELEVANCE]
        weak.extend(item for item in scored if FALLBACK_MIN <= item["score"] < MIN_RELEVANCE)
        if good:
            results = _resolve_results(_dedupe(good)[:count])
            info["query_used"] = query

    if results:
        best = max(results, key=lambda x: x["score"])
        info["engine"] = best["engine"]
        info["results"] = results
        logger.info(f"搜索「{query}」（实际用「{info['query_used']}」）合并 {len(merged_all)} 条，"
                    f"过滤后 {len(results)} 条（最相关来自 {info['engine']}，分数 {best['score']}）")
    elif weak:
        # 没有"可信"结果，但有一些沾边的：与其告诉用户"没查到"，不如交给模型判断
        # （模型判语义比这里的 bigram 打分靠谱），同时明确标注"相关性不高"
        results = _resolve_results(_dedupe(sorted(weak, key=lambda x: x["score"], reverse=True))[:3])
        info["engine"] = results[0]["engine"] if results else ""
        info["results"] = results
        info["low_relevance"] = True
        info["error"] = f"未找到高相关结果，以下 {len(results)} 条相关性较低，仅供参考"
        logger.info(f"搜索「{query}」没有高相关结果，退回 {len(results)} 条低相关参考（最高分 "
                    f"{results[0]['score'] if results else 0}）")
    else:
        dropped = len(merged_all)
        info["error"] = (f"没有找到相关结果（试了 {len(query_variants(query))} 种查询词，{dropped} 条都被判为不相关）"
                         if dropped else "；".join(errors) or "没有可用的搜索源")
        logger.warning(f"搜索「{query}」没有可用结果：{info['error']}")

    _cache[query] = (time.time(), info)
    return info


def build_context(results: list) -> str:
    """把搜索结果拼成给模型看的上下文。"""
    lines = []
    for index, item in enumerate(results, 1):
        lines.append(f"[{index}] {item['title']}\n    {item['url']}\n    {item.get('snippet') or ''}")
    return "\n".join(lines)


# ---------- 抓网页正文 ----------

_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|iframe|template|form)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BLOCK_END_RE = re.compile(r"</?(?:br|p|div|li|h[1-6]|tr|section|article|header|footer|blockquote)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")

# 常见正文容器，命中就只取它，能明显减少导航/推荐位噪声
_MAIN_PATTERNS = (
    r"<article\b[^>]*>(.*?)</article>",
    r"<main\b[^>]*>(.*?)</main>",
    r'<div\b[^>]*(?:id|class)="[^"]*(?:article|content|main|post|detail|text)[^"]*"[^>]*>(.*?)</div>',
)


def extract_text(page: str, max_chars: int = PAGE_MAX_CHARS) -> str:
    """从 HTML 里抠出正文（不引入 bs4/lxml，纯正则 + 常见容器启发式）。"""
    text = _COMMENT_RE.sub(" ", page or "")
    text = _SCRIPT_RE.sub(" ", text)
    for pattern in _MAIN_PATTERNS:
        match = re.search(pattern, text, re.S | re.I)
        if match and len(match.group(1)) > 400:
            text = match.group(1)
            break
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if len(line) > 1]
    return "\n".join(lines)[:max_chars].strip()


def fetch_page_text(url: str, max_chars: int = PAGE_MAX_CHARS, timeout: int = PAGE_TIMEOUT) -> str:
    """抓一个网页并抽取正文；任何失败都返回空串（调用方退回用摘要）。"""
    if not url.startswith("http"):
        return ""
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type and "text" not in content_type:
                logger.info(f"跳过非网页内容（{content_type}）: {url[:60]}")
                return ""
            raw = response.read(PAGE_MAX_BYTES)
    except Exception as e:
        logger.info(f"抓正文失败 {url[:60]}: {type(e).__name__}")
        return ""

    page = None
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            page = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if page is None:
        page = raw.decode("utf-8", "replace")
    text = extract_text(page, max_chars)
    if len(text) < MIN_PAGE_CHARS:
        # 反爬页 / 纯 JS 页抽出来只有寥寥几个字，当没抓到，别拿它充当"正文"
        logger.info(f"正文太短（{len(text)} 字符），忽略: {url[:60]}")
        return ""
    return text


def fetch_pages(urls: list, count: int = READ_PAGES, timeout: int = PAGE_TIMEOUT) -> dict:
    """并行抓取前 count 篇的正文，返回 {url: 正文}（抓失败的不会有键）。

    只抓真实地址：万一还有没还原成功的 /link? 跳转链接，抓它纯属浪费名额。
    """
    targets = [u for u in (urls or []) if u and not _is_internal(u)][:max(count, 0)]
    if not targets:
        return {}

    pages: dict = {}
    lock = threading.Lock()
    deadline = time.time() + PAGES_BUDGET

    def worker(target):
        text = fetch_page_text(target, timeout=timeout)
        if text:
            with lock:
                pages[target] = text

    threads = [threading.Thread(target=worker, args=(u,), daemon=True) for u in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.1, deadline - time.time()))
    logger.info(f"抓正文：{len(pages)}/{len(targets)} 篇成功，"
                f"共 {sum(len(t) for t in pages.values())} 字符")
    return pages


def site_of(url: str) -> str:
    """取来源网站域名（去掉 www.），界面上用来标明"信息来自哪个网站"。"""
    host = urllib.parse.urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def build_context(results: list, pages: dict = None) -> str:
    """把搜索结果（含正文节选）拼成给模型看的上下文。"""
    pages = pages or {}
    lines = []
    for index, item in enumerate(results, 1):
        lines.append(f"[{index}] {item['title']}\n"
                     f"    来源网站: {site_of(item.get('url', ''))}\n"
                     f"    {item['url']}\n"
                     f"    摘要: {item.get('snippet') or '(无)'}")
        text = pages.get(item["url"])
        if text:
            lines.append(f"    正文节选: {text}")
    return "\n".join(lines)
