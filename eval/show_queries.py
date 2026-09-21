"""查看联网搜索的后台查询记录（data/search_queries.jsonl）。

排查"为什么这次搜出来是这些东西"时用它：每条记录都写明了
**用了哪个查询词、打了哪些源、每个源什么状态、留下了什么、丢了什么**。

用法：
    py -3 eval/show_queries.py                 # 最近 10 条概览
    py -3 eval/show_queries.py --limit 30
    py -3 eval/show_queries.py --detail        # 每条展开：引擎状态 + 结果
    py -3 eval/show_queries.py --detail --only 灯饰展   # 只看包含该关键字的查询
    py -3 eval/show_queries.py --problems      # 只列"有问题"的（空结果/低相关/很慢/引擎全挂）
    py -3 eval/show_queries.py --clear         # 清空记录（清干净再复现问题）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STATE_LABEL = {"ok": "正常", "empty": "无结果", "blocked": "被拦", "error": "失败",
               "cooling": "冷却跳过", "cached": "命中缓存", "low_relevance": "低相关兜底"}


def log_path() -> str:
    try:
        import web_search
        return web_search.query_log_path()
    except Exception:
        return os.path.join(ROOT, "data", "search_queries.jsonl")


def load_records() -> list:
    path = log_path()
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue        # 写到一半断电之类留下的残行，跳过即可
    return records


def is_problem(record: dict) -> bool:
    if not record.get("returned"):
        return True
    if record.get("status") == "low_relevance":
        return True
    if (record.get("elapsed") or 0) >= 6:
        return True
    states = [e.get("state") for e in record.get("engines") or []]
    if states and all(s in ("blocked", "error", "empty", "cooling") for s in states):
        return True
    return False


def engine_line(record: dict) -> str:
    engines = record.get("engines") or []
    if not engines:
        return "(无引擎调用)"
    parts = []
    for e in engines:
        label = STATE_LABEL.get(e.get("state"), e.get("state"))
        parts.append(f"{e.get('engine')}={label}({e.get('found', 0)}条/{e.get('ms', 0)}ms)")
    return "  ".join(parts)


def show(record: dict, detail: bool):
    status = STATE_LABEL.get(record.get("status"), record.get("status"))
    query_used = record.get("query_used") or record.get("query")
    print(f"[{record.get('time', '?')}] {record.get('query', '')!r}")
    print(f"    状态={status}  实际查询词={query_used!r}  "
          f"合并{record.get('merged', 0)}条 → 返回{record.get('returned', 0)}条  "
          f"用时{record.get('elapsed', 0)}s")
    print(f"    引擎: {engine_line(record)}")
    if record.get("error"):
        print(f"    提示: {record['error']}")
    if detail:
        variants = record.get("variants") or []
        if len(variants) > 1:
            print(f"    候选查询词: {variants}")
        for tier in record.get("tiers_tried") or []:
            print(f"    批次 {tier.get('tier'):26s} 查出{tier.get('got', 0):3d}条 "
                  f"可信{tier.get('good', 0):2d} 低相关{tier.get('low', 0):2d}")
        for item in record.get("results") or []:
            print(f"    #{item.get('rank')} [{item.get('score', 0):.2f}] "
                  f"[{item.get('engine', ''):10s}] {item.get('site', ''):22s} "
                  f"{item.get('title', '')[:52]}")
        for item in (record.get("dropped") or [])[:3]:
            print(f"    ✗丢弃(分低) [{item.get('score', 0):.2f}] {item.get('title', '')[:56]}")
        for item in (record.get("overflow") or [])[:3]:
            print(f"    ·超出名额 [{item.get('score', 0):.2f}] {item.get('title', '')[:56]}")
        if record.get("status") == "cached":
            print("    (命中 5 分钟缓存，未重新联网；要看完整过程请让它过期或先 --clear)")
    print()


def main():
    argv = sys.argv[1:]
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 10
    detail = "--detail" in argv
    only = argv[argv.index("--only") + 1] if "--only" in argv else None

    path = log_path()
    print(f"查询记录: {path}")
    if "--clear" in argv:
        try:
            import web_search
            ok = web_search.clear_query_log()
        except Exception:
            try:
                os.remove(path)
                ok = True
            except OSError:
                ok = False
        print("已清空" if ok else "清空失败（文件可能正被占用）")
        return

    records = load_records()
    if only:
        records = [r for r in records if only in (r.get("query") or "")]
    if "--problems" in argv:
        records = [r for r in records if is_problem(r)]
        print(f"（只显示有问题的记录）")

    if not records:
        print("暂无记录。发起一次联网搜索后这里就有内容了。")
        return

    print(f"共 {len(records)} 条，显示最近 {min(limit, len(records))} 条：\n")
    for record in reversed(records[-limit:]):
        show(record, detail)

    # 汇总一下，方便一眼看出"这一段到底是哪个源在拖后腿"
    stat = {}
    for record in records[-limit:]:
        for e in record.get("engines") or []:
            entry = stat.setdefault(e.get("engine"), {"ok": 0, "bad": 0, "ms": []})
            if e.get("state") == "ok" and e.get("found"):
                entry["ok"] += 1
            elif e.get("state") in ("blocked", "error"):
                entry["bad"] += 1
            if e.get("ms"):
                entry["ms"].append(e["ms"])
    if stat:
        print("引擎汇总（本区间）:")
        for name, entry in sorted(stat.items(), key=lambda x: -(x[1]["ok"] + x[1]["bad"])):
            times = entry["ms"] or [0]
            print(f"    {name:11s} 出结果 {entry['ok']:3d} 次  被拦/失败 {entry['bad']:3d} 次  "
                  f"平均 {sum(times) // len(times):5d}ms")


if __name__ == "__main__":
    main()
