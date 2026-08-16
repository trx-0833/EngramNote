# -*- coding: utf-8 -*-
"""
EngramNote 全链路可靠性测试套件
================================
从登录 → 上传真实PDF → MinerU转换 → AI清洗 → AI审阅(理解) → 知识图谱 → RAG问答(SSE)
→ SM-2复习 → 学习评估 → 报告 → 掌握度/盲点 → 学习目标 → 版本历史 → 提醒 → 文件夹
→ 笔记管理 → 跨用户隔离 → 负面用例，全流程 API 级端到端测试。

前置条件：
- 后端运行在 http://127.0.0.1:8000（uvicorn）
- Celery worker 已启动（--pool=solo）
- backend/.env 已配置 DeepSeek(OpenCode GO) 与 MinerU API

用法：
    python tests/test_full_e2e.py
输出：
    - 控制台逐步结果
    - tests/results/E2E_测试报告_<时间戳>.md（Markdown 报告）
"""

import json
import os
import sys
import time
import datetime
import httpx

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
API_BASE = os.environ.get("E2E_API_BASE", "http://127.0.0.1:8000")
PDF_FILE = os.environ.get(
    "E2E_PDF_FILE",
    r"D:\engramnote\resource\电气\拉哇变电站主变运行技术标准.pdf",
)
RUN_ID = time.strftime("%Y%m%d_%H%M%S")
USERNAME = f"e2e{time.strftime('%m%d%H%M%S')}"
EMAIL = f"{USERNAME}@example.com"
PASSWORD = "Test@123456"
USERNAME_B = f"e2eb{time.strftime('%m%d%H%M%S')}"
EMAIL_B = f"{USERNAME_B}@example.com"

RESULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(RESULT_DIR, exist_ok=True)
REPORT_FILE = os.path.join(RESULT_DIR, f"E2E_测试报告_{RUN_ID}.md")

TIMEOUT_CONVERT = int(os.environ.get("E2E_TIMEOUT_CONVERT", "900"))
TIMEOUT_CLEAN = int(os.environ.get("E2E_TIMEOUT_CLEAN", "900"))
TIMEOUT_UNDERSTAND = int(os.environ.get("E2E_TIMEOUT_UNDERSTAND", "3600"))

# ---------------------------------------------------------------------------
# 测试记录器
# ---------------------------------------------------------------------------
class Reporter:
    def __init__(self):
        self.results = []  # {phase, name, status, detail, elapsed}

    def record(self, phase, name, status, detail="", elapsed=None):
        self.results.append({
            "phase": phase, "name": name, "status": status,
            "detail": str(detail)[:500], "elapsed": elapsed,
        })
        mark = "PASS" if status == "PASS" else ("WARN" if status == "WARN" else "FAIL")
        print(f"  [{mark}] {name}" + (f"  ({elapsed:.1f}s)" if elapsed else ""))
        if detail and status != "PASS":
            print(f"        {str(detail)[:300]}")

    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        warned = sum(1 for r in self.results if r["status"] == "WARN")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        return total, passed, warned, failed


REPORT = Reporter()


def check(phase, name, ok, detail="", elapsed=None, warn_only=False):
    status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
    REPORT.record(phase, name, status, detail, elapsed)
    return ok


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
class API:
    def __init__(self):
        self.client = httpx.Client(timeout=120, base_url=API_BASE)
        self.token = None
        self._retries = 3  # 瞬时连接错误（RemoteProtocolError/ConnectError）自动重试

    def headers(self):
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _with_retry(self, fn, *args, **kw):
        last_exc = None
        for attempt in range(self._retries):
            try:
                return fn(*args, **kw)
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
                last_exc = e
                print(f"        [retry {attempt + 1}/{self._retries}] 瞬时连接错误: {type(e).__name__}，2s 后重试")
                time.sleep(2)
        raise last_exc

    def post(self, path, **kw):
        kw.setdefault("headers", self.headers())
        return self._with_retry(self.client.post, path, **kw)

    def get(self, path, **kw):
        kw.setdefault("headers", self.headers())
        return self._with_retry(self.client.get, path, **kw)

    def put(self, path, **kw):
        kw.setdefault("headers", self.headers())
        return self._with_retry(self.client.put, path, **kw)

    def patch(self, path, **kw):
        kw.setdefault("headers", self.headers())
        return self._with_retry(self.client.patch, path, **kw)

    def delete(self, path, **kw):
        kw.setdefault("headers", self.headers())
        return self._with_retry(self.client.delete, path, **kw)


def poll_until(api, path, want, timeout, interval=4, field="status", label=""):
    """轮询直到 JSON[field] in want 或超时；返回 (ok, final_payload)"""
    start = time.monotonic()
    last = None
    while time.monotonic() - start < timeout:
        try:
            r = api.get(path)
            if r.status_code == 200:
                last = r.json()
                cur = last.get(field)
                print(f"        [{label or path}] {field}={cur}  ({(time.monotonic()-start):.0f}s)")
                if cur in want:
                    return True, last
                if isinstance(want, dict) and cur in want:
                    return True, last
        except Exception as e:
            print(f"        poll error: {e}")
        time.sleep(interval)
    return False, (last or {})


def phase(title):
    print(f"\n{'=' * 70}\n## {title}\n{'=' * 70}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("EngramNote 全链路可靠性测试")
    print(f"  API: {API_BASE}   运行ID: {RUN_ID}")
    print(f"  账号: {USERNAME} / {EMAIL}")
    print(f"  PDF: {PDF_FILE}")
    print("=" * 70)

    api = API()
    api_b = API()  # 第二个账号（隔离测试）
    note_id = None
    project_id = None
    folder_id = None
    goal_id = None
    created_ids = {"notes": [], "projects": [], "folders": [], "goals": []}

    # ================= Phase 1 认证 =================
    phase("Phase 1 认证（注册/登录/Token）")
    t0 = time.monotonic()
    r = api.post("/api/auth/register", json={"email": EMAIL, "username": USERNAME, "password": PASSWORD})
    ok = r.status_code == 201 and r.json().get("access_token")
    check("认证", "注册新账号返回 token", ok, r.text[:200], time.monotonic() - t0)
    if ok:
        api.token = r.json()["access_token"]
    else:
        print("[ABORT] 无法注册测试账号")
        sys.exit(1)

    t0 = time.monotonic()
    r = api.post("/api/auth/register", json={"email": EMAIL, "username": USERNAME, "password": PASSWORD})
    check("认证", "重复注册被拒绝(400)", r.status_code == 400, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    check("认证", "正确密码登录成功", r.status_code == 200 and r.json().get("access_token"), r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/auth/login", json={"email": EMAIL, "password": "wrong-password-1"})
    check("认证", "错误密码登录返回401", r.status_code == 401, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get("/api/auth/me")
    check("认证", "GET /auth/me 返回用户信息", r.status_code == 200 and r.json().get("id"), r.text[:200], time.monotonic() - t0)

    anon = httpx.Client(timeout=30, base_url=API_BASE)
    t0 = time.monotonic()
    r = anon.get("/api/auth/me")
    check("认证", "无 Token 访问 /auth/me 返回401", r.status_code == 401, r.text[:200], time.monotonic() - t0)
    t0 = time.monotonic()
    r = anon.get("/api/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    check("认证", "伪造 Token 返回401", r.status_code == 401, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/auth/register", json={"email": "bad-email", "username": "baduser1", "password": "123456"})
    check("认证", "非法邮箱注册返回422", r.status_code == 422, r.text[:200], time.monotonic() - t0)
    t0 = time.monotonic()
    r = api.post("/api/auth/register", json={"email": "x@x.com", "username": "baduser2", "password": "123"})
    check("认证", "过短密码注册返回422", r.status_code == 422, r.text[:200], time.monotonic() - t0)

    # ================= Phase 2 项目 =================
    phase("Phase 2 项目（项目隔离与 CRUD）")
    t0 = time.monotonic()
    r = api.post("/api/projects", json={"name": f"E2E测试项目_{RUN_ID}", "description": "端到端测试创建的项目"})
    ok = r.status_code == 201 and r.json().get("id")
    check("项目", "创建项目", ok, r.text[:200], time.monotonic() - t0)
    if ok:
        project_id = r.json()["id"]
        created_ids["projects"].append(project_id)

    t0 = time.monotonic()
    r = api.get("/api/projects")
    check("项目", "项目列表包含新项目", r.status_code == 200 and any(p.get("id") == project_id for p in r.json()), r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/projects/{project_id}")
    check("项目", "获取项目详情", r.status_code == 200 and r.json().get("id") == project_id, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.patch(f"/api/projects/{project_id}", json={"name": f"E2E项目_改名_{RUN_ID}"})
    check("项目", "更新项目名称", r.status_code == 200 and "改名" in r.json().get("name", ""), r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/projects", json={"name": ""})
    check("项目", "空名称创建项目返回422", r.status_code == 422, r.text[:200], time.monotonic() - t0)

    # ================= Phase 3 上传 + 转换 =================
    phase("Phase 3 上传真实 PDF + MinerU 转换")
    assert os.path.exists(PDF_FILE), f"PDF 不存在: {PDF_FILE}"
    size_kb = os.path.getsize(PDF_FILE) // 1024
    print(f"  [INFO] PDF: {os.path.basename(PDF_FILE)} ({size_kb} KB)")

    t0 = time.monotonic()
    with open(PDF_FILE, "rb") as f:
        files = {"file": (os.path.basename(PDF_FILE), f, "application/pdf")}
        data = {"project_ids": project_id} if project_id else None
        r = api.post("/api/upload", files=files, data=data)
    ok = r.status_code == 201 and r.json().get("id")
    check("上传", "上传真实 PDF 创建笔记", ok, r.text[:300], time.monotonic() - t0)
    if not ok:
        print("[ABORT] 上传失败")
        sys.exit(1)
    note_id = r.json()["id"]
    created_ids["notes"].append(note_id)
    print(f"  [INFO] note_id={note_id}")

    ok, data = poll_until(api, f"/api/upload/{note_id}/status",
                          ["converted", "cleaned", "archived", "cleaning", "learning", "failed"],
                          TIMEOUT_CONVERT, label="转换")
    # 转换成功后系统会自动触发清洗（converted → cleaning → cleaned），
    # 因此"非失败"的后续状态都视为转换成功
    conv_status = data.get("status")
    check("上传", "MinerU 转换成功(converted/后续状态)", ok and conv_status not in ("failed", "converting"),
          f"status={conv_status} error={data.get('error_message')}", time.monotonic() - t0)
    if not (ok and conv_status not in ("failed", "converting")):
        print("[ABORT] 转换失败，终止后续依赖测试")
        write_report()
        sys.exit(2)

    t0 = time.monotonic()
    r = api.get(f"/api/notes/{note_id}")
    detail = r.json() if r.status_code == 200 else {}
    md = detail.get("original_md_content") or ""
    check("上传", "笔记详情含 Markdown 内容", r.status_code == 200 and len(md) > 500,
          f"md_len={len(md)} page_count={detail.get('page_count')}", time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/upload", files={"file": ("evil.exe", b"MZ\x90\x00fake", "application/octet-stream")})
    check("上传", "非法扩展名上传返回400/422", r.status_code in (400, 422), r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/upload", files={"file": ("fake.pdf", b"not a real pdf content", "application/pdf")})
    check("上传", "伪装PDF(魔术字节不符)被拒绝", r.status_code in (400, 422), r.text[:200], time.monotonic() - t0)

    # ================= Phase 4 AI 清洗 =================
    phase("Phase 4 AI 清洗（规则去噪 + BGE-M3 向量去重）")
    t0 = time.monotonic()
    r = api.post(f"/api/cleaning/{note_id}/start")
    # 转换成功后系统会自动触发清洗；若已在清洗中（409），视为测试前置条件已满足
    ok = (r.status_code == 200 and r.json().get("status") == "cleaning") or (
        r.status_code == 409 and "正在进行" in r.text)
    check("清洗", "触发清洗任务", ok, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    ok, data = poll_until(api, f"/api/cleaning/{note_id}/status", ["cleaned", "cleaning_failed"],
                          TIMEOUT_CLEAN, label="清洗")
    check("清洗", "清洗完成(cleaned)", ok and data.get("status") == "cleaned",
          f"status={data.get('status')} error={data.get('error_message')}", time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/cleaning/{note_id}/diff")
    diff_ok = r.status_code == 200
    diff_data = r.json() if diff_ok else {}
    diff_blocks = diff_data.get("blocks") if isinstance(diff_data, dict) else diff_data
    check("清洗", "获取清洗 Diff 三视图", diff_ok and isinstance(diff_blocks, list),
          f"original_lines={diff_data.get('original_lines')} clean_lines={diff_data.get('clean_lines')} blocks={len(diff_blocks) if isinstance(diff_blocks, list) else '-'}",
          time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/notes/{note_id}")
    clean_md = (r.json().get("clean_md_content") or "") if r.status_code == 200 else ""
    check("清洗", "清洗副本内容非空", len(clean_md) > 200, f"clean_md_len={len(clean_md)}", time.monotonic() - t0)

    # ================= Phase 5 AI 审阅（理解管道） =================
    phase("Phase 5 AI 审阅（章节摘要 + 知识卡片 + 自动出题）")
    t0 = time.monotonic()
    r = api.post(f"/api/understanding/{note_id}/start", json={"confirm": False})
    check("理解", "触发 AI 审阅管道", r.status_code == 200, r.text[:300], time.monotonic() - t0)

    ok, data = poll_until(api, f"/api/understanding/{note_id}/status",
                          ["archived", "learning_failed"], TIMEOUT_UNDERSTAND, interval=10, label="AI审阅")
    check("理解", "AI 审阅完成(archived)", ok and data.get("status") == "archived",
          f"status={data.get('status')} error={data.get('error_message')}", time.monotonic() - t0)
    if not (ok and data.get("status") == "archived"):
        print("[WARN] AI 审阅未完成，后续卡片/出题测试可能为空")

    t0 = time.monotonic()
    r = api.get(f"/api/understanding/{note_id}/chapters")
    ch = r.json() if r.status_code == 200 else {}
    chapters = ch.get("chapters") if isinstance(ch, dict) else ch
    check("理解", "章节摘要列表", r.status_code == 200 and isinstance(chapters, list) and len(chapters) > 0,
          f"chapters={len(chapters) if isinstance(chapters, list) else r.text[:200]}", time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/understanding/{note_id}/cards")
    cd = r.json() if r.status_code == 200 else {}
    cards = cd.get("items") if isinstance(cd, dict) and "items" in cd else cd
    check("理解", "知识卡片提取", r.status_code == 200 and isinstance(cards, list) and len(cards) > 0,
          f"cards={len(cards) if isinstance(cards, list) else r.text[:200]}", time.monotonic() - t0)

    # 出题为异步任务：笔记状态 archived 时题目可能仍在生成，轮询等待题目出现
    questions = []
    t0 = time.monotonic()
    q_poll_ok = False
    while time.monotonic() - t0 < 300:
        r = api.get(f"/api/understanding/{note_id}/questions")
        qd = r.json() if r.status_code == 200 else {}
        questions = qd.get("items") if isinstance(qd, dict) and "items" in qd else qd
        if isinstance(questions, list) and len(questions) > 0:
            q_poll_ok = True
            break
        time.sleep(5)
    check("理解", "自动出题（异步生成完成）", q_poll_ok,
          f"questions={len(questions) if isinstance(questions, list) else '-'}",
          time.monotonic() - t0)

    card_id = cards[0]["id"] if (isinstance(cards, list) and cards) else None
    if card_id:
        t0 = time.monotonic()
        r = api.get(f"/api/understanding/cards/{card_id}")
        check("理解", "卡片详情", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.put(f"/api/understanding/cards/{card_id}", json={"title": cards[0]["title"] + "（已编辑）"})
        check("理解", "编辑卡片", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/understanding/{note_id}/duplicates")
    check("理解", "卡片查重接口", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    # ================= Phase 6 知识图谱 =================
    phase("Phase 6 知识图谱")
    t0 = time.monotonic()
    r = api.get("/api/graph")
    graph = r.json() if r.status_code == 200 else {}
    nodes = graph.get("nodes") or []
    links = graph.get("edges") or graph.get("links") or []
    check("图谱", "获取知识图谱", r.status_code == 200, f"nodes={len(nodes)} edges={len(links)}", time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get("/api/graph/stats")
    check("图谱", "图谱统计", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/graph/suggest")
    suggest_ok = r.status_code == 200
    sug = r.json() if suggest_ok else {}
    check("图谱", "关系建议接口", suggest_ok, f"suggestions={len(sug.get('suggestions') or [])}", time.monotonic() - t0, warn_only=True)

    # ================= Phase 7 RAG 问答 =================
    phase("Phase 7 RAG 智能问答（普通 + SSE 流式）")
    t0 = time.monotonic()
    r = api.post("/api/understanding/ask", json={"question": "主变压器的运行技术标准中，对油温有何规定？"})
    qa = r.json() if r.status_code == 200 else {}
    check("问答", "普通问答返回回答", r.status_code == 200 and len(qa.get("answer") or "") > 10,
          f"answer_len={len(qa.get('answer') or '')} sources={len(qa.get('sources') or [])} provider={qa.get('provider')}",
          time.monotonic() - t0)

    t0 = time.monotonic()
    try:
        tokens = []
        events = []
        with api.client.stream("POST", f"{API_BASE}/api/understanding/ask/stream",
                               json={"question": "变压器冷却方式有哪几种？请简要说明。"},
                               headers=api.headers(), timeout=180) as r:
            for line in r.iter_lines():
                if line.startswith("event:"):
                    events.append(line[6:].strip())
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip())
                        if "content" in d:
                            tokens.append(d["content"])
                    except Exception:
                        pass
        got_token = "token" in events
        got_done = "done" in events
        got_error = "error" in events
        check("问答", "SSE 流式问答(token/done事件)",
              got_token and got_done and not got_error and len("".join(tokens)) > 10,
              f"events={events} text_len={len(''.join(tokens))}", time.monotonic() - t0)
    except Exception as e:
        check("问答", "SSE 流式问答(token/done事件)", False, f"异常: {type(e).__name__}: {e}", time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/understanding/ask", json={"question": ""})
    check("问答", "空问题返回422", r.status_code == 422, r.text[:200], time.monotonic() - t0)

    # ================= Phase 8 SM-2 复习 =================
    phase("Phase 8 间隔重复复习（SM-2）")
    t0 = time.monotonic()
    r = api.get("/api/review/due")
    due = r.json() if r.status_code == 200 else {}
    due_items = due.get("items") or []
    check("复习", "获取到期题目", r.status_code == 200, f"due_total={due.get('total')}", time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get("/api/review/stats")
    check("复习", "复习统计", r.status_code == 200, r.text[:250], time.monotonic() - t0)

    submitted = 0
    for q in due_items[:3]:
        qid = q.get("id") or q.get("quiz_id")
        if not qid:
            continue
        t0 = time.monotonic()
        r = api.post("/api/review/submit", json={"quiz_id": qid, "user_answer": "A", "time_spent_ms": 3000})
        ok = r.status_code == 200 and "is_correct" in r.json()
        check("复习", f"提交答案(quiz {str(qid)[:8]})", ok, r.text[:200], time.monotonic() - t0)
        if ok:
            submitted += 1
    if not due_items:
        print("  [INFO] 当前无到期题目，跳过提交测试（新题应在生成后到期）")

    t0 = time.monotonic()
    r = api.get("/api/review/history")
    check("复习", "复习历史", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/review/quick/{note_id}")
    qr = r.json() if r.status_code == 200 else {}
    check("复习", "快速复习题目", r.status_code == 200, f"items={len(qr.get('items') or [])}", time.monotonic() - t0)
    qr_items = qr.get("items") or []
    if qr_items:
        q0 = qr_items[0]
        qid = q0.get("id") or q0.get("quiz_id")
        if qid:
            t0 = time.monotonic()
            r = api.post(f"/api/review/quick/{note_id}/submit",
                         json={"quiz_id": qid, "user_answer": "A", "time_spent_ms": 2000})
            check("复习", "快速复习提交答案", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    # ================= Phase 9 学习评估 =================
    phase("Phase 9 学习评估（笔记比对 + 盲点检测）")
    # 先创建一个 personal 笔记（上传 .md 文件）
    personal_note_id = None
    t0 = time.monotonic()
    md_content = ("# 我的学习笔记\n\n主变压器是变电站的核心设备，其运行技术标准规定了油温、绕组温度、"
                  "冷却方式等关键参数。变压器油温一般不超过85摄氏度，顶层油温报警值一般为85℃，"
                  "冷却方式包括自然冷却、强迫油循环风冷等。\n\n" * 20)
    r = api.post("/api/upload", files={"file": ("我的笔记.md", md_content.encode("utf-8"), "text/markdown")})
    ok = r.status_code == 201 and r.json().get("id")
    check("评估", "上传 Markdown 个人笔记", ok, r.text[:200], time.monotonic() - t0)
    if ok:
        personal_note_id = r.json()["id"]
        created_ids["notes"].append(personal_note_id)
        ok, data = poll_until(api, f"/api/upload/{personal_note_id}/status",
                              ["converted", "cleaned", "archived", "cleaning", "learning", "failed"],
                              300, label="md转换")
        check("评估", "Markdown 笔记转换完成", ok and data.get("status") not in ("failed", "converting"), str(data), time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post("/api/assessment/compare",
                 json={"material_note_ids": [note_id], "personal_note_ids": [personal_note_id or note_id]})
    assessment = r.json() if r.status_code == 200 else {}
    check("评估", "学习评估比对", r.status_code == 200 and assessment.get("id"),
          f"mode={assessment.get('mode')} overall={assessment.get('overall_score')} err={r.text[:200]}",
          time.monotonic() - t0)
    assessment_id = assessment.get("id")

    if assessment_id:
        t0 = time.monotonic()
        r = api.get(f"/api/assessment/history/{note_id}")
        check("评估", "评估历史", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        # submit-answer 仅支持开放性问题模式：先走 generate-quiz 生成开放题评估
        t0 = time.monotonic()
        r = api.post("/api/assessment/generate-quiz",
                     json={"material_note_ids": [note_id], "personal_note_id": personal_note_id or note_id})
        quiz_assessment = r.json() if r.status_code == 200 else {}
        check("评估", "生成开放题评估", r.status_code == 200 and quiz_assessment.get("id"),
              f"mode={quiz_assessment.get('mode')} err={r.text[:200]}", time.monotonic() - t0)
        if quiz_assessment.get("id"):
            quiz_qs = quiz_assessment.get("quiz_questions") or []
            answers = [{"quiz_id": q.get("id"), "user_answer": "A"} for q in quiz_qs[:2]]
            t0 = time.monotonic()
            r = api.post("/api/assessment/submit-answer",
                         json={"assessment_id": quiz_assessment["id"], "answers": answers})
            check("评估", "提交评估答案", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    # ================= Phase 10 学习报告 =================
    phase("Phase 10 学习报告")
    t0 = time.monotonic()
    r = api.get("/api/report/daily")
    check("报告", "每日报告", r.status_code == 200, r.text[:250], time.monotonic() - t0)
    t0 = time.monotonic()
    r = api.get("/api/report/weekly-trend")
    check("报告", "7天趋势", r.status_code == 200, r.text[:250], time.monotonic() - t0)
    t0 = time.monotonic()
    r = api.get("/api/report/weak-points")
    check("报告", "薄弱点列表", r.status_code == 200, r.text[:250], time.monotonic() - t0)

    # ================= Phase 11 掌握度/盲点 =================
    phase("Phase 11 掌握度与盲点")
    t0 = time.monotonic()
    r = api.get("/api/knowledge/mastery")
    check("掌握度", "掌握度概览", r.status_code == 200, r.text[:250], time.monotonic() - t0)
    t0 = time.monotonic()
    r = api.get("/api/knowledge/blind-spots")
    check("掌握度", "盲点列表", r.status_code == 200, r.text[:250], time.monotonic() - t0)

    # ================= Phase 12 学习目标 =================
    phase("Phase 12 学习目标与每日计划")
    t0 = time.monotonic()
    r = api.post("/api/goals", json={"name": "掌握主变运行标准", "type": "weekly",
                                     "scope_notes": [note_id], "target_mastery": 80})
    ok = r.status_code == 201 and r.json().get("id")
    check("目标", "创建周目标", ok, r.text[:200], time.monotonic() - t0)
    if ok:
        goal_id = r.json()["id"]
        created_ids["goals"].append(goal_id)

    t0 = time.monotonic()
    r = api.post("/api/goals", json={"name": "每日复习主变知识", "type": "daily", "scope_notes": [note_id]})
    check("目标", "创建日目标", r.status_code == 201, r.text[:200], time.monotonic() - t0)
    if r.status_code == 201:
        created_ids["goals"].append(r.json()["id"])

    t0 = time.monotonic()
    r = api.get("/api/goals")
    gl = r.json() if r.status_code == 200 else {}
    goals = gl.get("goals") if isinstance(gl, dict) else gl
    check("目标", "目标列表", r.status_code == 200 and len(goals or []) >= 1, r.text[:200], time.monotonic() - t0)

    if goal_id:
        t0 = time.monotonic()
        r = api.get(f"/api/goals/{goal_id}")
        check("目标", "目标详情", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.patch(f"/api/goals/{goal_id}", json={"name": "掌握主变运行标准（加强）"})
        check("目标", "更新目标", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get("/api/goals/daily-plan")
    check("目标", "每日推荐计划", r.status_code == 200, r.text[:250], time.monotonic() - t0)

    if goal_id:
        t0 = time.monotonic()
        r = api.post(f"/api/goals/{goal_id}/archive")
        check("目标", "归档目标", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.delete(f"/api/goals/{goal_id}")
        check("目标", "删除目标", r.status_code in (200, 204), r.text[:200], time.monotonic() - t0)
        created_ids["goals"].remove(goal_id)
        goal_id = None

    # ================= Phase 13 笔记版本历史 =================
    phase("Phase 13 笔记版本历史（编辑→快照→diff→恢复）")
    t0 = time.monotonic()
    r = api.get(f"/api/notes/{note_id}")
    cur_clean = (r.json().get("clean_md_content") or "") if r.status_code == 200 else ""
    mid = min(len(cur_clean), 800)
    edit_content = cur_clean[:mid] + "\n\n> E2E 测试手动编辑添加的行：这是一条测试修改。\n" + cur_clean[mid:]
    r = api.put(f"/api/notes/{note_id}/content", json={"content": edit_content, "target": "clean"})
    check("版本", "编辑清洗副本内容", r.status_code == 200, r.text[:200], time.monotonic() - t0)
    # 第二次编辑：产生第二个用户版本，保证 diff/restore 可测
    t0 = time.monotonic()
    edit2 = edit_content + "\n\n> E2E 第二次编辑：追加的又一条测试行。\n"
    r = api.put(f"/api/notes/{note_id}/content", json={"content": edit2, "target": "clean"})
    check("版本", "再次编辑产生新版本", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/notes/{note_id}/versions")
    vd = r.json() if r.status_code == 200 else {}
    versions = vd.get("versions") if isinstance(vd, dict) else vd
    check("版本", "版本列表（含自动快照）", r.status_code == 200 and isinstance(versions, list) and len(versions) >= 1,
          f"versions={len(versions) if isinstance(versions, list) else r.text[:200]}", time.monotonic() - t0)

    if isinstance(versions, list) and len(versions) >= 2:
        nums = sorted(v["version_number"] for v in versions)
        v_old, v_new = nums[0], nums[-1]
        t0 = time.monotonic()
        r = api.get(f"/api/notes/{note_id}/versions/{v_new}")
        check("版本", "版本内容预览", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.get(f"/api/notes/{note_id}/versions/diff", params={"v1": v_old, "v2": v_new})
        check("版本", "版本 Diff 对比", r.status_code == 200 and "diff_lines" in r.json(), r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.post(f"/api/notes/{note_id}/versions/{v_old}/restore")
        check("版本", "恢复历史版本", r.status_code == 200, r.text[:200], time.monotonic() - t0)
    else:
        print("  [WARN] 版本数不足，跳过 diff/restore 测试")

    # ================= Phase 14 复习提醒 =================
    phase("Phase 14 复习提醒")
    t0 = time.monotonic()
    r = api.get("/api/review/reminders")
    check("提醒", "到期提醒数据", r.status_code == 200, r.text[:250], time.monotonic() - t0)

    # ================= Phase 15 文件夹 =================
    phase("Phase 15 文件夹管理")
    t0 = time.monotonic()
    r = api.post("/api/folders", json={"name": f"E2E文件夹_{RUN_ID}", "description": "测试文件夹"})
    ok = r.status_code == 201 and r.json().get("id")
    check("文件夹", "创建文件夹", ok, r.text[:200], time.monotonic() - t0)
    if ok:
        folder_id = r.json()["id"]
        created_ids["folders"].append(folder_id)

    t0 = time.monotonic()
    r = api.get("/api/folders")
    check("文件夹", "文件夹列表", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    if folder_id:
        t0 = time.monotonic()
        r = api.patch(f"/api/folders/{folder_id}", json={"name": f"E2E文件夹_改名_{RUN_ID}"})
        check("文件夹", "更新文件夹", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.get(f"/api/folders/{folder_id}")
        check("文件夹", "文件夹详情", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    # ================= Phase 16 笔记管理 =================
    phase("Phase 16 笔记管理（标题/标注/角色/归档/链接）")
    t0 = time.monotonic()
    r = api.put(f"/api/notes/{note_id}", json={"title": "E2E-主变运行技术标准（测试改名）"})
    check("笔记", "更新笔记标题", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.post(f"/api/notes/{note_id}/annotations",
                 json={"view_mode": "clean", "type": "highlight", "text_content": "这是测试标注",
                       "color": "#ff0000"})
    ann_ok = r.status_code in (200, 201)
    ann_id = (r.json().get("id") if ann_ok else None)
    check("笔记", "创建标注", ann_ok, r.text[:200], time.monotonic() - t0)
    if ann_id:
        t0 = time.monotonic()
        r = api.get(f"/api/notes/{note_id}/annotations")
        check("笔记", "标注列表", r.status_code == 200, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api.delete(f"/api/notes/{note_id}/annotations/{ann_id}")
        check("笔记", "删除标注", r.status_code in (200, 204), r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.patch(f"/api/notes/{note_id}/role", params={"note_role": "material"})
    check("笔记", "更新笔记角色", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/notes/{note_id}/links")
    check("笔记", "笔记关联", r.status_code == 200, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get("/api/notes")
    check("笔记", "笔记列表", r.status_code == 200 and r.json().get("total", 0) >= 1, r.text[:200], time.monotonic() - t0)

    # 归档与恢复（归档接口为切换语义：理解完成后笔记为 archived，第一次调用=取消归档）
    t0 = time.monotonic()
    r = api.post(f"/api/notes/{note_id}/archive")
    check("笔记", "切换归档状态(取消归档)", r.status_code == 200, r.text[:200], time.monotonic() - t0)
    t0 = time.monotonic()
    r = api.post(f"/api/notes/{note_id}/archive")
    check("笔记", "再次归档", r.status_code == 200, r.text[:200], time.monotonic() - t0)
    t0 = time.monotonic()
    r = api.get("/api/notes/archive")
    arch_list = r.json() if r.status_code == 200 else []
    check("笔记", "归档列表包含笔记", r.status_code == 200 and any(
        (n.get("id") == note_id) for n in (arch_list.get("items") or arch_list if isinstance(arch_list, dict) else arch_list)
    ), r.text[:200], time.monotonic() - t0)

    # ================= Phase 17 跨用户隔离与负面用例 =================
    phase("Phase 17 跨用户隔离与负面用例")
    t0 = time.monotonic()
    r = api_b.post("/api/auth/register", json={"email": EMAIL_B, "username": USERNAME_B, "password": PASSWORD})
    check("隔离", "第二个账号注册", r.status_code == 201, r.text[:200], time.monotonic() - t0)
    if r.status_code == 201:
        api_b.token = r.json()["access_token"]

    if api_b.token:
        t0 = time.monotonic()
        r = api_b.get(f"/api/notes/{note_id}")
        check("隔离", "跨用户读取笔记返回404", r.status_code == 404, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api_b.post(f"/api/cleaning/{note_id}/start")
        check("隔离", "跨用户触发清洗返回404", r.status_code == 404, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api_b.get(f"/api/projects/{project_id}")
        check("隔离", "跨用户读取项目返回404", r.status_code == 404, r.text[:200], time.monotonic() - t0)
        t0 = time.monotonic()
        r = api_b.delete(f"/api/notes/{note_id}")
        check("隔离", "跨用户删除笔记返回404", r.status_code == 404, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get("/api/notes/00000000-0000-0000-0000-000000000000")
    check("负面", "不存在的笔记返回404", r.status_code == 404, r.text[:200], time.monotonic() - t0)

    t0 = time.monotonic()
    r = api.get(f"/api/cleaning/{note_id}/diff", headers={"Authorization": "Bearer bad"})
    check("负面", "无效Token访问返回401", r.status_code == 401, r.text[:200], time.monotonic() - t0)

    # ================= Phase 18 清理 =================
    phase("Phase 18 清理测试数据")
    # 笔记/文件夹/项目走 API 删除（级联删除卡片/题目/版本）
    for nid in created_ids["notes"]:
        try:
            api.delete(f"/api/notes/{nid}")
        except Exception:
            pass
    for fid in created_ids["folders"]:
        try:
            api.delete(f"/api/folders/{fid}")
        except Exception:
            pass
    for pid in created_ids["projects"]:
        try:
            api.delete(f"/api/projects/{pid}")
        except Exception:
            pass
    # 目标走 API 删除（软删除；DB 硬清理由 tools/e2e_cleanup.py 兜底）
    for gid in created_ids["goals"]:
        try:
            api.delete(f"/api/goals/{gid}")
        except Exception:
            pass
    print(f"  [INFO] 已清理 notes={created_ids['notes']} folders={created_ids['folders']} "
          f"projects={created_ids['projects']} goals={created_ids['goals']}")
    print("  [INFO] 提示：学习目标/每日计划/评估记录为用户级数据（无删除API），"
          "如需 DB 硬清理请运行 backend/e2e_cleanup.py")

    # ------------------------------------------------------------------
    write_report()
    total, passed, warned, failed = REPORT.summary()
    print("\n" + "=" * 70)
    print(f"测试结束：共 {total} 项 | PASS {passed} | WARN {warned} | FAIL {failed}")
    print(f"报告：{REPORT_FILE}")
    print("=" * 70)
    sys.exit(1 if failed else 0)


def write_report():
    total, passed, warned, failed = REPORT.summary()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append(f"# EngramNote 全链路可靠性测试报告")
    lines.append("")
    lines.append(f"- 运行时间：{now}")
    lines.append(f"- 运行ID：{RUN_ID}")
    lines.append(f"- API 地址：{API_BASE}")
    lines.append(f"- 测试账号：{USERNAME} / {EMAIL}")
    lines.append(f"- 测试 PDF：{os.path.basename(PDF_FILE)}（{os.path.getsize(PDF_FILE) // 1024} KB）")
    lines.append(f"- 结果统计：**共 {total} 项 | PASS {passed} | WARN {warned} | FAIL {failed}**")
    lines.append("")
    lines.append("| # | 阶段 | 测试项 | 结果 | 耗时(s) | 详情 |")
    lines.append("|---|------|--------|------|---------|------|")
    for i, r in enumerate(REPORT.results, 1):
        icon = "✅" if r["status"] == "PASS" else ("⚠️" if r["status"] == "WARN" else "❌")
        el = f"{r['elapsed']:.1f}" if r["elapsed"] else "-"
        detail = (r["detail"] or "").replace("|", "\\|").replace("\n", " ")[:200]
        lines.append(f"| {i} | {r['phase']} | {r['name']} | {icon} {r['status']} | {el} | {detail} |")
    lines.append("")
    if failed:
        lines.append("## 失败项")
        lines.append("")
        for i, r in enumerate(REPORT.results, 1):
            if r["status"] == "FAIL":
                lines.append(f"{i}. **{r['phase']} / {r['name']}**：{r['detail']}")
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[INFO] 报告已写入 {REPORT_FILE}")


if __name__ == "__main__":
    main()
