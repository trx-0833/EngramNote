"""`.env.example` 与 `Settings` 必须一致（阶段 2.3 的可执行判据）

## 这个文件防的是什么

`backend/.env.example` 是访客唯一的配置入口。它一度只登记了 **60 / 102** 个字段，
漏掉的恰恰是**会改变产品行为**的那些 —— `REVIEW_SCHEDULER`（用 FSRS 还是回退
SM-2）、`DAILY_REVIEW_LIMIT`、`LLM_DAILY_TOKEN_QUOTA`、`RAG_RRF_K`、
`MAX_STORAGE_PER_USER_MB`、`BACKUP_KEEP`……

于是"这个系统有配额、有调度器切换、有备份保留策略"这些事实，
**访客从模板里看不出来**：他只会以为这是个没有边界的小玩具。

`Settings` 有 101 个面向用户的字段且还在增长，靠人记得补模板必然漂移。
因此把判据做成可执行的：缺一个就红，多一个（代码已删、模板还留着）也红。

真正的检查逻辑在 `scripts/gen_env_example.py` —— 这里只是把它接进 pytest，
避免"CI 里跑了但本机没人跑"。
"""

import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "gen_env_example.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(BACKEND), capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )


class TestEnvExampleMatchesSettings:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"缺少一致性脚本：{SCRIPT}"

    def test_no_missing_and_no_unknown(self):
        """双向一致：没有未登记字段，也没有已失效的模板项"""
        proc = _run("--check")
        assert proc.returncode == 0, (
            "`.env.example` 与 `app/config.py` 不一致：\n"
            f"{proc.stdout}\n{proc.stderr}\n"
            "补齐方式：python scripts/gen_env_example.py --append"
        )

    def test_report_has_no_unknown_entries(self):
        """模板里不得出现代码中已不存在的变量（访客会照着填一个不存在的开关）"""
        proc = _run()
        assert "未知（模板有、代码无）: 0" in proc.stdout, (
            "模板里存在 app/config.py 中已不存在的变量：\n" + proc.stdout
        )

    def test_append_is_idempotent(self):
        """`--append` 在已一致时必须无副作用（否则 CI 会来回改文件）"""
        before = (BACKEND / ".env.example").read_text(encoding="utf-8")
        _run("--append")
        after = (BACKEND / ".env.example").read_text(encoding="utf-8")
        assert before == after, "已一致的情况下 --append 仍改动了模板"

    def test_append_keeps_the_equals_sign_for_empty_defaults(self):
        """★ 空默认值的行必须是 `KEY=` 而不是 `KEY`

        ## 这条防的是一个**真实发生过**的 bug

        生成器里写过 `.rstrip("=")`，于是默认值为空串的字段被写成
        `# TRUSTED_PROXIES`（**没有 `=`**）。而识别"已登记"的正则要求
        `KEY=` 的形状 —— 下一次 `--append` 就认为它缺失、**又追加一遍**，
        模板里于是出现两条同名变量（一条还长得不像变量）。

        判据：每条 `# KEY` 形式的注释都必须带 `=`。
        """
        import re

        text = (BACKEND / ".env.example").read_text(encoding="utf-8")
        offenders = [
            line for line in text.splitlines()
            if re.match(r"^#\s*[A-Z][A-Z0-9_]{2,}\s*$", line)
        ]
        assert not offenders, (
            "模板里有形如 `# KEY`（缺 `=`）的行 —— 它们会被当成未登记，"
            f"导致 --append 重复追加：{offenders[:5]}"
        )

    def test_no_variable_is_registered_twice(self):
        """生成器追加的变量不得重复登记

        ## 什么算"登记"

        只认**行首**的 `KEY=` 或 `# KEY=`（允许 `# ` 后**最多一个**空格）。
        这条边界很重要：模板里满是**散文式说明**，例如

            #   APP_ENV=prod   → 为空**拒绝启动**（有意的安全姿态）
            #    LOG_SQL=false 这些日志含 bcrypt 哈希…

        那些是**缩进 4 空格的解释文字**，不是变量登记。第一版断言用
        `^#?\\s*` 于是把它们全算成"重复"，一次报 4 个假阳性 ——
        假阳性会让守卫被绕过，这比没有守卫更糟。
        """
        import re

        text = (BACKEND / ".env.example").read_text(encoding="utf-8")
        names = [
            m.group(1)
            for m in re.finditer(r"(?m)^#? ?([A-Z][A-Z0-9_]{2,})=", text)
        ]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        assert not duplicated, (
            f"模板里重复登记的变量：{duplicated}\n"
            "（`--append` 的 rstrip bug 会造成这种形态；同名写两遍还会让"
            "『哪个值生效』取决于行序）"
        )

    def test_append_never_produces_an_indented_registration(self):
        """生成器追加的行必须顶格（`# KEY=`），否则上面那条断言看不到它"""
        import io

        source = io.open(BACKEND / "scripts" / "gen_env_example.py", encoding="utf-8").read()
        assert 'chunks.append(f"# {field.upper()}={value}")' in source, (
            "生成器追加行的格式变了 —— 它必须顶格，且**不能**对空默认值 rstrip('=')"
        )


class TestTemplateIsUsableAsIs:
    """模板被逐字复制后必须能让后端**启动**（配合批次 3 的改动）"""

    def test_app_env_is_active_so_copying_works(self):
        """`APP_ENV=dev` 必须是**生效行**，否则复制模板后 prod 姿态拒绝启动"""
        text = (BACKEND / ".env.example").read_text(encoding="utf-8")
        active = [
            line for line in text.splitlines()
            if line.strip().startswith("APP_ENV=") and not line.lstrip().startswith("#")
        ]
        assert active, (
            "`.env.example` 里没有生效的 APP_ENV —— 复制模板后 app_env 取默认 prod，"
            "而 prod 要求 JWT_SECRET_KEY，新访客会直接启动失败"
        )
        assert active[0].split("=", 1)[1].strip() == "dev", (
            f"模板里的 APP_ENV 应为 dev（开发姿态），实际：{active[0]}"
        )

    def test_glm_model_is_active(self):
        """`GLM_MODEL` 必须是生效行：用例与真实调用都要求它存在且全小写"""
        text = (BACKEND / ".env.example").read_text(encoding="utf-8")
        active = [
            line for line in text.splitlines()
            if line.strip().startswith("GLM_MODEL=") and not line.lstrip().startswith("#")
        ]
        assert active, "模板里 GLM_MODEL 是注释掉的，复制模板后 RAG/理解链路配置不完整"
        value = active[0].split("=", 1)[1].strip()
        assert value == value.lower(), f"GLM_MODEL 必须全小写（智谱要求），实际：{value}"
