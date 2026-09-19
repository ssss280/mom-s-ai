"""带保护的 cx_Freeze 构建入口（绕过 Python 3.14 + cx_Freeze 的字节码扫描崩溃）。

现象：`python setup.py build` 有时会以
    TypeError: unsupported operand type(s) for -: 'range_iterator' and 'int'
    或 0xC0000005 / 0xC0000409 崩溃
原因：cx_Freeze 自己实现的扫描器会调用 `dis._unpack_opargs`，
      而 Python 3.14 的 `dis._get_cache_size()` 在个别代码对象上会返回非 int，
      `caches -= 1` 就炸了（C 层崩溃则直接访问违例）。

这里在导入 cx_Freeze 之前把 dis._get_cache_size 包一层，保证永远返回 int。
用法：py build_safe.py        （等价于 py setup.py build）
"""

import dis
import runpy
import sys

_inline_cache_entries = getattr(dis, "_inline_cache_entries", None)
_original_get_cache_size = dis._get_cache_size


def _safe_get_cache_size(opname):
    try:
        value = _original_get_cache_size(opname)
    except Exception:
        return 0
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except Exception:
        return 0


dis._get_cache_size = _safe_get_cache_size


def main():
    args = sys.argv[1:] or ["build"]
    sys.argv = ["setup.py"] + args
    runpy.run_path("setup.py", run_name="__main__")


if __name__ == "__main__":
    main()
