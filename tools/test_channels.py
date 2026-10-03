"""验证双通道逻辑：预发布版不该打扰稳定版用户。"""
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import update_check as uc  # noqa: E402

print("=== 预发布识别 ===")
for version in ["1.7.0-beta.1", "1.7.0-rc.2", "2.0.0-alpha", "1.7.0", "1.6.0", "1.7.0-beta"]:
    print(f"  {version:16s} is_prerelease={uc.is_prerelease(version)}")

print("\n=== should_notify：稳定通道 vs 测试通道 ===")
CASES = [
    ("1.7.0", "1.6.0"),
    ("1.7.0-beta.1", "1.6.0"),
    ("1.7.0-rc.1", "1.6.0"),
    ("1.6.1", "1.6.0"),
    ("1.6.0", "1.6.0"),
    ("1.5.0", "1.6.0"),
]
print(f"  {'远端':16s} {'本地':8s} {'stable 通道':>12s} {'beta 通道':>10s}")
for latest, current in CASES:
    stable = uc.should_notify(latest, current, "stable")
    beta = uc.should_notify(latest, current, "beta")
    print(f"  {latest:16s} {current:8s} {str(stable):>12s} {str(beta):>10s}")

print("\n=== 关键断言 ===")
checks = [
    ("稳定通道不提示 beta", uc.should_notify("1.7.0-beta.1", "1.6.0", "stable") is False),
    ("测试通道提示 beta", uc.should_notify("1.7.0-beta.1", "1.6.0", "beta") is True),
    ("稳定通道提示正式版", uc.should_notify("1.7.0", "1.6.0", "stable") is True),
    ("两通道都不提示同版本", uc.should_notify("1.6.0", "1.6.0", "beta") is False),
    ("两通道都不提示旧版本", uc.should_notify("1.5.0", "1.6.0", "beta") is False),
    ("通道名非法时按 stable", uc.should_notify("1.7.0-beta.1", "1.6.0", "??") is False),
]
ok = 0
for label, passed in checks:
    ok += 1 if passed else 0
    print(f"  {'OK ' if passed else '!! '}{label}")
print(f"\n{ok}/{len(checks)} 通过")
