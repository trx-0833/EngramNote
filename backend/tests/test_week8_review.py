"""
第8周全流程集成测试 — 遗忘曲线与复习调度引擎

使用真实 PDF 文件测试完整链路 + 第8周新功能验证：
1. 注册/登录
2. 上传 PDF
3. 等待转换→清洗→理解完成
4. 获取到期题目
5. 提交答案（选择题、简答题）
6. 验证 SM-2 参数更新
7. 验证复习统计
8. 验证复习历史
9. 验证下次复习时间计算

运行方式：cd backend && conda activate mineru_env && python tests/test_week8_review.py
"""

import json
import time
import sys
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000/api"
PDF_PATH = r"D:\test\resources1\劳动合同书-田润鑫.pdf"

# 测试用户
TEST_USER = {
    "username": f"reviewtest{int(time.time())}",
    "email": f"review_{int(time.time())}@example.com",
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
    print("第8周全流程集成测试 — 遗忘曲线与复习调度引擎")
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

    # 3. 等待转换→清洗→理解完成
    print("\n【步骤3】等待转换完成")
    status, data = wait_for_status(note_id, token, ["converted", "cleaning", "cleaned", "archived"], max_wait=300, step_name="PDF→Markdown转换")
    if status in ("converted", "cleaning", "cleaned", "archived"):
        log_step("转换完成", True, f"状态: {status}")
    else:
        log_step("转换失败", False, f"状态: {status}")
        sys.exit(1)

    if status in ("converted", "cleaning"):
        print("\n【步骤3b】等待清洗完成")
        status, data = wait_for_status(note_id, token, ["cleaned", "archived"], max_wait=600, step_name="AI清洗")
        if status in ("cleaned", "archived"):
            log_step("清洗完成", True, f"状态: {status}")
        else:
            log_step("清洗失败", False, f"状态: {status}")
            sys.exit(1)

    # 触发理解（如果还没理解）
    if status == "cleaned":
        print("\n【步骤3c】触发理解管道")
        try:
            resp = client.post(f"{BASE_URL}/understanding/{note_id}/start", headers=headers)
            assert resp.status_code == 200, f"触发理解失败: {resp.text}"
            log_step("理解管道已触发", True)
        except Exception as e:
            log_step("触发理解失败", False, str(e))
            sys.exit(1)

    # 等待理解完成
    print("\n【步骤3d】等待理解完成")
    status, data = wait_for_status(note_id, token, ["archived", "learning_failed"], max_wait=600, step_name="AI理解")
    if status == "archived":
        log_step("理解完成", True)
    else:
        log_step("理解失败", False, f"状态: {status}, 错误: {data.get('error_message', '')}")
        sys.exit(1)

    # 4. 查看题目
    print("\n【步骤4】查看题目")
    try:
        resp = client.get(f"{BASE_URL}/understanding/{note_id}/questions?page=1&page_size=100", headers=headers)
        assert resp.status_code == 200
        questions_data = resp.json()
        questions = questions_data.get("items", [])
        log_step("题目获取成功", True, f"共 {len(questions)} 道题")

        if len(questions) == 0:
            log_step("没有题目可测试", False)
            sys.exit(0)

        # 打印题目类型分布
        type_dist = {}
        for q in questions:
            t = q.get("question_type", "unknown")
            type_dist[t] = type_dist.get(t, 0) + 1
        log_step("题目类型分布", True, str(type_dist))
    except Exception as e:
        log_step("获取题目失败", False, str(e))
        sys.exit(1)

    # 5. 获取到期题目
    print("\n【步骤5】获取到期题目（第8周新功能）")
    try:
        resp = client.get(f"{BASE_URL}/review/due", headers=headers)
        assert resp.status_code == 200, f"获取到期题目失败: {resp.text}"
        due_data = resp.json()
        due_items = due_data.get("items", [])
        log_step("到期题目获取成功", True, f"共 {len(due_items)} 道到期题目")

        # 验证新题目（next_review_at 为 None）都被视为到期
        new_count = sum(1 for q in due_items if q.get("next_review_at") is None)
        log_step("新题目（未设置复习时间）", True, f"共 {new_count} 道")

        # 验证 SM-2 字段存在
        if due_items:
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

    # 6. 提交选择题答案
    print("\n【步骤6】提交选择题答案（第8周新功能）")
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

        # 选择第一个选项作为答案
        user_answer = options[0] if options else "A"

        try:
            start_time = time.time()
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

            # 验证 SM-2 参数更新
            assert "sm2" in result, "缺少 sm2 信息"
            assert "interval" in result["sm2"], "缺少 interval"
            assert "easiness_factor" in result["sm2"], "缺少 easiness_factor"
            assert "next_review_at" in result["sm2"], "缺少 next_review_at"

            # 验证 quality 在合理范围
            assert 0 <= result["quality"] <= 5, f"quality 超出范围: {result['quality']}"

            log_step("SM-2 参数更新验证", True,
                     f"下次复习: {result['sm2']['next_review_at'][:10]}")
        except Exception as e:
            log_step("提交选择题答案失败", False, str(e))
    else:
        log_step("没有选择题可测试", True, "跳过")

    # 7. 提交简答题答案
    print("\n【步骤7】提交简答题答案（第8周新功能）")
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

            # 验证简答题评分（关键词匹配应给出合理评分）
            assert 0 <= result["quality"] <= 5, f"quality 超出范围: {result['quality']}"
        except Exception as e:
            log_step("提交简答题答案失败", False, str(e))
    else:
        log_step("没有简答题可测试", True, "跳过")

    # 8. 验证复习统计
    print("\n【步骤8】验证复习统计（第8周新功能）")
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

        # 验证今日完成数 > 0（前面已提交了答案）
        assert stats["today_done"] > 0, f"今日完成数应为 >0，实际 {stats['today_done']}"
        log_step("今日完成数验证", True, f"today_done = {stats['today_done']}")

        # 验证总复习次数
        assert stats["total_reviews"] > 0, f"总复习次数应为 >0，实际 {stats['total_reviews']}"
        log_step("总复习次数验证", True, f"total_reviews = {stats['total_reviews']}")
    except Exception as e:
        log_step("复习统计验证失败", False, str(e))

    # 9. 验证复习历史
    print("\n【步骤9】验证复习历史（第8周新功能）")
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

    # 10. 验证答题后 SM-2 参数持久化
    print("\n【步骤10】验证 SM-2 参数持久化")
    try:
        # 获取题目列表，检查 SM-2 字段
        resp = client.get(f"{BASE_URL}/understanding/{note_id}/questions?page=1&page_size=5", headers=headers)
        assert resp.status_code == 200
        questions_check = resp.json().get("items", [])

        reviewed = [q for q in questions_check if q.get("review_count", 0) > 0]
        if reviewed:
            q = reviewed[0]
            log_step("SM-2 持久化验证", True,
                     f"review_count={q.get('review_count')}, "
                     f"interval={q.get('interval')}, EF={q.get('easiness_factor')}")
            assert q.get("review_count", 0) > 0, "review_count 应 > 0"
            assert q.get("interval", 0) >= 1, "interval 应 >= 1"
        else:
            log_step("未找到已复习的题目", True, "跳过持久化验证")
    except Exception as e:
        log_step("SM-2 持久化验证失败", False, str(e))

    # 11. 验证连续答题后间隔变化
    print("\n【步骤11】验证连续正确答题后间隔递增")
    try:
        # 找一道选择题连续答对3次
        used_ids = set()
        if choice_questions:
            used_ids.add(choice_questions[0]["id"])
        remaining_choices = [q for q in due_items
                           if q.get("question_type") == "choice" and q["id"] not in used_ids]

        if remaining_choices:
            quiz = remaining_choices[0]
            quiz_id = quiz["id"]

            # 解析正确答案
            options = []
            if quiz.get("options"):
                try:
                    options = json.loads(quiz["options"]) if isinstance(quiz["options"], str) else quiz["options"]
                except:
                    pass

            # 获取正确答案（从题目列表中查找）
            correct_answer = None
            for q in questions:
                if q["id"] == quiz_id:
                    correct_answer = q["answer"]
                    break

            if correct_answer:
                # 连续答对3次
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
                             f"interval: {result['sm2']['interval']}天, EF: {result['sm2']['easiness_factor']}")

                # 验证间隔递增
                if len(intervals) >= 3:
                    assert intervals[1] >= intervals[0], f"间隔应递增: {intervals}"
                    assert intervals[2] >= intervals[1], f"间隔应递增: {intervals}"
                    log_step("间隔递增验证", True, f"间隔序列: {intervals}")
        else:
            log_step("没有更多选择题可测试", True, "跳过")
    except Exception as e:
        log_step("连续答题验证失败", False, str(e))

    # 汇总
    print("\n" + "=" * 60)
    print("第8周全流程集成测试完成")
    print("=" * 60)
    print(f"  测试用户: {TEST_USER['username']}")
    print(f"  笔记: {note_id}")
    print(f"  状态: 正常结束")
    print()


if __name__ == "__main__":
    main()
