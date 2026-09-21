"""搜索源健康巡检：哪个源现在还能用、平均多快、是不是被安全验证拦了。

引擎被反爬是常态，靠猜排序不如直接量。用法：

    py -3 eval/check_engines.py                # 用标准探针查询跑一遍
    py -3 eval/check_engines.py "查询词1" "查询词2"
"""
import importlib.util
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBES = ["香港国际秋季灯饰展 2026", "北京天气", "python asyncio gather vs wait", "iPhone 17 价格"]


def load_module():
    path = os.path.join(ROOT, "web_search.py")
    spec = importlib.util.spec_from_file_location("ws_health", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ws_health"] = module
    spec.loader.exec_module(module)
    return module


def main():
    queries = sys.argv[1:] or PROBES
    ws = load_module()
    print(f"探针查询 {len(queries)} 条：{' / '.join(queries)}\n")

    start = time.time()
    for query in queries:
        info = ws.search(query, count=6)
        print(f"「{query}」→ {len(info['results'])} 条，引擎 {info['engine'] or '-'}，"
              f"用了「{info['query_used'] or info['query']}」")
    cost = time.time() - start
    print(f"\n总耗时 {cost:.1f}s（平均 {cost / len(queries):.1f}s/次）\n")

    print(f"{'引擎':13s} {'成功':>4s} {'空':>4s} {'被拦':>4s} {'失败':>4s} {'累计结果':>8s} {'平均耗时':>8s}")
    print("-" * 56)
    for name, stat in ws.engine_stats().items():
        print(f"{name:13s} {stat['ok']:>4d} {stat['empty']:>4d} {stat['blocked']:>4d} "
              f"{stat['error']:>4d} {stat['found']:>8d} {stat['avg_ms']:>7d}ms")


if __name__ == "__main__":
    main()
