"""
`check_env.py` 的 Node 版本判据（步骤 2）必须有测试守着

## 为什么值得单独一个文件

2026-09-15 实测的缺陷：`check_env.py` 原来写死 `major >= 18`，于是在 Node 20 上
打印 `✅ Node.js v20` —— 而**同一台机器**上 `npm test` 报的是
`Test Files  no tests`：vitest 的 jsdom 环境（`jsdom@30`）要求
`^22.22.2 || ^24.15.0 || >=26.0.0`，它依赖的 `undici@8` 要求 `>=22.19`。

**"只比主版本"恰好漏掉这一类要求**：`^22.22.2` 这种"主版本相同、下界落在
minor/patch 上"的声明，主版本比较永远看不出来（22 与 22 相等，而
22.22.1 < 22.22.2 是真的）。于是一个**检查脚本**报绿、测试却跑不起来 ——
这与本仓库反复抓到的"检查自身空转"是同一族病，只是方向相反：
那边是"什么都没测却报绿"，这边是"要求没满足却报够"。

## 这个文件锁的是什么

1. 判据的**来源**必须是 `frontend/package.json` 的 `engines.node`（= npm 读的
   同一个字段），而不是任何写死的常量 —— 并用一条"把目录指空就必须读不到"的
   用例证明它**真的在读文件**；
2. **真矩阵**逐点：不满足的版本里刻意包含 `20.19.5`（CI 上真实踩到的那一版）
   与 `22.22.1`（只差一个 patch，主版本比较看不出来）；
3. 一条**反证**：证明"只看主版本"的旧写法确实会放过 `20.19.5` ——
   没有它，这个文件存在的理由就只剩注释里的一句话；
4. 不认识的算子必须返回 `None`（判不了），由调用方 `warn` **说出来**，
   而不是静默放过。

## 只测纯函数，不测 `check_node()`

`_satisfies_node()` 是纯函数，`_node_engines_requirement()` 只读一个 JSON 文件。
**不测** `check_node()`：它要起子进程跑 `node --version`，那样测试就依赖
"本机装没装 node、装的是哪一版"，正是本仓库反复登记过的"随环境变色"。
"""

import json
import sys
from pathlib import Path

import pytest

#: `backend/tests/xxx.py` → parents[2] = 仓库根（`check_env.py` 在那里）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须在 sys.path 之后导入：check_env.py 在仓库根目录，不在 backend/ 包内。
import check_env

FRONTEND_PACKAGE = PROJECT_ROOT / "frontend" / "package.json"


def _declared_requirement() -> str:
    """直接读文件（不经被测函数），用于对拍"""
    data = json.loads(FRONTEND_PACKAGE.read_text(encoding="utf-8"))
    return data["engines"]["node"]


class TestRequirementSource:
    """判据必须来自 `frontend/package.json`，不能是写死的常量"""

    def test_matches_frontend_package_json(self):
        assert check_env._node_engines_requirement() == _declared_requirement()

    def test_declaration_is_not_the_old_lie(self):
        """旧声明 `>=18.0.0` 会漏掉真实要求（它让 npm 只发警告）"""
        assert _declared_requirement() != ">=18.0.0"

    def test_reads_the_file_instead_of_returning_a_constant(self, monkeypatch, tmp_path):
        """把 `FRONTEND_DIR` 指到空目录 → 必须读不到

        这一条是给"以后有人为了省事又把要求写死成常量"准备的：
        写死的话这里会返回那个常量、而不是空串，测试立刻红。
        """
        monkeypatch.setattr(check_env, "FRONTEND_DIR", tmp_path)
        assert check_env._node_engines_requirement() == ""


class TestSatisfiesMatrix:
    """真矩阵逐点对拍（本机 v22.22.3 满足；CI 曾用 20.x，不满足）"""

    @pytest.mark.parametrize(
        "version,expected",
        [
            ("22.22.2", True),  # 下界本身
            ("22.22.3", True),  # 本机 / CI 现在的版本
            ("22.22.1", False),  # 只差一个 patch —— 主版本比较看不出来
            ("24.15.0", True),
            ("24.14.0", False),
            ("26.0.0", True),
            ("27.1.0", True),  # `>=26.0.0` 子句
            ("23.5.0", False),  # 奇数版被 jsdom 排除，两个子句都不覆盖
            ("21.7.0", False),
            ("20.19.5", False),  # ★ CI 上真实踩到的那一版
            ("18.20.4", False),  # 旧声明的下界
        ],
    )
    def test_matrix(self, version, expected):
        assert check_env._satisfies_node(version, _declared_requirement()) is expected

    def test_major_only_check_would_have_passed_node_20(self):
        """反证：旧写法（只比主版本）在真实踩到的那一版上会**报绿**

        它不测产品代码，测的是"上面那条矩阵用例有没有必要存在"。
        """
        naive_ok = int("20.19.5".split(".")[0]) >= 18
        assert naive_ok is True, "旧判据本身变了吗？变了就要重写这条反证"
        assert check_env._satisfies_node("20.19.5", _declared_requirement()) is False


class TestUnknownShapesAreNotSilentlyAccepted:
    """判不了就说判不了（`None`），不要静默放过"""

    def test_unknown_operator(self):
        assert check_env._satisfies_node("22.1.0", "~22.1.0") is None

    def test_empty_requirement(self):
        assert check_env._satisfies_node("22.1.0", "") is None

    def test_unparsable_version(self):
        assert check_env._satisfies_node("not-a-version", ">=22.0.0") is None
