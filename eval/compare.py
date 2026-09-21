"""把两次运行的指标摆在一起对比（跑完基线/优化后用来出结论）。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RUNS = r"D:\ai助手\eval\runs"

KEYS = [
    ("cases", "题目数"),
    ("no_result_rate", "空结果率"),
    ("low_relevance_rate", "低相关兜底率"),
    ("thin_result_rate", "结果<3条比例"),
    ("hit_rate_strict", "判据全中率"),
    ("hit_rate_loose", "判据基本中率"),
    ("mean_term_coverage", "判据平均覆盖"),
    ("domain_hit_rate", "期望域名命中率"),
    ("top1_hit_rate", "首条命中率"),
    ("avg_results", "平均结果数"),
    ("avg_good_results", "平均可信结果数"),
    ("distinct_domains", "不同来源域名数"),
    ("latency_p50", "延迟 P50(s)"),
    ("latency_p90", "延迟 P90(s)"),
    ("latency_max", "延迟 最大(s)"),
    ("link_alive_rate", "链接可打开率"),
    ("page_fetch_success_rate", "正文抓取成功率"),
]


def load(name):
    with open(os.path.join(RUNS, name, "metrics.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return data["metrics"], data["scores"]


def fmt(value):
    if value is None:
        return "  -   "
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def main(names):
    runs = [(n, load(n)) for n in names]
    header = "指标".ljust(20) + "".join(n[:22].rjust(24) for n, _ in runs)
    print(header)
    print("-" * len(header))
    for key, label in KEYS:
        row = label.ljust(20)
        for _, (metrics, _scores) in runs:
            row += fmt(metrics.get(key)).rjust(24)
        print(row)
    print()
    parts = list(runs[0][1][1]["parts"].keys())
    print("总分".ljust(20) + "".join(f"{s['total']}".rjust(24) for _, (_m, s) in runs))
    for part in parts:
        row = f"  {part}".ljust(20)
        for _, (_m, scores) in runs:
            row += fmt(scores["parts"].get(part)).rjust(24)
        print(row)


if __name__ == "__main__":
    main(sys.argv[1:] or ["01-baseline", "04-round3"])
