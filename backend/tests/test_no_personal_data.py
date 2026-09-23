"""防复发守卫：真实个人数据不得再进入被跟踪文件（阶段 1.5）

## 背景

2026-09-23 的去标识化批次之前，公开仓库里有 30 处真实个人信息：
一位**真实姓名**（出现在拟真提问里）与一份**真实个人文档的文件名**
（"劳动合同书-<姓名>.pdf"，出现在 16 个已跟踪文件里）。GitHub 代码搜索
与搜索引擎都能命中。

## 为什么需要守卫而不是"改一次就完了"

这类信息**不会自己消失**，而最容易重新进来的路径恰恰是"新写一个 e2e 脚本"：
本仓库历史上每加一个 `test_weekN_e2e.py` 就把那串真实路径复制一份
（`test_week9_10_e2e.py` 甚至还有第三种写法）。因此把它变成断言：
默认值只允许是中性文件名，真实语料一律走 `TEST_PDF_PATH` 环境变量。

## 实现注意

- 禁止串以**码点**形式写在下面，因此本测试的源码里不含明文姓名 ——
  否则它自己就会命中自己的规则。
- 用 `git grep` 扫**已跟踪文件**（只扫工作区文件会漏掉"已入库但本地删了"的形态）。
- `ALLOWLIST` 是审计报告：它必须**引用**原始证据才能说明问题。
  每加一条都要写清"为什么允许"。
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 禁止串（码点构造，避免明文进入本文件）
_REAL_NAME = "".join(map(chr, [0x7530, 0x6DA6, 0x946B]))
_DOC_TYPE = "".join(map(chr, [0x52B3, 0x52A8, 0x5408, 0x540C, 0x4E66]))

FORBIDDEN = {
    "真实姓名": _REAL_NAME,
    "个人文档文件名（文档类型-姓名）": f"{_DOC_TYPE}-{_REAL_NAME}",
}

#: 允许出现的位置：仅审计报告（它必须引用证据才能说明问题）
ALLOWLIST = {
    "docs/open-source-readiness.md",
}


def _git_grep(pattern: str) -> list[str]:
    """在已跟踪文件中查找（返回 `path:line` 列表；非 git 环境跳过）"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "grep", "-n", "-F", "--", pattern],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:  # pragma: no cover - 环境相关
        pytest.skip(f"无法执行 git grep: {exc}")
    if proc.returncode not in (0, 1):
        pytest.skip(f"git grep 失败（退出码 {proc.returncode}）：{proc.stderr[:200]}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


class TestNoPersonalDataInTrackedFiles:
    @pytest.mark.parametrize("label", sorted(FORBIDDEN))
    def test_forbidden_string_absent(self, label):
        hits = [
            ln for ln in _git_grep(FORBIDDEN[label])
            if ln.split(":", 1)[0] not in ALLOWLIST
        ]
        assert not hits, (
            f"被跟踪文件里出现了{label}：\n  "
            + "\n  ".join(hits[:10])
            + "\n\n真实语料请走 TEST_PDF_PATH 环境变量（见 tests/conftest.real_pdf_path），"
            "默认值只允许中性文件名。"
        )

    def test_allowlist_is_not_a_blanket_bypass(self):
        """allowlist 里的文件必须真的存在，且必须真的引用了证据

        否则它迟早会变成"随手加一条让测试变绿"的后门。
        """
        for rel in ALLOWLIST:
            path = REPO_ROOT / rel
            assert path.exists(), f"allowlist 指向不存在的文件：{rel}"
            text = path.read_text(encoding="utf-8")
            assert _REAL_NAME in text, (
                f"{rel} 在 allowlist 里，但已不再引用真实姓名 —— "
                "那就把它从 ALLOWLIST 里删掉"
            )

    def test_pdf_defaults_are_neutral(self):
        """所有读真实 PDF 的模块都必须走环境变量，不得再写死个人路径"""
        hits = _git_grep("resource")
        offenders = [
            ln for ln in hits
            if ".pdf" in ln and "TEST_PDF_PATH" not in ln and "gitignore" not in ln
        ]
        assert not offenders, (
            "有模块把真实 PDF 路径写死在代码里：\n  " + "\n  ".join(offenders[:10])
        )
