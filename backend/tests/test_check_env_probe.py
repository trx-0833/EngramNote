"""`check_env.py` 的依赖探测层（步骤 3）必须被测试守着

## 这个文件锁的是什么

2026-09-26 的改造把第 3 步从"22 个模块**串行真实导入**"改成"**并行 + 分层**"：

    原版（串行全量）      31.98s
    新版（分层 + 并行）    1.35s      ← 每个启动周期都跑的就是这条
    新版 --deep（全量）   26.34s     ← 严格性没有丢，只是改成按需

省下来的 30s 靠的不是"并行"（实测只值 -11%），而是**不再真实导入四个
"启动路径根本不会导入"的模块**（见 `check_env.HEAVY_MODULES`）。

**一旦这条前提被悄悄破坏，这个改造就从"更快"变成"更不安全"**：
那些模块只是"确认装了"，如果它们其实会被启动路径导入，
就等于给启动路径上的依赖开了个只验安装的绿灯。所以这里的每条断言
对应一个具体的失效方式，而不是为了覆盖率：

1. `HEAVY_MODULES` 里的键必须是真实存在的模块（防拼错、防留下已删除的包 ——
   本仓库在 `chromadb` 上踩过一模一样的坑，见 `test_check_env_consistency.py`）；
2. 每个重型模块必须**写明理由**（空字符串 = 这条豁免没有依据）；
3. 快速模式**只**对重型模块降级，其余一律真实导入（不能悄悄扩大豁免面）；
4. `--deep` 一律真实导入（严格性开关必须真的有效）；
5. 声明过的模块**一个都不能漏测**，且输出顺序与声明顺序一致
   （漏测 = 假绿；乱序 = 用户对不上号）；
6. 必需依赖缺失时仍必须判为失败（分层不能把"缺失"也一起放过）；
7. **失败项必须串行复验**：并行导入会制造"半初始化模块"假失败 ——
   实测 `sqlalchemy` + `alembic` 两线程并发 8/8 全中（见
   `test_probe_modules.py` 的对应用例），这不是理论风险；
8. 重型模块必须**真的**不在启动闭包里 —— 起子进程 `import app.main`
   再回头看 `sys.modules`，把上面那条前提变成可执行判据。

## 为什么用假探针，而不是真的导入

真导入 `sentence_transformers` 要 20.6s、`openai` 2.7s —— 测试套件不该为了
验证"调度逻辑"去付这份钱。因此除第 8 条（必须真实，否则证明不了任何事）
以外，全部用 monkeypatch 替换探测函数，只验证**调度、顺序、判定**。
"""

import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

#: `backend/tests/xxx.py` → parents[2] = 仓库根（`check_env.py` 在那里）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须在 sys.path 之后导入：check_env.py 在仓库根目录，不在 backend/ 包内。
import check_env  # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MARKER_LINE = re.compile(r"^\s*\[[✓✗!i]\]\s*(.*)$")


def _all_declared_modules() -> list[str]:
    """必需 + 可选，去重保序 —— 与 `check_backend_deps()` 内部的取法一致"""
    return list(dict.fromkeys([*check_env.REQUIRED_PACKAGES, *check_env.OPTIONAL_PACKAGES]))


def _messages(stdout: str) -> list[str]:
    """把带 ANSI 颜色的输出拆成"每个标记行后面的正文" """
    out = []
    for line in _ANSI.sub("", stdout).splitlines():
        m = _MARKER_LINE.match(line)
        if m:
            out.append(m.group(1))
    return out


@pytest.fixture(autouse=True)
def _isolate_result_counters():
    """`check_env.results` 是模块级全局：用例之间不能互相污染计数"""
    snapshot = dict(check_env.results)
    yield
    check_env.results.clear()
    check_env.results.update(snapshot)


class TestHeavyModuleList:
    """重型模块清单本身必须是可信的"""

    def test_keys_are_real_module_names(self):
        """键必须是真实存在的模块名（防止拼错或留下已删除的包）

        本仓库在 `chromadb` 上踩过这个坑：清单描述了一个不存在的架构，
        于是"假失败"和"假通过"同时发生（`test_check_env_consistency.py` 文件头）。
        """
        declared = set(check_env.REQUIRED_PACKAGES) | set(check_env.OPTIONAL_PACKAGES)
        ghosts = sorted(set(check_env.HEAVY_MODULES) - declared)
        assert not ghosts, (
            f"HEAVY_MODULES 里有不在 REQUIRED/OPTIONAL 清单里的模块：{ghosts}\n"
            "要么是拼错了，要么是依赖已删除 —— 两种情况都会让分层失去依据。"
        )

    def test_every_heavy_module_states_why(self):
        """每条豁免都要写清理由，否则它就变成"为了快而关掉检查"的后门"""
        empty = sorted(m for m, reason in check_env.HEAVY_MODULES.items() if not reason.strip())
        assert not empty, f"这些重型模块没有写理由：{empty}"


class TestProbeDispatch:
    """探测方式的选择：只有"昂贵 且 不在启动闭包内"的模块才降级"""

    def test_fast_mode_downgrades_heavy_modules_only(self):
        downgraded = {
            module
            for module in _all_declared_modules()
            if check_env._pick_probe(module, deep=False) is check_env._probe_by_find_spec
        }
        assert downgraded == set(check_env.HEAVY_MODULES), (
            "快速模式降级的模块集合必须**恰好等于** HEAVY_MODULES —— "
            f"实际降级了 {sorted(downgraded)}"
        )

    def test_deep_mode_imports_everything(self):
        still_downgraded = [
            module
            for module in _all_declared_modules()
            if check_env._pick_probe(module, deep=True) is not check_env._probe_by_import
        ]
        assert not still_downgraded, (
            f"--deep 下这些模块仍然没有真实导入：{still_downgraded}\n"
            "严格性开关失效 = 用户以为验过了其实没有。"
        )


class TestProbeEngine:
    """并行探测引擎：顺序、复验、覆盖"""

    def test_results_keep_declaration_order_despite_uneven_durations(self, monkeypatch):
        """慢的排前面也必须按入参顺序返回（`Executor.map` 的保序语义）"""
        delays = {"slow": 0.06, "medium": 0.03, "fast": 0.0}

        def fake_pick(module, deep):
            def probe(_module=module):
                time.sleep(delays[_module])
                return True, check_env.DETAIL_IMPORTED, delays[_module]

            return probe

        monkeypatch.setattr(check_env, "_pick_probe", fake_pick)
        modules = ["slow", "medium", "fast"]
        returned = [row[0] for row in check_env._probe_modules(modules, deep=False)]
        assert returned == modules

    def test_failure_is_retried_serially_and_reported_as_success(self, monkeypatch):
        """并行制造的"半初始化模块"假失败必须在串行复验后消失

        实测（独立子进程，两线程并发导入 `sqlalchemy` + `alembic`）**8/8 全中**：
            AttributeError: partially initialized module 'sqlalchemy.sql.roles'
                            has no attribute 'StatementRoles'
        如果直接采信第一次结果，用户会看到"socket 已安装却报未安装"的假失败。

        ⚠️ 模块名必须是**不存在的假名**：复验路径会清理 `sys.modules` 里该模块及其
        子模块，用真实包名（例如 `sqlalchemy`）会把**已在本进程里用着的**模块删掉，
        造成类身份分裂 —— 后续 `event.listen(engine, "connect")` 会报
        `No such event 'connect'`。这不是假设：本文件的第一版就是这么写的，
        它让整个测试套件多出 331 个错误（见提交记录）。
        """
        fake_module = "_engramnote_probe_fixture_pkg"
        calls = []

        def fake_probe(module):
            calls.append(module)
            if len(calls) == 1:
                return (
                    False,
                    "cannot import name 'Column' from partially initialized module 'sqlalchemy.sql.schema'",
                    0.0,
                )
            return True, check_env.DETAIL_IMPORTED, 0.01

        monkeypatch.setattr(check_env, "_pick_probe", lambda module, deep: fake_probe)
        module, passed, detail, _cost = check_env._probe_modules([fake_module], deep=False)[0]

        assert calls == [fake_module, fake_module], "失败项必须被复验一次"
        assert passed is True, "串行复验成功后不得再报失败"
        assert detail == check_env.DETAIL_IMPORTED
        assert module == fake_module

    def test_failure_that_survives_serial_retry_is_still_a_failure(self, monkeypatch):
        """反方向：复验也失败就是真失败 —— 复验不是"抹掉失败"的机制"""

        def fake_probe(module):
            return False, "No module named 'ghost'", 0.0

        monkeypatch.setattr(check_env, "_pick_probe", lambda module, deep: fake_probe)
        _module, passed, detail, _cost = check_env._probe_modules(["ghost"], deep=False)[0]

        assert passed is False
        assert "已串行复验" in detail, "真失败要标出「已复验」，与并行竞态区分开"

    def test_retry_does_not_purge_a_module_that_is_already_imported(self, monkeypatch):
        """已经在 `sys.modules` 里的模块**绝不能**被复验清掉

        ## 为什么这条必须有（本文件第一版踩过，代价是整个测试套件 331 个错误）

        复验失败项时会清理 `sys.modules` 里该模块的**子模块** —— 这是必要的：
        不清就会继承半初始化的废墟，复验"成功"但其实是晚到的假绿。
        但清理只对"**模块自己已不在 `sys.modules`**"成立。若模块已经导完了
        （并发时被别的线程放进去的），再清就会让已经持有它的代码**类身份分裂**，
        症状是后续 `event.listen(engine, "connect")` 报
        `No such event 'connect' for target 'Engine(...)'` —— 而报错的地方离
        肇事处十万八千里。

        因此：模块在 `sys.modules` 里时，复验必须原样重导（拿缓存），不许清。
        """
        # 用自己塞进去的哨兵模块，不拿真实模块做实验 ——
        # 万一被测代码真有 bug，也不会把本进程依赖的东西删掉
        sentinel = "_engramnote_probe_already_imported"
        sys.modules[sentinel] = types.ModuleType(sentinel)
        calls = []

        def fake_probe(module):
            calls.append(module)
            # 第一次报失败（模拟并行竞态），第二次成功
            return (len(calls) > 1), "fake", 0.0

        monkeypatch.setattr(check_env, "_pick_probe", lambda module, deep: fake_probe)
        try:
            check_env._probe_modules([sentinel], deep=False)
            assert sentinel in sys.modules, (
                f"复验把已经在用的 {sentinel!r} 从 sys.modules 里清掉了 —— "
                "这会让持有它的代码类身份分裂。"
            )
            assert calls == [sentinel, sentinel]
        finally:
            sys.modules.pop(sentinel, None)


class TestCheckBackendDeps:
    """`check_backend_deps()` 这一层的判定与输出"""

    @pytest.fixture
    def fake_probes(self, monkeypatch):
        """记录被探测的模块；全部返回成功"""
        probed: list[str] = []

        def fake_import(module):
            probed.append(module)
            return True, check_env.DETAIL_IMPORTED, 0.0

        def fake_spec(module):
            probed.append(module)
            return True, check_env.DETAIL_INSTALLED_ONLY, 0.0

        monkeypatch.setattr(check_env, "_probe_by_import", fake_import)
        monkeypatch.setattr(check_env, "_probe_by_find_spec", fake_spec)
        return probed

    def test_every_declared_module_is_probed_exactly_once(self, fake_probes, capsys):
        """一个都不能漏测，也不能重复测（漏测 = 假绿）"""
        assert check_env.check_backend_deps(deep=False) is True
        capsys.readouterr()
        assert fake_probes == _all_declared_modules()

    def test_output_follows_declaration_order(self, fake_probes, capsys):
        """输出顺序 = 声明顺序：必需在前、可选在后，用户才对得上号"""
        check_env.check_backend_deps(deep=False)
        messages = _messages(capsys.readouterr().out)

        required = list(check_env.REQUIRED_PACKAGES.values())
        head = messages[: len(required)]
        assert len(head) == len(required), "必需包的输出行数不对"
        for index, (message, package) in enumerate(zip(head, required, strict=True)):
            assert message == package or message.startswith(f"{package}（"), (
                f"第 {index + 1} 行应该是 {package}，实际是 {message!r}"
            )

    def test_missing_required_module_is_a_failure(self, monkeypatch, capsys):
        """分层不能把"没装"也一起放过 —— find_spec 说没有就是没有"""

        def fake_import(module):
            if module == "fastapi":
                return False, "未安装", 0.0
            return True, check_env.DETAIL_IMPORTED, 0.0

        monkeypatch.setattr(check_env, "_probe_by_import", fake_import)
        assert check_env.check_backend_deps(deep=False) is False

        out = _ANSI.sub("", capsys.readouterr().out)
        assert any(line.strip().startswith("[✗] fastapi") for line in out.splitlines()), (
            "缺失的必需依赖必须以 [✗] 出现，而不是混在 ✓ 里"
        )

    def test_fast_mode_says_out_loud_what_it_did_not_verify(self, fake_probes, capsys):
        """只确认"已安装"时必须在输出里说明 —— 这是"不给假绿"的落点"""
        check_env.check_backend_deps(deep=False)
        messages = _messages(capsys.readouterr().out)
        joined = "\n".join(messages)

        assert check_env.DETAIL_INSTALLED_ONLY in joined
        for module in check_env.HEAVY_MODULES:
            assert module in joined, f"{module} 属于「仅确认已安装」，必须在输出里点名"
        assert "--deep" in joined, "要告诉用户怎么才能验到真实导入"

    def test_deep_mode_reports_full_import(self, fake_probes, capsys):
        """`--deep` 下不该再出现"仅确认已安装" """
        check_env.check_backend_deps(deep=True)
        joined = "\n".join(_messages(capsys.readouterr().out))
        assert check_env.DETAIL_INSTALLED_ONLY not in joined


class TestHeavyModulesAreOutsideTheStartupPath:
    """判据 2 的可执行版本：重型模块必须**真的**不在启动闭包里

    分层检测省下 30s 的全部依据就是这一条。它必须是**实测**：
    `import app.main` 之后回头看 `sys.modules`，而不是静态读代码 ——
    静态分析会被 `from .x import y`、动态导入和间接依赖骗过去。

    ## 为什么值得起一个子进程（约 4~6s）
    因为这是唯一能证明"启动不会导入它们"的方法。有人把 `torch` 或 `openai`
    加进启动路径时，这里的失败信息会直接指名道姓，而不是等到用户机器上
    "自检全绿但服务起不来"（本仓库在 `pypdfium2` / `Pillow` 上踩过，
    见 `check_env.py:419-423` 的说明）。
    """

    def test_startup_does_not_import_heavy_modules(self, tmp_path):
        heavy = sorted(check_env.HEAVY_MODULES)
        code = (
            "import json, sys\n"
            "import app.main\n"
            f"heavy = {heavy!r}\n"
            "print('HEAVY=' + json.dumps(sorted(m for m in heavy if m in sys.modules)))\n"
        )

        # 显式把库与 Vault 指到临时目录：这条用例绝不能碰真实数据
        # （`tests/conftest.py:108-133` 记过"测试写到真实 Vault"的代价）
        env = os.environ.copy()
        env["APP_ENV"] = "dev"
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'startup-probe.db'}"
        env["VAULT_DIR"] = str(tmp_path / "vault")

        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PROJECT_ROOT / "backend"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=600,
        )
        assert proc.returncode == 0, (
            "`import app.main` 失败了，本用例无法判断启动闭包：\n" + (proc.stderr or "")[-2000:]
        )

        marker = [line for line in proc.stdout.splitlines() if line.startswith("HEAVY=")]
        assert marker, f"探针没有输出结果：\nstdout={proc.stdout[-500:]}\nstderr={proc.stderr[-500:]}"

        imported = json.loads(marker[0][len("HEAVY=") :])
        assert imported == [], (
            f"这些模块被当成「不在启动闭包内」，但 `import app.main` 之后它们在 sys.modules 里：{imported}\n"
            "后果：它们只被验到「已安装」，而启动路径其实需要它们「能真正导入」。\n"
            "修法二选一：(a) 从 check_env.HEAVY_MODULES 里删掉它们（恢复真实导入）；"
            "(b) 如果这份导入是**惰性**的（函数内 import），把启动路径改成惰性。"
        )
