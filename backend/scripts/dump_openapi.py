"""把 FastAPI 应用的 OpenAPI schema 落盘为 JSON（overhaul-plan 阶段 5.1 的输入）

## 为什么要有这个脚本

阶段 5.1 要把前端手写的 API 客户端（`frontend/src/api/*.ts`）换成由后端 schema
生成的类型 + 客户端。生成的第一步是**拿到 schema**，而这个 schema 必须来自
**真实注册的路由**，不能靠人读代码抄。

## 为什么是 `app.openapi()`，而不是 `app.routes`

本项目 `backend/app/api/router.py` 用一个自定义的 `_IncludedRouter` 挂子路由，
**`app.routes` 里枚举不到业务路由**（实测只有 6 项：默认的 docs/redoc/openapi
路由 + CORS 等，一条业务路由都没有）。项目自己的覆盖测试
（`backend/tests/test_rate_limit_coverage.py::TestRuleCoverageAgainstRealRoutes`）
就是因此改用 `app.openapi()["paths"]`，并专门留了一条
`test_route_enumeration_works` 守卫防止这个检查静默空转。

所以：**`app.openapi()` 是唯一可靠的枚举来源**，本脚本沿用它。

## 为什么不启服务

`app.openapi()` 是纯内存计算：它读的是路由对象上已有的 `response_model` /
`parameters` 声明，**不连数据库、不连 Redis、不触发 lifespan**。因此本脚本
可以在任何安装了 `backend/requirements.txt` 的环境里离线跑，也不需要占用端口
（两个并行的 agent 都在用固定端口，这点很重要）。

⚠️ 一个已知的副作用：`app.main` 在导入时会执行 `get_settings()`，也就可能读取
`backend/.env`。它只读配置，不写任何东西。

## 输出路径的选择

写到 `backend/openapi.json`（仓库内、可 diff），理由：

1. **前端生成不再依赖 Python**：`npm run gen:api` 只需要这份 JSON，
   任何只装了 Node 的机器（CI 前端 job、新同事的笔记本）都能重跑生成；
2. **漂移可见**：后端加了一个字段/路由，`git diff backend/openapi.json` 会直接
   显示出来，而不是等到前端运行时才发现；
3. **可校验**：`--check` 模式让 CI 断言"落盘的 schema 与当前代码一致"，
   防的是"改了后端忘了重新生成"。

## 用法

    # 生成（默认写 backend/openapi.json）
    python backend/scripts/dump_openapi.py

    # 只校验落盘文件是否与当前代码一致（CI 用，不一致则退出码 1）
    python backend/scripts/dump_openapi.py --check

    # 写到别处 / 打到 stdout
    python backend/scripts/dump_openapi.py -o /tmp/openapi.json
    python backend/scripts/dump_openapi.py --stdout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# 脚本位于 backend/scripts/，需要把 backend/ 放进 sys.path 才能 `import app.*`
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: 默认输出路径：仓库内的 `backend/openapi.json`（见模块 docstring 里的理由）
DEFAULT_OUTPUT = BACKEND_DIR / "openapi.json"


def build_schema() -> dict:
    """在进程内构建 OpenAPI schema

    刻意**不**调用 `uvicorn` / `TestClient`：`app.openapi()` 不需要事件循环，
    也不需要数据库。用 TestClient 会把 lifespan 拖进来（建表、连 LLM 配置），
    把"生成一份文档"变成"启动一次服务"。

    Returns:
        OpenAPI schema（dict）
    """
    from app.main import app  # noqa: PLC0415 — 必须在 sys.path 处理之后导入

    return app.openapi()


def render(schema: dict) -> str:
    """把 schema 渲染成**确定性**的 JSON 文本

    确定性（deterministic）是 `--check` 能成立的前提：
    - `sort_keys=True`：字典序固定，避免插入顺序变化造成假 diff；
    - `ensure_ascii=False`：中文 summary/description 原样保留，文件小且可读；
    - 末尾一个换行：符合 POSIX 文本文件约定，也让 diff 不出现 "\\ No newline"。

    **不写时间戳**：写入时间会让每次生成都产生 diff，`--check` 就永远失败。
    需要知道"这是哪次生成的"，看 git 提交即可。

    Args:
        schema: `app.openapi()` 的返回值

    Returns:
        以 "\\n" 结尾的 JSON 字符串
    """
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def summarize(schema: dict) -> str:
    """生成一行摘要，供生成/校验时打印

    摘要里带上 schema 的 sha256 前 12 位：前后端联调时对不上时，第一件事就是
    比对两边的指纹，而不是逐字段猜。
    """
    paths = schema.get("paths", {})
    schemas = schema.get("components", {}).get("schemas", {})
    operations = sum(
        1
        for item in paths.values()
        for method in item
        if method in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    )
    digest = hashlib.sha256(render(schema).encode("utf-8")).hexdigest()[:12]
    return (
        f"paths={len(paths)} operations={operations} schemas={len(schemas)} sha256={digest}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 FastAPI 的 OpenAPI schema 落盘为确定性 JSON（阶段 5.1）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出路径（默认 {DEFAULT_OUTPUT}）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="不写文件，只校验 `--output` 与当前代码生成的结果是否一致；不一致退出码 1",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="打到 stdout 而不是写文件（与 --check 互斥）",
    )
    args = parser.parse_args()

    if args.check and args.stdout:
        parser.error("--check 与 --stdout 不能同时使用")

    schema = build_schema()
    text = render(schema)
    summary = summarize(schema)

    if args.stdout:
        sys.stdout.write(text)
        return 0

    output: Path = args.output
    if args.check:
        if not output.exists():
            print(f"[FAIL] {output} 不存在 —— 请先运行本脚本生成", file=sys.stderr)
            return 1
        on_disk = output.read_text(encoding="utf-8")
        if on_disk != text:
            print(
                f"[FAIL] {output} 与当前代码不一致 —— 后端契约已变更，请重新生成\n"
                f"       当前代码: {summary}",
                file=sys.stderr,
            )
            return 1
        print(f"[ OK ] {output} 与当前代码一致 | {summary}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    # newline="" + utf-8：避免 Windows 平台把 "\n" 翻成 "\r\n"，
    # 否则同一份 schema 在 Windows/Linux 上生成出不同的字节，--check 会跨平台误报。
    with output.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"[ OK ] 已写入 {output} ({len(text.encode('utf-8'))} 字节) | {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
