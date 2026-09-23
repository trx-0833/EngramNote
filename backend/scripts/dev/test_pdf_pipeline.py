"""
端到端测试脚本：绕过前端，直接测试 PDF 上传 + Celery 转换全链路

使用方法：
    conda activate mineru_env
    cd D:\engramnote\backend
    python test_pdf_pipeline.py

流程：
    1. 注册/登录获取 JWT Token
    2. 通过 API 上传 PDF 文件
    3. 直接调用 _convert_document 执行转换（绕过 Celery broker 的 Windows 兼容问题）
    4. 通过 API 获取笔记详情，输出 Markdown 内容
"""

import asyncio
import os
import sys
import time
import urllib.request
import json
from app.test_support.corpus import require_pdf_path

# ============================================================
# 配置
# ============================================================
BASE_URL = "http://localhost:8000"
PDF_PATH = require_pdf_path()
TEST_EMAIL = f"pipeline_test_{int(time.time())}@test.com"
TEST_USERNAME = f"tester_{int(time.time())}"
TEST_PASSWORD = "test123456"

# ============================================================
# HTTP 工具函数
# ============================================================


def post_json(path, data, token=None):
    """发送 JSON POST 请求"""
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            return json.loads(error_body), e.code
        except json.JSONDecodeError:
            return {"error": error_body}, e.code


def get_json(path, token=None):
    """发送 GET 请求"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode()
        return json.loads(body) if body else {}, resp.status
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            return json.loads(error_body), e.code
        except json.JSONDecodeError:
            return {"error": error_body}, e.code


def upload_file_api(file_path, token):
    """通过 API 上传文件"""
    filename = os.path.basename(file_path)
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(f"{BASE_URL}/api/upload", data=body, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            return json.loads(error_body), e.code
        except json.JSONDecodeError:
            return {"error": error_body}, e.code


# ============================================================
# 直接调用转换函数（绕过 Celery broker）
# ============================================================

async def run_conversion(note_id, file_path, source_type):
    """直接调用 _convert_document 执行转换"""
    # 确保项目模块可导入
    # 本文件位于 backend/scripts/dev/（计划 0.2 的搬迁），项目根 = 上溯三级
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.tasks.convert_tasks import _convert_document
    print(f"\n  开始转换: note_id={note_id}, file={file_path}, type={source_type}")
    await _convert_document(note_id, file_path, source_type)
    print("  转换函数执行完毕")


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 70)
    print("  EngramNote PDF 上传 + 转换 端到端测试")
    print("=" * 70)

    # 检查 PDF 文件是否存在
    if not os.path.exists(PDF_PATH):
        print(f"\n[错误] PDF 文件不存在: {PDF_PATH}")
        return
    file_size = os.path.getsize(PDF_PATH)
    print(f"\nPDF 文件: {PDF_PATH}")
    print(f"文件大小: {file_size / 1024:.1f} KB")

    # ----------------------------------------------------------
    # Step 1: 健康检查
    # ----------------------------------------------------------
    print("\n" + "-" * 70)
    print("Step 1: 健康检查")
    result, status = get_json("/health")
    if status != 200 or result.get("status") != "ok":
        print("  [失败] 后端服务未运行，请先启动: uvicorn app.main:app --port 8000")
        return
    print("  [通过] 后端服务正常运行")

    # ----------------------------------------------------------
    # Step 2: 注册用户
    # ----------------------------------------------------------
    print("\n" + "-" * 70)
    print("Step 2: 注册测试用户")
    result, status = post_json("/api/auth/register", {
        "email": TEST_EMAIL,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
    })
    if status == 201 and "access_token" in result:
        token = result["access_token"]
        print(f"  [通过] 注册成功: {TEST_USERNAME}")
    elif status == 400:
        # 用户已存在，尝试登录
        print("  用户已存在，尝试登录...")
        result, status = post_json("/api/auth/login", {
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        if status == 200 and "access_token" in result:
            token = result["access_token"]
            print("  [通过] 登录成功")
        else:
            print(f"  [失败] 登录失败: {result}")
            return
    else:
        print(f"  [失败] 注册失败: {result}")
        return

    # ----------------------------------------------------------
    # Step 3: 上传 PDF
    # ----------------------------------------------------------
    print("\n" + "-" * 70)
    print("Step 3: 上传 PDF 文件")
    result, status = upload_file_api(PDF_PATH, token)
    if status != 201:
        print(f"  [失败] 上传失败 (HTTP {status}): {result}")
        return

    note_id = result["id"]
    note_status = result["status"]
    note_title = result["title"]
    print("  [通过] 上传成功")
    print(f"    笔记 ID:  {note_id}")
    print(f"    标题:      {note_title}")
    print(f"    状态:      {note_status}")

    # ----------------------------------------------------------
    # Step 4: 直接调用转换（绕过 Celery broker）
    # ----------------------------------------------------------
    print("\n" + "-" * 70)
    print("Step 4: 执行文档转换（直接调用 _convert_document）")
    print("  说明: 由于 Windows 上 Celery 文件系统 broker 存在兼容问题，")
    print("        此处直接调用转换函数，与 Celery worker 调用的是同一个函数。")

    # 从笔记记录中获取存储路径
    result, status = get_json(f"/api/notes/{note_id}", token)
    if status != 200:
        print(f"  [失败] 无法获取笔记详情: {result}")
        return

    file_path_in_storage = result.get("original_file_path", "")
    source_type = result.get("source_type", "pdf")
    print(f"    存储路径:  {file_path_in_storage}")
    print(f"    文件类型:  {source_type}")

    # 执行转换
    start_time = time.time()
    try:
        asyncio.run(run_conversion(note_id, file_path_in_storage, source_type))
        elapsed = time.time() - start_time
        print(f"  [通过] 转换完成，耗时 {elapsed:.1f} 秒")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"  [失败] 转换异常（耗时 {elapsed:.1f} 秒）: {e}")
        import traceback
        traceback.print_exc()

    # ----------------------------------------------------------
    # Step 5: 查询转换结果
    # ----------------------------------------------------------
    print("\n" + "-" * 70)
    print("Step 5: 查询笔记状态和内容")
    result, status = get_json(f"/api/notes/{note_id}", token)
    if status != 200:
        print(f"  [失败] 无法获取笔记: {result}")
        return

    final_status = result.get("status", "unknown")
    error_msg = result.get("error_message")
    md_content = result.get("original_md_content")
    md_path = result.get("original_md_path")

    print(f"    最终状态:  {final_status}")
    if error_msg:
        print(f"    错误信息:  {error_msg}")
    if md_path:
        print(f"    Markdown 路径: {md_path}")

    # ----------------------------------------------------------
    # Step 6: 输出 Markdown 内容
    # ----------------------------------------------------------
    print("\n" + "-" * 70)
    print("Step 6: Markdown 内容")
    if md_content:
        print(f"    内容长度: {len(md_content)} 字符")
        print()
        print("    " + "=" * 60)
        for line in md_content.split("\n"):
            print("    " + line)
        print("    " + "=" * 60)
    else:
        print("    [无内容] Markdown 内容为空")
        if final_status == "converting":
            print("    可能转换仍在进行中，请稍后再查询")
        elif final_status == "failed":
            print("    转换失败，请检查错误信息")

    # ----------------------------------------------------------
    # 最终结果
    # ----------------------------------------------------------
    print("\n" + "=" * 70)
    if final_status == "converted" and md_content:
        print("  测试结果: 全部通过 - PDF 成功转换为 Markdown")
    elif final_status == "failed":
        print("  测试结果: 转换失败 - 请检查上方错误信息")
    else:
        print(f"  测试结果: 状态异常 - {final_status}")
    print("=" * 70)


if __name__ == "__main__":
    main()
