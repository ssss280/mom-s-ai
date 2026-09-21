"""联网搜索评测骨架：随机抽题 -> 跑真实 search() -> 客观评分 -> 落盘。

设计要点（为什么这么做）：
- **随机测试集可复现**：题库在 cases_bank.json，用 --seed 抽子集，同一 seed 抽出的题目完全一致，
  这样"优化前 / 优化后"跑的是同一批题，分数才有可比性。
- **评分不靠感觉**：每个用例带 expected_terms（客观事实关键词分组），
  再加上"链接是否真能打开""正文是否能抓到""延迟"这些可测量指标。
- **不污染题库**：题库只读，测试结果写进 eval/runs/<时间戳>/，两次运行的产物各自独立。

用法：
    py -3 eval/harness.py --seed 20260101 --cases 24 --run-name baseline
    py -3 eval/harness.py --seed 20260101 --cases 24 --run-name after-fix --all
"""
import argparse
import importlib.util
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BANK = os.path.join(HERE, "cases_bank.json")
RUNS_DIR = os.path.join(HERE, "runs")

# 延迟预算：search() 自身 TOTAL_BUDGET=12s，留出跳转还原/抓正文的余量
LATENCY_BUDGET_P50 = 5.0
LATENCY_BUDGET_P90 = 11.0
LATENCY_HARD = 20.0

FILLER_MARKERS = re.compile(
    r"(点击查看|查看更多|广告|登录|注册|首页|导航|下载APP|扫码|关注我们|版权所有)", re.I)

# 结果的"来源等级"：批次越靠前越可信（顺序由 eval/engine_eval.py 实测决定）
TIER_OF_ENGINE = {}
for _tier_index, _tier in enumerate((("so360", "bing_cn"),
                                     ("bing_web", "bing_news"),
                                     ("duckduckgo", "wikipedia"),
                                     ("sogou", "baidu"))):
    for _name in _tier:
        TIER_OF_ENGINE[_name] = 2 if _tier_index == 0 else (1 if _tier_index == 1 else 0)


def load_module():
    """按文件路径加载 web_search，保证测的就是仓库里这一份代码。"""
    path = os.path.join(ROOT, "web_search.py")
    spec = importlib.util.spec_from_file_location("ws_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ws_under_test"] = module
    spec.loader.exec_module(module)
    return module


def reset_state(ws):
    """清空缓存、引擎统计与批次冷却，否则第 1 题被拦会让后面所有题都受影响，分数不可比。"""
    if hasattr(ws, "reset_health"):
        ws.reset_health()
    else:
        ws._cache.clear()
        ws._blocked_until.clear()


def load_bank():
    with open(BANK, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def build_testset(seed: int, count: int):
    """随机抽题。追问用例固定纳入（它们测的是上下文拼接，不该被随机漏掉）。"""
    cases = load_bank()
    rng = random.Random(seed)
    followups = [c for c in cases if c["category"] == "edge-followup"]
    others = [c for c in cases if c["category"] != "edge-followup"]
    rng.shuffle(others)
    picked = followups + others[: max(0, count - len(followups))]
    order = list(range(len(picked)))
    rng.shuffle(order)
    return [picked[i] for i in order]


def parse_expectations(case: dict) -> list:
    """把 "分组1|分组2;分组3" 解析成 [[同义词...], [同义词...]]。"""
    groups = []
    for group in (case.get("expected_terms") or "").split(";"):
        options = [opt.strip().lower() for opt in group.split("|") if opt.strip()]
        if options:
            groups.append(options)
    return groups


def grade_terms(groups: list, text: str) -> dict:
    """统计命中了多少个分组，以及每组是在哪些结果里命中的。"""
    text = (text or "").lower()
    hit_groups, detail = 0, []
    for options in groups:
        matched = [opt for opt in options if opt in text]
        if matched:
            hit_groups += 1
        detail.append(matched)
    return {"hit": hit_groups, "total": len(groups), "detail": detail}


def check_links(urls: list, timeout: int = 6, limit: int = 40) -> list:
    """检测链接是否真能打开（搜索最致命的失败是给模型一堆死链）。"""
    targets = list(dict.fromkeys([u for u in urls if u and u.startswith("http")]))[:limit]
    out, lock = [], threading.Lock()

    def worker(url):
        start = time.time()
        record = {"url": url, "status": 0, "kind": "", "error": ""}
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                record["status"] = response.status
                record["kind"] = (response.headers.get("Content-Type") or "")[:40]
        except urllib.error.HTTPError as e:
            record["status"] = e.code
            record["error"] = f"HTTP {e.code}"
        except Exception as e:
            record["error"] = type(e).__name__
        record["ms"] = round((time.time() - start) * 1000)
        with lock:
            out.append(record)

    threads = [threading.Thread(target=worker, args=(u,), daemon=True) for u in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout + 2)
    return sorted(out, key=lambda x: x["url"])


def run_case(ws, case: dict, count: int = 5, pages_count: int = 1) -> dict:
    """跑一个用例：真实调用 search()，再对结果做客观测量。

    默认值刻意对齐**线上真实配置**（server.py：`SEARCH_COUNT=5`，
    `search_read_pages` 打开时抓 1 篇正文）——之前用 count=6 / 抓 3 篇打分，
    量到的是比线上更宽松的条件，分数会虚高。
    """
    question = case["question"]
    history = case.get("history") or []
    query = ws.build_query(question, history) if history else ws.clean_query(question)

    start = time.time()
    info = ws.search(query, count=count)
    latency = time.time() - start

    groups = parse_expectations(case)
    results = info.get("results") or []

    # 判据只认"呈现给模型的东西"：标题 + 摘要 + 抓到的正文
    snippets_text = " \n".join(f"{r.get('title','')} {r.get('snippet','')}" for r in results)
    pages = {}
    if results and pages_count > 0:
        pages = ws.fetch_pages([r["url"] for r in results], count=pages_count)
    pages_text = " \n".join(pages.values())
    full_text = snippets_text + " \n" + pages_text

    snippet_grade = grade_terms(groups, snippets_text)
    full_grade = grade_terms(groups, full_text)

    hosts = [ws.site_of(r.get("url", "")) for r in results]
    expected_domains = case.get("expected_domains") or []
    domain_hit = any(any(dom in host for host in hosts) for dom in expected_domains) \
        if expected_domains else None

    min_relevance = getattr(ws, "MIN_RELEVANCE", 0.28)
    good = [r for r in results if r.get("score", 0) >= min_relevance]

    return {
        "id": case["id"],
        "category": case["category"],
        "question": question,
        "history": history,
        "query": query,
        "query_used": info.get("query_used", ""),
        "expected_terms": case.get("expected_terms", ""),
        "expected_domains": expected_domains,
        "wrong_premise": bool(case.get("wrong_premise")),
        "retrieved": len(results),
        "good_count": len(good),
        "weak_count": len(results) - len(good),
        "engines": sorted({r.get("engine", "") for r in results}),
        "tier_score": max([TIER_OF_ENGINE.get(r.get("engine", ""), 0) for r in results] or [0]),
        "engine": info.get("engine", ""),
        "low_relevance": bool(info.get("low_relevance")),
        "error": info.get("error", ""),
        "latency": round(latency, 2),
        "snippet_grade": snippet_grade,
        "full_grade": full_grade,
        "domain_hit": domain_hit,
        "hosts": hosts,
        "pages_fetched": len(pages),
        "page_chars": sum(len(t) for t in pages.values()),
        "results": [
            {"rank": i, "title": r.get("title", ""), "url": r.get("url", ""),
             "site": ws.site_of(r.get("url", "")), "score": r.get("score", 0),
             "engine": r.get("engine", ""), "snippet": (r.get("snippet") or "")[:200]}
            for i, r in enumerate(results, 1)
        ],
    }


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((p / 100) * len(values) + 0.5)) - 1))
    return values[index]


def summarize(records: list, links: list) -> dict:
    total = len(records) or 1
    retrieved = [r["retrieved"] for r in records]
    latencies = [r["latency"] for r in records]

    empty = [r for r in records if r["retrieved"] == 0]
    low_rel = [r for r in records if r["low_relevance"]]
    weak = [r for r in records if r["retrieved"] and r["retrieved"] < 3]

    strict = [r for r in records
              if r["full_grade"]["total"] and r["full_grade"]["hit"] == r["full_grade"]["total"]]
    loose = [r for r in records
             if r["full_grade"]["hit"] >= max(1, r["full_grade"]["total"] - 1)]

    with_domain = [r for r in records if r["expected_domains"]]
    domain_ok = [r for r in with_domain if r["domain_hit"]]

    coverage = [r["full_grade"]["hit"] / r["full_grade"]["total"] for r in records if r["full_grade"]["total"]]

    engine_share = {}
    for r in records:
        if r["engine"]:
            engine_share[r["engine"]] = engine_share.get(r["engine"], 0) + 1

    page_runs = [r for r in records if r["retrieved"]]
    page_ok = [r for r in page_runs if r["pages_fetched"] > 0]

    alive = [l for l in links if 200 <= l["status"] < 400]
    dead = [l for l in links if l["status"] and not (200 <= l["status"] < 400)]
    unknown = [l for l in links if not l["status"]]

    # 相关性校准：模块自己的 score 与实际关键词命中数是否一致
    pairs = []
    for r in records:
        if not r["results"]:
            continue
        for item in r["results"]:
            text = (item["title"] + " " + item["snippet"]).lower()
            groups = parse_expectations(r)
            hits = sum(1 for opts in groups if any(o in text for o in opts))
            pairs.append((item["score"], hits))
    top_correct = [p for p in pairs if p[1] > 0]
    top_score_mean = statistics.mean([p[0] for p in top_correct]) if top_correct else 0.0

    good_counts = [r.get("good_count", 0) for r in records]
    tier_scores = [r.get("tier_score", 0) for r in records]
    all_hosts = [h for r in records for h in r.get("hosts", [])]
    top1_grades = []
    for r in records:
        if not r["results"]:
            continue
        top = r["results"][0]
        text = (top["title"] + " " + top["snippet"]).lower()
        groups = parse_expectations(r)
        top1_grades.append(1 if groups and any(any(o in text for o in opts) for opts in groups) else 0)

    metrics = {
        "cases": len(records),
        "no_result_rate": round(len(empty) / total, 3),
        "low_relevance_rate": round(len(low_rel) / total, 3),
        "thin_result_rate": round(len(weak) / total, 3),
        "hit_rate_strict": round(len(strict) / total, 3),
        "hit_rate_loose": round(len(loose) / total, 3),
        "mean_term_coverage": round(statistics.mean(coverage), 3) if coverage else 0.0,
        "domain_hit_rate": round(len(domain_ok) / len(with_domain), 3) if with_domain else None,
        "avg_results": round(statistics.mean(retrieved), 2),
        "avg_good_results": round(statistics.mean(good_counts), 2),
        "thin_after_fix_rate": round(len([c for c in good_counts if c < 3]) / total, 3),
        "tier1_rate": round(len([t for t in tier_scores if t == 2]) / total, 3),
        "tier2_rate": round(len([t for t in tier_scores if t == 1]) / total, 3),
        "distinct_domains": len(set(all_hosts)),
        "top1_hit_rate": round(statistics.mean(top1_grades), 3) if top1_grades else None,
        "latency_p50": round(pct(latencies, 50), 2),
        "latency_p90": round(pct(latencies, 90), 2),
        "latency_max": round(max(latencies), 2) if latencies else 0.0,
        "over_budget_rate": round(len([x for x in latencies if x > LATENCY_HARD]) / total, 3),
        "link_checked": len(links),
        "link_alive_rate": round(len(alive) / len(links), 3) if links else None,
        "link_dead_count": len(dead),
        "link_unknown_count": len(unknown),
        "page_fetch_success_rate": round(len(page_ok) / len(page_runs), 3) if page_runs else None,
        "engine_share": engine_share,
        "score_keyword_consistency": round(top_score_mean, 3),
    }
    return metrics


def composite(metrics: dict) -> dict:
    """把多维指标折成一个 0~100 的总分，并给出各维度得分（便于看是哪里拖后腿）。"""
    def clamp(value):
        return max(0.0, min(1.0, value))

    s_hit = clamp(metrics["hit_rate_strict"] * 0.7 + metrics["hit_rate_loose"] * 0.3)
    s_empty = clamp(1 - metrics["no_result_rate"])
    s_link = clamp(metrics["link_alive_rate"] if metrics["link_alive_rate"] is not None else 1.0)
    s_page = clamp(metrics["page_fetch_success_rate"] if metrics["page_fetch_success_rate"] is not None else 1.0)

    p50, p90 = metrics["latency_p50"], metrics["latency_p90"]
    s_lat = clamp(1 - (max(0, p50 - LATENCY_BUDGET_P50) / LATENCY_BUDGET_P50) * 0.5
                  - (max(0, p90 - LATENCY_BUDGET_P90) / LATENCY_BUDGET_P90) * 0.5)

    share = metrics["engine_share"] or {}
    total = sum(share.values()) or 1
    dom = max(share.values()) / total if share else 1.0
    s_engine = clamp(1 - max(0, dom - 0.5) * 2)  # 单一引擎占比 50% 以内满分

    parts = {
        "命中正确性": round(s_hit * 35, 1),
        "非空率": round(s_empty * 20, 1),
        "链接有效性": round(s_link * 15, 1),
        "正文可读": round(s_page * 10, 1),
        "延迟": round(s_lat * 10, 1),
        "引擎均衡": round(s_engine * 10, 1),
    }
    return {"total": round(sum(parts.values()), 1), "parts": parts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--cases", type=int, default=24)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--check-links", action="store_true")
    parser.add_argument("--all", action="store_true", help="跑完整题库（忽略 --cases）")
    parser.add_argument("--count", type=int, default=5,
                        help="search() 取几条结果（线上 server.py 是 5）")
    parser.add_argument("--pages", type=int, default=1,
                        help="抓几篇正文喂给模型（线上开启读取正文时是 1；0 = 不抓）")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ws = load_module()
    # 整轮开始前清一次即可：批次冷却现在只活在单次 search() 里（局部变量），
    # 不会跨题污染；每道题之间反复清反而会把引擎统计清零，看不出"哪个源还能用"。
    reset_state(ws)

    if args.all:
        cases = list(load_bank())
        random.Random(args.seed).shuffle(cases)
    else:
        cases = build_testset(args.seed, args.cases)

    run_name = args.run_name or time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(RUNS_DIR, run_name)
    os.makedirs(out_dir, exist_ok=True)

    print(f"测试集: seed={args.seed} 题目 {len(cases)} 道 -> {out_dir}")
    print(f"线上对齐参数: count={args.count} 抓正文={args.pages} 篇")
    records = []
    for index, case in enumerate(cases, 1):
        record = run_case(ws, case, count=args.count, pages_count=args.pages)
        records.append(record)
        covered = f"{record['full_grade']['hit']}/{record['full_grade']['total']}"
        print(f"  [{index:2d}/{len(cases)}] {record['id']:28s} "
              f"结果{record['retrieved']:2d} 判据{covered} "
              f"{record['latency']:5.1f}s 引擎={record['engine'] or '-'} "
              f"实际查询词={record['query_used'] or record['query']!r}")
        sys.stdout.flush()

    links = check_links([r["url"] for rec in records for r in rec["results"]]) if args.check_links else []
    metrics = summarize(records, links)
    scores = composite(metrics)
    engines = ws.engine_stats() if hasattr(ws, "engine_stats") else {}
    metrics["engine_health"] = engines

    with open(os.path.join(out_dir, "testset.json"), "w", encoding="utf-8") as fh:
        json.dump({"seed": args.seed, "cases": cases}, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "records.json"), "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "links.json"), "w", encoding="utf-8") as fh:
        json.dump(links, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump({"metrics": metrics, "scores": scores, "run": run_name,
                   "seed": args.seed, "cases": len(cases),
                   "config": {"count": args.count, "pages": args.pages}},
                  fh, ensure_ascii=False, indent=2)

    print("\n=== 指标 ===")
    for key, value in metrics.items():
        if key != "engine_health":
            print(f"  {key}: {value}")
    print("\n=== 引擎健康 ===")
    for name, stat in (engines or {}).items():
        print(f"  {name:11s} 成功{stat['ok']:3d} 空{stat['empty']:3d} 被拦{stat['blocked']:3d} "
              f"失败{stat['error']:3d} 累计结果{stat['found']:4d} 平均{stat['avg_ms']:5d}ms")
    print("\n=== 总分 ===")
    for key, value in scores["parts"].items():
        print(f"  {key}: {value}")
    print(f"  总分: {scores['total']} / 100")


if __name__ == "__main__":
    main()
