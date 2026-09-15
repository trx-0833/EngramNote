# -*- coding: utf-8 -*-
"""统一错误契约的**采用守卫**（overhaul-plan 阶段 0.11）

## 这份测试防的是什么

阶段 0.11 的验收是"后端改文案不破坏前端"。设施（`core/app_error.py` +
`middleware/error_handler.py` + `main.py` 的处理器）**早就落地了**，但采用面
一度只有 2/154：`raise AppError` 2 处，而 `raise HTTPException` **152 处**。
设施齐全而无人使用，是这类改造最典型的失败形态 —— 它不会报错，只会让
"统一错误契约"这句话停在文档里（overhaul-plan **阶段 0 表格的 0.11 行**原文：
"只要还有一处 `message.includes('每日上限')` 就不成立"）。
⚠️ 这里刻意**不引用行号**：附录每加一篇，行号就整体位移一次（附录 BK.7 第 8 条
记的正是这个教训），引用要落在"表格里的哪一行 / 哪一篇附录"上。

所以这里守的不是"今天有多少处"，而是**"有没有新的 HTTPException 冒出来"**：

  * `app/api/**` 与 `app/services/**` 下任何新增的 `raise HTTPException` 都会红，
    除非显式写明豁免理由（服务层连豁免都不允许：那里不需要响应头）；
  * 扫描器自身必须被证明"真的会红" —— 否则一个坏掉的扫描器会永远返回
    "没有违规"，而那正是"一切正常"的样子（本仓库反复踩过这个形态，
    见 tests/test_dependency_drift_check.py 的同类反空洞设计）；
  * `AppError` 的 code 必须是 `core/app_error.py` 里声明过的常量名
    （手写字面量会绕过常量区，写出没有语义的码）。

## 为什么允许"豁免"，而不是一刀切禁止

有一类 HTTPException **必须**留着：`AppError` 走 `ErrorHandlerMiddleware`，
而该中间件构造响应时**不转发 headers**。凡是响应需要带额外头的地方
（如 401 的 `WWW-Authenticate: Bearer`），迁到 AppError 会让那个头静默
消失 —— 那才是真的行为倒退（`tests/test_auth_contract.py` 正是为此存在）。

豁免必须写成紧挨着 raise 的一行注释：`# error-contract: exempt — <理由>`。
写明理由这件事本身就是守卫的一部分：豁免要有代价，才不会被随手加上。
"""

import ast
import io
import os
import re
import uuid

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(BACKEND, "app", "api")
SERVICES_DIR = os.path.join(BACKEND, "app", "services")
APP_DIR = os.path.join(BACKEND, "app")

#: 豁免标记（必须与理由同行，理由分隔符之后不得为空）
EXEMPT_MARKER = "error-contract: exempt"

#: 标记出现在 raise 语句**上方多少行以内**算数（只允许紧邻或同一行）
_MARKER_LOOKBACK = 3

#: 守卫要求的**最少扫描文件数**：app/api 下真实存在的路由模块数远多于此。
#: 这个数字是反空洞用的下限，不是精确期望值 —— 目录被整体挪走/改名时，
#: 扫描会退化成"零个文件、零个违规"，而那看起来正是通过。
MIN_SCANNED_FILES = 15


def _iter_py_files(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _unparse(node):
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - 只可能出现在语法怪异的表达式上
        return "<unparse failed>"


def _is_http_exception_raise(node):
    """判断一个 ast.Raise 是不是 `raise HTTPException(...)`"""
    if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
        return False
    return _unparse(node.exc.func).split(".")[-1] == "HTTPException"


def _exempt_reason(lines, lineno):
    """返回该 raise 的豁免理由；没有豁免标记时返回 None

    标记允许出现在 raise 同一行（尾注释）或紧邻的上方若干行（多行 raise 的
    常见写法是注释写在 `raise HTTPException(` 之上）。
    """
    for offset in range(0, _MARKER_LOOKBACK + 1):
        idx = lineno - 1 - offset
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        if EXEMPT_MARKER in line:
            reason = line.split(EXEMPT_MARKER, 1)[1].lstrip(" —-:")
            return reason.strip() or None
    return None


def scan_source(path, source):
    """扫描一份源码，返回 (违规列表, 豁免列表, raise 总数)

    做成**纯函数**（路径 + 文本 → 结果），这样反空洞用例可以喂人造源码，
    不必真的往 app/api 里塞一个违规文件。
    """
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    violations = []
    exemptions = []
    total = 0
    for node in ast.walk(tree):
        if not _is_http_exception_raise(node):
            continue
        total += 1
        reason = _exempt_reason(lines, node.lineno)
        if reason:
            exemptions.append("%s:%d —— %s" % (path, node.lineno, reason))
        else:
            violations.append("%s:%d" % (path, node.lineno))
    return violations, exemptions, total


def scan_tree(root):
    """扫描目录下所有 .py，返回 (违规, 豁免, 扫描文件数, raise 总数)"""
    violations, exemptions = [], []
    scanned_files = 0
    total = 0
    for path in _iter_py_files(root):
        with io.open(path, encoding="utf-8") as fh:
            source = fh.read()
        rel = os.path.relpath(path, BACKEND)
        v, e, n = scan_source(rel, source)
        violations.extend(v)
        exemptions.extend(e)
        total += n
        scanned_files += 1
    return violations, exemptions, scanned_files, total


# ---------------------------------------------------------------------------
# 1. 真实扫描：新增的 HTTPException 必须红
# ---------------------------------------------------------------------------

class TestNoUnmigratedHttpException:
    """app/api/** 下不允许出现未经豁免的 `raise HTTPException`"""

    def test_no_unexempted_http_exception_in_api(self):
        """**核心**：新增一处 `raise HTTPException` 就会在这里红

        违规会带上 `文件:行号`，修法二选一：
        1. 迁移到 `raise AppError(CODE, "<原文案>", <原状态码>)`
           —— **状态码与文案必须原样保留**，这是契约重构不是文案改造；
        2. 确认它确实必须带额外响应头（ErrorHandlerMiddleware 不转发 headers），
           紧邻写一行 `# error-contract: exempt — <理由>`。
        """
        violations, exemptions, scanned_files, total = scan_tree(API_DIR)

        assert scanned_files >= MIN_SCANNED_FILES, (
            f"app/api 下只扫到 {scanned_files} 个 .py 文件（期望 >= {MIN_SCANNED_FILES}）"
            " —— 扫描范围失效了，这份守卫正在空转"
        )
        assert not violations, (
            "以下位置仍在直接抛 HTTPException，未接入统一错误契约：\n  "
            + "\n  ".join(sorted(violations))
            + f"\n（当前豁免 {len(exemptions)} 处；豁免写法见本文件顶部说明）"
        )

    def test_exemptions_are_present_and_justified(self):
        """豁免不是"扫不到"，而是**被数出来的**：今天恰好 3 处，且理由非空

        断言具体数量（3）是有意的：豁免列表必须窄到能一眼看完。若哪天有人
        为了消红而放宽扫描或加豁免，这条会先红，逼他在 diff 里解释。
        """
        _violations, exemptions, _files, total = scan_tree(API_DIR)

        assert total >= 1, (
            "app/api 下一处 HTTPException 都没扫到 —— 要么全仓已彻底迁移"
            "（那也该有豁免：401 的 WWW-Authenticate 头需要它），要么扫描器坏了"
        )
        assert len(exemptions) == 3, (
            "豁免数量变了（当前 %d 处），请确认新增/删除的豁免都有站得住的理由：\n  %s"
            % (len(exemptions), "\n  ".join(exemptions))
        )
        for entry in exemptions:
            assert "——" in entry and entry.split("——", 1)[1].strip(), (
                f"豁免缺少理由: {entry}"
            )

    def test_no_http_exception_in_services_either(self):
        """服务层同样不得直接抛 HTTPException（**豁免列表为空**）

        为什么把服务层也纳入：两个既有先例（`version_service.py` 的 2 处）
        用的都是 `AppError`，说明"服务层抛业务异常"这条路已经走得通；
        而 `goal_service.py` 一度有 5 处 `HTTPException` —— 服务层直接抛
        HTTP 异常既让 service 与 web 框架耦合，又同样绕过统一错误信封。

        为什么可以要求"零豁免"：服务层没有任何需要设置响应头的场景
        （`headers=` 只出现在 auth 的路由依赖里），所以这里没有理由留口子。
        """
        violations, exemptions, scanned_files, _total = scan_tree(SERVICES_DIR)
        assert scanned_files >= 20, (
            f"app/services 下只扫到 {scanned_files} 个 .py 文件 —— 扫描范围失效"
        )
        assert not violations, (
            "服务层仍在直接抛 HTTPException：\n  " + "\n  ".join(sorted(violations))
        )
        assert not exemptions, (
            "服务层不该出现豁免（那里不需要响应头）：\n  " + "\n  ".join(exemptions)
        )


# ---------------------------------------------------------------------------
# 2. 反空洞：扫描器必须真的会红
# ---------------------------------------------------------------------------

class TestScannerIsNotVacuous:
    """一个永远返回"无违规"的扫描器与"全部合规"在输出上无法区分"""

    def test_scanner_detects_a_planted_violation(self):
        """**核心**：人造的违规必须被抓住"""
        planted = (
            "from fastapi import HTTPException\n"
            "def f():\n"
            "    raise HTTPException(status_code=404, detail='x')\n"
        )
        violations, exemptions, total = scan_source("planted.py", planted)
        assert total == 1, "人造违规没被计入 raise 总数"
        assert violations == ["planted.py:3"], f"人造违规没被抓到: {violations}"
        assert exemptions == []

    def test_scanner_accepts_a_justified_exemption(self):
        """写明理由的豁免被放行，且理由被记录下来（不是静默忽略）"""
        planted = (
            "from fastapi import HTTPException\n"
            "def f():\n"
            "    # error-contract: exempt — 需要 WWW-Authenticate 头\n"
            "    raise HTTPException(status_code=401, detail='x')\n"
        )
        violations, exemptions, total = scan_source("planted.py", planted)
        assert total == 1
        assert violations == []
        assert len(exemptions) == 1 and "WWW-Authenticate" in exemptions[0]

    def test_exemption_without_reason_is_a_violation(self):
        """**豁免必须有理由**：只有标记、不写理由时仍然算违规

        否则豁免会退化成 `# error-contract: exempt` 六个字的口头禅。
        """
        planted = (
            "from fastapi import HTTPException\n"
            "def f():\n"
            "    # error-contract: exempt\n"
            "    raise HTTPException(status_code=401, detail='x')\n"
        )
        violations, _exemptions, _total = scan_source("planted.py", planted)
        assert violations == ["planted.py:4"]

    def test_faraway_marker_does_not_exempt(self):
        """标记必须**紧邻** raise：隔着几行就不算数"""
        planted = (
            "from fastapi import HTTPException\n"
            "# error-contract: exempt — 理由\n"
            "\n"
            "\n"
            "def f():\n"
            "    raise HTTPException(status_code=404, detail='x')\n"
        )
        violations, _exemptions, _total = scan_source("planted.py", planted)
        assert violations == ["planted.py:6"]

    def test_non_http_exception_raises_are_ignored(self):
        """别的异常/同名局部变量不受影响（守卫只认 HTTPException）"""
        planted = (
            "class AppError(Exception):\n"
            "    pass\n"
            "def f():\n"
            "    raise AppError('X_NOT_FOUND', 'x', 404)\n"
            "    raise ValueError('y')\n"
            "    raise SomeHTTPExceptionLike('z')\n"
        )
        violations, exemptions, total = scan_source("planted.py", planted)
        assert (violations, exemptions, total) == ([], [], 0)


# ---------------------------------------------------------------------------
# 3. 契约本身的一致性：code 必须是 core.app_error 里声明过的常量
# ---------------------------------------------------------------------------

class TestErrorCodesAreDeclared:
    """`AppError` 的第一个参数必须是 `core/app_error.py` 里的常量名

    防的是两类退化：手写字符串字面量（`AppError("ERROR", ...)` —— 名字里
    没有语义，前端拿它分不了流），以及笔误导致 code 拼错却照样能跑。
    """

    CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

    @staticmethod
    def _declared_codes():
        path = os.path.join(APP_DIR, "core", "app_error.py")
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        codes = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                        codes[target.id] = node.value.value
        return codes

    @staticmethod
    def _app_error_calls():
        """收集 app/** 下所有 AppError(...) 调用传入的 code 实参

        两种写法都算：位置参数 `AppError(CODE, ...)` 与关键字
        `AppError(code=CODE, ...)`（version_service.py 用的是后者）。
        """
        calls = []
        for path in _iter_py_files(APP_DIR):
            with io.open(path, encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=path)
            rel = os.path.relpath(path, BACKEND)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and _unparse(node.func).split(".")[-1] == "AppError"):
                    continue
                code_arg = node.args[0] if node.args else None
                if code_arg is None:
                    for kw in node.keywords:
                        if kw.arg == "code":
                            code_arg = kw.value
                            break
                calls.append((rel, node.lineno, code_arg))
        return calls

    def test_every_code_is_a_declared_constant(self):
        declared = self._declared_codes()
        assert len(declared) >= 30, (
            f"core/app_error.py 只解析出 {len(declared)} 个错误码常量 —— "
            "解析方式或常量区结构变了"
        )
        calls = self._app_error_calls()
        assert len(calls) >= 100, (
            f"只找到 {len(calls)} 处 AppError(...) 调用 —— 采用面塌了或扫描失效了"
        )

        problems = []
        for rel, lineno, code_arg in calls:
            if code_arg is None:
                problems.append(f"{rel}:{lineno} 没有传 code")
                continue
            if isinstance(code_arg, ast.Name):
                if code_arg.id not in declared:
                    problems.append(f"{rel}:{lineno} 用了未声明的 code 常量 {code_arg.id}")
            else:
                problems.append(
                    f"{rel}:{lineno} 的 code 不是常量名（{_unparse(code_arg)}）—— "
                    "字面量绕过常量区，会写出没有语义的 code"
                )
        assert not problems, "错误码不合规：\n  " + "\n  ".join(problems)

    def test_code_constants_are_upper_snake_and_unique_in_value(self):
        declared = self._declared_codes()
        for name, value in declared.items():
            assert self.CODE_RE.match(name), f"常量名不是 UPPER_SNAKE_CASE: {name}"
            assert self.CODE_RE.match(value), f"常量值不是 UPPER_SNAKE_CASE: {name}={value!r}"
        # 值必须互不相同：两个常量取同一个值会让前端无法区分
        values = list(declared.values())
        assert len(values) == len(set(values)), (
            "存在取值重复的错误码常量: "
            + ", ".join(sorted({v for v in values if values.count(v) > 1}))
        )


# ---------------------------------------------------------------------------
# 4. 端到端：迁移后的端点真的按统一信封返回，且状态码/文案没变
# ---------------------------------------------------------------------------

class TestContractEndToEnd:
    """HTTP 往返级别的证据：状态码与文案原样，`error_code` 是新的语义码

    为什么必须走真实路由：`AppError` 由 `ErrorHandlerMiddleware` 渲染，而
    `HTTPException` 由 FastAPI 的 ExceptionMiddleware + `main.py` 的
    `http_exception_handler` 渲染 —— **两条渲染路径不同**。只断言
    "抛出的对象换了类型"证明不了响应体没变，必须真的发一次请求。
    """

    _ip_seq = 0

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"198.51.100.{20 + type(self)._ip_seq}", 9901))

    def _auth(self, client) -> dict:
        """注册一个用户并返回认证头（用户名/邮箱加随机后缀防撞库）"""
        suffix = uuid.uuid4().hex[:8]
        resp = client.post("/api/auth/register", json={
            "email": f"contract+{suffix}@example.com",
            "username": "contract" + suffix,
            "password": "ContractPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_task_not_found_keeps_status_and_message_and_adds_code(self, test_db):
        """`GET /api/tasks/{id}`：迁移前后**状态码 404、detail "任务不存在"** 不变，
        `error_code` 由 `HTTP_404` 变为语义化的 `TASK_NOT_FOUND`

        （迁移前的实测响应见本次改造的报告：`{"detail": "任务不存在",
        "error_code": "HTTP_404", "request_id": ...}`，状态码 404。）
        """
        client = self._client()
        headers = self._auth(client)

        resp = client.get("/api/tasks/does-not-exist", headers=headers)
        body = resp.json()

        assert resp.status_code == 404
        assert body["detail"] == "任务不存在"
        assert body["error_code"] == "TASK_NOT_FOUND"
        assert "request_id" in body
        # 非 2xx 也要能凭 request_id 对日志（与改造前一致）
        assert resp.headers.get("X-Request-ID")

    def test_project_not_found_is_migrated(self, test_db):
        """`GET /api/projects/{id}`：同一份前后对比用的端点

        迁移前实测：`{"detail": "项目不存在", "error_code": "HTTP_404"}`，
        状态码 404；迁移后状态码与 detail 必须是同一份。
        """
        client = self._client()
        headers = self._auth(client)

        resp = client.get("/api/projects/does-not-exist", headers=headers)
        body = resp.json()

        assert resp.status_code == 404
        assert body["detail"] == "项目不存在"
        assert body["error_code"] == "PROJECT_NOT_FOUND"

    def test_exempted_auth_401_keeps_legacy_code_and_challenge_header(self, test_db):
        """豁免的 401（auth.py）必须**既有** WWW-Authenticate 又保留旧码

        这条同时说明豁免的边界：`ErrorHandlerMiddleware` 不转发 headers，
        所以带质询头的 401 只能继续走 HTTPException（code 仍是 `HTTP_401`）。
        """
        client = self._client()
        resp = client.get("/api/auth/me")

        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        assert resp.json()["error_code"] == "HTTP_401"


# ---------------------------------------------------------------------------
# 5. 跨端对账：前端分支用的码必须就是后端声明的那个值
# ---------------------------------------------------------------------------

class TestFrontendBranchesOnCode:
    """前端按错误码分流，且**不再**匹配中文文案

    这是阶段 0.11 的验收本身（"后端改文案不破坏前端"）。放在后端测试里
    是因为要两侧的**字面量**对账：后端常量改名/改值而前端没跟，前端会静默
    走进"普通报错"分支 —— 不报错，只是不跳转。
    """

    FRONTEND = os.path.join(os.path.dirname(BACKEND), "frontend")
    DAILY_LIMIT_PAGES = (
        os.path.join("src", "pages", "TodayLearn.tsx"),
        os.path.join("src", "pages", "Review.tsx"),
    )

    def _read(self, rel):
        path = os.path.join(self.FRONTEND, rel)
        assert os.path.isfile(path), f"前端文件不存在: {path}"
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_daily_limit_code_is_the_declared_value(self):
        from app.core.app_error import DAILY_REVIEW_LIMIT_REACHED

        assert DAILY_REVIEW_LIMIT_REACHED == "DAILY_REVIEW_LIMIT_REACHED"

    def test_both_pages_branch_on_the_code(self):
        for rel in self.DAILY_LIMIT_PAGES:
            source = self._read(rel)
            assert "'DAILY_REVIEW_LIMIT_REACHED'" in source, (
                f"{rel} 没有按 DAILY_REVIEW_LIMIT_REACHED 分流 —— 每日限额的分支丢了"
            )
            assert "errorCodeOf(e)" in source, (
                f"{rel} 没有从异常上读 error_code —— 分支依据不是错误码"
            )

    #: 在**字符串内容**里找中文的分流写法（includes/indexOf/startsWith/… + 中文字面量）。
    #: 这是 F-19 记的那个形态：判据挂在文案上，后端改措辞即静默失效。
    CHINESE_TEXT_MATCH_RE = re.compile(
        r"\.(includes|indexOf|lastIndexOf|startsWith|endsWith|match|search|test)"
        r"\s*\(\s*['\"][^'\"]*[\u4e00-\u9fff]"
    )

    def test_no_page_matches_chinese_error_text(self):
        """**核心**：不允许再用中文文案做分流判据

        只要还有一处 `message.includes('每日上限')`，后端改文案就会打穿前端
        分支（overhaul-plan **阶段 0 表格的 0.11 行** —— 这正是 0.11 当时未验收的原因；
        行号不写，理由见本文件开头）。

        判据写成"字符串搜索方法 + 中文字面量"的正则，而不是"源码里不许出现
        '每日上限'四个字"—— 后者会把解释这条规则的**注释**也判成违规，
        逼着人删注释（信息更少），是坏判据。
        """
        for rel in self.DAILY_LIMIT_PAGES:
            source = self._read(rel)
            found = self.CHINESE_TEXT_MATCH_RE.search(source)
            assert not found, (
                f"{rel} 仍在用中文文案做判据: {found.group(0)!r} —— "
                "应改为按 error_code 分流"
            )
