# -*- coding: utf-8 -*-
"""依赖漂移检查（scripts/check_dependency_drift.py）自身的测试 —— 反空洞

## 这个文件防的是什么

那个脚本的结论只有两种：**有漂移** / **没漂移**。一个坏掉的检查永远给第二种，
而"没漂移"看上去正是好消息 —— 于是坏掉的检查与"一切正常"在输出上无法区分。
本仓库已经反复踩过这个形态：CI 的依赖清单手抄漂移、rate limit 规则从未匹配上、
`pip-audit` 的结论被当成在跑系统的结论。

因此这里断言的不是"今天漂移有多少"（那是会变的环境事实），而是
**"这个检查确实查了东西"**：

  * 清单真的被解析成条目，且**一行都没被静默跳过**（解析器没退化成空列表）；
  * CI 必装的那份清单里，每一条都真的查到了已安装版本（元数据查询没整体失效）；
  * 比较器对"范围内 / 范围外"给出**不同**答案（不是一个恒真的判断）；
  * 传递依赖探测确实产出了条目（没退化成空列表）；
  * 清单缺失时**必须响**（退出码 2），而不是报"没漂移"。

刻意**不**断言 `drift > 0`：本机今天确有若干包超出声明范围，但把"今天漂移的数量"
写进断言，等于让"有人同步了版本"变成一次假失败 —— 而那时该庆祝，不该红。
"""

import importlib.util
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "check_dependency_drift.py"


@pytest.fixture(scope="module")
def drift():
    """把脚本当模块加载（scripts/ 不在 import 路径上，且不 import app 更干净）"""
    assert SCRIPT.is_file(), f"漂移检查脚本不存在: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("check_dependency_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(drift):
    """真实清单 + 真实已安装版本的一份报告（模块级缓存，避免重复读元数据）"""
    return drift.analyze()


# ---------------------------------------------------------------------------
# 反空洞：检查必须真的查到了东西
# ---------------------------------------------------------------------------
class TestCheckIsNotVacuous:

    def test_no_internal_problems(self, report):
        """检查自身没有失效（problems 非空就是退出码 2）"""
        assert report["problems"] == []

    def test_every_declaration_line_is_parsed(self, drift, report):
        """**核心**：requirements.txt 里每一行声明都必须被解析出来

        只要解析正则退化（比如只认带 `~=` 的行、或把名字吃掉），条目数就会
        少于真实声明行数，而报告看上去一切正常 —— 那是"永远没问题"的检查。
        这里用一个刻意更笨的独立数法（非注释非空行）来对账。
        """
        raw_lines = [
            line
            for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()
        ]
        parsed = [e for e in report["entries"] if e["source"] == "requirements.txt"]
        assert len(parsed) == len(raw_lines), (
            f"requirements.txt 有 {len(raw_lines)} 行声明，只解析出 {len(parsed)} 条 —— "
            "解析器在静默跳过行"
        )
        for entry in parsed:
            assert entry["name"]
            assert entry["line"] > 0
            assert entry["specifier"], f"{entry['name']} 的版本约束没被解析出来"

    def test_installed_versions_are_actually_looked_up(self, report):
        """**核心**：CI 必装的那份清单，每一条都要查到已安装版本

        元数据查询一旦整体失效（包名规范化写错、用了错误的解释器等），
        所有条目会变成"未安装"，而"未安装"很容易被读成"没有漂移"。
        用 requirements-test.txt 对账，因为 CI 一定 `pip install -r` 它。
        """
        test_entries = [
            e for e in report["entries"] if e["source"] == "requirements-test.txt"
        ]
        assert len(test_entries) >= 15, f"requirements-test.txt 只解析出 {len(test_entries)} 条"
        missing = [e["name"] for e in test_entries if e["installed"] is None]
        assert not missing, (
            f"CI 必装的依赖查不到已安装版本: {missing} —— 已安装版本的查询路径失效了"
        )
        assert report["counts"]["installed_known"] >= 15

    def test_transitive_walk_produces_entries(self, report):
        """传递依赖探测确实在工作（它是 starlette 这类包唯一的露面处）"""
        assert report["transitive_total"] >= 5, (
            "传递依赖一条都没查出来 —— 该段已退化成空列表，"
            "而 starlette / pydantic-core 这类包只会出现在这里"
        )
        for item in report["transitive"]:
            assert item["required_by"], f"{item['name']} 没有记录任何父包"
            assert item["installed"] is not None

    def test_comparator_is_not_a_constant(self, drift):
        """**核心**：比较器必须能区分"范围内"与"范围外"

        取值的正确含义（PEP 440 兼容版本子句，写错这里就会把漂移判反）：

            ~=X.Y.Z   ->  >=X.Y.Z, ==X.Y.*    仅补丁位可浮动
            ~=X.Y     ->  >=X.Y,   ==X.*      次版本位可浮动

        若哪天比较器被写成恒真或恒假，上面几个"查到了东西"的用例仍会全绿，
        只有这里会红。
        """
        # 三位写法：只有补丁位能浮动
        assert drift.classify("~=2.0.0", "2.0.52") == drift.V_IN_RANGE
        assert drift.classify("~=2.0.0", "2.13.5") == drift.V_OUT_OF_RANGE
        assert drift.classify("~=0.115.0", "0.115.14") == drift.V_IN_RANGE
        assert drift.classify("~=0.115.0", "0.141.1") == drift.V_OUT_OF_RANGE
        assert drift.classify("~=1.24.0", "2.2.6") == drift.V_OUT_OF_RANGE
        # 两位写法：次版本位也能浮动
        assert drift.classify("~=2.2", "2.13.5") == drift.V_IN_RANGE
        # 其它情形
        assert drift.classify("~=2.2.0", "3.0.1") == drift.V_OUT_OF_RANGE
        assert drift.classify("", "1.0.0") == drift.V_UNPINNED
        assert drift.classify("~=1.0.0", None) == drift.V_NOT_INSTALLED

    def test_missing_manifest_is_loud(self, drift, tmp_path):
        """清单缺失时必须是"检查没跑起来"（2），不是"没漂移"（0）"""
        empty_report = drift.analyze([tmp_path / "does-not-exist.txt"])
        assert empty_report["problems"], "清单缺失却没有记录任何 problem"
        assert drift.exit_code(empty_report) == drift.EXIT_VACUOUS == 2


# ---------------------------------------------------------------------------
# 今天的漂移是"已记录的事实"，不能让检查常红
# ---------------------------------------------------------------------------
class TestDriftTodayDoesNotFailTheCheck:

    def test_default_exit_code_is_zero_even_with_drift(self, drift, report):
        """**默认只报告**：本机今天的漂移不影响退出码

        这条与上一条同样重要：一个长期红的检查会被人 `|| true` 掉，
        然后连"检查还在不在"都没人知道。门禁必须由使用方显式选择。
        """
        assert report["counts"]["drift"] == report["counts"][drift.V_OUT_OF_RANGE]
        assert drift.exit_code(report) == drift.EXIT_OK == 0
        # 显式门禁时才允许非 0（今天确实是 1，但这里不断言具体值）
        assert drift.exit_code(report, fail_on_drift=True) in (drift.EXIT_OK, drift.EXIT_DRIFT)

    def test_json_report_is_serialisable(self, report):
        """CI 要用 --json 归档，报告必须可序列化"""
        payload = json.dumps(report, ensure_ascii=False)
        assert "counts" in json.loads(payload)

    def test_verdicts_are_partitioned(self, report):
        """每条声明的判定必须落在已知取值里，且计数与明细一致"""
        counts = report["counts"]
        allowed = {"in_range", "out_of_range", "unpinned", "not_installed", "unknown"}
        verdicts = [e["verdict"] for e in report["entries"]]
        assert set(verdicts) <= allowed, f"出现了未知判定: {set(verdicts) - allowed}"
        assert counts["declared"] == len(report["entries"])
        assert counts["pinned"] + counts["unpinned"] == counts["declared"]
        assert counts["out_of_range"] == verdicts.count("out_of_range")
        assert counts["in_range"] == verdicts.count("in_range")
