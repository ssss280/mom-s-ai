"""构建带标注的相关性数据集：给打分函数调参提供 ground truth。

标注规则（可复现、可审查）：
- 正样本：一条结果同时命中该题的「实体分组（第 1 组）」以及至少一个其他分组 → relevant=1
- 负样本：来自明显跑题的引擎（www.bing.com 的诱饵页）→ relevant=0
- 其余：relevant=-1（不计入指标）

这样得到的标签不依赖主观判断，任何一次调参都跑同一份数据集。
"""
import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\ai助手"
sys.path.insert(0, ROOT)
import web_search as ws  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
OUT = os.path.join(ROOT, "eval", "tuning", "labeled.json")


def fetch(url, timeout=10, headers=None):
    base = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    base.update(headers or {})
    request = urllib.request.Request(url, headers=base)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def parse_items(page, kind):
    items = []
    if kind == "bing":
        for block in re.split(r'<li class="b_algo', page)[1:]:
            m = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
            if not m:
                continue
            s = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
            items.append({"title": ws._text(m.group(2)),
                          "url": ws._unwrap_bing(ws._clean_url(m.group(1))),
                          "snippet": ws._text(s.group(1)) if s else ""})
    elif kind == "rss":
        import html as _html
        for it in re.findall(r"<item>(.*?)</item>", page, re.S):
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
            l = re.search(r"<link>(.*?)</link>", it, re.S)
            d = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", it, re.S)
            url = ws._clean_url(l.group(1)) if l else ""
            if "bing.com/news/apiclick.aspx" in url:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(_html.unescape(url)).query)
                url = (params.get("url") or [url])[0]
            items.append({"title": ws._text(t.group(1)) if t else "", "url": url,
                          "snippet": ws._text(_html.unescape(d.group(1))) if d else ""})
    return items


def label(case, item, kind):
    groups = [[o.strip().lower() for o in g.split("|") if o.strip()]
              for g in case["expected_terms"].split(";")]
    text = (item["title"] + " " + item["snippet"]).lower()
    if kind == "decoy":
        return 0
    if not groups:
        return -1
    entity_hit = any(o in text for o in groups[0])
    others = sum(1 for g in groups[1:] if any(o in text for o in g))
    if entity_hit and (others >= 1 or len(groups) == 1):
        return 1
    if entity_hit or others:
        return -1
    return 0


def main():
    bank = json.load(open(os.path.join(ROOT, "eval", "cases_bank.json"), encoding="utf-8"))["cases"]
    rows = []
    for case in bank:
        query = case["question"]
        for kind, url in (
            ("bing", "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&mkt=zh-CN&setlang=zh-CN"),
            ("rss", "https://www.bing.com/news/search?q=" + urllib.parse.quote(query) + "&format=RSS"),
            ("decoy", "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=zh-CN&count=20"),
        ):
            try:
                items = parse_items(fetch(url), kind)
            except Exception as e:
                print(f"  {case['id'][:24]:24s} {kind}: ERR {type(e).__name__}")
                continue
            for item in items:
                row = dict(item)
                row.update({"case_id": case["id"], "query": query, "source": kind,
                            "relevant": label(case, item, kind)})
                rows.append(row)
            time.sleep(0.2)
        print(f"  {case['id'][:26]:26s} 累计 {len(rows)} 条")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)

    pos = sum(1 for r in rows if r["relevant"] == 1)
    neg = sum(1 for r in rows if r["relevant"] == 0)
    skip = sum(1 for r in rows if r["relevant"] == -1)
    print(f"\n数据集: {len(rows)} 条 → 正样本 {pos} / 负样本 {neg} / 不参与 {skip}")
    print(f"落盘: {OUT}")


if __name__ == "__main__":
    main()
