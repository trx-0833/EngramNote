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
  * 传递依赖探测确实产出了条目（没退化成空列表），且"没查到"被**分桶**：
    可选 extra 缺失 = 信息；硬依赖缺失 = 发现（见下面的 TestMissingKindIsDistinguished）；
  * 清单缺失时**必须响**（退出码 2），而不是报"没漂移"。

刻意**不**断言 `drift > 0`：本机今天确有若干包超出声明范围，但把"今天漂移的数量"
写进断言，等于让"有人同步了版本"变成一次假失败 —— 而那时该庆祝，不该红。

同样刻意**不**断言"本机一定有可选的缺失条目"（那会是另一个随环境翻脸的门槛）：
分桶判据用**人造父包**（`_FakeDist`）确定性地钉住，真实报告只用来做
"分桶结果与明细一致"这类自洽断言。
"""

import importlib.util
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "check_dependency_drift.py"


class _FakeDist:
    """人造的"已安装父包"元数据（只为驱动传递依赖走查，不碰真实环境）

    `transitive_dependencies` 只用到 `version` 与 `requires` 两个属性。
    """

    def __init__(self, version: str, requires: list):
        self.version = version
        self.requires = requires


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
        """传递依赖探测确实在工作（它是 starlette 这类包唯一的露面处）

        ⚠️ 这里**刻意不再**断言"每个条目都查到了已安装版本"：传递依赖里有
        `uvicorn[standard]` 的 extras（httptools / watchfiles），瘦环境里它们
        本来就不在（本轮修的正是这条 —— 它让检查在瘦环境里常红，而那是环境
        事实，不是依赖漂移）。判据改成**按桶**检查：查到版本的自不必说；
        没查到的，必须被明确标成"只靠 extra 才需要"（`optional_only` + 记下
        extra 名），否则就是硬依赖缺失，必须报出来。
        """
        assert report["transitive_total"] >= 5, (
            "传递依赖一条都没查出来 —— 该段已退化成空列表，"
            "而 starlette / pydantic-core 这类包只会出现在这里"
        )
        for item in report["transitive"]:
            assert item["required_by"], f"{item['name']} 没有记录任何父包"
            if item["installed"] is None:
                assert item["optional_only"], (
                    f"{item['name']} 查不到已安装版本，却不是可选依赖 —— "
                    "硬依赖缺失不能悄悄放过去（应进 transitive_missing_required）"
                )
                assert item["extras"], (
                    f"{item['name']} 被标成可选，却没记录是哪个 extra 带来的"
                )
        # 两个桶必须与明细严格互斥且穷尽（不是各算各的）
        absent = sorted(i["name"] for i in report["transitive"] if i["installed"] is None)
        bucketed = sorted(
            [b["name"] for b in report["transitive_missing_optional"]]
            + [b["name"] for b in report["transitive_missing_required"]]
        )
        assert absent == bucketed, (
            f"'未安装'的分桶与明细对不上：明细 {absent} vs 分桶 {bucketed}"
        )
        assert report["counts"]["transitive_missing_optional"] == len(
            report["transitive_missing_optional"]
        )
        assert report["counts"]["transitive_missing_required"] == len(
            report["transitive_missing_required"]
        )

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
# "没查到"必须分成两种：可选缺失 = 信息；声明缺失 / 硬依赖缺失 = 发现
# ---------------------------------------------------------------------------
class TestMissingKindIsDistinguished:
    """本轮修复的核心：把"未安装"从**一个失败**拆成"信息"与"发现"两种

    ## 为什么必须拆

    脚本原来只有一句 `installed is None`，于是"`uvicorn[standard]` 的
    httptools / watchfiles 在瘦环境里没有"与"清单写了却装不上"在输出上
    长得一模一样。前者的正确处理是**如实报告为信息**（它取决于 extras 与
    平台），后者才是发现。混在一起只有两种结局：检查在瘦环境里常红
    （现状），或者有人为了让 CI 变绿而把整段断言删掉。

    ## 为什么用**人造父包**而不是拿本机环境当判据

    拿真实环境当判据就是把"本机恰好没装 httptools"写成门槛：换一台装全了的
    机器，用例会因为"没有可选缺失条目"而红 —— 与它要防的形态一模一样。
    所以分桶判据喂 `_FakeDist`（确定性），真实报告只做互斥穷尽的自洽断言。
    """

    @staticmethod
    def _items_with_fake_parent(drift, monkeypatch):
        """走一遍走查：人造父包带一条**可选**依赖 + 一条**硬**依赖，两条都没装"""
        fake = _FakeDist(
            "1.0.0",
            [
                # 只有 extra == "standard" 成立 ⇒ 可选
                "optdep>=1.0; extra == 'standard'",
                # 无标记 ⇒ 无条件（硬）依赖
                "harddep>=2.0",
            ],
        )
        monkeypatch.setattr("importlib.metadata.distribution", lambda name: fake)
        monkeypatch.setattr(drift, "installed_version", lambda name: None)
        declared = [{
            "name": "fakeparent",
            "extras": ["standard"],
            "specifier": "",
            "source": "fake.txt",
            "line": 1,
            "raw": "fakeparent[standard]",
        }]
        return drift.transitive_dependencies(declared, {"fakeparent"}, set())

    def test_extra_only_absence_is_optional(self, drift, monkeypatch):
        """**核心**：只靠 extra 带上来的包没装 ⇒ optional_only=True + 记下 extra"""
        items = self._items_with_fake_parent(drift, monkeypatch)
        by_name = {i["name"]: i for i in items}
        assert set(by_name) == {"optdep", "harddep"}, by_name

        opt = by_name["optdep"]
        assert opt["optional_only"] is True, (
            "extra 专属依赖被当成了硬依赖 —— 瘦环境里的可选缺失会被判成失败"
        )
        assert opt["extras"] == ["standard"], opt["extras"]
        assert opt["required_by"][0]["extras"] == ["standard"], (
            "父包条目上没有记下是哪个 extra 让它成立的（报告里就没法解释为什么算信息）"
        )

        hard = by_name["harddep"]
        assert hard["optional_only"] is False, (
            "无条件声明的依赖被当成了可选 —— 环境自相矛盾时会静默放过去"
        )
        assert hard["extras"] == []
        assert hard["required_by"][0]["extras"] == []

    def test_split_puts_them_in_different_buckets(self, drift, monkeypatch):
        """两条都没装，但必须落进**不同**的桶（信息 vs 需处理）"""
        items = self._items_with_fake_parent(drift, monkeypatch)
        optional, required = drift.split_missing_transitive(items)
        assert [i["name"] for i in optional] == ["optdep"]
        assert [i["name"] for i in required] == ["harddep"]
        # 已安装的条目两边都不进（桶只装"未安装"）
        assert drift.split_missing_transitive([{"name": "x", "installed": "1.0",
                                                "optional_only": False}]) == ([], [])

    def test_declared_missing_is_a_finding_not_information(self, drift, tmp_path):
        """**核心**：清单写了却没装 = 判定 `not_installed`（发现），不进信息桶

        合成的清单里放一条真包（pytest，保证元数据查询路径确实在工作 ——
        否则会退化成"所有条目都查不到"的空洞守卫）与一条必然不存在的包。
        """
        manifest = tmp_path / "requirements.txt"
        manifest.write_text(
            "pytest\ndefinitely-not-installed-pkg-xyz~=1.0.0\n", encoding="utf-8"
        )
        rep = drift.analyze([manifest])

        # 查询路径有效 ⇒ 没有触发任何"空洞"问题（problems 非空就是退出码 2）
        assert rep["problems"] == [], rep["problems"]
        missing = [e for e in rep["entries"] if e["installed"] is None]
        assert [e["name"] for e in missing] == ["definitely-not-installed-pkg-xyz"]
        assert missing[0]["verdict"] == drift.V_NOT_INSTALLED
        assert rep["counts"]["not_installed"] == 1
        assert rep["counts"]["declared_missing"] == 1, (
            "声明缺失没有被单独计数 —— 报告里就只剩一个没名字的 not_installed"
        )
        # 与"可选缺失"是两个桶：声明缺失**不得**被降级成信息
        optional_names = {b["name"] for b in rep["transitive_missing_optional"]}
        required_names = {b["name"] for b in rep["transitive_missing_required"]}
        assert "definitely-not-installed-pkg-xyz" not in optional_names, (
            "**声明**缺失被塞进了'可选信息'桶 —— 发现被降级成了信息"
        )
        assert "definitely-not-installed-pkg-xyz" not in required_names

    def test_declared_missing_is_labelled_loudly(self, drift):
        """判定标签必须**刺眼**：声明缺失沿用 `!!`，不能退回 `??`（与"无法判定"同级）"""
        not_installed = drift._VERDICT_LABEL[drift.V_NOT_INSTALLED]
        assert not_installed.startswith("!!"), not_installed
        assert not_installed != drift._VERDICT_LABEL[drift.V_UNKNOWN]
        assert drift._VERDICT_LABEL[drift.V_OUT_OF_RANGE].startswith("!!")

    def test_report_text_separates_information_from_findings(
        self, drift, tmp_path, capsys
    ):
        """**输出层**的证据：可选缺失被写成"信息"，声明缺失被写成"发现"

        只在报告里改桶、不改输出，等于修给机器看：人读的那份仍然分不清
        "本机瘦"与"清单写错了"。这条把两者的措辞钉住。
        """
        manifest = tmp_path / "requirements.txt"
        manifest.write_text("pytest\ndefinitely-not-installed-pkg-xyz~=1.0.0\n",
                            encoding="utf-8")
        rep = drift.analyze([manifest])

        # 注入一条**人造**的可选缺失传递依赖（不依赖本机是否真的缺 httptools）
        item = {
            "name": "optdep",
            "installed": None,
            "required_by": [{
                "parent": "fakeparent", "parent_installed": "1.0.0",
                "parent_in_declared_range": True, "specifier": ">=1.0",
                "extras": ["standard"],
            }],
            "constraint_from_out_of_range_parent": False,
            "optional_only": True,
            "extras": ["standard"],
            "version_ok": None,
        }
        rep["transitive"] = [item]
        rep["transitive_total"] = 1
        rep["transitive_missing_optional"] = [drift._missing_brief(item)]
        rep["transitive_missing_required"] = []
        rep["counts"]["transitive_missing_optional"] = 1
        rep["counts"]["transitive_missing_required"] = 0

        drift.print_report(rep)
        out = capsys.readouterr().out

        assert "可选依赖未安装" in out and "信息，不算漂移" in out
        assert "!! 未安装（声明了却没装）" in out, (
            "声明缺失的判定标签不再是刺眼的 !! —— 它会被读成'信息'"
        )
        assert "可选 extras" in out and "信息，不算漂移" in out
        # 没有硬依赖缺失时，不该出现"需处理"的 [3b] 段
        assert "[3b]" not in out
        assert "definitely-not-installed-pkg-xyz" in out

    def test_hard_transitive_absence_is_printed_as_a_finding(
        self, drift, tmp_path, capsys
    ):
        """硬依赖缺失要单列（[3b] ⚠），与可选缺失在同一份报告里明确区分"""
        manifest = tmp_path / "requirements.txt"
        manifest.write_text("pytest\n", encoding="utf-8")
        rep = drift.analyze([manifest])
        item = {
            "name": "harddep",
            "installed": None,
            "required_by": [{
                "parent": "fakeparent", "parent_installed": "1.0.0",
                "parent_in_declared_range": True, "specifier": ">=2.0",
                "extras": [],
            }],
            "constraint_from_out_of_range_parent": False,
            "optional_only": False,
            "extras": [],
            "version_ok": None,
        }
        rep["transitive"] = [item]
        rep["transitive_total"] = 1
        rep["transitive_missing_optional"] = []
        rep["transitive_missing_required"] = [drift._missing_brief(item)]
        rep["counts"]["transitive_missing_required"] = 1

        drift.print_report(rep)
        out = capsys.readouterr().out

        assert "[3b]" in out and "硬依赖缺失" in out
        assert "需处理" in out
        # 同时不能把它说成信息
        assert "可选依赖未安装（extra" not in out

    def test_real_report_buckets_are_json_serialisable(self, report):
        """分桶结果会进 `--json` 归档 ⇒ 必须是普通类型（set 之类会当场炸）"""
        payload = json.dumps(report, ensure_ascii=False)
        reloaded = json.loads(payload)
        assert isinstance(reloaded["transitive_missing_optional"], list)
        assert isinstance(reloaded["transitive_missing_required"], list)



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
