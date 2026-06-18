"""
第8周复习功能集成测试（使用已有数据）

直接使用数据库中已有的笔记和题目数据测试复习功能：
1. 登录已有用户
2. 获取到期题目
3. 提交选择题答案
4. 提交简答题答案
5. 验证 SM-2 参数更新
6. 验证复习统计
7. 验证复习历史
8. 验证连续正确答题后间隔递增
9. 验证错误答题后间隔重置

运行方式：cd backend && python tests/test_week8_review_existing.py
"""

import json
import time
import sys

import httpx

BASE_URL = "http://localhost:8000/api"

client = httpx.Client(timeout=60.0)


def log_step(step: str, success: bool = True, detail: str = ""):
    icon = "✓" if success else "✗"
    msg = f"  [{icon}] {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def main():
    print("\n" + "=" * 60)
    print("第8周复习功能集成测试（使用已有数据）")
    print("=" * 60)

    # 1. 查找已有用户并登录
    print("\n【步骤1】查找已有用户并登录")
    import sqlite3
    conn = sqlite3.connect("data/db/engramnote.db")
    cur = conn.cursor()
    cur.execute("SELECT id, email, username FROM users LIMIT 1")
    user = cur.fetchone()
    conn.close()

    if not user:
        print("  数据库中没有用户，请先运行全流程测试创建用户")
        sys.exit(1)

    user_id, email, username = user
    log_step("找到用户", True, f"用户: {username} ({email})")

    # 尝试登录（密码可能不对，尝试注册新用户）
    token = None
    try:
        resp = client.post(f"{BASE_URL}/auth/login", json={
            "email": email,
            "password": "TestPass123!",
        })
        if resp.status_code == 200:
            token = resp.json()["access_token"]
            log_step("登录成功", True)
    except:
        pass

    if not token:
        # 注册新用户
        new_email = f"review_{int(time.time())}@example.com"
        new_username = f"reviewer{int(time.time())}"
        try:
            resp = client.post(f"{BASE_URL}/auth/register", json={
                "email": new_email,
                "username": new_username,
                "password": "TestPass123!",
            })
            if resp.status_code == 201:
                token = resp.json()["access_token"]
                log_step("注册新用户成功", True, f"用户: {new_username}")
                # 新用户没有题目，需要用已有用户的 ID 来操作
                # 但新用户无法访问已有用户的题目，所以需要用已有用户登录
                print("  注意：新用户没有题目数据，尝试其他方式...")
        except Exception as e:
            log_step("注册失败", False, str(e))

    if not token:
        print("  无法获取 token，退出")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    # 2. 获取到期题目
    print("\n【步骤2】获取到期题目")
    try:
        resp = client.get(f"{BASE_URL}/review/due", headers=headers)
        assert resp.status_code == 200, f"获取到期题目失败: {resp.text}"
        due_data = resp.json()
        due_items = due_data.get("items", [])
        log_step("到期题目获取成功", True, f"共 {len(due_items)} 道到期题目")

        if len(due_items) == 0:
            log_step("没有到期题目", True, "该用户可能没有题目数据")
            # 尝试获取所有题目
            resp2 = client.get(f"{BASE_URL}/understanding/questions?page=1&page_size=5", headers=headers)
            if resp2.status_code == 200:
                all_q = resp2.json().get("items", [])
                log_step("用户总题目数", True, f"{resp2.json().get('total', 0)} 道")
            print("\n  测试结束（无到期题目可测试）")
            return

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

    # 3. 提交选择题答案
    print("\n【步骤3】提交选择题答案")
    choice_questions = [q for q in due_items if q.get("question_type") == "choice"]
    if choice_questions:
        quiz = choice_questions[0]
        quiz_id = quiz["id"]

        # 解析选项
        options = []
        if quiz.get("options"):
            try:
                options = json.loads(quiz["options"]) if isinstance(quiz["options"], str) else quiz["options"]
            except:
                pass

        user_answer = options[0] if options else "A"

        try:
            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": quiz_id,
                "user_answer": user_answer,
                "time_spent_ms": 5000,
            })
            assert resp.status_code == 200, f"提交答案失败: {resp.text}"
            result = resp.json()

            log_step("答案提交成功", True,
                     f"正确: {result['is_correct']}, quality: {result['quality']}, "
                     f"SM2 interval: {result['sm2']['interval']}天, EF: {result['sm2']['easiness_factor']}")

            # 验证 SM-2 参数
            assert "sm2" in result, "缺少 sm2 信息"
            assert "interval" in result["sm2"], "缺少 interval"
            assert "easiness_factor" in result["sm2"], "缺少 easiness_factor"
            assert "next_review_at" in result["sm2"], "缺少 next_review_at"
            assert 0 <= result["quality"] <= 5, f"quality 超出范围: {result['quality']}"

            log_step("SM-2 参数更新验证", True,
                     f"下次复习: {result['sm2']['next_review_at'][:10]}")
        except Exception as e:
            log_step("提交选择题答案失败", False, str(e))
    else:
        log_step("没有选择题可测试", True, "跳过")

    # 4. 提交简答题答案
    print("\n【步骤4】提交简答题答案")
    short_answer_questions = [q for q in due_items if q.get("question_type") == "short_answer"]
    if short_answer_questions:
        quiz = short_answer_questions[0]
        quiz_id = quiz["id"]

        try:
            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": quiz_id,
                "user_answer": "劳动合同是劳动者与用人单位签订的协议",
                "time_spent_ms": 15000,
            })
            assert resp.status_code == 200, f"提交简答题答案失败: {resp.text}"
            result = resp.json()

            log_step("简答题答案提交成功", True,
                     f"正确: {result['is_correct']}, quality: {result['quality']}, "
                     f"SM2 interval: {result['sm2']['interval']}天")
            assert 0 <= result["quality"] <= 5, f"quality 超出范围: {result['quality']}"
        except Exception as e:
            log_step("提交简答题答案失败", False, str(e))
    else:
        log_step("没有简答题可测试", True, "跳过")

    # 5. 验证复习统计
    print("\n【步骤5】验证复习统计")
    try:
        resp = client.get(f"{BASE_URL}/review/stats", headers=headers)
        assert resp.status_code == 200, f"获取统计失败: {resp.text}"
        stats = resp.json()

        log_step("复习统计获取成功", True,
                 f"待复习: {stats['due_count']}, 今日完成: {stats['today_done']}, "
                 f"今日正确率: {stats['today_accuracy']}%")

        assert "due_count" in stats
        assert "today_done" in stats
        assert "today_accuracy" in stats
        assert "total_reviews" in stats
        assert "total_accuracy" in stats
        assert "total_quizzes" in stats

        if stats["today_done"] > 0:
            log_step("今日完成数验证", True, f"today_done = {stats['today_done']}")
        if stats["total_reviews"] > 0:
            log_step("总复习次数验证", True, f"total_reviews = {stats['total_reviews']}")
    except Exception as e:
        log_step("复习统计验证失败", False, str(e))

    # 6. 验证复习历史
    print("\n【步骤6】验证复习历史")
    try:
        resp = client.get(f"{BASE_URL}/review/history?page=1&page_size=10", headers=headers)
        assert resp.status_code == 200, f"获取历史失败: {resp.text}"
        history = resp.json()

        items = history.get("items", [])
        log_step("复习历史获取成功", True, f"共 {history['total']} 条记录")

        if items:
            first = items[0]
            assert "quiz_id" in first, "缺少 quiz_id"
            assert "is_correct" in first, "缺少 is_correct"
            assert "quality" in first, "缺少 quality"
            assert "user_answer" in first, "缺少 user_answer"
            log_step("复习历史字段验证", True,
                     f"正确: {first['is_correct']}, quality: {first['quality']}")
    except Exception as e:
        log_step("复习历史验证失败", False, str(e))

    # 7. 验证连续正确答题后间隔递增
    print("\n【步骤7】验证连续正确答题后间隔递增")
    try:
        used_ids = set()
        if choice_questions:
            used_ids.add(choice_questions[0]["id"])
        remaining_choices = [q for q in due_items
                           if q.get("question_type") == "choice" and q["id"] not in used_ids]

        if remaining_choices:
            quiz = remaining_choices[0]
            quiz_id = quiz["id"]

            # 获取正确答案
            correct_answer = None
            for q in due_items:
                if q["id"] == quiz_id:
                    # 需要从题目列表中获取答案
                    pass

            # 从 understanding API 获取题目详情
            resp = client.get(f"{BASE_URL}/understanding/questions?page=1&page_size=100", headers=headers)
            if resp.status_code == 200:
                all_questions = resp.json().get("items", [])
                for q in all_questions:
                    if q["id"] == quiz_id:
                        correct_answer = q["answer"]
                        break

            if correct_answer:
                intervals = []
                for i in range(3):
                    resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                        "quiz_id": quiz_id,
                        "user_answer": correct_answer,
                        "time_spent_ms": 3000,
                    })
                    assert resp.status_code == 200, f"提交失败: {resp.text}"
                    result = resp.json()
                    intervals.append(result["sm2"]["interval"])
                    log_step(f"第{i+1}次正确答题", True,
                             f"interval: {result['sm2']['interval']}天, EF: {result['sm2']['easiness_factor']}")

                # 验证间隔递增
                if len(intervals) >= 3:
                    assert intervals[1] >= intervals[0], f"间隔应递增: {intervals}"
                    assert intervals[2] >= intervals[1], f"间隔应递增: {intervals}"
                    log_step("间隔递增验证", True, f"间隔序列: {intervals}")
            else:
                log_step("无法获取正确答案", True, "跳过")
        else:
            log_step("没有更多选择题可测试", True, "跳过")
    except Exception as e:
        log_step("连续答题验证失败", False, str(e))

    # 8. 验证错误答题后间隔重置
    print("\n【步骤8】验证错误答题后间隔重置")
    try:
        fill_blank_questions = [q for q in due_items if q.get("question_type") == "fill_blank"]
        if fill_blank_questions:
            quiz = fill_blank_questions[0]
            quiz_id = quiz["id"]

            # 先答对一次
            resp = client.get(f"{BASE_URL}/understanding/questions?page=1&page_size=100", headers=headers)
            correct_answer = None
            if resp.status_code == 200:
                for q in resp.json().get("items", []):
                    if q["id"] == quiz_id:
                        correct_answer = q["answer"]
                        break

            if correct_answer:
                # 答对
                resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                    "quiz_id": quiz_id,
                    "user_answer": correct_answer,
                    "time_spent_ms": 3000,
                })
                result_correct = resp.json()
                interval_after_correct = result_correct["sm2"]["interval"]
                log_step("答对后间隔", True, f"interval: {interval_after_correct}天")

                # 答错
                resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                    "quiz_id": quiz_id,
                    "user_answer": "完全错误的答案XXX",
                    "time_spent_ms": 2000,
                })
                result_wrong = resp.json()
                interval_after_wrong = result_wrong["sm2"]["interval"]
                log_step("答错后间隔", True, f"interval: {interval_after_wrong}天, repetition: {result_wrong['sm2']['repetition']}")

                # 验证间隔重置为1
                assert interval_after_wrong == 1, f"答错后间隔应为1，实际 {interval_after_wrong}"
                assert result_wrong["sm2"]["repetition"] == 0, f"答错后 repetition 应为0，实际 {result_wrong['sm2']['repetition']}"
                log_step("间隔重置验证", True, "答错后 interval=1, repetition=0")
        else:
            log_step("没有填空题可测试", True, "跳过")
    except Exception as e:
        log_step("间隔重置验证失败", False, str(e))

    # 汇总
    print("\n" + "=" * 60)
    print("第8周复习功能集成测试完成")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
