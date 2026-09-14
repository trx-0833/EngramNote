"""
EngramNote 上传→转换→清洗 全链路测试
=====================================

Karpathy 风格：单文件、零额外依赖、每步打印可见信息。

完整流程：
1. 注册用户 + 获取 Token
2. 上传 PDF 文件
3. 轮询等待转换完成（converted）
4. 轮询等待清洗完成（cleaned）
5. 获取笔记详情，检查清洗版内容
6. 获取清洗 diff 数据
7. 测试恢复/删除重复块
8. 清理测试数据

前提条件：
- FastAPI 服务器已启动：uvicorn app.main:app --reload --port 8000
- Celery worker 已启动：celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
- 已安装依赖：pip install sentence-transformers chromadb
"""

import os
import sys
import time
import httpx

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

PDF_FILE_PATH = r"D:\engramnote\resource\tests\劳动合同书-田润鑫.pdf"
API_BASE = "http://127.0.0.1:8000"
TEST_EMAIL = "clean_test@test.com"
TEST_USERNAME = "3162323563"
TEST_PASSWORD = "Tianrunxin123@"
MAX_POLL_SECONDS = 300   # 清洗可能较慢，给 5 分钟
POLL_INTERVAL = 5


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def print_step(step_num: int, title: str):
    print(f"\n{'='*60}")
    print(f"  Step {step_num}: {title}")
    print(f"{'='*60}")


def print_result(label: str, value):
    text = str(value)
    if len(text) > 300:
        text = text[:300] + "..."
    print(f"  {label}: {text}")


def poll_status(client: httpx.Client, token: str, note_id: str, target_status: str, step_name: str) -> dict:
    """轮询笔记状态直到达到目标状态或失败"""
    headers = {"Authorization": f"Bearer {token}"}
    start_time = time.time()
    poll_count = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed > MAX_POLL_SECONDS:
            print(f"\n  [FAIL] 超时 ({MAX_POLL_SECONDS}s)")
            sys.exit(1)

        try:
            resp = client.get(f"{API_BASE}/api/upload/{note_id}/status", headers=headers, timeout=15)
        except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout) as e:
            print(f"  [WARN] 请求异常: {e}, 重试...")
            time.sleep(POLL_INTERVAL)
            continue

        if resp.status_code != 200:
            print(f"  [WARN] 状态查询失败: {resp.text}")
            time.sleep(POLL_INTERVAL)
            continue

        status_data = resp.json()
        current_status = status_data["status"]
        poll_count += 1
        print(f"  [POLL #{poll_count}] status={current_status}  ({elapsed:.0f}s)")

        if current_status == target_status:
            print(f"\n  [OK] {step_name}完成！耗时 {elapsed:.1f}s")
            return status_data
        elif current_status == "failed":
            error_msg = status_data.get("error_message", "未知错误")
            print(f"\n  [FAIL] {step_name}失败: {error_msg}")
            return status_data

        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  EngramNote 上传→转换→清洗 全链路测试")
    print("=" * 60)

    # Step 0: 环境检查
    print_step(0, "环境检查")
    if not os.path.exists(PDF_FILE_PATH):
        print(f"  [FAIL] PDF 文件不存在: {PDF_FILE_PATH}")
        sys.exit(1)
    file_size = os.path.getsize(PDF_FILE_PATH)
    print(f"  [OK] PDF 文件存在: {PDF_FILE_PATH} ({file_size / 1024:.1f} KB)")
    print("  [INFO] 请确保 FastAPI 和 Celery worker 已启动")

    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        # Step 1: 健康检查
        print_step(1, "健康检查")
        try:
            resp = client.get(f"{API_BASE}/health")
            print(f"  [OK] 服务器运行中: {resp.json()}")
        except httpx.ConnectError:
            print(f"  [FAIL] 无法连接服务器 {API_BASE}")
            print("  请先启动: uvicorn app.main:app --reload --port 8000")
            sys.exit(1)

        # Step 2: 注册/登录
        print_step(2, "注册用户 + 获取 Token")
        register_data = {"email": TEST_EMAIL, "username": TEST_USERNAME, "password": TEST_PASSWORD}
        resp = client.post(f"{API_BASE}/api/auth/register", json=register_data)

        if resp.status_code == 201:
            token = resp.json()["access_token"]
            print("  [OK] 注册成功")
        elif resp.status_code == 400:
            # 已存在，走登录
            resp = client.post(f"{API_BASE}/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
            token = resp.json()["access_token"]
            print("  [OK] 登录成功（用户已存在）")
        else:
            print(f"  [FAIL] 注册/登录失败: {resp.text}")
            sys.exit(1)

        headers = {"Authorization": f"Bearer {token}"}

        # Step 3: 上传 PDF
        print_step(3, "上传 PDF 文件")
        with open(PDF_FILE_PATH, "rb") as f:
            files = {"file": ("劳动合同书-田润鑫.pdf", f, "application/pdf")}
            resp = client.post(f"{API_BASE}/api/upload", headers=headers, files=files)

        if resp.status_code == 201:
            note = resp.json()
            note_id = note["id"]
            print("  [OK] 上传成功")
            print_result("note_id", note_id)
            print_result("title", note["title"])
            print_result("status", note["status"])
        else:
            print(f"  [FAIL] 上传失败: {resp.text}")
            sys.exit(1)

        # Step 4: 等待转换完成
        print_step(4, "等待转换完成（converted）")
        status_data = poll_status(client, token, note_id, "converted", "转换")
        if status_data["status"] == "failed":
            print("  [FAIL] 转换失败，无法继续测试清洗")
            sys.exit(1)

        # Step 5: 等待清洗完成（转换成功后会自动触发清洗）
        print_step(5, "等待清洗完成（cleaned）")
        # 先检查当前状态，可能已经进入 cleaning
        resp = client.get(f"{API_BASE}/api/upload/{note_id}/status", headers=headers)
        current = resp.json()["status"]
        print(f"  [INFO] 当前状态: {current}")

        if current == "cleaned":
            print("  [OK] 清洗已完成！")
        elif current == "cleaning":
            status_data = poll_status(client, token, note_id, "cleaned", "清洗")
            if status_data["status"] == "failed":
                print(f"  [FAIL] 清洗失败: {status_data.get('error_message')}")
                sys.exit(1)
        elif current == "converted":
            # 转换完成但清洗未自动触发，手动触发
            print("  [INFO] 清洗未自动触发，手动触发...")
            resp = client.post(f"{API_BASE}/api/cleaning/{note_id}/start", headers=headers)
            print(f"  [INFO] 手动触发结果: {resp.json()}")
            status_data = poll_status(client, token, note_id, "cleaned", "清洗")
            if status_data["status"] == "failed":
                print(f"  [FAIL] 清洗失败: {status_data.get('error_message')}")
                sys.exit(1)
        else:
            print(f"  [WARN] 意外状态: {current}，尝试手动触发清洗")
            resp = client.post(f"{API_BASE}/api/cleaning/{note_id}/start", headers=headers)
            if resp.status_code in (200, 201):
                status_data = poll_status(client, token, note_id, "cleaned", "清洗")
            else:
                print(f"  [FAIL] 无法触发清洗: {resp.text}")
                sys.exit(1)

        # Step 6: 获取笔记详情，检查清洗版
        print_step(6, "获取笔记详情（检查清洗版内容）")
        resp = client.get(f"{API_BASE}/api/notes/{note_id}", headers=headers)
        if resp.status_code == 200:
            detail = resp.json()
            print("  [OK] 获取详情成功")
            print_result("status", detail["status"])
            print_result("clean_md_path", detail.get("clean_md_path"))

            # 原始版内容
            orig_md = detail.get("original_md_content", "")
            print("\n  --- 原始 Markdown (前 300 字符) ---")
            print(f"  {orig_md[:300]}")
            print(f"  ... (共 {len(orig_md)} 字符)")

            # 清洗版内容
            clean_md = detail.get("clean_md_content", "")
            if clean_md:
                print("\n  --- 清洗版 Markdown (前 300 字符) ---")
                print(f"  {clean_md[:300]}")
                print(f"  ... (共 {len(clean_md)} 字符)")
            else:
                print("  [WARN] 清洗版内容为空")

            # 元数据中的清洗统计
            metadata = detail.get("metadata_", {})
            if metadata:
                print("\n  --- 清洗统计 ---")
                print_result("total_chunks", metadata.get("total_chunks"))
                print_result("duplicate_blocks", metadata.get("duplicate_blocks"))
                clean_stats = metadata.get("clean_stats", {})
                if clean_stats:
                    print_result("empty_lines_removed", clean_stats.get("empty_lines_removed"))
                    print_result("headers_footers_removed", clean_stats.get("headers_footers_removed"))
                    print_result("watermarks_removed", clean_stats.get("watermarks_removed"))
                duplicates_detail = metadata.get("duplicates_detail", [])
                if duplicates_detail:
                    print("\n  --- 重复块详情 ---")
                    for dup in duplicates_detail:
                        print(f"    块 {dup['block_index']} 与块 {dup['duplicate_of']} 相似度 {dup['similarity']:.4f}")
        else:
            print(f"  [FAIL] 获取详情失败: {resp.text}")

        # Step 7: 获取清洗 diff 数据
        print_step(7, "获取清洗 diff 数据")
        resp = client.get(f"{API_BASE}/api/cleaning/{note_id}/diff", headers=headers)
        if resp.status_code == 200:
            diff = resp.json()
            print("  [OK] 获取 diff 成功")
            print_result("original_lines", diff["original_lines"])
            print_result("clean_lines", diff["clean_lines"])
            print_result("diff_blocks_count", len(diff["blocks"]))
            if diff.get("stats"):
                print("  --- diff 统计 ---")
                for k, v in diff["stats"].items():
                    print(f"    {k}: {v}")
            # 显示前几个 diff 块
            for i, block in enumerate(diff["blocks"][:3]):
                print(f"\n  --- Diff Block {i+1} ---")
                for line in block["lines"][:5]:
                    prefix = "+" if line["type"] == "added" else "-" if line["type"] == "removed" else " "
                    print(f"    {prefix} {line['content'][:80]}")
        else:
            print(f"  [FAIL] 获取 diff 失败: {resp.text}")

        # Step 8: 测试恢复/删除重复块
        print_step(8, "测试恢复/删除重复块")
        resp = client.get(f"{API_BASE}/api/cleaning/{note_id}/status", headers=headers)
        if resp.status_code == 200:
            cleaning_status = resp.json()
            metadata = cleaning_status.get("metadata_", {})
            duplicates_detail = metadata.get("duplicates_detail", []) if metadata else []

            if duplicates_detail:
                # 测试恢复第一个重复块
                first_dup = duplicates_detail[0]
                block_index = first_dup["block_index"]
                print(f"  [INFO] 测试恢复块 {block_index}...")
                resp = client.post(f"{API_BASE}/api/cleaning/{note_id}/restore/{block_index}", headers=headers)
                if resp.status_code == 200:
                    print(f"  [OK] 恢复成功: {resp.json()['message']}")
                else:
                    print(f"  [FAIL] 恢复失败: {resp.text}")

                # 如果还有第二个重复块，测试删除
                if len(duplicates_detail) > 1:
                    second_dup = duplicates_detail[1]
                    block_index2 = second_dup["block_index"]
                    print(f"  [INFO] 测试删除块 {block_index2}...")
                    resp = client.delete(f"{API_BASE}/api/cleaning/{note_id}/block/{block_index2}", headers=headers)
                    if resp.status_code == 200:
                        print(f"  [OK] 删除成功: {resp.json()['message']}")
                    else:
                        print(f"  [FAIL] 删除失败: {resp.text}")
            else:
                print("  [INFO] 无重复块，跳过恢复/删除测试")
        else:
            print("  [WARN] 无法获取清洗状态")

        # Step 9: 清理测试数据
        print_step(9, "清理测试数据")
        resp = client.delete(f"{API_BASE}/api/notes/{note_id}", headers=headers)
        if resp.status_code == 204:
            print("  [OK] 测试笔记已删除")
        else:
            print(f"  [WARN] 删除失败: {resp.text}")

    # 最终总结
    print("\n" + "=" * 60)
    print("  [RESULT] 上传→转换→清洗 全链路测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
