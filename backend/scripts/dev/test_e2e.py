"""
EngramNote 端到端测试脚本
========================

Karpathy 风格：单文件、零额外依赖、每步打印可见信息。

这个脚本完全模拟前端的真实行为：
1. 启动 FastAPI 服务器
2. 注册用户 + 获取 Token
3. 上传 PDF 文件（走 HTTP API，和前端一模一样）
4. 轮询笔记状态，等待 Celery worker 消费任务
5. 获取笔记详情，输出 Markdown 内容
6. 清理测试数据

前提条件：
- 需要先启动 Celery worker：
  celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
- 本脚本会自动启动 FastAPI 服务器

注意：
- convert_tasks.py 中硬编码了 backend="vlm-http-client"
- 如果 MINERU_API_TOKEN 未配置，mineru_plus 会返回错误
- 这是预期行为，说明 Celery 流程是通的
"""

import os
import sys
import time
import httpx
import subprocess
from app.test_support.corpus import require_pdf_path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 测试用的 PDF 文件路径
PDF_FILE_PATH = require_pdf_path()

# FastAPI 服务器地址
API_BASE = "http://127.0.0.1:8765"

# 测试用户信息
TEST_EMAIL = "e2e_test@test.com"
TEST_USERNAME = "e2e_tester"
TEST_PASSWORD = "test123456"

# 轮询状态的最大等待时间（秒）
MAX_POLL_SECONDS = 120

# 轮询间隔（秒）
POLL_INTERVAL = 3


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def print_step(step_num: int, title: str):
    """打印步骤标题，让每一步都清晰可见"""
    print(f"\n{'='*60}")
    print(f"  Step {step_num}: {title}")
    print(f"{'='*60}")


def print_result(label: str, value):
    """打印结果，截断过长的内容"""
    text = str(value)
    if len(text) > 200:
        text = text[:200] + "..."
    print(f"  {label}: {text}")


# ---------------------------------------------------------------------------
# Step 0: 环境检查
# ---------------------------------------------------------------------------

def check_environment():
    """检查测试前提条件是否满足"""
    print_step(0, "环境检查")

    # 检查 PDF 文件是否存在
    if not os.path.exists(PDF_FILE_PATH):
        print(f"  [FAIL] PDF 文件不存在: {PDF_FILE_PATH}")
        sys.exit(1)
    file_size = os.path.getsize(PDF_FILE_PATH)
    print(f"  [OK] PDF 文件存在: {PDF_FILE_PATH} ({file_size / 1024:.1f} KB)")

    # 检查 Celery worker 是否在运行
    print("  [INFO] 请确保 Celery worker 已在另一个终端启动：")
    print("         celery -A app.tasks.celery_app worker --loglevel=info --pool=solo")

    # 检查 MINERU_API_TOKEN 环境变量
    token = os.environ.get("MINERU_API_TOKEN", "")
    if token:
        print(f"  [OK] MINERU_API_TOKEN 已配置 (长度: {len(token)})")
    else:
        print("  [WARN] MINERU_API_TOKEN 未配置")
        print("         convert_tasks.py 使用 backend='vlm-http-client'")
        print("         未配置 token 时，mineru_plus 会返回错误")
        print("         这是预期行为 — 说明 Celery 流程是通的")

    return True


# ---------------------------------------------------------------------------
# Step 1: 启动 FastAPI 服务器
# ---------------------------------------------------------------------------

def start_fastapi_server():
    """在子进程中启动 FastAPI 服务器"""
    print_step(1, "启动 FastAPI 服务器")

    # 检查服务器是否已经在运行
    # ⚠️ 探针必须是 /health（存活），**不能**是 /docs：
    # 阶段 0.10 起生产姿态（`APP_ENV` 非 dev，含 legacy `DEBUG=false`）下
    # `/docs` / `/openapi.json` / `/redoc` 三条路由**根本不注册**（见
    # `app/main.py::_schema_endpoint_kwargs`），请求得到 404 → 旧探针会一直等到
    # 30 秒超时并打印"[FAIL] 服务器启动超时"，而服务器其实早就起来了。
    # 这不是"把 /docs 换成 /health"那么简单：**任何**门禁（认证/关闭）都会让
    # 一个调试端点不再返回 200 —— 拿调试端点当存活探针本身就是缺陷。
    # /health 是刻意做成不依赖任何东西的存活探针（不碰 DB / broker / 外部服务），
    # 任何姿态下都注册、都 200。这里刻意**不**用 /ready：/ready 表达"现在能不能
    # 干活"（DB 连得上 + schema 在），把依赖抖动翻译成"服务器启动超时"会指向
    # 错误的排查方向；"忙/坏"的分工见 `main.py` 里 /ready 上方的说明。
    try:
        resp = httpx.get(f"{API_BASE}/health", timeout=2)
        if resp.status_code == 200:
            print("  [OK] FastAPI 服务器已在运行，跳过启动")
            return None
    except (httpx.ConnectError, httpx.ConnectTimeout):
        pass

    # 启动服务器
    # 本文件位于 backend/scripts/dev/（计划 0.2 的搬迁），项目根 = 上溯三级
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print("  [INFO] 启动服务器: uvicorn app.main:app --host 127.0.0.1 --port 8765")
    print(f"  [INFO] 工作目录: {backend_dir}")
    print(f"  [INFO] Python: {sys.executable}")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765"],
        cwd=backend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # 等待服务器启动
    print("  [INFO] 等待服务器启动...", end="", flush=True)
    for _ in range(30):
        time.sleep(1)
        print(".", end="", flush=True)
        # 检查进程是否已经退出（启动失败）
        if proc.poll() is not None:
            output = proc.stdout.read().decode("utf-8", errors="replace")
            print(f"\n  [FAIL] 服务器启动失败 (exit code: {proc.returncode})")
            print(f"  [OUTPUT] {output[:500]}")
            sys.exit(1)
        try:
            resp = httpx.get(f"{API_BASE}/health", timeout=2)  # 存活探针，理由见上
            if resp.status_code == 200:
                print(f"\n  [OK] FastAPI 服务器已启动 (PID: {proc.pid})")
                return proc
        except (httpx.ConnectError, httpx.ConnectTimeout):
            continue

    print("\n  [FAIL] 服务器启动超时")
    proc.terminate()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 2: 注册用户 + 获取 Token
# ---------------------------------------------------------------------------

def register_and_get_token(client: httpx.Client) -> str:
    """注册测试用户并获取 JWT Token"""
    print_step(2, "注册用户 + 获取 Token")

    # 先尝试注册
    register_data = {
        "email": TEST_EMAIL,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
    }
    resp = client.post(f"{API_BASE}/api/auth/register", json=register_data)

    if resp.status_code == 201:
        result = resp.json()
        token = result["access_token"]
        print("  [OK] 注册成功")
        print_result("username", result["user"]["username"])
        print_result("token (前20字符)", token[:20] + "...")
        return token
    elif resp.status_code == 400 and "已被注册" in resp.json().get("detail", ""):
        # 用户已存在，走登录
        print("  [INFO] 用户已存在，走登录流程")
        login_data = {"email": TEST_EMAIL, "password": TEST_PASSWORD}
        resp = client.post(f"{API_BASE}/api/auth/login", json=login_data)
        if resp.status_code == 200:
            result = resp.json()
            token = result["access_token"]
            print("  [OK] 登录成功")
            print_result("token (前20字符)", token[:20] + "...")
            return token
        else:
            print(f"  [FAIL] 登录失败: {resp.text}")
            sys.exit(1)
    else:
        print(f"  [FAIL] 注册失败: {resp.text}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 3: 上传 PDF 文件
# ---------------------------------------------------------------------------

def upload_pdf(client: httpx.Client, token: str) -> dict:
    """通过 HTTP API 上传 PDF 文件，和前端行为完全一致"""
    print_step(3, "上传 PDF 文件（走 HTTP API，和前端一模一样）")

    headers = {"Authorization": f"Bearer {token}"}

    with open(PDF_FILE_PATH, "rb") as f:
        files = {"file": ("劳动合同书.pdf", f, "application/pdf")}
        resp = client.post(f"{API_BASE}/api/upload", headers=headers, files=files)

    if resp.status_code == 201:
        note = resp.json()
        print("  [OK] 上传成功")
        print_result("note_id", note["id"])
        print_result("title", note["title"])
        print_result("status", note["status"])
        print_result("source_type", note["source_type"])
        print_result("file_size", f"{note['file_size'] / 1024:.1f} KB")
        return note
    else:
        print(f"  [FAIL] 上传失败: {resp.text}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 4: 轮询笔记状态，等待 Celery worker 消费任务
# ---------------------------------------------------------------------------

def poll_note_status(client: httpx.Client, token: str, note_id: str) -> dict:
    """轮询笔记状态，等待 Celery worker 处理完成"""
    print_step(4, "轮询笔记状态（等待 Celery worker 消费任务）")

    headers = {"Authorization": f"Bearer {token}"}
    start_time = time.time()
    poll_count = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed > MAX_POLL_SECONDS:
            print(f"\n  [FAIL] 超时 ({MAX_POLL_SECONDS}s)，Celery worker 可能未启动")
            print("         请在另一个终端运行：")
            print("         celery -A app.tasks.celery_app worker --loglevel=info --pool=solo")
            sys.exit(1)

        resp = client.get(f"{API_BASE}/api/upload/{note_id}/status", headers=headers)
        if resp.status_code != 200:
            print(f"  [WARN] 状态查询失败: {resp.text}")
            time.sleep(POLL_INTERVAL)
            continue

        status_data = resp.json()
        current_status = status_data["status"]
        poll_count += 1

        print(f"  [POLL #{poll_count}] status={current_status}  ({elapsed:.0f}s)")

        # 终态判断
        if current_status == "converted":
            print(f"\n  [OK] 转换成功！耗时 {elapsed:.1f}s")
            return status_data
        elif current_status == "failed":
            error_msg = status_data.get("error_message", "未知错误")
            print("\n  [EXPECTED] 转换失败（可能 MINERU_API_TOKEN 未配置）")
            print(f"  [ERROR] {error_msg}")
            print("  [INFO] 这说明 Celery 流程是通的！worker 成功消费了任务")
            return status_data

        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Step 5: 获取笔记详情
# ---------------------------------------------------------------------------

def get_note_detail(client: httpx.Client, token: str, note_id: str) -> dict:
    """获取笔记详情，包含 Markdown 内容"""
    print_step(5, "获取笔记详情")

    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(f"{API_BASE}/api/notes/{note_id}", headers=headers)

    if resp.status_code == 200:
        detail = resp.json()
        print("  [OK] 获取详情成功")
        print_result("title", detail["title"])
        print_result("status", detail["status"])
        print_result("page_count", detail.get("page_count"))
        print_result("original_file_path", detail.get("original_file_path"))
        print_result("original_md_path", detail.get("original_md_path"))

        # 输出 Markdown 内容
        md_content = detail.get("original_md_content", "")
        if md_content:
            print("\n  --- Markdown 内容 (前 500 字符) ---")
            print(md_content[:500])
            if len(md_content) > 500:
                print(f"  ... (共 {len(md_content)} 字符)")
        else:
            print("  [INFO] 无 Markdown 内容（转换可能失败）")

        return detail
    else:
        print(f"  [FAIL] 获取详情失败: {resp.text}")
        return {}


# ---------------------------------------------------------------------------
# Step 6: 验证笔记列表
# ---------------------------------------------------------------------------

def verify_notes_list(client: httpx.Client, token: str):
    """验证笔记列表 API"""
    print_step(6, "验证笔记列表")

    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(f"{API_BASE}/api/notes", headers=headers)

    if resp.status_code == 200:
        data = resp.json()
        print("  [OK] 笔记列表获取成功")
        print_result("total", data["total"])
        print_result("page", data["page"])
        for item in data["items"]:
            print(f"         - {item['title']} (status={item['status']}, id={item['id'][:8]}...)")
    else:
        print(f"  [FAIL] 获取列表失败: {resp.text}")


# ---------------------------------------------------------------------------
# Step 7: 清理测试数据
# ---------------------------------------------------------------------------

def cleanup_test_note(client: httpx.Client, token: str, note_id: str):
    """删除测试创建的笔记"""
    print_step(7, "清理测试数据")

    headers = {"Authorization": f"Bearer {token}"}
    resp = client.delete(f"{API_BASE}/api/notes/{note_id}", headers=headers)

    if resp.status_code == 204:
        print("  [OK] 测试笔记已删除")
    else:
        print(f"  [WARN] 删除失败: {resp.text}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  EngramNote 端到端测试")
    print("  模拟前端完整流程：注册 → 上传 → Celery 转换 → 轮询 → 详情")
    print("=" * 60)

    # Step 0: 环境检查
    check_environment()

    # Step 1: 启动 FastAPI 服务器
    server_proc = start_fastapi_server()

    try:
        # 创建 HTTP 客户端
        with httpx.Client(timeout=30) as client:
            # Step 2: 注册 + 获取 Token
            token = register_and_get_token(client)

            # Step 3: 上传 PDF
            note = upload_pdf(client, token)
            note_id = note["id"]

            # Step 4: 轮询状态（等待 Celery worker 消费）
            status_data = poll_note_status(client, token, note_id)

            # Step 5: 获取详情
            get_note_detail(client, token, note_id)

            # Step 6: 验证列表
            verify_notes_list(client, token)

            # Step 7: 清理
            cleanup_test_note(client, token, note_id)

        # 最终总结
        print("\n" + "=" * 60)
        final_status = status_data.get("status", "unknown")
        if final_status == "converted":
            print("  [RESULT] 端到端测试通过！PDF 成功转换为 Markdown")
        elif final_status == "failed":
            error_msg = status_data.get("error_message", "")
            if "API Token" in error_msg or "MINERU_API_TOKEN" in error_msg:
                print("  [RESULT] Celery 流程通畅！")
                print("           转换失败是因为 MINERU_API_TOKEN 未配置")
                print("           配置 token 后即可成功转换")
            else:
                print(f"  [RESULT] 转换失败: {error_msg}")
        else:
            print(f"  [RESULT] 最终状态: {final_status}")
        print("=" * 60)

    finally:
        # 如果我们启动了服务器，就关闭它
        if server_proc is not None:
            print(f"\n  [INFO] 关闭 FastAPI 服务器 (PID: {server_proc.pid})")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()


if __name__ == "__main__":
    main()
