"""
第7周新功能全流程集成测试

使用真实 PDF 文件测试完整链路 + 新功能验证：
1. 注册/登录
2. 上传 PDF
3. 等待转换完成
4. 等待清洗完成
5. 触发理解管道
6. 等待理解完成
7. 查看知识卡片
8. 编辑卡片标题和内容（新功能）
9. 删除一张卡片（新功能）
10. 归档/取消归档笔记（新功能）

运行方式：cd backend && conda activate mineru_env && python tests/test_full_flow.py
"""

import os
import time
import sys
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000/api"
PDF_PATH = os.environ.get("TEST_PDF_PATH", r"D:\engramnote\resource\tests\劳动合同书-田润鑫.pdf")

# 测试用户
TEST_USER = {
    "username": f"testuser{int(time.time())}",
    "email": f"test_{int(time.time())}@example.com",
    "password": "TestPass123!",
}

client = httpx.Client(timeout=60.0)


def log_step(step: str, success: bool = True, detail: str = ""):
    icon = "✓" if success else "✗"
    msg = f"  [{icon}] {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def wait_for_status(note_id: str, token: str, target_statuses: list, max_wait: int = 600, step_name: str = ""):
    """轮询笔记状态，等待达到目标状态"""
    if step_name:
        print(f"  等待状态 {'/'.join(target_statuses)}: {step_name}...", end="", flush=True)
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = client.get(f"{BASE_URL}/notes/{note_id}", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "")
                if status in target_statuses:
                    elapsed = int(time.time() - start)
                    print(f" 完成 ({elapsed}s)")
                    return status, data
                if status in ("failed", "cleaning_failed", "learning_failed"):
                    error = data.get("error_message", "未知错误")
                    print(f" 失败: {error}")
                    return status, {"error_message": error}
        except Exception:
            pass
        time.sleep(5)
    print(f" 超时 ({max_wait}s)")
    return "timeout", {}


def main():
    print("\n" + "=" * 60)
    print("第7周全流程集成测试")
    print("=" * 60)

    token = None
    note_id = None

    # 1. 注册
    print("\n【步骤1】注册用户")
    try:
        resp = client.post(f"{BASE_URL}/auth/register", json=TEST_USER)
        if resp.status_code == 201:
            data = resp.json()
            token = data["access_token"]
            log_step("注册成功", True, f"用户: {TEST_USER['username']}")
        elif resp.status_code == 400 and "已被注册" in resp.text:
            # 用户已存在，尝试登录
            resp = client.post(f"{BASE_URL}/auth/login", json={
                "email": TEST_USER["email"],
                "password": TEST_USER["password"],
            })
            assert resp.status_code == 200
            data = resp.json()
            token = data["access_token"]
            log_step("用户已存在，已登录", True)
        else:
            assert False, f"注册失败: {resp.text}"
    except Exception as e:
        log_step("注册失败", False, str(e))
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    # 2. 上传 PDF
    print("\n【步骤2】上传 PDF")
    pdf_file = Path(PDF_PATH)
    if not pdf_file.exists():
        log_step("PDF 文件不存在", False, PDF_PATH)
        sys.exit(1)

    try:
        with open(pdf_file, "rb") as f:
            resp = client.post(
                f"{BASE_URL}/upload",
                files={"file": (pdf_file.name, f, "application/pdf")},
                headers=headers,
            )
        assert resp.status_code == 201, f"上传失败: {resp.text}"
        note_data = resp.json()
        note_id = note_data["id"]
        log_step("上传成功", True, f"笔记ID: {note_id}")
    except Exception as e:
        log_step("上传失败", False, str(e))
        sys.exit(1)

    # 3. 等待转换完成
    print("\n【步骤3】等待转换完成")
    status, data = wait_for_status(note_id, token, ["converted", "cleaning", "cleaned", "archived"], max_wait=300, step_name="PDF→Markdown转换")
    if status in ("converted", "cleaning", "cleaned", "archived"):
        log_step("转换完成", True, f"状态: {status}")
    else:
        log_step("转换失败", False, f"状态: {status}, 错误: {data.get('error_message', '')}")
        sys.exit(1)

    # 4. 等待清洗完成（如果还没清洗）
    if status in ("converted", "cleaning"):
        print("\n【步骤4】等待清洗完成")
        status, data = wait_for_status(note_id, token, ["cleaned", "archived"], max_wait=600, step_name="AI清洗")
        if status in ("cleaned", "archived"):
            log_step("清洗完成", True, f"状态: {status}")
        else:
            log_step("清洗失败", False, f"状态: {status}, 错误: {data.get('error_message', '')}")
            sys.exit(1)

    # 5. 触发理解管道
    print("\n【步骤5】触发理解管道")
    try:
        resp = client.post(f"{BASE_URL}/understanding/{note_id}/start", headers=headers)
        assert resp.status_code == 200, f"触发理解失败: {resp.text}"
        log_step("理解管道已触发", True)
    except Exception as e:
        log_step("触发理解失败", False, str(e))
        sys.exit(1)

    # 6. 等待理解完成
    print("\n【步骤6】等待理解完成")
    status, data = wait_for_status(note_id, token, ["archived", "learning_failed"], max_wait=600, step_name="AI理解")
    if status == "archived":
        log_step("理解完成", True)
    else:
        log_step("理解失败", False, f"状态: {status}, 错误: {data.get('error_message', '')}")
        sys.exit(1)

    # 7. 查看知识卡片
    print("\n【步骤7】验证知识卡片")
    try:
        resp = client.get(f"{BASE_URL}/understanding/{note_id}/cards", headers=headers)
        assert resp.status_code == 200
        cards_data = resp.json()
        items = cards_data.get("items", [])
        card_count = len(items)
        log_step("知识卡片获取成功", True, f"共 {card_count} 张卡片")

        if card_count == 0:
            log_step("没有卡片可测试", False)
            sys.exit(0)
    except Exception as e:
        log_step("获取卡片失败", False, str(e))
        sys.exit(1)

    # 8. 编辑卡片（新功能）
    print("\n【步骤8】测试编辑卡片")
    first_card = items[0]
    card_id = first_card["id"]
    original_title = first_card["title"]
    new_title = f"[已编辑] {original_title}"
    new_content = f"（编辑测试）{first_card['content']}"

    try:
        resp = client.put(
            f"{BASE_URL}/understanding/cards/{card_id}",
            headers=headers,
            json={"title": new_title, "content": new_content},
        )
        assert resp.status_code == 200, f"编辑卡片失败: {resp.text}"
        updated = resp.json()
        assert updated["title"] == new_title, f"标题未更新: {updated['title']}"
        assert updated["content"] == new_content, "内容未更新"
        log_step("编辑卡片成功", True, f"标题: {original_title} → {new_title}")

        # 恢复卡片 - 将内容改回原始值（不影响后续测试）
        resp = client.put(
            f"{BASE_URL}/understanding/cards/{card_id}",
            headers=headers,
            json={"title": original_title},
        )
        assert resp.status_code == 200
        log_step("卡片已恢复", True)
    except Exception as e:
        log_step("编辑卡片失败", False, str(e))

    # 9. 删除卡片（新功能）
    print("\n【步骤9】测试删除卡片")
    if card_count >= 2:
        try:
            delete_card_id = items[1]["id"]
            delete_card_title = items[1]["title"]
            resp = client.delete(f"{BASE_URL}/understanding/cards/{delete_card_id}", headers=headers)
            assert resp.status_code == 204, f"删除卡片失败: {resp.text}"

            # 验证卡片已删除
            resp = client.get(f"{BASE_URL}/understanding/cards/{delete_card_id}", headers=headers)
            assert resp.status_code == 404, "删除后卡片仍可访问"

            log_step("删除卡片成功", True, f"已删除: {delete_card_title}")

            # 验证题目也被删除
            resp = client.get(f"{BASE_URL}/understanding/{note_id}/questions", headers=headers)
            if resp.status_code == 200:
                questions = resp.json().get("items", [])
                remaining = [q for q in questions if q.get("card_id") == delete_card_id]
                assert len(remaining) == 0, "卡片虽删除但题目未级联删除"
                log_step("关联题目已级联删除", True)
        except Exception as e:
            log_step("删除卡片失败", False, str(e))
    else:
        log_step("卡片数量不足，跳过删除测试", True, f"需要2张，仅有{card_count}张")

    # 10. 归档/取消归档笔记（新功能）
    print("\n【步骤10】测试归档与取消归档")
    try:
        # 检查当前状态
        resp = client.get(f"{BASE_URL}/notes/{note_id}", headers=headers)
        assert resp.status_code == 200
        current_status = resp.json().get("status", "")
        log_step("当前笔记状态", True, current_status)

        if current_status == "archived":
            # 取消归档
            resp = client.post(f"{BASE_URL}/notes/{note_id}/archive", headers=headers)
            assert resp.status_code == 200
            new_status = resp.json().get("status", "")
            assert new_status == "cleaned", f"取消归档后状态应为 cleaned，实际为 {new_status}"
            log_step("取消归档成功", True, f"状态: {current_status} → {new_status}")

            # 重新归档
            resp = client.post(f"{BASE_URL}/notes/{note_id}/archive", headers=headers)
            assert resp.status_code == 200
            new_status = resp.json().get("status", "")
            assert new_status == "archived", f"归档后状态应为 archived，实际为 {new_status}"
            log_step("重新归档成功", True, f"状态: {new_status}")
        else:
            # 归档
            resp = client.post(f"{BASE_URL}/notes/{note_id}/archive", headers=headers)
            assert resp.status_code == 200
            new_status = resp.json().get("status", "")
            assert new_status == "archived", f"归档后状态应为 archived，实际为 {new_status}"
            log_step("归档成功", True, f"状态: {current_status} → {new_status}")

            # 取消归档
            resp = client.post(f"{BASE_URL}/notes/{note_id}/archive", headers=headers)
            assert resp.status_code == 200
            new_status = resp.json().get("status", "")
            assert new_status == "cleaned", f"取消归档后状态应为 cleaned，实际为 {new_status}"
            log_step("取消归档成功", True, f"状态: {new_status}")
    except Exception as e:
        log_step("归档测试失败", False, str(e))

    # 11. 验证归档笔记列表
    print("\n【步骤11】验证已归档笔记列表")
    try:
        resp = client.get(f"{BASE_URL}/notes/archive?page=1&page_size=20", headers=headers)
        assert resp.status_code == 200
        archived_list = resp.json().get("items", [])
        log_step("归档列表获取成功", True, f"共 {len(archived_list)} 条已归档笔记")
    except Exception as e:
        log_step("获取归档列表失败", False, str(e))

    # 12. 去重检测
    print("\n【步骤12】测试卡片去重检测")
    try:
        resp = client.get(f"{BASE_URL}/understanding/{note_id}/duplicates", headers=headers)
        assert resp.status_code == 200
        dup_data = resp.json()
        dup_count = len(dup_data.get("duplicates", []))
        log_step("去重检测完成", True, f"发现 {dup_count} 个重复候选")
    except Exception as e:
        log_step("去重检测失败", False, str(e))

    # 汇总
    print("\n" + "=" * 60)
    print("全流程集成测试完成")
    print("=" * 60)
    print(f"  测试用户: {TEST_USER['username']}")
    print(f"  笔记: {note_id}")
    print(f"  状态: 正常结束")
    print()


if __name__ == "__main__":
    main()
