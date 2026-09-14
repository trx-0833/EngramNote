# -*- coding: utf-8 -*-
"""`.gitignore` 的**作用范围**守卫：只该忽略草稿，不该忽略项目内容

## 这份测试防的是什么

`.gitignore` 里那几条一次性脚本规则（`backend/test_*.py` 等）原本只匹配
`backend/` **根目录**。2026-09-14 那 15 个脚本被搬到 `backend/scripts/dev/`
并随提交入库之后，一个看起来最自然的"修法"是把规则跟着改到新目录 ——
而那会踩两个坑，且**两个都不会报错**：

1. 对已入库的文件毫无作用（`.gitignore` 只作用于未跟踪文件），却让该目录里
   将来新增的同名文件被静默忽略（"以为提交了，其实没有"）；
2. 更糟的是顺手把路径前缀去掉（`test_*.py` / `**/test_*.py`）：
   `backend/tests/**` 整套测试会**一起被忽略**，而当时的 `git status`
   反而更"干净" —— 这类事故只有专门问一次 git 才会现形。

因此这里不问 `.gitignore` 的文本长什么样（那是抄注释），而是拿**真实的 git
判定**去量一组代表性路径：

  * 必须**不**被任何规则命中的：测试套件、`app/**`、`scripts/*.py`（含漂移检查
    脚本本身）、`scripts/dev/**`（归档脚本）；
  * 必须**仍然**被命中的：`backend/` 根的一次性草稿名（那六条规则还得活着）。

## 两个必须说清的技术点

* **`--no-index`**：`git check-ignore` 默认**不报告已跟踪文件**（"被跟踪"压过
  "被忽略"）。直接问 `backend/scripts/dev/test_api.py` 会得到"没被忽略"这个
  **假**答案 —— 它只是已入库而已。要量**模式本身**，必须绕过索引。
* **没有 git 就跳过**：`git check-ignore` 需要工作区是个 git 仓库（打包出来的
  源码树不是）。此时 `returncode` 不是 0/1，用例 skip 而不是红 —— 本文件守的是
  "git 眼里的可见性"，没有 git 时这个问题本身不存在。
"""

import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEV_DIR = os.path.join("backend", "scripts", "dev")

#: **必须保持可见（不被忽略）** 的代表性路径。
#: 前四组是真实存在的项目内容；最后一组是"将来会写在这里的文件"的形态 ——
#: 忽略规则一旦被放宽到不带路径前缀，最先中招的就是它们。
MUST_NOT_BE_IGNORED = (
    # 测试套件本身（放宽成 `test_*.py` / `**/test_*.py` 时第一个消失的东西）
    "backend/tests/test_fixes.py",
    "backend/tests/conftest.py",
    "backend/tests/test_api_helpers.py",
    # 应用代码
    "backend/app/main.py",
    "backend/app/middleware/error_handler.py",
    # scripts/ 下的工具脚本（含漂移检查自身）
    "backend/scripts/check_dependency_drift.py",
    "backend/scripts/eval_retrieval.py",
    "backend/scripts/dev/README.md",
    # 归档脚本：它们已入库，且不该"半隐形"（新增同名文件必须可见）
    "backend/scripts/dev/e2e_cleanup.py",
    "backend/scripts/dev/test_api.py",
    "backend/scripts/dev/verify_clean.py",
    "backend/scripts/dev/restore_note.py",
    "backend/scripts/dev/test_new_dev_script.py",
)

#: **仍然应该被忽略**的 `backend/` 根草稿名（六条规则的原始意图）
SHOULD_BE_IGNORED = (
    "backend/test.py",
    "backend/test_api.py",
    "backend/test_scratch_anything.py",
    "backend/verify_whatever.py",
    "backend/reset_something.py",
    "backend/restore_something.py",
)


def _check_ignore(paths) -> dict:
    """返回 {路径: 命中的规则描述}（`--no-index`：只按模式判定，不看索引）

    git 不可用（不是仓库 / 没装 git）时 skip —— 见文件头说明。
    """
    try:
        result = subprocess.run(
            ["git", "-C", REPO, "check-ignore", "--no-index", "-v", *paths],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"无法执行 git check-ignore: {exc}")

    # `git check-ignore` 的退出码：0 = 至少一条命中，1 = 一条都没命中，其它 = 出错
    if result.returncode not in (0, 1):  # pragma: no cover - 取决于环境
        pytest.skip(
            "git check-ignore 不可用（很可能不是 git 仓库）: "
            + (result.stderr or "").strip()[:200]
        )

    hits = {}
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        spec, name = line.split("\t", 1)
        hits[name.replace("\\", "/")] = spec
    return hits


def _tracked_files() -> set:
    """`git ls-files` 的集合（相对仓库根，斜杠分隔）；不可用时返回 None"""
    try:
        result = subprocess.run(
            ["git", "-C", REPO, "ls-files"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    if result.returncode != 0:  # pragma: no cover - 取决于环境
        return None
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines()}


class TestIgnoredScope:

    def test_project_content_is_not_ignored(self):
        """**核心**：测试套件 / app / scripts 下的内容一条规则都不该命中"""
        hits = _check_ignore(MUST_NOT_BE_IGNORED)
        assert not hits, (
            "以下本该入库的路径被 .gitignore 命中了（`.gitignore` 的作用范围被放大了）：\n  "
            + "\n  ".join(f"{p}  ←  {spec}" for p, spec in sorted(hits.items()))
            + "\n最常见的成因：把 `backend/test_*.py` 改成了不带路径前缀的 "
            "`test_*.py` / `**/test_*.py`（后者会连 backend/tests/** 一起忽略）。"
        )

    def test_backend_root_drafts_are_still_ignored(self):
        """六条规则的原始意图还在：`backend/` 根的一次性草稿名仍被忽略

        （它们只匹配根目录那一层 —— 这一条与上一条一起，把"范围"钉死了。）
        """
        hits = _check_ignore(SHOULD_BE_IGNORED)
        missing = [p for p in SHOULD_BE_IGNORED if p not in hits]
        assert not missing, (
            f"这些 backend/ 根的草稿名不再被忽略了: {missing} —— "
            "若是有意删掉这几条规则，请一并更新本文件与 .gitignore 的说明"
        )

    def test_archived_dev_scripts_are_either_visible_or_tracked(self):
        """归档目录里不允许出现"未跟踪 **且** 被忽略"的半隐形文件

        这是本轮复核的结论落成机器判据：那 15 个脚本已入库（保持现状即可），
        **将来**新增的文件无论是否入库，都必须能在 `git status` 里被看见 ——
        两种状态（已入库 / 未跟踪但可见）都合格，只有"未跟踪又被忽略"不合格。
        """
        tracked = _tracked_files()
        if tracked is None:  # pragma: no cover - 取决于环境
            pytest.skip("git ls-files 不可用（很可能不是 git 仓库）")

        candidates = [
            f"{DEV_DIR}/{name}" for name in sorted(os.listdir(os.path.join(REPO, DEV_DIR)))
            if not name.startswith("__")
        ]
        assert candidates, f"{DEV_DIR} 下什么都没找到 —— 路径推导失效了"
        hits = _check_ignore(candidates)

        half_hidden = [p for p in candidates if p not in tracked and p in hits]
        assert not half_hidden, (
            "以下文件既未入库、又被忽略（在 git status 里完全看不见）：\n  "
            + "\n  ".join(f"{p}  ←  {hits[p]}" for p in half_hidden)
            + "\n若它们确实是草稿，请从归档目录移走；若要入库，"
            "请 `git add` 并调整 .gitignore（见该目录 README 的遗留说明）。"
        )
