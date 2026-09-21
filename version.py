"""版本号与更新检测的目标仓库（界面展示、更新检测共用）。

**版本号规则**：以 GitHub 仓库上的版本为准，提交新版本时取它的**下一个**。
例如 GitHub 上当前是 1.2.0，本次就提交 1.3.0。
用 `py release.py` 可以自动查出该用哪个号（`--apply` 会直接写回本文件）。

每次发版改完这里的 `__version__`，记得同步在 CHANGELOG.md 顶部加一条记录。
"""

__version__ = "1.6.0"

# 更新检测使用的 GitHub 仓库（owner/repo）与默认分支
UPDATE_REPO = "ssss280/mom-s-ai"
UPDATE_BRANCH = "main"
