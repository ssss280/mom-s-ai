"""快速引擎评测：只打分"响应快"的源，顺序执行、带间隔。

维基/DDG 当前不可达（每次 5s 超时），先用超时砍掉它们，避免整轮跑十几分钟。

用法: py -3 eval/engine_eval.py --fast [--repeat 1] [--gap 0.2]
"""
import importlib.util
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = os.path.join(ROOT, "eval", "cases_bank.json")
OUT = os.path.join(ROOT, "eval", "engine_eval.json")

# 只测这些源（快速轮）；维基/DDG 单独用 --slow 轮
FAST = ("bing_cn", "bing_news", "bing_web", "so360", "sogou", "baidu")


def load_module():
    path = os.path.join(ROOT, "web_search.py")
    spec = importlib.util.spec_from_file_location("ws_engine_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ws_engine_eval"] = module
    spec.loader.exec_module(module)
    return module


def groups_of(case):
    return [[o.strip().lower() for o in g.split("|") if o.strip()]
            for g in case["expected_terms"].split(";")]


def usefulness(groups, item):
    text = ((item.get("title") or "") + " " + (item.get("snippet") or "")).lower()
    hits = sum(1 for g in groups if any(o in text for o in g))
    total = len(groups)
    return hits, total, (hits >= max(1, total - 1)), (hits == total and total > 0)


def main():
    repeat = 1
    gap = 0.2
    if "--repeat" in sys.argv:
        repeat = int(sys.argv[sys.argv.index("--repeat") + 1])
    if "--gap" in sys.argv:
        gap = float(sys.argv[sys.argv.index("--gap") + 1])

    ws = load_module()
    bank = json.load(open(BANK, encoding="utf-8"))["cases"]
    engines = [(n, f) for n, f in ws.ENGINES if n in FAST]

    stats = {name: {"calls": 0, "ok": 0, "blocked": 0, "error": 0, "empty": 0,
                    "raw": 0, "loose": 0, "strict": 0, "ms": [], "queries_with_hit": 0,
                    "hit_ids": []}
             for name, _ in engines}

    for round_index in range(repeat):
        for case in bank:
            query = ws.build_query(case["question"], case.get("history") or [])
            groups = groups_of(case)
            for name, fn in engines:
                start = time.time()
                state = "ok"
                try:
                    items = fn(query, 8)
                except Exception as e:
                    state = "blocked" if type(e).__name__ == "EngineBlocked" else "error"
                    items = []
                if not items and state == "ok":
                    state = "empty"
                loose = strict = 0
                for item in items:
                    _h, _t, is_loose, is_strict = usefulness(groups, item)
                    loose += 1 if is_loose else 0
                    strict += 1 if is_strict else 0
                entry = stats[name]
                entry["calls"] += 1
                entry[state] += 1
                entry["raw"] += len(items)
                entry["loose"] += loose
                entry["strict"] += strict
                entry["ms"].append((time.time() - start) * 1000)
                if loose:
                    entry["queries_with_hit"] += 1
                    if case["id"] not in entry["hit_ids"]:
                        entry["hit_ids"].append(case["id"])
                time.sleep(gap)
        print(f"  第 {round_index + 1}/{repeat} 轮完成")

    total_queries = len(bank) * repeat
    print(f"\n共 {total_queries} 次查询 × {len(engines)} 个源（顺序执行，间隔 {gap}s）\n")
    print(f"{'引擎':13s} {'成功':>5s} {'空':>4s} {'被拦':>5s} {'失败':>5s} {'原始':>6s} "
          f"{'宽松':>6s} {'严格':>6s} {'有效题':>7s} {'延迟P50':>8s}")
    print("-" * 78)
    ranking = []
    for name, entry in stats.items():
        calls = entry["calls"] or 1
        p50 = statistics.median(entry["ms"]) if entry["ms"] else 0
        print(f"{name:13s} {entry['ok']:>5d} {entry['empty']:>4d} {entry['blocked']:>5d} "
              f"{entry['error']:>5d} {entry['raw']:>6d} {entry['loose']:>6d} {entry['strict']:>6d} "
              f"{entry['queries_with_hit']:>7d} {p50:>7.0f}ms")
        ranking.append((name, entry["queries_with_hit"] / total_queries,
                        entry["loose"] / calls, entry["loose"], p50, entry["blocked"]))

    print("\n按「有贡献的查询比例」排名：")
    for name, rate, per_call, loose_total, p50, blocked in sorted(ranking, key=lambda x: -x[1]):
        print(f"  {name:13s} 贡献 {rate:>4.0%}  每次宽松达标 {per_call:.2f} 条  累计 {loose_total:>3d} 条  "
              f"被拦 {blocked:>2d}/{total_queries}  延迟 {p50:.0f}ms")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"queries": total_queries, "gap": gap, "engines": list(FAST), "stats": stats,
                   "ranking": [{"engine": n, "hit_query_rate": r, "loose_per_call": p,
                                "loose_total": t, "p50_ms": m, "blocked": b}
                               for n, r, p, t, m, b in sorted(ranking, key=lambda x: -x[1])]},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n落盘: {OUT}")


if __name__ == "__main__":
    main()
