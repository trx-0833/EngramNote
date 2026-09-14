"""OpenAPI 响应契约完整性守卫（overhaul-plan 阶段 5.1 前置修复）

## 这个文件守的是什么

阶段 5.1 要把前端 111 个手写 API 函数换成「从 OpenAPI 生成」。做不了的原因是
**后端有 22 个端点的 2xx 响应在 schema 里是空壳**（`{}` 或
`{"type":"object","additionalProperties":true}`）—— 生成出来的 TS 类型是
`unknown`，切换等于用 `unknown` 换掉手写类型。另有 4 处"外层模型有、内层是空壳"。

那 22 个端点在本轮补齐了 `response_model=`，但**没有任何测试锁住这件事**：
把 `response_model=GraphStats` 删掉，现有测试全绿（前端漂移报告才会发现，
而那是另一个仓库/另一条 CI 链）。

因此这里把判据搬到后端：**用 `app.openapi()` 机械地枚举所有操作，
断言每个带 JSON 响应的 2xx 都没有空壳 schema**。

## 为什么用 `app.openapi()` 而不是 `app.routes`

与 `scripts/dump_openapi.py` 同一个理由：本项目子路由挂在自定义的
`_IncludedRouter` 上，`app.routes` **一条业务路由都枚举不到**（只有 6 项）。
`test_rate_limit_coverage.py::TestRouteEnumerationWorks` 也是因此改用 schema，
并留了防空转守卫。本文件沿用同一口径，并同样带一条"枚举到的路径数必须够多"的守卫。

## 为什么这条判据值得写

"有没有 `response_model`"是**静态契约**，但它决定的后果是**运行时的**：
没有它时客户端拿不到任何字段名，只能靠手抄；而手抄的副本不会在
后端改字段时报警。这个文件就是把"不能再退回去"这件事写成可执行的。
"""

from __future__ import annotations

import pytest

#: 允许"2xx 没有 JSON 响应体"的端点（白名单，每一条都要写清理由）
#:
#: ⚠️ 白名单是**穷举**的：新增一条就必须在这里加一行并说明为什么。
#: 用"忽略前 N 个"或按前缀匹配会让这条守卫慢慢失效。
NON_JSON_2XX = {
    # 204 No Content —— 本来就没有响应体
    ("DELETE", "/api/goals/{goal_id}"),
    ("DELETE", "/api/notes/{note_id}"),
    ("DELETE", "/api/notes/{note_id}/purge"),
    ("DELETE", "/api/understanding/cards/{card_id}"),
    # SSE：`text/event-stream`，OpenAPI 表达不了事件契约；
    # 因此这里如实声明 media type（不是 `application/json` + 空 schema）。
    # 详见 app/api/notes/ask.py 与 app/api/understanding.py 的模块 docstring。
    ("POST", "/api/notes/{note_id}/ask/stream"),
    ("POST", "/api/understanding/ask/stream"),
    # 视频字节流（本地模式 200/206，MinIO 模式 307 重定向到预签名 URL）
    ("GET", "/api/notes/{note_id}/video"),
}

#: 本轮补的 4 处"内层空壳"：字段本身必须指向一个 `$ref`，而不是
#: `additionalProperties: true` 或裸 `{}`。
#: 形如 (组件名, 字段名)：断言该字段（含 anyOf[..., null] 的成员）里有 `$ref`。
INNER_SHELLS = [
    ("AssessmentResponse", "scores"),
    ("AssessmentResponse", "quiz_questions"),
    ("SubmitAnswerResponse", "grading_detail"),
    ("LinkListResponse", "linked_materials"),
    ("LinkListResponse", "linked_personal_notes"),
]

_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")


def _spec() -> dict:
    from app.main import app

    return app.openapi()


def _operations():
    """产出 (method, path, operation) —— 全部来自 schema（唯一可靠的枚举来源）"""
    for path, item in _spec()["paths"].items():
        for method, op in item.items():
            if method in _METHODS:
                yield method.upper(), path, op


def _components() -> dict:
    return _spec().get("components", {}).get("schemas", {})


def _is_empty_shell(node: dict, comps: dict, depth: int = 0) -> bool:
    """判断一个 schema 节点是不是"空壳"（等于没声明结构）

    覆盖三种形态：
    - `{}`（FastAPI 没写 response_model 时的原样输出）
    - `{"type": "object", "additionalProperties": true}`（`Dict[str, Any]`）
    - `anyOf` / `oneOf` 的所有分支都是空壳
    """
    if not isinstance(node, dict):
        return False
    if "$ref" in node:
        if depth > 8:
            return False
        name = node["$ref"].rsplit("/", 1)[-1]
        target = comps.get(name)
        return _is_empty_shell(target, comps, depth + 1) if target else False
    if not node:
        return True
    if node.get("additionalProperties") is True and not node.get("properties"):
        return True
    if (
        node.get("type") == "object"
        and "properties" not in node
        and "additionalProperties" not in node
    ):
        return True
    for key in ("anyOf", "oneOf"):
        members = node.get(key)
        if members:
            return all(_is_empty_shell(m, comps, depth + 1) for m in members)
    return False


class TestRouteEnumerationWorks:
    """防"检查空转"守卫：枚举不到路由时，下面所有断言都会静默通过"""

    def test_schema_enumerates_the_whole_api(self):
        spec = _spec()
        paths = spec.get("paths", {})
        operations = list(_operations())
        assert len(paths) >= 100, (
            f"schema 里只有 {len(paths)} 条路径 —— 枚举方式失效了（app.routes 只有 6 项），"
            "下面的空壳断言会变成空转"
        )
        assert len(operations) >= 110, f"只枚举到 {len(operations)} 个操作"

    def test_known_operation_is_present(self):
        """抽一条真实业务路由，确认枚举到的是业务接口而不是 docs 路由"""
        keys = {(m, p) for m, p, _ in _operations()}
        assert ("GET", "/api/graph/stats") in keys
        assert ("DELETE", "/api/folders/{folder_id}") in keys


class TestNoEmptyResponseShells:
    """阶段 5.1 的核心断言：2xx JSON 响应不得是空壳"""

    def test_no_2xx_json_response_is_an_empty_schema(self):
        comps = _components()
        offenders: list[str] = []
        for method, path, op in _operations():
            if (method, path) in NON_JSON_2XX:
                continue
            for code, resp in sorted(op.get("responses", {}).items()):
                if not code.startswith("2"):
                    continue
                json_body = (resp.get("content") or {}).get("application/json")
                if json_body is None:
                    # 没有 application/json —— 要么是 204，要么是 SSE/视频。
                    # 后者必须在白名单里（否则就是"悄悄换掉了 media type"）。
                    if resp.get("content"):
                        offenders.append(
                            f"{method} {path} {code}: 非 JSON media type "
                            f"{sorted(resp['content'])} 但不在白名单里"
                        )
                    continue
                if _is_empty_shell(json_body.get("schema", {}), comps):
                    offenders.append(
                        f"{method} {path} {code}: 响应 schema 是空壳 "
                        f"({json_body.get('schema')!r}) —— 缺 response_model="
                    )
        assert not offenders, (
            "以下端点的 2xx 响应在 OpenAPI 里仍等于没声明结构，"
            "生成的前端类型会是 unknown：\n  " + "\n  ".join(offenders)
        )

    def test_whitelist_entries_still_describe_real_endpoints(self):
        """白名单不能有僵尸条目（端点改名/删除后必须同步清理）"""
        existing = {(m, p) for m, p, _ in _operations()}
        stale = sorted(NON_JSON_2XX - existing)
        assert not stale, f"白名单里有已不存在的端点，请清理：{stale}"


class TestInnerShellsAreTyped:
    """4 处"外层模型有、内层是空壳"必须指向真实结构"""

    @pytest.mark.parametrize("component,field", INNER_SHELLS)
    def test_field_points_at_a_real_schema(self, component, field):
        comps = _components()
        model = comps.get(component)
        assert model is not None, f"组件 {component} 不存在"
        prop = model.get("properties", {}).get(field)
        assert prop is not None, f"{component}.{field} 不存在"

        def has_ref(node: dict, depth: int = 0) -> bool:
            if not isinstance(node, dict) or depth > 4:
                return False
            if "$ref" in node:
                return True
            for key in ("anyOf", "oneOf"):
                if any(has_ref(m, depth + 1) for m in node.get(key) or []):
                    return True
            if node.get("type") == "array":
                return has_ref(node.get("items", {}), depth + 1)
            return False

        assert has_ref(prop), (
            f"{component}.{field} 仍是空壳（{prop!r}）—— "
            "它会让生成类型里的这个字段变成 unknown / {[k: string]: unknown}"
        )
        assert not _is_empty_shell(prop, comps), f"{component}.{field} 是空壳"


class TestLogoutBodyIsOptionalAndPlain:
    """`POST /api/auth/logout` 的 body：可选，但不是 `anyOf[LogoutRequest, null]`

    "无 body 也能登出"是**行为**（`test_refresh_tokens.py` 里有真实请求锁住），
    这里锁的是**契约**：`Optional[X] = None` 会让 FastAPI 生成
    `anyOf: [LogoutRequest, null]`，生成的客户端于是把 body 类型写成
    `LogoutRequest | null` —— 调用方被迫显式传 null 才能满足类型。
    `X = None` 才是"可缺省但类型干净"。
    """

    def test_request_body_is_optional(self):
        op = _spec()["paths"]["/api/auth/logout"]["post"]
        body = op.get("requestBody")
        assert body, "登出没有声明 requestBody（客户端就不知道能传什么）"
        assert not body.get("required", False), "登出请求体必须是可选的"

    def test_request_body_is_not_nullable(self):
        op = _spec()["paths"]["/api/auth/logout"]["post"]
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "anyOf" not in schema, (
            f"登出请求体仍是 anyOf（{schema!r}）—— 签名里写成了 Optional[LogoutRequest]；"
            "改成 `req: LogoutRequest = None` 即可让它变成干净的 $ref"
        )
        assert "$ref" in schema, f"登出请求体不是 $ref：{schema!r}"


class TestStreamingEndpointsDeclareHonestMediaTypes:
    """SSE / 视频端点：不能声明成 `application/json`（那是在说谎）

    `response_model=` 对 SSE 是**错误的工具**（它会把流校验/包装成一次性 JSON）。
    正确做法是如实声明 media type，并把事件契约写在代码里。
    这条断言防的是"为了让 schema 有类型而给它硬塞一个 JSON 模型"。
    """

    @pytest.mark.parametrize("path", [
        "/api/understanding/ask/stream",
        "/api/notes/{note_id}/ask/stream",
    ])
    def test_sse_declares_event_stream(self, path):
        content = _spec()["paths"][path]["post"]["responses"]["200"]["content"]
        assert list(content) == ["text/event-stream"], (
            f"{path} 的 200 响应 media type 是 {list(content)} —— "
            "SSE 必须是 text/event-stream，声明成 application/json 会让生成的客户端去 response.json()"
        )

    def test_video_declares_mp4(self):
        content = _spec()["paths"]["/api/notes/{note_id}/video"]["get"]["responses"]["200"]["content"]
        assert list(content) == ["video/mp4"], f"视频端点的 media type 是 {list(content)}"
