"""
Week5-6 全流程集成测试

使用真实 PDF 文件测试完整链路：
1. 注册/登录获取 Token
2. 上传 PDF
3. 等待转换完成
4. 等待清洗完成
5. 触发理解管道
6. 等待理解完成
7. 查看知识卡片
8. 触发题目生成
9. 查看题目
10. RAG 问答

运行方式：cd backend && conda activate mineru_env && python tests/test_week5_6_integration.py
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


def wait_for_status(note_id: str, token: str, target_statuses: list, max_wait: int = 300, step_name: str = ""):
    """轮询笔记状态，等待达到目标状态"""
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = client.get(f"{BASE_URL}/notes/{note_id}", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "")
                if status in target_statuses:
                    return status, data
                if status in ("failed", "cleaning_failed", "learning_failed"):
                    error = data.get("error_message", "未知错误")
                    return status, {"error_message": error}
                elapsed = int(time.time() - start)
                print(f"    ... 等待{step_name} (当前状态: {status}, 已等待 {elapsed}s)", end="\r")
        except Exception as e:
            print(f"    ... 请求异常: {e}, 重试中", end="\r")
        time.sleep(5)
    return "timeout", {}


def main():
    print("=" * 60)
    print("  Week5-6 全流程集成测试")
    print("=" * 60)

    # ===== Step 1: 注册用户 =====
    print("\n[Step 1] 注册测试用户")
    resp = client.post(f"{BASE_URL}/auth/register", json=TEST_USER)
    if resp.status_code in (200, 201):
        log_step("注册成功", detail=f"用户名: {TEST_USER['username']}")
    else:
        print(f"  注册失败: {resp.status_code} {resp.text}")
        sys.exit(1)

    # ===== Step 2: 登录获取 Token =====
    print("\n[Step 2] 登录获取 Token")
    resp = client.post(f"{BASE_URL}/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    if resp.status_code in (200, 201):
        token = resp.json()["access_token"]
        log_step("登录成功", detail=f"Token: {token[:20]}...")
    else:
        print(f"  登录失败: {resp.status_code} {resp.text}")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    # ===== Step 3: 上传 PDF =====
    print("\n[Step 3] 上传 PDF 文件")
    pdf_path = Path(PDF_PATH)
    if not pdf_path.exists():
        print(f"  PDF 文件不存在: {PDF_PATH}")
        sys.exit(1)

    with open(pdf_path, "rb") as f:
        resp = client.post(
            f"{BASE_URL}/upload",
            headers=headers,
            files={"file": (pdf_path.name, f, "application/pdf")},
            data={"title": "劳动合同书-田润鑫"},
            timeout=60.0,
        )

    if resp.status_code in (200, 201):
        note_id = resp.json()["id"]
        log_step("上传成功", detail=f"笔记 ID: {note_id}")
    else:
        print(f"  上传失败: {resp.status_code} {resp.text}")
        sys.exit(1)

    # ===== Step 4: 等待转换完成 =====
    print("\n[Step 4] 等待文档转换完成（可能需要1-3分钟）")
    status, data = wait_for_status(note_id, token, ["converted", "cleaned", "learning", "archived"], max_wait=300, step_name="转换")
    if status in ("converted", "cleaned", "learning", "archived"):
        log_step("转换完成", detail=f"状态: {status}")
    elif status == "failed":
        print(f"  转换失败: {data.get('error_message', '')}")
        sys.exit(1)
    else:
        print(f"  转换超时")
        sys.exit(1)

    # ===== Step 5: 等待清洗完成 =====
    print("\n[Step 5] 等待清洗完成")
    status, data = wait_for_status(note_id, token, ["cleaned", "learning", "archived"], max_wait=300, step_name="清洗")
    if status in ("cleaned", "learning", "archived"):
        log_step("清洗完成", detail=f"状态: {status}")
    elif status == "cleaning_failed":
        print(f"  清洗失败: {data.get('error_message', '')}")
        sys.exit(1)
    else:
        print(f"  清洗超时")
        sys.exit(1)

    # ===== Step 6: 触发理解管道 =====
    print("\n[Step 6] 触发理解管道")
    resp = client.post(f"{BASE_URL}/understanding/{note_id}/start", headers=headers)
    if resp.status_code == 200:
        log_step("理解管道已触发", detail=resp.json().get("message", ""))
    else:
        print(f"  触发失败: {resp.status_code} {resp.text}")
        sys.exit(1)

    # ===== Step 7: 等待理解完成 =====
    print("\n[Step 7] 等待理解完成（调用 LLM，可能需要2-5分钟）")
    status, data = wait_for_status(note_id, token, ["archived"], max_wait=600, step_name="理解")
    if status == "archived":
        log_step("理解完成", detail="状态: archived")
    elif status == "learning_failed":
        print(f"  理解失败: {data.get('error_message', '')}")
        sys.exit(1)
    else:
        print(f"  理解超时")
        sys.exit(1)

    # ===== Step 8: 查看知识卡片 =====
    print("\n[Step 8] 查看知识卡片")
    resp = client.get(f"{BASE_URL}/understanding/{note_id}/cards", headers=headers)
    if resp.status_code == 200:
        cards_data = resp.json()
        total_cards = cards_data["total"]
        log_step("知识卡片查询成功", detail=f"共 {total_cards} 张卡片")
        if total_cards > 0:
            for i, card in enumerate(cards_data["items"][:3]):
                print(f"    卡片 {i+1}: [{card['card_type']}] {card['title'][:50]}")
            if total_cards > 3:
                print(f"    ... 还有 {total_cards - 3} 张卡片")
    else:
        print(f"  查询失败: {resp.status_code} {resp.text}")

    # ===== Step 9: 查看章节摘要 =====
    print("\n[Step 9] 查看章节摘要")
    resp = client.get(f"{BASE_URL}/understanding/{note_id}/chapters", headers=headers)
    if resp.status_code == 200:
        chapters_data = resp.json()
        chapters = chapters_data.get("chapters", [])
        log_step("章节摘要查询成功", detail=f"共 {len(chapters)} 个章节")
        for ch in chapters[:3]:
            print(f"    章节: {ch['chapter_title']} (卡片数: {ch['card_count']})")
    else:
        print(f"  查询失败: {resp.status_code} {resp.text}")

    # ===== Step 10: 等待题目生成 =====
    print("\n[Step 10] 等待题目生成（理解完成后自动触发）")
    time.sleep(10)  # 等待 Celery 任务被消费

    resp = client.get(f"{BASE_URL}/understanding/{note_id}/questions", headers=headers)
    if resp.status_code == 200:
        questions_data = resp.json()
        total_questions = questions_data["total"]
        if total_questions > 0:
            log_step("题目查询成功", detail=f"共 {total_questions} 道题")
            for i, q in enumerate(questions_data["items"][:3]):
                print(f"    题目 {i+1}: [{q['question_type']}/{q['difficulty']}] {q['question'][:60]}")
        else:
            # 如果自动生成还没完成，手动触发
            print("    题目尚未生成，手动触发...")
            resp = client.post(f"{BASE_URL}/understanding/{note_id}/generate-questions", headers=headers)
            if resp.status_code == 200:
                log_step("题目生成已触发", detail="等待生成...")
                time.sleep(30)
                resp = client.get(f"{BASE_URL}/understanding/{note_id}/questions", headers=headers)
                if resp.status_code == 200:
                    questions_data = resp.json()
                    total_questions = questions_data["total"]
                    log_step("题目查询成功", detail=f"共 {total_questions} 道题")
                    for i, q in enumerate(questions_data["items"][:3]):
                        print(f"    题目 {i+1}: [{q['question_type']}/{q['difficulty']}] {q['question'][:60]}")
    else:
        print(f"  查询失败: {resp.status_code} {resp.text}")

    # ===== Step 11: 查看所有知识卡片 =====
    print("\n[Step 11] 查看所有知识卡片（跨笔记）")
    resp = client.get(f"{BASE_URL}/understanding/cards", headers=headers)
    if resp.status_code == 200:
        all_cards = resp.json()
        log_step("全部知识卡片查询成功", detail=f"共 {all_cards['total']} 张卡片")
    else:
        print(f"  查询失败: {resp.status_code} {resp.text}")

    # ===== Step 12: 查看所有题目 =====
    print("\n[Step 12] 查看所有题目（跨笔记）")
    resp = client.get(f"{BASE_URL}/understanding/questions", headers=headers)
    if resp.status_code == 200:
        all_questions = resp.json()
        log_step("全部题目查询成功", detail=f"共 {all_questions['total']} 道题")
    else:
        print(f"  查询失败: {resp.status_code} {resp.text}")

    # ===== Step 13: RAG 问答 =====
    print("\n[Step 13] RAG 问答测试")
    test_questions = [
        "劳动合同的期限是多久？",
        "田润鑫的工资是多少？",
    ]
    for q in test_questions:
        resp = client.post(f"{BASE_URL}/understanding/ask", headers=headers, json={"question": q})
        if resp.status_code == 200:
            answer_data = resp.json()
            log_step(f"问答成功", detail=f"Q: {q}")
            print(f"    A: {answer_data['answer'][:100]}...")
            if answer_data.get("sources"):
                for s in answer_data["sources"][:2]:
                    print(f"    引用: {s['note_title']}")
            print(f"    提供商: {answer_data.get('provider', 'unknown')}")
        else:
            print(f"  问答失败: {resp.status_code} {resp.text}")

    # ===== Step 14: 查看知识卡片详情 =====
    print("\n[Step 14] 查看知识卡片详情")
    if total_cards > 0:
        first_card_id = cards_data["items"][0]["id"]
        resp = client.get(f"{BASE_URL}/understanding/cards/{first_card_id}", headers=headers)
        if resp.status_code == 200:
            card_detail = resp.json()
            log_step("卡片详情查询成功", detail=f"标题: {card_detail['title'][:50]}")
            print(f"    类型: {card_detail['card_type']}")
            print(f"    内容: {card_detail['content'][:100]}...")
        else:
            print(f"  查询失败: {resp.status_code} {resp.text}")

    # ===== 总结 =====
    print("\n" + "=" * 60)
    print("  全流程集成测试完成！")
    print("=" * 60)
    print(f"  笔记 ID: {note_id}")
    print(f"  知识卡片: {total_cards} 张")
    print(f"  题目: {total_questions if 'total_questions' in dir() else '?'} 道")
    print(f"  问答: 已测试 {len(test_questions)} 个问题")


if __name__ == "__main__":
    main()
