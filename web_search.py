"""免密钥联网搜索：Bing 网页 + Bing 资讯 RSS 主攻，360 补充，搜狗/百度兜底。

为什么不用"谁先出结果用谁"：实测搜「2026香港秋季照明展」时，Bing 返回的是一堆
「2026 年日历/放假安排」——和照明展毫无关系，但它"有结果"，就把真正搜到了
展会时间的搜狗结果挤掉了。所以必须**并行问多个引擎、合并去重、按相关性排序**。

为什么把 Bing 拆成两个源（2026-XX 实测结论）：
- `https://www.bing.com/search?q=...&setlang=zh-CN` 对**爬虫形状的请求**会返回
  一整页**诱饵结果**：搜「香港国际秋季灯饰展 2026」给你 Lady Gaga / Microsoft Support，
  搜「ChatSight」给你一堆物联网文章，页面结构完全正常（10 个 `<li class="b_algo">`），
  所以"解析成功"不等于"搜对了"。这是最隐蔽的失败：模型会拿到一页假事实。
- `https://cn.bing.com/search?q=...&mkt=zh-CN` 返回的是真实结果（实测同样查询命中灯饰展/会展中心）。
- `https://www.bing.com/news/search?q=...&format=RSS` 是官方 RSS，**不受诱饵页影响**，
  实测在 25 道评测题上 17 道直接达标（时间/地点/价格这类问题尤其准）。
- 因此主攻源换成 `bing_cn` + `bing_news`，`bing_web` 只作为最后兜底，
  并且对它的结果**必须**过相关性阈值（诱饵页得分接近 0，会被自然筛掉）。

相关性打分（不引入分词库）：
- 中文按 2 字滑窗（bigram）拆，"2026香港秋季照明展" → 香港/港秋/秋季/季照/照明/明展
- 英文/数字按单词拆
- 命中数 / 总项数，标题命中额外加权、整体命中再加分
- 低于 MIN_RELEVANCE 的结果直接丢掉（宁可返回空，也不把无关网页喂给模型）

其他要点：
- 结果按 query 缓存 5 分钟；
- Bing 的结果链接常是 /ck/a?...&u=a1<base64> 跳转，资讯 RSS 是
  /news/apiclick.aspx?...&url=<百分号编码>，两种都还原成真实 URL；
- 百度优先取块里的 mu="真实地址"；
- 引擎按"抗压程度"分四批打，第一批全空才打下一批（见 ENGINE_TIERS）；
- 任何失败都不抛异常，写进返回的 error 里。
"""

import base64
import html
import json
import logging
import os
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
TIMEOUT = 5              # 单次引擎请求超时（秒）。原来 8s：DuckDuckGo 不可达时会各占 8s，
                         # 一个批次就能吃掉一大半总预算
DEFAULT_COUNT = 6
CACHE_TTL = 300
MAX_SNIPPET = 300
MIN_RELEVANCE = 0.28     # 相关性达到这个值才算"可信结果"
FALLBACK_MIN = 0.15      # 达不到上面但到了这个值：作为"低相关参考"喂给模型，并提示它自行判断
GOOD_ENOUGH = 3          # 一批里拿到这么多"可信结果"就够用（保留给调用方参考）
TAIL_RATIO = 0.55        # 一批里最高分 × 这个比例 = 垫底结果的相对下限（见 search 内的 floor）
TOTAL_BUDGET = 8         # 一轮搜索的总预算（秒）。原来 12s：一轮里只要有一个源连不上，
                         # 用户就要等十几秒才看到回复；搜索是"够用就行"的环节，宁可少几条
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
    """还原 Bing 的跳转链接。

    两种形式：
    - 网页结果的 /ck/a?...&u=a1<base64url>
    - 资讯 RSS 的 /news/apiclick.aspx?...&url=<百分号编码的真实地址>
    后者不还原的话，正文抓取会去抓 bing.com 的跳转页，白白浪费名额。
    """
    if "bing.com/ck/a" in url:
        match = re.search(r"[?&]u=a1([^&]+)", url)
        if match:
            token = match.group(1).replace("-", "+").replace("_", "/")
            token += "=" * (-len(token) % 4)
            try:
                return base64.b64decode(token).decode("utf-8", "replace")
            except Exception:
                pass
    if "bing.com/news/apiclick.aspx" in url or "apiclick.aspx" in url:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(url)).query)
        target = (params.get("url") or [""])[0]
        if target.startswith("http"):
            return target
    return url


# 图片/视频/AI 聚合这类垂直页面对"给模型提供事实"没有价值，直接排除
MEDIA_HOSTS = (
    "image.baidu.com", "v.baidu.com", "image.so.com", "ai.so.com", "360kan.com",
    "pic.sogou.com", "images.google.", "gstatic.com",
)

# 被反爬拦下时页面里会出现这些字样（配合"一条结果都没有 + 页面异常短"判断）
BLOCK_MARKERS = re.compile(
    r"安全验证|验证码|请输入验证|访问过于频繁|访问异常|wappass|seccode|captcha|滑动验证|antispider",
    re.I,
)

# 2026 实测：360 的 6KB「访问异常页面」就是一次干净的判定——页面短、零结果、带标记。
# 冷却按"批次"而不是按引擎：整批都打不出结果时，说明这个 IP 在当前时刻被针对了，
# 换查询词也没必要再打同一批（旧代码按引擎冷却 5 分钟，反而把自己饿死）。
BLOCK_PAGE_MAX = 40000
COOLDOWN = 120          # 一个批次连续两次空手时，冷却 2 分钟


class EngineBlocked(RuntimeError):
    """引擎返回了安全验证页／异常页，短时间内别再打它。"""


def _looks_blocked(page: str, result_count: int) -> bool:
    """没有结果、页面还异常短、又带验证/异常字样 → 判定被拦。"""
    return result_count == 0 and len(page) < BLOCK_PAGE_MAX and bool(BLOCK_MARKERS.search(page))


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


# 纯"追问维度"词：它们本身不是话题，必须靠上一句才有意义
_BARE_CONSTRAINT_WORDS = {
    "时间", "日期", "时候", "地点", "地址", "在哪", "位置", "价格", "门票", "费用",
    "多少钱", "举办", "主办", "介绍", "详情", "信息", "情况", "结果", "天气",
}


def _has_own_entity(query: str) -> bool:
    """这句追问自己是否带着可检索的实体/约束（而不是纯粹的问句碎片）。

    实测动机（真实对话）：用户先问「香港玩具展是什么时候」，接着追问「香港照明展呢」。
    旧逻辑把新话题当成"附加约束"拼到旧问题上，得到
    「香港玩具展是什么时候 香港照明展」——这句话削掉问句成分后只剩「香港照明展」，
    要等换查询词那一轮才可能救回来，实际对话里预算已经耗在别的源上了。
    而「香港照明展」本身是个完整的话题，自己就能搜。

    反过来，「2026年的呢」「那地点呢」这类**没有自己话题**的追问必须拼上一句，
    单搜必然是一堆无关结果——所以这两类要区分开。
    """
    core = term_text(query)

    # 去掉年份/日期后什么都不剩（"2026年的呢"）→ 没有自己的话题
    if not _YEAR_RE.sub(" ", core).strip(" \t，,。.！!？?、;；:："):
        return False
    # 只剩"时间/地点/价格"这类追问维度词 → 也没有自己的话题
    bare = _tidy(core)
    if bare in _BARE_CONSTRAINT_WORDS:
        return False

    # 有中文实词（≥2 字）就算自带实体
    if re.search(r"[\u4e00-\u9fff]{2,}", core):
        return True
    # 有拉丁词（≥2 字）也算（"python 呢"）
    if re.search(r"[a-z0-9]{2,}", core.lower()):
        return True
    return False


def build_query(message: str, history: list = None) -> str:
    """结合上下文生成搜索词。

    history 是之前的用户消息（从旧到新）。只有**自己没带实体**的追问
    （「2026年的呢」「那地点呢」「还有呢」）才拼上上一句；
    像「香港照明展呢」这种自带新话题的追问，直接用自己搜——
    它跟上一句往往是**并列的另一个话题**，硬拼只会把两个话题混成一句搜不好的话。
    """
    cleaned = clean_query(message)
    if not cleaned:
        return ""
    if not is_follow_up(cleaned):
        return cleaned
    if _has_own_entity(cleaned):
        return cleaned
    for previous in reversed(history or []):
        prev = clean_query(previous)
        # 上一句也得有信息量，否则继续往前找
        if prev and len(term_text(prev).strip()) >= 2 and not is_follow_up(prev):
            # 追问句里的新约束（年份等）要保留，但"是什么时候/呢"这类噪声去掉：
            # 「香港灯具展是什么时候」+「2026年的呢」->「香港灯具展是什么时候 2026年」
            # 若削完什么都不剩（「还有呢」），说明这句没带来任何新约束，直接沿用上一句
            tail = term_text(cleaned).strip()
            return f"{prev} {tail}".strip() if len(tail) >= 2 else prev
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

    # 再去掉"帮我查一下/怎么样"这类问句成分，只留核心实体
    core = no_year or query
    for _ in range(3):   # 反复削：`香港灯具展是什么时候` -> `香港灯具展是` -> `香港灯具展`
        stripped = _tidy(_TAIL_QUESTION_RE.sub("", _LEAD_COMMAND_RE.sub("", core)))
        if stripped == core:
            break
        core = stripped

    # 只保留"还剩真正检索价值"的候选（按顺序）：
    # - 去完年份只剩问句噪声的（「2026年的呢」->「的呢」）直接不要；
    #   挑原始查询词，因为里面还留着真时间约束，比一个问句碎片强
    # - 允许削到只剩 2 个字：实测「北京天气怎么样」要削掉"怎么样"才能搜到天气页
    #   （不削的话 bing_cn 只按"北京"给一堆「北京市_百度百科」）；
    #   而 2 字是搜索的下限，再短就没有检索价值了
    candidates = []
    if no_year and no_year != query and len(term_text(no_year).strip()) >= 2:
        candidates.append(no_year)
    if core and core != query and core != no_year and len(term_text(core).strip()) >= 2:
        candidates.append(core)
    if not candidates:
        candidates.append(query)

    for candidate in candidates:
        if candidate not in variants:
            variants.append(candidate)
    return [v for v in variants if len(v.strip()) >= 2][:3]


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


# 权威站点后缀：政府/学术/百科/官方手册。命中给一点点加成，用来把"官方文档"
# 顶到"内容农场转载"前面（实测搜 Python 版本时，python.org 与百家号分数接近）。
_AUTHORITY_HOSTS = (
    "gov.cn", "gov.hk", "gov.tw", "edu.cn", "edu.hk", "ac.cn",
    "wikipedia.org", "baike.baidu.com", "who.int", "un.org", "unesco.org",
    "python.org", "docs.docker.com", "git-scm.com", "rust-lang.org", "mozilla.org",
    "deepseek.com", "openai.com", "fifa.com", "nobelprize.org", "mtr.com.hk",
    "hktdc.com", "12306.cn", "apple.com", "weather.com.cn", "cnsa.gov.cn",
)


def _is_authority(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in _AUTHORITY_HOSTS)


def _host_matches_query(host: str, cjk: str, words: set) -> bool:
    """查询词是否直接出现在域名里——"官网"类结果的最强信号。"""
    if host.endswith(".gov.cn") or host.endswith(".gov.hk"):
        return False  # 政府站域名和查询词无关，别沾这个光
    if any(len(word) >= 4 and word in host for word in words):
        return True
    for size in (4, 3, 2):
        if len(cjk) >= size:
            for start in range(0, len(cjk) - size + 1):
                if cjk[start:start + size] in host:
                    return True
    return False


# 泛指向的"实体总览页/首页"：标题里只有实体名（可能带"百科/官网"），没有限定词。
# 实测危害：搜「北京中轴线是什么时候列入世界遗产名录的」时 bing_cn 给「北京 _ 百科」，
# 搜「DeepSeek API 现在什么价格」时给「DeepSeek | API Platform」——bigram 命中率天然高，
# 却不是答案，会把真正的新闻/定价页挤下去。
_SECTION_ONLY_PATHS = {"", "index", "index.html", "index.htm", "home", "default",
                       "docs", "doc", "api", "download", "downloads", "en", "zh", "cn"}
# 注意：路径**非空**时不再一律算"栏目页"。实测「iPhone - Apple (中国大陆)」的
# /iphone/ 路径很短，却是这道题的正确落地页；一律扣分会误伤正常结果。
_ENTITY_NOISE_RE = re.compile(r"(百度百科|维基百科|官方网站|官网|首页|百科|home|official)", re.I)
GENERIC_PENALTY = 0.08   # 泛指向页扣分：够把 0.18 压到阈值之下，又不至于把正常页打死
OFFTOPIC_PENALTY = 0.10  # 完全跑题（一个类目词都没有）再扣一档，把它压到阈值之下
META_ENTITY_COVERAGE = 0.6   # 标题对"查询实体"的 bigram 覆盖率低于此值 → 判定只是泛泛介绍


def _query_content_terms(query: str) -> set:
    """查询里"必须被结果覆盖"的实词（剔掉问句成分与语气词）。"""
    text = term_text(query) or query
    terms = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.add(chunk)
        for size in (3, 2):                     # 长串再补几个子串，避免只能整串匹配
            if len(chunk) > size:
                for start in range(0, len(chunk) - size + 1):
                    terms.add(chunk[start:start + size])
    terms.update(w for w in re.findall(r"[a-z0-9]{2,}", text.lower()))
    return terms


def _category_words(query: str) -> set:
    """查询所属类目的所有说法（含同义词），用来判断结果"跑没跑题"。

    例：查询「香港玩具展」→ {"玩具"}；「香港灯展」→ {"照明","灯饰","灯具","灯光","灯展",...}。

    为什么要用词典而不是"标题覆盖查询实体"这种字面规则：用户说「照明」、官方名写「灯饰」，
    字面规则会把**官方页面**误判成跑题页（实测 HKTDC 官方页因此被扣分）。
    按类目同义词判断则两种说法都算"对题"，而完全不相干的页面（搜玩具展返回入境事务处）
    一个类目词都没有，才判为跑题。
    """
    try:
        from fair_aliases import CATEGORY_ALIASES, parse_region_category
    except Exception:
        return set()
    _region, category = parse_region_category(query)
    if not category:
        return set()
    words = {category}
    words.update(alias for alias, canon in CATEGORY_ALIASES.items() if canon == category)
    return words


def _is_offtopic(item: dict, query: str) -> bool:
    """结果是否与查询的类目完全不相干（一个类目说法都没有）。"""
    words = _category_words(query)
    if not words:
        return False
    haystack = " ".join([
        (item.get("title") or "").lower(),
        (item.get("snippet") or "").lower(),
        urllib.parse.unquote(item.get("url") or "").lower(),
    ])
    return not any(word.lower() in haystack for word in words)


def _is_generic_page(item: dict, query: str, cjk: str, words: set,
                     content_terms: set = None) -> bool:
    """这条结果是不是"只是个实体总览/首页"，而不是"回答了查询"。

    只在证据明确时返回 True（宁可漏判，不要误伤）：
    - 标题看起来就是查询实体，但查询里的实词（年份/英文/关键中文词）它一个都没覆盖；
    - URL 路径是典型的首页/栏目页，且查询里有限定词没被覆盖；
    - 标题只讲了实体的一部分（查询「北京中轴线…」→ 标题「北京 _ 百科」）。
    """
    url = item.get("url") or ""
    path = urllib.parse.urlparse(url).path.strip("/").lower()

    title = (item.get("title") or "").strip()
    normalized = _ENTITY_NOISE_RE.sub("", title) if title else ""
    normalized = re.sub(r"[\s_\-|·:：()（）\[\]【】]+", "", normalized).lower()
    entity = re.sub(r"[\s_+]+", "", (cjk or "")).lower()

    # 查询里的年份/英文限定词有没有被这条结果覆盖
    missed = [w for w in words if w not in normalized and w not in path]
    missed += [y for y in re.findall(r"(?:19|20)\d{2}", query or "")
               if y not in normalized and y not in path]
    # 中文限定词：查询里是中文、而标题里是英文的（搜「…什么价格」命中英文 API 首页）
    missed += [chunk for chunk in re.findall(r"[\u4e00-\u9fff]+", query or "")
               if chunk not in normalized]

    # 情况 0：结果和查询的类目完全不相干 —— 搜展会返回入境事务处这种，直接算跑题。
    # 用类目同义词判断（用户说"照明"、官方写"灯饰"都算对题），不用字面覆盖，
    # 避免把官方页面误判成泛指向页。
    if _is_offtopic(item, query):
        return True

    # 情况 A：典型的首页/栏目页——但对"查询本身就没别的限定词"的题不算（搜「北京天气」
    # 落到天气网首页就是正确答案）；只有当查询带着没被覆盖的年份/英文/中文限定词时才算泛指向页
    if path in _SECTION_ONLY_PATHS:
        return bool(missed)

    if not normalized or not entity:
        return False

    # 情况 B：标题只讲了实体的一部分，剩下的部分没被覆盖
    # 例：查询「北京中轴线…」→ 标题「北京 _ 百科」只讲了"北京"
    if entity.startswith(normalized) and normalized != entity:
        tail = entity[len(normalized):]
        if len(tail) >= 2 and len(tail) >= len(entity) * 0.4:
            return True

    # 情况 C：标题**正好**是实体本身，但查询里的年份/英文限定词一个都没出现
    # 例：查询「DeepSeek API 现在什么价格」→ 标题「DeepSeek | API Platform」
    if normalized == entity:
        return bool(missed)
    return False


def _score_one(query: str, item: dict) -> float:
    """单条结果的相关性分数。

    除了正文命中，还看两条"来源是否对味"的信号（实测能改善排序，且不会把无关结果
    救活——加成只有 0.06/0.10）：
    - 查询里的词出现在结果域名里（搜 DeepSeek 价格 → api-docs.deepseek.com）
    - 结果来自政府/百科/官方文档这类权威站点

    但对"实体总览页/首页"要**扣分**：这类页面 bigram 命中率天然很高（标题里就有实体名），
    却不是答案。实测不扣分时它会把真正的定价页/新闻挤下去。
    """
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

    host = site_of(item.get("url", ""))
    for booster, bonus in ((_host_matches_query(host, cjk, words), 0.10),
                           (_is_authority(host), 0.06)):
        if booster:
            score += bonus

    # 完全跑题（地区对上了、类目一个词都没有）单独再扣一档。
    # 实测：搜「香港玩具展」时百度百科的「香港特别行政区」靠"香港"+零散 bigram 拿到 0.35，
    # 刚好压在阈值 0.28 之上，于是既没触发官方名兜底、也没被判为跑题。
    if _is_offtopic(item, query):
        score -= OFFTOPIC_PENALTY
        item["_offtopic"] = True
    if _is_generic_page(item, query, cjk, words):
        score -= GENERIC_PENALTY
        item["_generic"] = True
    return max(0.0, min(1.0, round(score, 3)))


def relevance(query: str, item: dict) -> float:
    """查询词与结果标题/摘要的匹配程度，0~1。"""
    return _score_one(query, item)


def _score_and_filter(query: str, merged: list) -> list:
    """按相关性打分并排序。

    这里**不做阈值过滤**：可信（≥MIN_RELEVANCE）和低相关（≥FALLBACK_MIN）由调用方分层，
    否则"低相关兜底"那一档永远是空的。
    """
    for item in merged:
        item["score"] = _score_one(query, item)
    return sorted(merged, key=lambda x: x["score"], reverse=True)


# ---------- 各引擎 ----------

# 必须带 mkt：不带市场参数的 www.bing.com 会给爬虫形状的请求返回一整页诱饵结果
# （实测「香港国际秋季灯饰展 2026」→ Lady Gaga / Microsoft Support），页面上 10 个
# b_algo 块全是假的，只有靠相关性阈值才能筛掉。cn.bing.com + mkt 是实测正常的入口。
BING_URL = "https://cn.bing.com/search?q={query}&mkt=zh-CN&setlang=zh-CN"
BING_NEWS_RSS = "https://www.bing.com/news/search?q={query}&format=RSS"


def _parse_bing_page(page: str, unwrap: bool) -> list:
    results = []
    for block in re.split(r'<li class="b_algo', page)[1:]:
        title_match = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_match:
            continue
        link = _clean_url(title_match.group(1))
        if unwrap:
            link = _unwrap_bing(link)
        title = _text(title_match.group(2))
        if not title or not _acceptable(link, ("bing.com", "microsoft.com", "msn.com")):
            continue
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        results.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet_match.group(1)) if snippet_match else "",
            "engine": "bing_cn" if unwrap else "bing_web",
        })
    return results


def _search_bing(query: str, count: int) -> list:
    """Bing 中文网页搜索（主力）。"""
    page = _get(BING_URL.format(query=urllib.parse.quote(query)))
    results = _parse_bing_page(page, unwrap=True)
    if _looks_blocked(page, len(results)):
        raise EngineBlocked("bing_cn 需要安全验证")
    return results


def _search_bing_web(query: str, count: int) -> list:
    """Bing 国际站网页搜索（兜底）。

    实测它会给爬虫返回诱饵结果，所以只在其它源全空时才用；
    返回的内容靠 search() 里的相关性阈值把关。
    """
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=zh-CN"
    page = _get(url)
    results = _parse_bing_page(page, unwrap=True)
    if _looks_blocked(page, len(results)):
        raise EngineBlocked("bing_web 需要安全验证")
    return results


def _search_bing_news(query: str, count: int) -> list:
    """Bing 资讯 RSS：官方接口，不受诱饵页影响，摘要信息密度高。

    实测「2025年诺贝尔文学奖颁给了谁」这类时效问题，网页搜索给百科，
    资讯 RSS 直接给"2025年诺贝尔文学奖揭晓"的新闻报道。
    """
    page = _get(BING_NEWS_RSS.format(query=urllib.parse.quote(query)))
    results = []
    for item in re.findall(r"<item>(.*?)</item>", page, re.S):
        title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        link_match = re.search(r"<link>(.*?)</link>", item, re.S)
        desc_match = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", item, re.S)
        title = _text(title_match.group(1)) if title_match else ""
        link = _unwrap_bing(_clean_url(link_match.group(1))) if link_match else ""
        if not title or not link or not _acceptable(link, ("bing.com", "microsoft.com", "msn.com")):
            continue
        snippet = html.unescape(desc_match.group(1)) if desc_match else ""
        results.append({
            "title": title,
            "url": link,
            "snippet": _text(snippet),
            "engine": "bing_news",
        })
    if _looks_blocked(page, len(results)):
        raise EngineBlocked("bing_news 需要安全验证")
    return results


def _search_wikipedia(query: str, count: int) -> list:
    """维基百科 API（免费、无爬虫对抗），用来补实体类查询。

    实测「天宫空间站」「香港国际秋季灯饰展」都能给出干净的百科条目，
    尤其是国内引擎都需要验证码的时候，它是少数稳定可用的源。
    中文维基条目少时会自动补一次英文维基（英文技术词条都在那边）。
    """
    results = _wiki_search("zh", query)
    if len(results) < 2:
        results += _wiki_search("en", query)
    return results


def _wiki_search(lang: str, query: str) -> list:
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&format=json&utf8=1"
           "&srlimit=8&srsearch=" + urllib.parse.quote(query))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))

    results = []
    for hit in (data.get("query", {}).get("search") or []):
        title = hit.get("title") or ""
        if not title:
            continue
        results.append({
            "title": title,
            "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            # snippet 里带 <span class="searchmatch"> 高亮标签，_text 会去掉
            "snippet": _text(hit.get("snippet") or ""),
            "engine": "wikipedia" if lang == "zh" else "wikipedia_en",
        })
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
    ("so360", _search_so360),
    ("bing_cn", _search_bing),
    ("bing_web", _search_bing_web),
    ("bing_news", _search_bing_news),
    ("duckduckgo", _search_duckduckgo),
    ("wikipedia", _search_wikipedia),
    ("sogou", _search_sogou),
    ("baidu", _search_baidu),
)

# 批次顺序由 **实测** 决定（`py -3 eval/engine_eval.py` 顺序跑 25 题 × 每个源，间隔 0.2s）：
#
#   源          有贡献的题数   每次宽松达标   被拦     延迟P50
#   so360          96%          3.00 条      0/25    1481ms
#   bing_cn        40%          1.40 条      0/25     300ms
#   bing_web       36%          1.36 条      0/25     508ms
#   bing_news       0%          0.00 条      0/25     707ms   ← 完全没用了
#   sogou           0%          0.00 条      0/25     409ms
#   baidu           0%          0.00 条     25/25     395ms   ← 全程被安全验证
#
# 第 1 批只有两个源，是刻意的：**并行猛打会把源打爆**——同一时段并行打 8 个源时
# so360 被拦 23/25；顺序、少量请求时 25/25 全通。少打几个源既快又不招反爬。
# 上一版把 so360 排到最后一批、把已经失效的 bing_news 排第一批，是这一轮最大的错误。
ENGINE_TIERS = (
    ("so360", _search_so360),
    ("bing_cn", _search_bing),
), (
    ("bing_web", _search_bing_web),
    ("bing_news", _search_bing_news),
), (
    ("duckduckgo", _search_duckduckgo),
    ("wikipedia", _search_wikipedia),
), (
    ("sogou", _search_sogou),
    ("baidu", _search_baidu),
)
PRIMARY_ENGINES = ENGINE_TIERS[0]
SECONDARY_ENGINES = ENGINE_TIERS[2]
FALLBACK_ENGINE = ENGINE_TIERS[3]

# 英文查询要换顺序：实测 cn.bing 对英文技术问题根本不"看"查询词，
# 搜「python asyncio gather vs wait difference」返回 Python 官网首页、Docker 官网首页，
# 英文查询要换顺序：实测 cn.bing 对英文技术问题根本不"看"查询词，
# 搜「python asyncio gather vs wait difference」返回 Python 官网首页、Docker 官网首页。
# 英文技术题真正给得出答案的是 bing_web（国际站，实测 asyncio 题给 stackoverflow）
# 与 DuckDuckGo / 英文维基。so360 仍留在后面兜底：它对英文题也常有结果。
_ENGLISH_TIERS = (
    ("bing_web", _search_bing_web),
    ("duckduckgo", _search_duckduckgo),
), (
    ("wikipedia", _search_wikipedia),
    ("so360", _search_so360),
), (
    ("bing_cn", _search_bing),
    ("bing_news", _search_bing_news),
), (
    ("sogou", _search_sogou),
    ("baidu", _search_baidu),
)


def is_english_query(query: str) -> bool:
    """判断查询词是不是"英文技术查询"（几乎全是拉丁字母/数字）。

    中文为主、或中英混排（"DeepSeek API 价格"）都算中文查询，因为 cn.bing 对它们是好用的。
    """
    text = (query or "").strip()
    if len(text) < 3:
        return False
    latin = len(re.findall(r"[A-Za-z0-9]", text))
    return latin / max(1, len(text)) >= 0.85


_engine_stats: dict = {}
_stats_lock = threading.Lock()
# 引擎"打不通"之后的临时冷却。两种情况都要记，因为两者都会让每一轮搜索白等：
# - 传输层失败（超时/连不上）：DuckDuckGo 实测会在网络不通时每次连到超时；
# - 安全验证（EngineBlocked）：so360 实测被拦时每次都返回 6KB 的「访问异常页面」，
#   而旧代码对"被拦"只记统计、不冷却，于是整轮 25 道题里反复去打那个已知被拦的源，
#   中位延迟被拖到 12 秒（搜索总预算被耗光，后面的兜底源根本没机会跑）。
# 只对这两种情况生效；"返回 0 条"不触发，否则正常空结果会把可用引擎禁用掉。
_engine_down_until: dict = {}
ENGINE_DOWN_COOLDOWN = 90        # 被安全验证拦下：实测要几分钟才放行
ENGINE_ERROR_COOLDOWN = 30       # 纯网络失败：短一点，可能只是抖动


def _note_engine(name: str, outcome: str, ms: int = 0, found: int = 0):
    """记录每个引擎的健康状况（供 .bld/check_engines.py 做健康巡检）。

    引擎被反爬是常态，所以"哪个源还能用"必须能被观察到，否则排序只能靠猜。
    """
    with _stats_lock:
        entry = _engine_stats.setdefault(name, {"ok": 0, "blocked": 0, "error": 0, "empty": 0,
                                                "last": "", "ms": [], "found": 0})
        entry[outcome] = entry.get(outcome, 0) + 1
        entry["last"] = time.strftime("%H:%M:%S")
        if ms:
            entry["ms"].append(ms)
            entry["ms"] = entry["ms"][-20:]
        if found:
            entry["found"] += found


def engine_stats() -> dict:
    """返回各引擎健康快照（平均耗时、成功/被拦次数）。"""
    out = {}
    for name, entry in _engine_stats.items():
        times = entry.get("ms") or []
        out[name] = {
            "ok": entry.get("ok", 0), "blocked": entry.get("blocked", 0),
            "error": entry.get("error", 0), "empty": entry.get("empty", 0),
            "found": entry.get("found", 0),
            "avg_ms": round(sum(times) / len(times)) if times else 0,
            "last": entry.get("last", ""),
        }
    return out


def _gather(query: str, engines, count: int, deadline: float, trace: list = None):
    """并行跑多个引擎，收集结果与错误；被安全验证拦下的引擎记入统计。

    trace 传入一个列表时，会把**每个引擎这一次的结果**（状态/条数/耗时/错误）追加进去，
    供查询记录使用——排查"为什么没结果"时需要看的是源头，而不只是最终留下的几条。
    """
    merged: list = []
    errors: list = []
    lock = threading.Lock()

    def worker(name, engine):
        start = time.time()

        def note(state, found=0, detail=""):
            if trace is None:
                return
            with lock:
                trace.append({"engine": name, "state": state, "found": found,
                              "ms": int((time.time() - start) * 1000),
                              "detail": detail})

        if time.time() < _engine_down_until.get(name, 0):
            with lock:
                errors.append(f"{name}: 冷却中（刚被拦/连不上）")
            note("cooling", detail="冷却中，本次跳过")
            return
        try:
            found = engine(query, count)
        except EngineBlocked as e:
            with lock:
                errors.append(f"{name}: 被安全验证拦下，冷却 {ENGINE_DOWN_COOLDOWN}s")
            # 关键：被拦也要冷却。否则每一轮搜索都会再打一次这个已知被拦的源，
            # 白等几百毫秒到几秒，把 8 秒总预算耗光（实测中位延迟就是这么涨到 12s 的）。
            with _stats_lock:
                _engine_down_until[name] = time.time() + ENGINE_DOWN_COOLDOWN
            _note_engine(name, "blocked", int((time.time() - start) * 1000))
            logger.warning(f"搜索源 {name} 被拦下（{e}），{ENGINE_DOWN_COOLDOWN}s 内不再打它")
            note("blocked", detail=str(e))
            return
        except Exception as e:
            with lock:
                errors.append(f"{name}: {type(e).__name__}")
            # HTTP 层面的失败（超时/连不上）说明它现在是坏的，短时间别再打它
            with _stats_lock:
                _engine_down_until[name] = time.time() + ENGINE_ERROR_COOLDOWN
            _note_engine(name, "error", int((time.time() - start) * 1000))
            logger.debug(f"搜索源 {name} 失败: {e}")
            note("error", detail=f"{type(e).__name__}: {e}"[:120])
            return
        _note_engine(name, "ok" if found else "empty", int((time.time() - start) * 1000), len(found))
        note("ok" if found else "empty", len(found))
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


# ---------- 查询记录（方便事后排查"这次到底搜了什么、为什么是这个结果"）----------

QUERY_LOG_FILE = "search_queries.jsonl"   # 落盘位置：data/search_queries.jsonl（JSON Lines）
QUERY_LOG_KEEP = 300                       # 内存里保留最近多少条（供接口/巡检脚本查看）
QUERY_LOG_MAX_BYTES = 3 * 1024 * 1024      # 文件超过这个大小就滚动成 .1

_query_log: list = []                      # 最近若干条，新的在最后
_query_log_lock = threading.Lock()


def query_log_path() -> str:
    """查询记录文件路径（与 app.log 同目录；目录不可写时回退到系统临时目录）。"""
    try:
        from paths import DATA_DIR, ensure_writable_dir
        return os.path.join(ensure_writable_dir(DATA_DIR), QUERY_LOG_FILE)
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "ChatSight", QUERY_LOG_FILE)


def _rotate_query_log(path: str):
    """文件太大就滚一次（只留一份历史，够排查即可）。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > QUERY_LOG_MAX_BYTES:
            backup = path + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(path, backup)
    except OSError:
        pass


def _note_query(record: dict):
    """把一次搜索的完整过程记下来：写了什么、打了哪些源、留下了什么、丢了什么。

    只做"记录"这一件事，而且**任何写入失败都不允许影响搜索**——排查用的日志
    绝不该成为新的故障点，所以整个函数吞掉所有异常。
    """
    record["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    record["ts"] = round(time.time(), 3)
    with _query_log_lock:
        _query_log.append(record)
        if len(_query_log) > QUERY_LOG_KEEP:
            del _query_log[:-QUERY_LOG_KEEP]
    try:
        path = query_log_path()
        _rotate_query_log(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:      # noqa: BLE001 —— 排查日志不能反过来搞坏搜索
        logger.debug(f"写查询记录失败（不影响搜索）: {type(e).__name__}: {e}")


def recent_queries(limit: int = 20) -> list:
    """最近若干次搜索记录（新的在前），给接口和巡检脚本用。"""
    with _query_log_lock:
        items = list(_query_log)
    return list(reversed(items[-max(1, limit):]))


def clear_query_log() -> bool:
    """清空查询记录（内存 + 文件）。用于"清干净再复现一次问题"。"""
    with _query_log_lock:
        _query_log.clear()
    try:
        path = query_log_path()
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        return False


# ---------- 对外接口 ----------

def _try_official_name(query: str, count: int, deadline: float, variants: list):
    """搜不到时，按「地区 + 种类」推断官方展会名再搜一次。

    为什么值得存在（实测）：用户嘴里的说法和业界官方名常常对不上，而且**口语说法会一条都
    搜不到**——实测「香港灯展」「香港照明展览会」「香港灯光展」「香港文具展」「香港婴儿用品展」
    全部 0 条；换成官方名（香港国际秋季灯饰展 / 香港国际文具及学习用品展 …）就能命中官方页。
    这里只做"换用通用名称再检索"，不做任何事实推断——日期地点仍旧由检索结果给出。

    返回 (结果列表, 错误说明)；没有把握的建议或仍旧搜不到时返回 ([], 原因)。
    """
    try:
        from fair_aliases import suggest_official
    except Exception as e:                      # 词典缺失不该影响搜索
        logger.debug(f"加载展会别名词典失败: {e}")
        return [], ""

    suggestion = suggest_official(query)
    if not suggestion:
        return [], ""

    official = suggestion["official"]
    # 官方名已经和用户的说法一致（或用户本来就说的官方名）就不必再搜一遍
    if official in (query or "") or official in variants:
        return [], ""

    logger.info(f"搜索「{query}」无结果，按地区({suggestion['region']})+种类"
                f"({suggestion['category']})推断官方名「{official}」，再搜一次")
    try:
        alt = search(official, count=count, use_cache=False)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    results = alt.get("results") or []
    if not results:
        return [], alt.get("error", "")
    for item in results:
        item["_official_query"] = official
    return results, ""


def reset_health():
    """清空缓存与引擎统计（评测/自检用）。

    注意：批次冷却**不是**全局状态，它只活在单次 search() 里（见 search 内的
    _tier_empty_streak / _tier_cooling_until）。否则一次搜歪了的查询会把某个批次
    在接下来几分钟里彻底禁用，后面的查询全部跟着遭殃——旧版本按引擎冷却 5 分钟就是这个毛病。
    """
    _cache.clear()
    _engine_stats.clear()
    _engine_down_until.clear()


def search(query: str, count: int = DEFAULT_COUNT, use_cache: bool = True) -> dict:
    """搜索并返回统一结构，任何情况下都不抛异常。

    返回: {"query", "engine", "results": [{"title","snippet","url","engine","score"}], "error"}
    `engine` 是贡献结果最多的引擎；结果已按相关性排序并过滤掉不相关的。
    """
    query = clean_query(query)
    start_at = time.time()
    info = {"query": query, "query_used": "", "engine": "", "results": [], "error": "",
            "low_relevance": False, "suggestion": {}, "asked": ""}
    if not query:
        info["error"] = "搜索内容为空"
        return info

    if use_cache:
        cached = _cache.get(query)
        if cached and time.time() - cached[0] < CACHE_TTL:
            # 命中缓存也记一条：否则排查时会出现"用户说搜过、日志里没有"的困惑
            _note_query({"query": query, "query_used": cached[1].get("query_used", ""),
                         "variants": [query], "tiers_order": [], "tiers_tried": [], "engines": [],
                         "errors": [], "merged": 0, "returned": len(cached[1].get("results") or []),
                         "low_relevance": bool(cached[1].get("low_relevance")),
                         "status": "cached", "elapsed": 0.0,
                         "cost": {"count": count, "budget": TOTAL_BUDGET,
                                  "best_score": max((r.get("score", 0)
                                                     for r in cached[1].get("results") or []),
                                                    default=0)},
                         "results": [], "dropped": [], "overflow": [],
                         "error": cached[1].get("error", "")})
            return dict(cached[1], cached=True)

    deadline = time.time() + TOTAL_BUDGET
    merged_all, errors, results = [], [], []
    weak: list = []          # 相关性不够"可信"但也不是垃圾的结果，最后兜底用
    tier_names: set = set()
    # 英文查询换一套批次顺序（见 _ENGLISH_TIERS 的注释）
    tiers = _ENGLISH_TIERS if is_english_query(query) else ENGINE_TIERS
    # 批次冷却只活在这一次 search() 里（局部变量），不跨查询累积
    tier_empty_streak: dict = {}
    tier_cooling_until: dict = {}
    # 排查用：记下每个引擎、每个批次这一轮干了什么（见 _note_query）
    attempts: list = []
    tiers_tried: list = []

    def _tier_key(tier):
        return tuple(name for name, _ in tier)

    def _run_tier(tier, variant: str):
        """跑一批引擎，返回 (全部候选, 可信结果, 低相关候选)。"""
        merged, tier_errors = _gather(variant, tier, count * 2, deadline, trace=attempts)
        merged_all.extend(merged)
        errors.extend(tier_errors)
        scored = _score_and_filter(variant, merged)
        good = [item for item in scored if item["score"] >= MIN_RELEVANCE]
        # "泛指向页"不算搜到：实测搜「北京天气怎么样」时 bing_cn 给「北京市_百度百科」
        # 等泛指向页（0.36 分，过阈值），于是**换查询词重试这条路根本走不到**——
        # 而换成「北京天气」就立刻拿到真正的天气页（1.00 分）。
        # 所以只要"最相关的那条"是泛指向页、或可信结果里全是泛指向页，就当作没搜到。
        if good:
            generic_count = sum(1 for item in good if item.get("_generic"))
            if generic_count == len(good) or good[0].get("_generic"):
                logger.info(f"搜索「{query}」查询词「{variant}」只拿到泛指向页"
                            f"（{generic_count}/{len(good)} 条），当作没搜到")
                good = []
        low = [item for item in scored if FALLBACK_MIN <= item["score"] < MIN_RELEVANCE]
        tiers_tried.append({"tier": "/".join(name for name, _ in tier), "variant": variant,
                            "got": len(merged), "good": len(good), "low": len(low)})
        return scored, good, low

    # 先换查询词、再降级换源：同一批引擎里把"原样 / 去年份 / 去问句"都试一遍，
    # 都拿不到才去动更差的引擎。
    #
    # 为什么这个顺序重要（实测教训）：搜「北京天气怎么样」时，cn.bing 对带"怎么样"的
    # 查询词一律返回「北京市_百度百科」这类泛指向页；而查询词削成「北京天气」后**同一个
    # 引擎立刻给真天气页**。旧顺序是"先把这个查询词的 4 个批次全部打完、再换词"——
    # 结果预算被后面的差引擎耗光，换词那条路根本走不到，用户拿到 5 条百科垃圾。
    variants = query_variants(query)          # 只算一次：日志里要显示、循环里要用
    dropped_items: list = []                  # 被相对下限挡掉的（低质，排查用）
    overflow_items: list = []                 # 只是超出 count 名额的（不低质，排查用）
    results = []

    for tier in tiers:
        if time.time() >= deadline:
            break
        key = _tier_key(tier)
        if time.time() < tier_cooling_until.get(key, 0):
            continue
        tier_names.add("/".join(key))

        pool: list = []
        for variant in variants:
            if time.time() >= deadline:
                break
            scored, good, low = _run_tier(tier, variant)
            weak.extend(low)
            if good:
                tier_empty_streak[key] = 0
                logger.info(f"搜索「{query}」批次 {key} 用查询词「{variant}」"
                            f"拿到 {len(good)} 条可信结果")
                pool = scored
                info["query_used"] = variant
                break

        if not pool:
            # 这一批 + 所有查询词都没戏：连续两次空手就冷却，别在后面的批次上再浪费预算
            tier_empty_streak[key] = tier_empty_streak.get(key, 0) + 1
            if tier_empty_streak[key] >= 2:
                tier_cooling_until[key] = time.time() + COOLDOWN
            continue

        # 必须在这里截到 count 条：pool 是"整批打分排序后的候选"，
        # 不截断的话会把 10 多条（含 0.1 分的百科/攻略垃圾）全喂给模型——
        # 调用方按 count 申请 5 条却收到 14 条，模型上下文被噪声占满。
        pool = sorted(pool, key=lambda x: x.get("score", 0), reverse=True)
        # 别让"垫底凑数"的结果混进来：一批里已经有 1.00 分的好结果时，
        # 0.18 分的「北京 _ 百科」对它毫无价值，只会稀释模型的注意力。
        # 相对线（最高分的 55%）和绝对线（MIN_RELEVANCE）取高者。
        floor = max(MIN_RELEVANCE, pool[0].get("score", 0) * TAIL_RATIO)
        above_floor = [item for item in pool if item.get("score", 0) >= floor]
        kept = above_floor[:count]
        kept_urls = {item.get("url") for item in kept}

        def _brief(item):
            return {"title": item.get("title", ""), "url": item.get("url", ""),
                    "engine": item.get("engine", ""), "score": item.get("score", 0)}

        # 区分两种"没进去"：分数太低 vs 只是名额满了——排查时这两件事完全不同
        dropped_items = [_brief(i) for i in pool
                         if i.get("score", 0) < floor and i.get("url") not in kept_urls]
        overflow_items = [_brief(i) for i in above_floor if i.get("url") not in kept_urls]
        results = _resolve_results(_dedupe(kept))
        if results:
            break

    # 两条兜底，顺序是"先纠正说法、再退回弱证据"：
    #
    # 1) 尝试"把话说对"：按「地区 + 种类」推断官方展会名再搜一次。
    #    实测这类说法确实搜不到或只能搜到泛页：香港灯展 / 香港照明展览会 / 香港灯光展 /
    #    香港文具展；换成官方名（香港国际秋季灯饰展 / 香港国际文具及学习用品展 …）
    #    立刻命中官方页。这不是"猜答案"，是换成业界通用名称再检索。
    #    —— 比"退回低相关泛页"更好，所以优先做它。
    # 2) 仍不行才退回"擦边"候选（≥FALLBACK_MIN，最多 3 条）交给模型当线索。
    #
    # 只在"原说法没搜到可信结果"时才做这件事：实测口语说法（香港照明展、香港玩具展）
    # 本身就能搜到官方页，那种情况不该再搜一遍、也不该改用户的说法。
    if not results or info["low_relevance"]:
        try:
            from fair_aliases import suggest_official
            suggestion = suggest_official(query)
        except Exception:
            suggestion = {}
        if suggestion:
            alt, alt_error = _try_official_name(query, count, deadline, variants)
            if alt:
                info["suggestion"] = suggestion
                info["asked"] = query     # 用户原本的说法，供"我没找到关于「X」的内容"使用
                results = alt
                info["query"] = alt[0].get("_official_query", "")
                info["query_used"] = info["query"]
                info["engine"] = max(results, key=lambda x: x["score"])["engine"]
                info["low_relevance"] = False
                info["error"] = (f"没有检索到关于「{query}」的内容；"
                                 f"已按官方名称「{info['query']}」为你检索")
                logger.info(f"搜索「{query}」改按官方名「{info['query']}」检索，得到 "
                            f"{len(results)} 条")
            elif alt_error:
                logger.debug(f"官方名兜底也没搜到：「{query}」-> {alt_error}")

    # 只接受"擦边"（≥FALLBACK_MIN）的候选，最多 3 条；**再低就宁可返回空**。
    # 实测教训：为了"不空手"把 0.12 分的候选（搜灯饰展时的「2026年放假安排」、
    # 搜中轴线时的「北京 _ 百科」）塞进上下文，模型会把它们当资料照抄，
    # 用户完全看不出这条信息是垃圾——这比明确回答"没查到"危害大得多。
    if not results and weak:
        results = _resolve_results(_dedupe(sorted(weak, key=lambda x: x["score"], reverse=True))[:3])
        if results:
            info["engine"] = results[0]["engine"]
            info["results"] = results
            info["low_relevance"] = True
            info["query_used"] = query      # 兜底路径用的是原查询词，别让界面显示成空
            info["error"] = f"未找到高相关结果，以下 {len(results)} 条相关性较低，仅供参考"
            logger.info(f"搜索「{query}」没有高相关结果，退回 {len(results)} 条低相关参考"
                        f"（最高分 {results[0]['score']}）")

    if not results:
        dropped = len(merged_all)
        tried = len(variants)
        info["error"] = (f"没有找到相关结果（试了 {tried} 种查询词、{len(tier_names)} 个批次，"
                         f"{dropped} 条都被判为不相关）"
                         if dropped else "；".join(errors) or "没有可用的搜索源")
        logger.warning(f"搜索「{query}」没有可用结果：{info['error']}")

    if results:
        best = max(results, key=lambda x: x["score"])
        info["engine"] = best["engine"]
        info["results"] = results
        logger.info(f"搜索「{query}」（实际用「{info['query_used']}」）合并 {len(merged_all)} 条，"
                    f"过滤后 {len(results)} 条（最相关来自 {info['engine']}，分数 {best['score']}）")

    # 落一条查询记录：这是排查问题时唯一能回答"当时到底发生了什么"的东西
    _note_query({
        "query": query,
        "query_used": info["query_used"],
        "suggested_official": (info.get("suggestion") or {}).get("official", ""),
        "variants": variants,
        "tiers_order": [[name for name, _ in tier] for tier in tiers],
        "tiers_tried": tiers_tried,
        "engines": attempts,
        "errors": errors[:20],
        "merged": len(merged_all),
        "returned": len(results),
        "low_relevance": info["low_relevance"],
        "status": ("ok" if results and not info["low_relevance"]
                   else ("low_relevance" if results else "empty")),
        "elapsed": round(time.time() - start_at, 2),
        "cost": {"count": count, "budget": TOTAL_BUDGET,
                 "best_score": max((r.get("score", 0) for r in results), default=0)},
        "results": [{"rank": i, "title": r.get("title", ""), "url": r.get("url", ""),
                     "site": site_of(r.get("url", "")), "engine": r.get("engine", ""),
                     "score": r.get("score", 0)}
                    for i, r in enumerate(results, 1)],
        "dropped": dropped_items[:10],
        "overflow": overflow_items[:10],
        "error": info["error"],
    })

    _cache[query] = (time.time(), info)
    return info


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
