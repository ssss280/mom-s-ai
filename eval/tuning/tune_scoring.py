"""打分函数调参：对候选打分规则在标注集上算 AUC / 最优阈值下的 P、R、F1。

指标定义：
- auc：正样本得分高于负样本的概率（排序质量，与阈值无关）
- 调阈值 t ∈ [0.15,0.60]，取 F1 最高的 t，报告 precision / recall / f1
- 另外报告 t=0.3（现行阈值）与 t=0.2（现行兜底阈值）下的指标，便于对比现状
"""
import json
import os
import re
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\ai助手"
sys.path.insert(0, ROOT)
import web_search as ws  # noqa: E402

DATA = os.path.join(ROOT, "eval", "tuning", "labeled.json")


def load():
    rows = json.load(open(DATA, encoding="utf-8"))
    for row in rows:
        row["text"] = (row.get("title", "") + " " + row.get("snippet", "")).lower()
        row["title_l"] = (row.get("title", "") or "").lower()
    return [r for r in rows if r["relevant"] in (0, 1)]


def auc(pos, neg):
    """用秩和公式算 AUC，避免 O(n²) 暴力比较。"""
    if not pos or not neg:
        return None
    values = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    ranks, i = {}, 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1][0] == values[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[id(values[k])] = avg
        i = j + 1
    # 简化：直接按 (score,label) 列表重算
    scores = sorted(values, key=lambda x: x[0])
    rank_sum = 0
    for idx, (_, lab) in enumerate(scores, 1):
        if lab == 1:
            rank_sum += idx
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


# ---------------- 候选打分规则 ----------------

def current(query, row):
    return ws.relevance(query, {"title": row.get("title", ""), "snippet": row.get("snippet", "")})


def _chunks(query):
    """把查询拆成实体串（CJK 连续段 + 拉丁词），这是"必须要命中"的部分。"""
    q = ws.term_text(query) or query
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", q)
    latin = re.findall(r"[a-z0-9][a-z0-9.+#-]{1,}", q.lower())
    return cjk, latin


def _segment_score(entity, text, qlen):
    if entity in text:
        return 1.0
    length = len(entity)
    if length <= 2:
        # 二元组只能算弱命中：宁可漏，也不要把"香港"误当成"灯饰展"
        return 0.4 if entity in text else 0.0
    best = 0.0
    for size in range(min(4, length - 1), 1, -1):
        for start in range(0, length - size + 1):
            if entity[start:start + size] in text:
                best = max(best, size / length)
    return best


def rule_weighted(query, row, entity_weight=0.6, latin_weight=0.25, title_bonus=0.15):
    """候选规则：实体覆盖率（长词权重高）为主 + 拉丁词 + 标题加成。"""
    text, title = row["text"], row["title_l"]
    cjk, latin = _chunks(query)
    if not cjk and not latin:
        return 0.0
    entity_score = 0.0
    if cjk:
        entity_score = sum(_segment_score(e, text, len(e)) for e in cjk) / len(cjk)
    latin_score = 0.0
    if latin:
        latin_score = sum(1.0 if w in text else 0.0 for w in latin) / len(latin)
    score = entity_weight * entity_score + latin_weight * latin_score
    if latin and not cjk:
        score = 0.7 * latin_score + 0.1 * (1.0 if latin_score == 1.0 else 0.0)
    if cjk and not latin:
        score = 0.85 * entity_score
    title_hit = sum(1 for e in cjk if _segment_score(e, title, len(e)) > 0.5)
    title_hit += sum(1 for w in latin if w in title)
    total_terms = len(cjk) + len(latin)
    if total_terms:
        score += title_bonus * (title_hit / total_terms)
    return min(1.0, round(score, 3))


def rule_plain_coverage(query, row):
    """候选规则：只按"查询词有多少比例出现在文中"，不做长度加权。"""
    q = ws.term_text(query) or query
    terms = set(re.findall(r"[\u4e00-\u9fff]{2}|[a-z0-9]{2,}", q.lower()))
    if not terms:
        return 0.0
    text = row["text"]
    return round(sum(1 for t in terms if t in text) / len(terms), 3)


CANDIDATES = {
    "current(现行)": current,
    "idf_pool": None,          # 在 main 里按题目分组构造（需要候选池）
    "plain_coverage": rule_plain_coverage,
    "weighted_entity": rule_weighted,
}


def rule_idf_pool(rows):
    """把同一道题的所有候选当成一个池子，用池内文档频率做 IDF 加权（现行实现的真实行为）。"""
    by_query = {}
    for row in rows:
        by_query.setdefault(row["query"], []).append(row)

    scores = {}
    for query, items in by_query.items():
        corpus = len(items)
        doc_freq = {}
        bodies = [item["text"] for item in items]
        for term in {t for body in bodies for t in re.findall(r"[\u4e00-\u9fff]{2}|[a-z0-9]{2,}", body)}:
            doc_freq[term] = sum(1 for body in bodies if term in body)
        for item in items:
            scores[id(item)] = ws._score_one(query, {"title": item["title"], "snippet": item["snippet"],
                                                     "url": item.get("url", "")}, doc_freq, corpus)
    return scores


def adversarial_rows():
    """对抗负样本：域名权威、但内容完全跑题的结果。

    专门用来验证"权威域加成"（relevance 里对 gov.cn/百科/官网的 +0.06）会不会
    把无关结果救活——如果会，AUC 会掉下来，就必须把加成调小或去掉。
    """
    pairs = [
        ("中国声环境功能区标准里 1 类区夜间噪声限值是多少分贝",
         "生态环境部发布2025年全国生态环境质量简况", "https://www.mee.gov.cn/ywdt/xwfb/202506/t20250605_1.shtml"),
        ("2026香港国际秋季灯饰展什么时候在哪办",
         "世界遗产名录新增23处遗产地", "https://whc.unesco.org/en/news/1000"),
        ("DeepSeek API 现在什么价格",
         "OpenAI 发布新一代模型，定价策略调整", "https://openai.com/index/new-model/"),
        ("Python 现在最新的稳定版本是多少",
         "Docker Desktop 4.30 发布说明", "https://docs.docker.com/desktop/release-notes/"),
        ("香港地铁东铁线经过哪些站",
         "港铁公布票价调整方案", "https://www.mtr.com.hk/ch/customer/tickets/fare.html"),
        ("2025年诺贝尔文学奖颁给了谁",
         "2025年诺贝尔物理学奖公布", "https://www.nobelprize.org/prizes/physics/2025/summary/"),
        ("iPhone 17 国行起售价多少钱",
         "Apple 发布 watchOS 新版本", "https://www.apple.com.cn/newsroom/2026/09/watchos/"),
        ("天宫空间站现在有几个舱段",
         "中国载人航天工程办公室发布年度报告", "https://www.cmse.gov.cn/xwzx/202601/t20260101_1.html"),
    ]
    rows = []
    for query, title, url in pairs:
        rows.append({"case_id": "adversarial", "query": query, "title": title, "snippet": "",
                     "url": url, "source": "adversarial", "relevant": 0,
                     "text": (title + " ").lower(), "title_l": title.lower()})
    return rows


def evaluate(rows, fn=None, precomputed=None):
    scored = []
    for row in rows:
        score = precomputed[id(row)] if precomputed is not None else fn(row["query"], row)
        scored.append((score, row["relevant"]))
    pos = [s for s, lab in scored if lab == 1]
    neg = [s for s, lab in scored if lab == 0]

    best = None
    for step in range(15, 61):
        t = step / 100
        tp = sum(1 for s, lab in scored if lab == 1 and s >= t)
        fp = sum(1 for s, lab in scored if lab == 0 and s >= t)
        fn_ = sum(1 for s, lab in scored if lab == 1 and s < t)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn_) if tp + fn_ else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if best is None or f1 > best["f1"]:
            best = {"t": t, "precision": precision, "recall": recall, "f1": f1}

    def at(t):
        tp = sum(1 for s, lab in scored if lab == 1 and s >= t)
        fp = sum(1 for s, lab in scored if lab == 0 and s >= t)
        fn_ = sum(1 for s, lab in scored if lab == 1 and s < t)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn_) if tp + fn_ else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}

    return {
        "auc": round(auc(pos, neg) or 0, 3),
        "pos_mean": round(statistics.mean(pos), 3) if pos else 0,
        "neg_mean": round(statistics.mean(neg), 3) if neg else 0,
        "best": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in best.items()},
        "at_0.30": at(0.30),
        "at_0.20": at(0.20),
        "pos_pass_0.30": round(sum(1 for s in pos if s >= 0.30) / len(pos), 3) if pos else 0,
    }


def main():
    rows = load()
    rows += adversarial_rows()
    pos = sum(1 for r in rows if r["relevant"] == 1)
    neg = sum(1 for r in rows if r["relevant"] == 0)
    print(f"标注集: {len(rows)} 条（正 {pos} / 负 {neg}，含 {len(adversarial_rows())} 条权威域负样本）\n")

    idf_scores = rule_idf_pool(rows)
    results = {}
    for name, fn in CANDIDATES.items():
        if fn is None:
            results[name] = evaluate(rows, precomputed=idf_scores)
        else:
            results[name] = evaluate(rows, fn=fn)
        r = results[name]
        print(f"== {name} ==")
        print(f"   AUC={r['auc']}  正样本均分={r['pos_mean']}  负样本均分={r['neg_mean']}")
        print(f"   最优阈值 t={r['best']['t']}  P={r['best']['precision']} R={r['best']['recall']} F1={r['best']['f1']}")
        print(f"   t=0.30: {r['at_0.30']}   正样本通过率={r['pos_pass_0.30']}")
        print(f"   t=0.20: {r['at_0.20']}\n")

    with open(os.path.join(ROOT, "eval", "tuning", "scoring_results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    # 逐条看现行规则漏掉的题目（正样本但分数低）
    print("=== 现行规则下被漏掉的正样本（score<0.3）样例 ===")
    missed = [(current(r["query"], r), r) for r in rows
              if r["relevant"] == 1 and current(r["query"], r) < 0.3]
    missed.sort(key=lambda x: x[0])
    for score, row in missed[:10]:
        print(f"  [{score:.2f}] {row['case_id'][:24]:24s} {row['title'][:44]!r}")

    print("\n=== 权威域对抗负样本的得分（IDF 版，应全部 < 0.28）===")
    for row in adversarial_rows():
        score = idf_scores[id(row)]
        flag = "OK " if score < 0.28 else "!! 误判"
        print(f"  {flag} [{score:.2f}] {row['title'][:36]!r} <- {row['query'][:30]!r}")


if __name__ == "__main__":
    main()
