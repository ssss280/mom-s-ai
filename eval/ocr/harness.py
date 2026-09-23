"""OCR 评测 harness —— 把 eval/（搜索）那套「先量基线、再改、再量」搬到 OCR 上。

为什么需要它
    `tools/test_ocr.py` 验的是**逻辑**（编码往返、异常提示、回退路径），5 项全过也说明不了
    「识别得准不准」。OCR 的质量维度此前**没有任何客观度量**——这就是这个目录存在的理由。

判据
    CER      字符错误率 = 编辑距离(预测, 真值) / len(真值)，越低越好（主指标）
    行命中    真值里的每条消息，是否在预测里被完整认出来（辅助指标）
    两个指标都先做归一化：去掉所有空白、把全角冒号统一成半角——发送者格式的差异不算错。

用法
    py eval/ocr/harness.py                      # 只列题库 + 预计调用次数（**默认不跑**，防误花钱）
    py eval/ocr/harness.py --dry-run            # 生成图并落盘，不调任何 OCR（零成本，核对题目本身）
    py eval/ocr/harness.py --limit 3            # 只跑前 3 题（控成本）
    py eval/ocr/harness.py --all                # 跑全部
    py eval/ocr/harness.py --engine tesseract   # 指定引擎（本机没装会明确 SKIP，**不伪造结果**）

落盘
    eval/ocr/runs/<时间戳>/cases.json    本次题库快照
    eval/ocr/runs/<时间戳>/result.json   汇总指标 + 逐题明细
    eval/ocr/runs/<时间戳>/preview/*.png 生成的题图（便于人工核对判据是否合理）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FONT_CANDIDATES = {
    "msyh": ["C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
    "simhei": ["C:/Windows/Fonts/simhei.ttf"],
    "simsun": ["C:/Windows/Fonts/simsun.ttc"],
    "arial": ["C:/Windows/Fonts/arial.ttf"],
}


# --------------------------------------------------------------------------- 文本工具
def normalize(text: str) -> str:
    """归一化：去掉所有空白，全角冒号统一成半角。"""
    if not text:
        return ""
    out = "".join(ch for ch in text if not ch.isspace())
    return out.replace("：", ":")


def levenshtein(a: str, b: str) -> int:
    """编辑距离（滚动数组，内存 O(len(b))）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(pred: str, truth: str) -> dict:
    p, t = normalize(pred), normalize(truth)
    dist = levenshtein(p, t)
    cer = dist / len(t) if t else (0.0 if not p else 1.0)

    truth_lines = [normalize(line) for line in truth.splitlines() if normalize(line)]
    pred_blob = p
    hit = sum(1 for line in truth_lines if line and line in pred_blob)
    return {
        "cer": round(cer, 4),
        "distance": dist,
        "truth_chars": len(t),
        "pred_chars": len(p),
        "lines_total": len(truth_lines),
        "lines_hit": hit,
        "line_rate": round(hit / len(truth_lines), 4) if truth_lines else 0.0,
    }


# --------------------------------------------------------------------------- 出题
def resolve_font(name: str) -> str:
    for path in FONT_CANDIDATES.get(name, []):
        if os.path.exists(path):
            return path
    for paths in FONT_CANDIDATES.values():
        for path in paths:
            if os.path.exists(path):
                return path
    raise RuntimeError("找不到任何中文字体，无法合成题图（Windows 上应有 C:/Windows/Fonts/msyh.ttc）")


def truth_of(case: dict) -> str:
    return "\n".join(f"{sender}: {text}" for sender, text in case["messages"])


def render_case(case: dict):
    """把一条用例画成聊天截图风格的图。返回 (PIL.Image, 真值文本)。"""
    from PIL import Image, ImageDraw, ImageFont

    style = case["style"]
    font = ImageFont.truetype(resolve_font(style["font"]), style["size"])
    pad, gap, width = style.get("pad", 16), style.get("gap", 12), style["width"]
    content_w = width - 2 * pad

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    line_h = style["size"] + gap

    rows = []          # [(sender_or_None, text_line)]
    for sender, text in case["messages"]:
        prefix = f"{sender}: "
        first = True
        cur = ""
        for ch in text:
            head = prefix if first else ""
            if probe.textlength(cur + ch, font=font) <= content_w - (probe.textlength(head, font=font) if first else 0):
                cur += ch
            else:
                rows.append((sender if first else None, cur))
                cur, first = ch, False
        rows.append((sender if first else None, cur))

    height = pad * 2 + line_h * len(rows)
    image = Image.new("RGB", (width, height), style["bg"])
    draw = ImageDraw.Draw(image)
    y = pad
    for sender, text in rows:
        if sender:
            label = f"{sender}: "
            draw.text((pad, y), label, font=font, fill=style["sender_fg"])
            draw.text((pad + draw.textlength(label, font=font), y), text, font=font, fill=style["fg"])
        else:
            draw.text((pad + style["size"], y), text, font=font, fill=style["fg"])
        y += line_h

    return image, truth_of(case)


def apply_degrade(image, spec):
    """按题库里的 `degrade` 规格给题图加退化：模拟真实截图的模糊 / 有损压缩 / 缩放。

    为什么需要它：合成图是"完美"的——锐利抗锯齿、无噪点、无压缩伪影。
    实测十道原始题视觉模型**全部 CER=0**，说明清晰的合成图**测不出差距**；
    而真实截图的问题恰恰出在退化上。加档位才能找到"从哪一档开始出错"。
    """
    if not spec:
        return image
    from PIL import Image, ImageFilter
    if spec.get("blur"):
        image = image.filter(ImageFilter.GaussianBlur(spec["blur"]))
    if spec.get("jpeg"):
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=int(spec["jpeg"]))
        buf.seek(0)
        image = Image.open(buf).convert("RGB")
    if spec.get("scale"):
        w, h = image.size
        image = image.resize((max(1, int(w * spec["scale"])), max(1, int(h * spec["scale"]))),
                             Image.LANCZOS)
    return image


# --------------------------------------------------------------------------- 引擎
def build_vision_client():
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        return None, "没有 config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        from models import ChatSightModel
        model = ChatSightModel(cfg)
        if not model.is_vision_configured:      # 注意：这是 @property，不是方法
            return None, "视觉模型未配置（设置里填视觉 API Key）"
        return model.vision_client, None
    except Exception as exc:
        return None, f"构造视觉客户端失败：{exc!r}"


def run_one(image, engine: str, vision_model: str, client):
    """返回 (status, text, reason)。status ∈ ok / skip / error。不伪造结果。"""
    import ocr
    if engine == "tesseract" and not ocr.HAS_TESSERACT:
        return "skip", "", "本机未安装 Tesseract（本地 OCR 不可测，不是缺陷）"
    if engine in ("vision", "auto") and client is None:
        return "skip", "", "视觉模型未配置"
    try:
        text = ocr.extract_chat_text(image, method=engine, client=client, vision_model=vision_model)
        return "ok", text or "", ""
    except Exception as exc:
        return "error", "", f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- 主流程
def load_cases() -> dict:
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="OCR 评测（默认只列题，不实际调用）")
    ap.add_argument("--all", action="store_true", help="跑全部题目")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 题（控成本）")
    ap.add_argument("--only", default="", help="只跑指定 id，逗号分隔")
    ap.add_argument("--engine", default="vision", choices=["vision", "tesseract", "auto"])
    ap.add_argument("--dry-run", action="store_true", help="只生成图并落盘，不调任何 OCR")
    args = ap.parse_args()

    bank = load_cases()
    cases = bank["cases"]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    print(f"OCR 评测 · 题库 {len(bank['cases'])} 题 · 引擎 {args.engine}")
    print(f"  本次选中: {len(cases)} 题")
    for c in cases:
        print(f"    - {c['id']:22s} {c['desc']}")
    print()

    if not (args.all or args.limit or args.only or args.dry_run):
        print("默认不实际运行（视觉 API 会花钱）。要跑请显式指定：")
        print("  py eval/ocr/harness.py --dry-run         # 零成本，只出图")
        print("  py eval/ocr/harness.py --limit 3         # 跑前 3 题")
        print("  py eval/ocr/harness.py --all             # 跑全部")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = HERE / "runs" / f"{stamp}-{args.engine}"
    (run_dir / "preview").mkdir(parents=True, exist_ok=True)
    (run_dir / "cases.json").write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")

    cfg = {}
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    vision_model = cfg.get("vision_model", "qwen3-vl-plus")

    client, client_err = (None, "dry-run 不构造客户端") if args.dry_run else build_vision_client()
    if client_err and not args.dry_run:
        print(f"注意：{client_err}")

    calls = 0
    results = []
    for case in cases:
        image, truth = render_case(case)
        image = apply_degrade(image, case.get("degrade"))
        image.save(run_dir / "preview" / f"{case['id']}.png")

        if args.dry_run:
            results.append({"id": case["id"], "desc": case["desc"], "status": "dry-run",
                            "truth": truth, "metrics": None})
            print(f"  [图] {case['id']:22s} {image.size[0]}x{image.size[1]}  {len(truth)} 字")
            continue

        status, text, reason = run_one(image, args.engine, vision_model, client)
        if status == "ok":
            calls += 1
        metrics = score(text, truth) if status == "ok" else None
        results.append({"id": case["id"], "desc": case["desc"], "status": status, "reason": reason,
                        "truth": truth, "pred": text, "metrics": metrics})
        if status == "ok":
            print(f"  [OK ] {case['id']:22s} CER={metrics['cer']:.3f}  行命中 {metrics['lines_hit']}/{metrics['lines_total']}")
        else:
            print(f"  [{status.upper():4s}] {case['id']:22s} {reason}")

    summary = {}
    scored = [r for r in results if r["status"] == "ok"]
    if scored:
        avg_cer = sum(r["metrics"]["cer"] for r in scored) / len(scored)
        line_hit = sum(r["metrics"]["lines_hit"] for r in scored)
        line_all = sum(r["metrics"]["lines_total"] for r in scored)
        summary = {
            "scored": len(scored),
            "avg_cer": round(avg_cer, 4),
            "line_rate": round(line_hit / line_all, 4) if line_all else 0.0,
            "perfect": sum(1 for r in scored if r["metrics"]["cer"] == 0),
        }
        print()
        print(f"=== 汇总（{args.engine}）===")
        print(f"  可计分 {summary['scored']}/{len(results)} 题")
        print(f"  平均 CER      {summary['avg_cer']:.4f}   （越低越好，0 = 完全正确）")
        print(f"  行命中率      {summary['line_rate']:.4f}")
        print(f"  完全正确题数  {summary['perfect']}")

    (run_dir / "result.json").write_text(json.dumps(
        {"engine": args.engine, "vision_model": vision_model, "summary": summary, "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"落盘: {run_dir}")
    print(f"视觉 API 调用次数: {calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
