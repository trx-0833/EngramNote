"""`.env.example` 与 `Settings` 的双向一致性检查（阶段 2.3）

## 为什么需要它

`backend/.env.example` 是访客唯一的配置入口，但它一度只登记了 **60 / 102** 个
字段 —— 漏掉的恰恰是**会改变产品行为**的那些：`REVIEW_SCHEDULER`（用 FSRS 还是
回退 SM-2）、`DAILY_REVIEW_LIMIT`、`LLM_DAILY_TOKEN_QUOTA`、`RAG_RRF_K`、
`MAX_STORAGE_PER_USER_MB`、`BACKUP_KEEP`……

于是"这个系统有配额、有调度器切换、有备份保留策略"这些事实，
**访客从模板里看不出来**。

而 `Settings` 已经 102 个字段、还在增长，靠人记得补模板必然漂移 ——
本仓库对"同一事实写两遍"的代价吃过很多次亏，所以这里做成**可执行的判据**：

    python scripts/gen_env_example.py --check     # CI 与 pytest 都跑

## 判据（双向）

1. **缺失**：`Settings` 里有、模板里一次都没提到（既非生效行也非注释行）→ 报错。
   注释行也算"提到"：模板允许把不常用项写成说明。
2. **未知**：模板里提到的变量，`Settings` 里已经没有 → 报错。
   这条防的是反向漂移：字段被删了、模板还留着，访客照着填一个不存在的开关。

## 用法

    python scripts/gen_env_example.py            # 只报告，不改文件
    python scripts/gen_env_example.py --check    # 有缺失/未知则退出 1（CI 用）
    python scripts/gen_env_example.py --append   # 把缺失项按分组追加到模板末尾
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PY = BACKEND / "app" / "config.py"
ENV_EXAMPLE = BACKEND / ".env.example"

#: `    field_name: type = default` —— 只取 Settings 类的字段定义行
_FIELD_RE = re.compile(r"^    ([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z\[]", re.M)

#: 模板里的一条变量（生效行或注释行都算）
_ENV_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]{2,})\s*=", re.M)

#: 这些字段由代码内部推导，不需要用户配置
NOT_USER_FACING = {
    "app_name",           # 固定为 EngramNote
    "api_prefix",         # 路由前缀，改动需同步前端
}

#: 追加时的分组建议（关键词 → 分组标题），未命中的进"其他"
GROUPS = [
    ("llm_", "LLM 调用治理（重试 / 限额 / 缓存 / 计价）"),
    ("rag_", "检索融合（RRF 与候选池）"),
    ("fsrs_", "调度算法（FSRS）"),
    ("review_", "复习调度（每日上限 / 到期时刻 / fuzz）"),
    ("max_storage", "配额与限额"),
    ("embedding_", "嵌入模型与切块"),
    ("backup_", "备份保留"),
    ("log_", "日志"),
    ("jwt_", "JWT 令牌"),
]


def settings_fields() -> list[str]:
    source = CONFIG_PY.read_text(encoding="utf-8")
    fields = _FIELD_RE.findall(source)
    return [f for f in fields if f not in NOT_USER_FACING]


def template_names() -> set[str]:
    return {m.group(1).lower() for m in _ENV_RE.finditer(ENV_EXAMPLE.read_text(encoding="utf-8"))}


def group_of(field: str) -> str:
    for prefix, title in GROUPS:
        if field.startswith(prefix):
            return title
    return "其他配置"


def report() -> tuple[list[str], list[str]]:
    fields = settings_fields()
    documented = template_names()
    missing = [f for f in fields if f not in documented]
    unknown = sorted(documented - set(fields))
    return missing, unknown


def append_missing(missing: list[str]) -> int:
    """把缺失字段按分组追加到模板末尾（保留既有内容与顺序）"""
    if not missing:
        return 0
    source = CONFIG_PY.read_text(encoding="utf-8")
    defaults: dict[str, str] = {}
    for line in source.splitlines():
        m = re.match(r"^    ([a-z_][a-z0-9_]*)\s*:\s*[^=]+=\s*(.+?)\s*$", line)
        if m:
            defaults[m.group(1)] = m.group(2)

    buckets: dict[str, list[str]] = {}
    for field in missing:
        buckets.setdefault(group_of(field), []).append(field)

    chunks = ["\n# ---- 由 scripts/gen_env_example.py 补齐的配置项（阶段 2.3）----",
              "# 这些字段此前只在 app/config.py 里存在，模板未登记 —— 访客因此看不出",
              "# 系统有配额、有调度器切换、有备份保留策略。默认值即代码默认值。",
              ""]
    for title, items in buckets.items():
        chunks.append(f"# ---- {title} ----")
        for field in items:
            value = defaults.get(field, "")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]  # .env 里字符串不写引号
            # ⚠️ 这里的 `=` 必须**总是**保留，即使默认值是空串：
            # 解析器（本文件的 `_ENV_RE`）按 `KEY=` 的形状识别变量，
            # 空默认值被 rstrip("=") 成 `# TRUSTED_PROXIES` 后就不再算"已登记"，
            # 于是下一次 --append 会把它当缺失项**再追加一遍**（实测发生过）。
            chunks.append(f"# {field.upper()}={value}")
        chunks.append("")

    with open(ENV_EXAMPLE, "a", encoding="utf-8") as fh:
        fh.write("\n".join(chunks) + "\n")
    return len(missing)


def main() -> int:
    # Windows 控制台默认 GBK：输出里的 ✅/❌ 会 UnicodeEncodeError。
    # 这是本仓库在别处也踩过的坑（见 scripts/security_scan.py 的处理）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - 非交互环境
        pass

    parser = argparse.ArgumentParser(description="校验/补齐 .env.example")
    parser.add_argument("--check", action="store_true", help="有缺失或未知则退出 1")
    parser.add_argument("--append", action="store_true", help="把缺失项按分组追加到模板")
    args = parser.parse_args()

    missing, unknown = report()
    total = len(settings_fields())
    documented = len(template_names())

    print(f"Settings 字段（面向用户）: {total}")
    print(f"模板已登记变量: {documented}")
    print(f"缺失: {len(missing)}")
    if missing:
        print("  " + ", ".join(missing[:40]) + (" …" if len(missing) > 40 else ""))
    print(f"未知（模板有、代码无）: {len(unknown)}")
    if unknown:
        print("  " + ", ".join(unknown[:20]))

    if args.append and missing:
        appended = append_missing(missing)
        print(f"\n已追加 {appended} 项到 {ENV_EXAMPLE.name}")
        missing, unknown = report()
        print(f"复查：缺失 {len(missing)}，未知 {len(unknown)}")
        return 0 if not missing and not unknown else 1

    consistent = not missing and not unknown
    if not consistent and args.check:
        print("\n❌ .env.example 与 app/config.py 不一致（见上方清单）")
        print("   补齐：python scripts/gen_env_example.py --append")
        return 1

    # ⚠️ 这里曾经只判 `args.check and (...)`，于是**不带 --check** 时
    # 缺失 1 项也会打印"✅ 一致" —— 一个会说谎的守卫比没有守卫更糟。
    # 现在无论哪种调用方式，判定都取自 consistent。
    if consistent:
        print("\n✅ .env.example 与 app/config.py 一致")
        return 0
    print("\n⚠️ 存在未登记字段（未加 --check，故不返回非 0；补齐用 --append）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
