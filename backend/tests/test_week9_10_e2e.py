"""
第9-10周全流程端到端测试

使用 TEST_PDF_PATH 环境变量指定的真实PDF文件（默认 D:\\test\\resources1\\劳动合同书-田润鑫.pdf） 跑完全流程：
1. 注册/登录
2. 上传PDF文件
3. 轮询转换状态直到完成
4. 触发清洗，轮询直到完成
5. 触发理解管道，轮询直到完成
6. 验证知识卡片和题目生成
7. 获取到期题目并答题
8. 获取今日学习报告，验证数据
9. 获取7天趋势，验证数据
10. 获取薄弱点，验证数据
11. 验证全局异常处理（发送无效请求）
12. 验证API认证校验（无Token请求应返回401）

运行方式：cd backend && python tests/test_week9_10_e2e.py
"""

import json
import os
import sys
import time

import httpx

BASE_URL = "http://localhost:8000/api"
PDF_PATH = os.environ.get("TEST_PDF_PATH", r"D:\engramnote\resource\tests\劳动合同书-田润鑫.pdf")

client = httpx.Client(timeout=120.0)


def log_step(step: str, success: bool = True, detail: str = ""):
    icon = "✓" if success else "✗"
    msg = f"  [{icon}] {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def poll_status(note_id: str, headers: dict, target_status: str, max_wait: int = 600, interval: int = 10, also_accept: list = None):
    """轮询笔记状态直到达到目标状态或超时

    Args:
        also_accept: 除了 target_status 外，也视为成功的状态列表
    """
    accept_set = {target_status}
    if also_accept:
        accept_set.update(also_accept)
    elapsed = 0
    while elapsed < max_wait:
        resp = client.get(f"{BASE_URL}/upload/{note_id}/status", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "")
            if status in accept_set:
                return True, status
            if status in ("failed", "cleaning_failed", "learning_failed"):
                return False, status
            print(f"    ... 当前状态: {status}, 等待 {interval}s (已等待 {elapsed}s)")
        time.sleep(interval)
        elapsed += interval
    return False, "timeout"


def main():
    print("\n" + "=" * 60)
    print("第9-10周全流程端到端测试")
    print(f"测试文件: {PDF_PATH}")
    print("=" * 60)

    # 检查PDF文件是否存在
    if not os.path.exists(PDF_PATH):
        print(f"\n[错误] 测试PDF文件不存在: {PDF_PATH}")
        sys.exit(1)

    # ===== 步骤1: 注册新用户 =====
    print("\n【步骤1】注册新用户")
    ts = int(time.time())
    test_user = {
        "username": f"week9test{ts}",
        "email": f"week9test{ts}@example.com",
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

    # ===== 步骤2: 上传PDF文件 =====
    print("\n【步骤2】上传PDF文件")
    try:
        with open(PDF_PATH, "rb") as f:
            files = {"file": (os.path.basename(PDF_PATH), f, "application/pdf")}
            resp = client.post(f"{BASE_URL}/upload", headers=headers, files=files)

        assert resp.status_code == 201, f"上传失败: {resp.text}"
        note_data = resp.json()
        note_id = note_data["id"]
        log_step("上传成功", True, f"笔记ID: {note_id[:8]}..., 状态: {note_data['status']}")
    except Exception as e:
        log_step("上传失败", False, str(e))
        sys.exit(1)

    # ===== 步骤3: 轮询转换状态 =====
    print("\n【步骤3】等待PDF转换完成")
    # 转换可能自动进入后续阶段（cleaning/cleaned/archived），都视为转换成功
    success, status = poll_status(note_id, headers, "converted", max_wait=600, interval=15,
                                  also_accept=["cleaning", "cleaned", "learning", "archived"])
    if success:
        log_step("转换完成", True, f"状态: {status}")
    else:
        log_step("转换失败", False, f"最终状态: {status}")
        sys.exit(1)

    # ===== 步骤4: 触发清洗 =====
    print("\n【步骤4】触发清洗")
    # 先检查当前状态，可能已经自动清洗完成
    resp = client.get(f"{BASE_URL}/upload/{note_id}/status", headers=headers)
    current_status = resp.json().get("status", "")

    if current_status in ("cleaned", "learning", "archived"):
        log_step("清洗已完成（自动）", True, f"状态: {current_status}")
    else:
        try:
            resp = client.post(f"{BASE_URL}/cleaning/{note_id}/start", headers=headers)
            if resp.status_code in (200, 201):
                log_step("清洗触发成功", True)
            else:
                log_step("清洗触发响应", True, f"状态码: {resp.status_code}")
        except Exception as e:
            log_step("清洗触发失败", False, str(e))

        # 等待清洗完成
        print("  等待清洗完成...")
        success, status = poll_status(note_id, headers, "cleaned", max_wait=600, interval=15,
                                      also_accept=["learning", "archived"])
        if success:
            log_step("清洗完成", True, f"状态: {status}")
        else:
            log_step("清洗失败", False, f"最终状态: {status}")
            sys.exit(1)

    # ===== 步骤5: 触发理解管道 =====
    print("\n【步骤5】触发理解管道")
    # 先检查当前状态
    resp = client.get(f"{BASE_URL}/upload/{note_id}/status", headers=headers)
    current_status = resp.json().get("status", "")

    if current_status == "archived":
        log_step("理解已完成（自动）", True)
    else:
        try:
            resp = client.post(f"{BASE_URL}/understanding/{note_id}/start", headers=headers)
            if resp.status_code in (200, 201):
                log_step("理解管道触发成功", True)
            else:
                log_step("理解管道触发响应", True, f"状态码: {resp.status_code}")
        except Exception as e:
            log_step("理解管道触发失败", False, str(e))

        # 等待理解完成
        print("  等待理解完成（可能需要几分钟）...")
        success, status = poll_status(note_id, headers, "archived", max_wait=900, interval=20)
        if success:
            log_step("理解完成", True, f"状态: {status}")
        else:
            log_step("理解未完成", False, f"最终状态: {status}")
            # 继续测试，即使理解未完全完成

    # ===== 步骤6: 验证知识卡片和题目 =====
    print("\n【步骤6】验证知识卡片和题目")

    # 等待题目生成完成（题目生成是异步的，在理解完成后自动触发）
    print("  等待题目生成...")
    for wait in range(30):
        try:
            resp = client.get(f"{BASE_URL}/understanding/questions?note_id={note_id}&page=1&page_size=100", headers=headers)
            if resp.status_code == 200:
                q_data = resp.json()
                if q_data.get("total", 0) > 0:
                    break
        except:
            pass
        time.sleep(10)

    try:
        # 获取知识卡片
        resp = client.get(f"{BASE_URL}/understanding/cards?note_id={note_id}&page=1&page_size=100", headers=headers)
        assert resp.status_code == 200
        cards_data = resp.json()
        card_count = cards_data.get("total", 0)
        log_step("知识卡片", True, f"共 {card_count} 张")

        # 获取题目
        resp = client.get(f"{BASE_URL}/understanding/questions?note_id={note_id}&page=1&page_size=100", headers=headers)
        assert resp.status_code == 200
        questions_data = resp.json()
        question_count = questions_data.get("total", 0)
        log_step("题目", True, f"共 {question_count} 道")
    except Exception as e:
        log_step("知识卡片/题目验证失败", False, str(e))

    # ===== 步骤7: 获取到期题目并答题 =====
    print("\n【步骤7】获取到期题目并答题")
    try:
        resp = client.get(f"{BASE_URL}/review/due?limit=10", headers=headers)
        assert resp.status_code == 200
        due_data = resp.json()
        due_items = due_data.get("items", [])
        log_step("到期题目", True, f"共 {len(due_items)} 道")

        # 答几道题
        answered = 0
        for quiz in due_items[:5]:
            quiz_id = quiz["id"]
            question_type = quiz.get("question_type", "choice")

            if question_type == "choice":
                # 选择题：尝试答对
                options_raw = quiz.get("options")
                if options_raw:
                    try:
                        options = json.loads(options_raw) if isinstance(options_raw, str) else options_raw
                        user_answer = options[0] if options else "A"
                    except:
                        user_answer = "A"
                else:
                    user_answer = "A"
            elif question_type == "fill_blank":
                user_answer = "测试答案"
            else:
                user_answer = "这是一个测试答案"

            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": quiz_id,
                "user_answer": user_answer,
                "time_spent_ms": 5000,
            })

            if resp.status_code == 200:
                result = resp.json()
                answered += 1
                log_step(
                    f"答题 {answered}",
                    True,
                    f"类型: {question_type}, 正确: {result['is_correct']}, quality: {result['quality']}, "
                    f"interval: {result['sm2']['interval']}天"
                )
            else:
                log_step(f"答题 {answered + 1} 失败", False, resp.text[:100])

        log_step("答题完成", True, f"共答 {answered} 道")
    except Exception as e:
        log_step("答题验证失败", False, str(e))

    # ===== 步骤8: 获取今日学习报告 =====
    print("\n【步骤8】验证今日学习报告")
    try:
        resp = client.get(f"{BASE_URL}/report/daily", headers=headers)
        assert resp.status_code == 200, f"获取报告失败: {resp.text}"
        report = resp.json()

        log_step("今日学习报告", True,
                 f"日期: {report['date']}, 新掌握: {report['new_mastered']}, "
                 f"复习: {report['total_reviews']}, 正确率: {report['today_accuracy']}%, "
                 f"时长: {report['total_review_time_ms']}ms")

        assert "date" in report, "缺少 date 字段"
        assert "new_mastered" in report, "缺少 new_mastered 字段"
        assert "total_reviews" in report, "缺少 total_reviews 字段"
        assert "today_accuracy" in report, "缺少 today_accuracy 字段"
        assert "question_type_accuracy" in report, "缺少 question_type_accuracy 字段"
        assert isinstance(report["question_type_accuracy"], list), "question_type_accuracy 应为列表"
        log_step("报告字段验证", True, "所有字段完整")
    except Exception as e:
        log_step("学习报告验证失败", False, str(e))

    # ===== 步骤9: 获取7天趋势 =====
    print("\n【步骤9】验证7天趋势")
    try:
        resp = client.get(f"{BASE_URL}/report/weekly-trend", headers=headers)
        assert resp.status_code == 200, f"获取趋势失败: {resp.text}"
        trend = resp.json()

        log_step("7天趋势", True,
                 f"共 {len(trend['items'])} 天, 总复习: {trend['total_reviews']}, "
                 f"平均正确率: {trend['avg_accuracy']}%")

        assert "items" in trend, "缺少 items 字段"
        assert "total_reviews" in trend, "缺少 total_reviews 字段"
        assert "avg_accuracy" in trend, "缺少 avg_accuracy 字段"
        assert len(trend["items"]) == 7, f"应有7天数据，实际 {len(trend['items'])} 天"

        # 验证每天的数据结构
        for item in trend["items"]:
            assert "date" in item, "趋势项缺少 date"
            assert "review_count" in item, "趋势项缺少 review_count"
            assert "correct_count" in item, "趋势项缺少 correct_count"
            assert "accuracy" in item, "趋势项缺少 accuracy"

        log_step("趋势数据结构验证", True, "7天数据完整")
    except Exception as e:
        log_step("7天趋势验证失败", False, str(e))

    # ===== 步骤10: 获取薄弱点 =====
    print("\n【步骤10】验证薄弱点")
    try:
        resp = client.get(f"{BASE_URL}/report/weak-points?limit=5", headers=headers)
        assert resp.status_code == 200, f"获取薄弱点失败: {resp.text}"
        weak = resp.json()

        log_step("薄弱点", True, f"共 {weak['total']} 个")

        assert "items" in weak, "缺少 items 字段"
        assert "total" in weak, "缺少 total 字段"

        for item in weak["items"]:
            assert "card_id" in item, "薄弱点缺少 card_id"
            assert "card_title" in item, "薄弱点缺少 card_title"
            assert "error_count" in item, "薄弱点缺少 error_count"
            assert "accuracy" in item, "薄弱点缺少 accuracy"

        log_step("薄弱点数据结构验证", True, "字段完整")
    except Exception as e:
        log_step("薄弱点验证失败", False, str(e))

    # ===== 步骤11: 验证全局异常处理 =====
    print("\n【步骤11】验证全局异常处理")
    try:
        # 发送无效JSON请求
        resp = client.post(
            f"{BASE_URL}/review/submit",
            headers={**headers, "Content-Type": "application/json"},
            content="invalid json{{{",
        )
        # 应该返回422（验证错误）
        assert resp.status_code == 422, f"应返回422，实际 {resp.status_code}"
        data = resp.json()
        # 全局异常中间件应返回统一格式
        log_step("无效JSON请求", True, f"状态码: {resp.status_code}, error_code: {data.get('error_code', 'N/A')}")

        # 请求不存在的资源
        resp = client.get(f"{BASE_URL}/notes/nonexistent-id", headers=headers)
        assert resp.status_code == 404, f"应返回404，实际 {resp.status_code}"
        data = resp.json()
        log_step("不存在的资源", True, f"状态码: 404, detail: {data.get('detail', 'N/A')[:50]}")
    except Exception as e:
        log_step("全局异常处理验证失败", False, str(e))

    # ===== 步骤12: 验证API认证校验 =====
    print("\n【步骤12】验证API认证校验")
    try:
        # 无Token请求应返回401
        no_auth_client = httpx.Client(timeout=30.0)

        # 测试需要认证的端点
        protected_endpoints = [
            ("GET", "/notes"),
            ("GET", "/review/due"),
            ("GET", "/review/stats"),
            ("GET", "/report/daily"),
            ("GET", "/report/weekly-trend"),
            ("GET", "/report/weak-points"),
        ]

        all_401 = True
        for method, path in protected_endpoints:
            resp = no_auth_client.request(method, f"{BASE_URL}{path}")
            if resp.status_code != 401:
                all_401 = False
                log_step(f"认证校验 {path}", False, f"应返回401，实际 {resp.status_code}")

        if all_401:
            log_step("所有受保护端点认证校验", True, f"共 {len(protected_endpoints)} 个端点均返回401")
    except Exception as e:
        log_step("认证校验验证失败", False, str(e))

    # ===== 步骤13: 验证复习统计 =====
    print("\n【步骤13】验证复习统计")
    try:
        resp = client.get(f"{BASE_URL}/review/stats", headers=headers)
        assert resp.status_code == 200
        stats = resp.json()

        log_step("复习统计", True,
                 f"待复习: {stats['due_count']}, 今日完成: {stats['today_done']}, "
                 f"今日正确率: {stats['today_accuracy']}%, 总复习: {stats['total_reviews']}")

        if stats["today_done"] == 0:
            log_step("提示", True, "今日完成数为0（可能没有答题或题目未生成）")
        elif stats["total_reviews"] == 0:
            log_step("提示", True, "总复习次数为0")
    except Exception as e:
        log_step("复习统计验证失败", False, str(e))

    # ===== 步骤14: 验证复习历史 =====
    print("\n【步骤14】验证复习历史")
    try:
        resp = client.get(f"{BASE_URL}/review/history?page=1&page_size=10", headers=headers)
        assert resp.status_code == 200
        history = resp.json()

        log_step("复习历史", True, f"共 {history['total']} 条记录")

        if history["items"]:
            first = history["items"][0]
            assert "quiz_id" in first
            assert "is_correct" in first
            assert "quality" in first
            log_step("历史记录字段验证", True,
                     f"正确: {first['is_correct']}, quality: {first['quality']}")
    except Exception as e:
        log_step("复习历史验证失败", False, str(e))

    # 汇总
    print("\n" + "=" * 60)
    print("第9-10周全流程端到端测试完成")
    print("=" * 60)
    print(f"  测试用户: {test_user['username']}")
    print(f"  测试文件: {PDF_PATH}")
    print(f"  笔记ID: {note_id[:8]}...")
    print(f"  状态: 正常结束")
    print()


if __name__ == "__main__":
    main()
