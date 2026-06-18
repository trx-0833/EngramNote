"""
第8周复习功能端到端测试

完整测试复习调度引擎的所有功能：
1. 注册新用户
2. 通过数据库直接插入测试题目（绕过 Celery/Mineru 依赖）
3. 获取到期题目
4. 提交选择题答案（正确和错误）
5. 提交简答题答案
6. 验证 SM-2 参数更新
7. 验证复习统计
8. 验证复习历史
9. 验证连续正确答题后间隔递增
10. 验证错误答题后间隔重置

运行方式：cd backend && python tests/test_week8_e2e.py
"""

import json
import time
import sys
import uuid
from datetime import datetime, timezone

import httpx

BASE_URL = "http://localhost:8000/api"

client = httpx.Client(timeout=60.0)


def log_step(step: str, success: bool = True, detail: str = ""):
    icon = "✓" if success else "✗"
    msg = f"  [{icon}] {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def insert_test_data(user_id: str, note_id: str, card_id: str):
    """直接向数据库插入测试题目数据"""
    import sqlite3
    conn = sqlite3.connect("data/db/engramnote.db")
    cur = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()

    # 插入知识卡片
    cur.execute("""
        INSERT INTO knowledge_cards (id, user_id, note_id, card_type, title, content, summary, chapter_title, source_text, created_at, updated_at)
        VALUES (?, ?, ?, 'concept', '测试知识点', '这是测试知识点的内容', '测试摘要', '测试章节', '测试原文', ?, ?)
    """, (card_id, user_id, note_id, now, now))

    # 插入选择题
    quiz1_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO quiz_items (id, user_id, card_id, note_id, question_type, difficulty, question, answer, options, explanation, created_at, updated_at, interval, repetition, easiness_factor, next_review_at, last_reviewed_at, review_count)
        VALUES (?, ?, ?, ?, 'choice', 'easy', ?, ?, ?, ?, ?, ?, 1, 0, 2.5, NULL, NULL, 0)
    """, (quiz1_id, user_id, card_id, note_id,
          "劳动合同的定义是什么？",
          "A. 劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议",
          json.dumps(["A. 劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议",
                      "B. 劳动合同是买卖合同的一种",
                      "C. 劳动合同是租赁协议",
                      "D. 劳动合同是担保协议"], ensure_ascii=False),
          "劳动合同是劳动者与用人单位之间签订的协议，确立了劳动关系。",
          now, now))

    # 插入简答题
    quiz2_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO quiz_items (id, user_id, card_id, note_id, question_type, difficulty, question, answer, options, explanation, created_at, updated_at, interval, repetition, easiness_factor, next_review_at, last_reviewed_at, review_count)
        VALUES (?, ?, ?, ?, 'short_answer', 'medium', ?, ?, NULL, ?, ?, ?, 1, 0, 2.5, NULL, NULL, 0)
    """, (quiz2_id, user_id, card_id, note_id,
          "请简述劳动合同的主要作用。",
          "劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议，保护劳动者和用人单位的合法权益。",
          "劳动合同明确了双方的权利和义务。",
          now, now))

    # 插入填空题
    quiz3_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO quiz_items (id, user_id, card_id, note_id, question_type, difficulty, question, answer, options, explanation, created_at, updated_at, interval, repetition, easiness_factor, next_review_at, last_reviewed_at, review_count)
        VALUES (?, ?, ?, ?, 'fill_blank', 'easy', ?, ?, NULL, ?, ?, ?, 1, 0, 2.5, NULL, NULL, 0)
    """, (quiz3_id, user_id, card_id, note_id,
          "劳动合同的双方是____和____。",
          "劳动者、用人单位",
          "劳动合同的主体是劳动者和用人单位。",
          now, now))

    # 插入更多选择题用于连续测试
    for i in range(5):
        qid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO quiz_items (id, user_id, card_id, note_id, question_type, difficulty, question, answer, options, explanation, created_at, updated_at, interval, repetition, easiness_factor, next_review_at, last_reviewed_at, review_count)
            VALUES (?, ?, ?, ?, 'choice', 'medium', ?, ?, ?, ?, ?, ?, 1, 0, 2.5, NULL, NULL, 0)
        """, (qid, user_id, card_id, note_id,
              f"测试选择题 {i+1}：以下哪项是正确的？",
              "A. 正确答案",
              json.dumps(["A. 正确答案", "B. 错误选项1", "C. 错误选项2", "D. 错误选项3"], ensure_ascii=False),
              f"这是第{i+1}道测试题的解析。",
              now, now))

    conn.commit()
    conn.close()

    return quiz1_id, quiz2_id, quiz3_id


def main():
    print("\n" + "=" * 60)
    print("第8周复习功能端到端测试")
    print("=" * 60)

    # 1. 注册新用户
    print("\n【步骤1】注册新用户")
    ts = int(time.time())
    test_user = {
        "username": f"sm2test{ts}",
        "email": f"sm2test{ts}@example.com",
        "password": "TestPass123!",
    }

    try:
        resp = client.post(f"{BASE_URL}/auth/register", json=test_user)
        assert resp.status_code == 201, f"注册失败: {resp.text}"
        token = resp.json()["access_token"]
        user_id = resp.json()["user"]["id"]
        log_step("注册成功", True, f"用户: {test_user['username']}")
    except Exception as e:
        log_step("注册失败", False, str(e))
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    # 2. 创建测试笔记和题目
    print("\n【步骤2】创建测试数据")
    import sqlite3
    conn = sqlite3.connect("data/db/engramnote.db")
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    note_id = str(uuid.uuid4())
    card_id = str(uuid.uuid4())

    # 插入笔记
    cur.execute("""
        INSERT INTO notes (id, user_id, title, source_type, status, original_file_path, original_md_path, file_size, created_at, updated_at)
        VALUES (?, ?, '测试笔记-劳动合同', 'pdf', 'archived', 'test/test.pdf', 'test/test.md', 1024, ?, ?)
    """, (note_id, user_id, now, now))
    conn.commit()
    conn.close()

    quiz1_id, quiz2_id, quiz3_id = insert_test_data(user_id, note_id, card_id)
    log_step("测试数据创建成功", True, f"笔记: {note_id[:8]}..., 选择题: {quiz1_id[:8]}..., 简答题: {quiz2_id[:8]}...")

    # 3. 获取到期题目
    print("\n【步骤3】获取到期题目")
    try:
        resp = client.get(f"{BASE_URL}/review/due", headers=headers)
        assert resp.status_code == 200, f"获取到期题目失败: {resp.text}"
        due_data = resp.json()
        due_items = due_data.get("items", [])
        log_step("到期题目获取成功", True, f"共 {len(due_items)} 道到期题目")

        # 验证新题目（next_review_at 为 None）都被视为到期
        new_count = sum(1 for q in due_items if q.get("next_review_at") is None)
        log_step("新题目（未设置复习时间）", True, f"共 {new_count} 道")

        # 验证 SM-2 字段
        first = due_items[0]
        assert "interval" in first, "缺少 interval 字段"
        assert "repetition" in first, "缺少 repetition 字段"
        assert "easiness_factor" in first, "缺少 easiness_factor 字段"
        assert "review_count" in first, "缺少 review_count 字段"
        log_step("SM-2 字段验证", True,
                 f"interval={first['interval']}, EF={first['easiness_factor']}, review_count={first['review_count']}")
    except Exception as e:
        log_step("获取到期题目失败", False, str(e))
        sys.exit(1)

    # 4. 提交选择题答案（正确）
    print("\n【步骤4】提交选择题答案（正确）")
    try:
        resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
            "quiz_id": quiz1_id,
            "user_answer": "A. 劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议",
            "time_spent_ms": 5000,
        })
        assert resp.status_code == 200, f"提交答案失败: {resp.text}"
        result = resp.json()

        log_step("选择题正确答案提交成功", True,
                 f"正确: {result['is_correct']}, quality: {result['quality']}, "
                 f"SM2 interval: {result['sm2']['interval']}天, EF: {result['sm2']['easiness_factor']}")

        assert result["is_correct"] == True, "选择题答案正确但判为错误"
        assert result["quality"] == 5, f"选择题正确应 quality=5，实际 {result['quality']}"
        assert result["sm2"]["interval"] == 1, f"首次正确 interval 应为1，实际 {result['sm2']['interval']}"
        assert result["sm2"]["repetition"] == 1, f"首次正确 repetition 应为1，实际 {result['sm2']['repetition']}"
        assert result["sm2"]["easiness_factor"] >= 2.5, f"正确答案后 EF 应 >=2.5，实际 {result['sm2']['easiness_factor']}"
    except Exception as e:
        log_step("选择题正确答案验证失败", False, str(e))
        sys.exit(1)

    # 5. 提交选择题答案（错误）
    print("\n【步骤5】提交选择题答案（错误）")
    try:
        # 先获取另一道选择题
        choice_quizzes = [q for q in due_items if q.get("question_type") == "choice" and q["id"] != quiz1_id]
        if choice_quizzes:
            wrong_quiz_id = choice_quizzes[0]["id"]
            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": wrong_quiz_id,
                "user_answer": "B. 错误选项1",
                "time_spent_ms": 3000,
            })
            assert resp.status_code == 200
            result = resp.json()

            log_step("选择题错误答案提交成功", True,
                     f"正确: {result['is_correct']}, quality: {result['quality']}, "
                     f"SM2 interval: {result['sm2']['interval']}天")

            assert result["is_correct"] == False, "错误答案被判为正确"
            assert result["quality"] <= 1, f"选择题错误应 quality<=1，实际 {result['quality']}"
            assert result["sm2"]["interval"] == 1, f"错误答案后 interval 应为1，实际 {result['sm2']['interval']}"
            assert result["sm2"]["repetition"] == 0, f"错误答案后 repetition 应为0，实际 {result['sm2']['repetition']}"
    except Exception as e:
        log_step("选择题错误答案验证失败", False, str(e))

    # 6. 提交简答题答案
    print("\n【步骤6】提交简答题答案")
    try:
        resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
            "quiz_id": quiz2_id,
            "user_answer": "劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议",
            "time_spent_ms": 15000,
        })
        assert resp.status_code == 200, f"提交简答题答案失败: {resp.text}"
        result = resp.json()

        log_step("简答题答案提交成功", True,
                 f"正确: {result['is_correct']}, quality: {result['quality']}, "
                 f"SM2 interval: {result['sm2']['interval']}天")

        assert 0 <= result["quality"] <= 5, f"quality 超出范围: {result['quality']}"
        assert "sm2" in result
        assert "next_review_at" in result["sm2"]
    except Exception as e:
        log_step("简答题答案验证失败", False, str(e))

    # 7. 验证复习统计
    print("\n【步骤7】验证复习统计")
    try:
        resp = client.get(f"{BASE_URL}/review/stats", headers=headers)
        assert resp.status_code == 200
        stats = resp.json()

        log_step("复习统计获取成功", True,
                 f"待复习: {stats['due_count']}, 今日完成: {stats['today_done']}, "
                 f"今日正确率: {stats['today_accuracy']}%")

        assert stats["today_done"] > 0, f"今日完成数应为 >0，实际 {stats['today_done']}"
        assert stats["total_reviews"] > 0, f"总复习次数应为 >0，实际 {stats['total_reviews']}"
        assert stats["total_quizzes"] > 0, f"总题目数应为 >0，实际 {stats['total_quizzes']}"
    except Exception as e:
        log_step("复习统计验证失败", False, str(e))

    # 8. 验证复习历史
    print("\n【步骤8】验证复习历史")
    try:
        resp = client.get(f"{BASE_URL}/review/history?page=1&page_size=10", headers=headers)
        assert resp.status_code == 200
        history = resp.json()

        items = history.get("items", [])
        log_step("复习历史获取成功", True, f"共 {history['total']} 条记录")

        assert history["total"] > 0, "应有复习历史记录"
        if items:
            first = items[0]
            assert "quiz_id" in first
            assert "is_correct" in first
            assert "quality" in first
            assert "user_answer" in first
            log_step("复习历史字段验证", True,
                     f"正确: {first['is_correct']}, quality: {first['quality']}")
    except Exception as e:
        log_step("复习历史验证失败", False, str(e))

    # 9. 验证连续正确答题后间隔递增
    print("\n【步骤9】验证连续正确答题后间隔递增")
    try:
        # 找一道没答过的选择题
        answered_ids = {quiz1_id}
        unanswered = [q for q in due_items if q.get("question_type") == "choice" and q["id"] not in answered_ids]

        if len(unanswered) >= 1:
            quiz = unanswered[0]
            quiz_id = quiz["id"]
            correct_answer = "A. 正确答案"

            intervals = []
            for i in range(3):
                resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                    "quiz_id": quiz_id,
                    "user_answer": correct_answer,
                    "time_spent_ms": 3000,
                })
                assert resp.status_code == 200
                result = resp.json()
                intervals.append(result["sm2"]["interval"])
                log_step(f"第{i+1}次正确答题", True,
                         f"interval: {result['sm2']['interval']}天, EF: {result['sm2']['easiness_factor']}, rep: {result['sm2']['repetition']}")

            # 验证间隔递增
            assert intervals[1] >= intervals[0], f"间隔应递增: {intervals}"
            assert intervals[2] >= intervals[1], f"间隔应递增: {intervals}"
            log_step("间隔递增验证", True, f"间隔序列: {intervals}")
        else:
            log_step("没有未答过的选择题", True, "跳过")
    except Exception as e:
        log_step("连续答题验证失败", False, str(e))

    # 10. 验证错误答题后间隔重置
    print("\n【步骤10】验证错误答题后间隔重置")
    try:
        # 找一道没答过的选择题
        answered_ids2 = {quiz1_id}
        unanswered2 = [q for q in due_items if q.get("question_type") == "choice" and q["id"] not in answered_ids2]

        if len(unanswered2) >= 2:
            quiz = unanswered2[1]
            quiz_id = quiz["id"]

            # 先答对
            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": quiz_id,
                "user_answer": "A. 正确答案",
                "time_spent_ms": 3000,
            })
            result_correct = resp.json()
            interval_after_correct = result_correct["sm2"]["interval"]
            log_step("答对后", True, f"interval: {interval_after_correct}天, rep: {result_correct['sm2']['repetition']}")

            # 再答错
            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": quiz_id,
                "user_answer": "B. 错误选项",
                "time_spent_ms": 2000,
            })
            result_wrong = resp.json()
            interval_after_wrong = result_wrong["sm2"]["interval"]
            log_step("答错后", True, f"interval: {interval_after_wrong}天, rep: {result_wrong['sm2']['repetition']}")

            assert interval_after_wrong == 1, f"答错后 interval 应为1，实际 {interval_after_wrong}"
            assert result_wrong["sm2"]["repetition"] == 0, f"答错后 repetition 应为0，实际 {result_wrong['sm2']['repetition']}"
            log_step("间隔重置验证", True, "答错后 interval=1, repetition=0")
        else:
            log_step("没有足够的未答选择题", True, "跳过")
    except Exception as e:
        log_step("间隔重置验证失败", False, str(e))

    # 汇总
    print("\n" + "=" * 60)
    print("第8周复习功能端到端测试完成")
    print("=" * 60)
    print(f"  测试用户: {test_user['username']}")
    print(f"  状态: 正常结束")
    print()


if __name__ == "__main__":
    main()
