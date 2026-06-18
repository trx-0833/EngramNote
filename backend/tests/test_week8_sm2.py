"""
第8周 SM-2 算法单元测试

验证 SM-2 间隔重复算法的核心逻辑正确性：
1. quality=5 时 EF 增加、interval 递增
2. quality<3 时 repetition 重置、interval 回到1
3. EF 最小值 1.3
4. 边界条件：首次复习、连续正确/错误
5. quality_from_answer 评分映射

运行方式：cd backend && python tests/test_week8_sm2.py
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sm2_service import calculate_sm2, quality_from_answer, SM2Result


def test_sm2_quality_5_increases_interval():
    """quality=5 时 interval 应递增"""
    # 首次复习 quality=5
    result = calculate_sm2(quality=5, interval=1, repetition=0, easiness_factor=2.5)
    assert result.interval == 1, f"首次正确 interval 应为1，实际 {result.interval}"
    assert result.repetition == 1, f"首次正确 repetition 应为1，实际 {result.repetition}"

    # 第二次复习 quality=5
    result2 = calculate_sm2(quality=5, interval=1, repetition=1, easiness_factor=2.5)
    assert result2.interval == 6, f"第二次正确 interval 应为6，实际 {result2.interval}"
    assert result2.repetition == 2

    # 第三次复习 quality=5，interval = 6 * EF
    result3 = calculate_sm2(quality=5, interval=6, repetition=2, easiness_factor=2.6)
    expected = round(6 * 2.6)
    assert result3.interval == expected, f"第三次正确 interval 应为 {expected}，实际 {result3.interval}"
    assert result3.repetition == 3

    print("  [OK] quality=5 时 interval 递增")


def test_sm2_quality_low_resets():
    """quality<3 时 repetition 重置为0，interval 回到1"""
    # 连续正确3次后失败
    result = calculate_sm2(quality=1, interval=15, repetition=3, easiness_factor=2.5)
    assert result.interval == 1, f"回忆失败 interval 应为1，实际 {result.interval}"
    assert result.repetition == 0, f"回忆失败 repetition 应为0，实际 {result.repetition}"

    # quality=2 也应重置
    result2 = calculate_sm2(quality=2, interval=10, repetition=5, easiness_factor=2.3)
    assert result2.interval == 1
    assert result2.repetition == 0

    print("  [OK] quality<3 时 repetition 重置、interval 回到1")


def test_sm2_ef_minimum():
    """EF 最小值应为 1.3"""
    # 极低 quality 多次，EF 不应低于 1.3
    ef = 2.5
    for _ in range(10):
        result = calculate_sm2(quality=0, interval=1, repetition=0, easiness_factor=ef)
        ef = result.easiness_factor
        assert ef >= 1.3, f"EF 不应低于 1.3，实际 {ef}"

    # 直接传入低于 1.3 的 EF
    result = calculate_sm2(quality=3, interval=1, repetition=0, easiness_factor=0.5)
    assert result.easiness_factor >= 1.3, f"传入低 EF 时仍应 >= 1.3，实际 {result.easiness_factor}"

    print("  [OK] EF 最小值 1.3")


def test_sm2_ef_decreases_with_low_quality():
    """低 quality 时 EF 应下降"""
    result = calculate_sm2(quality=0, interval=1, repetition=0, easiness_factor=2.5)
    assert result.easiness_factor < 2.5, f"quality=0 时 EF 应下降，实际 {result.easiness_factor}"

    result2 = calculate_sm2(quality=3, interval=1, repetition=0, easiness_factor=2.5)
    # quality=3: EF' = 2.5 + (0.1 - 2*(0.08+2*0.02)) = 2.5 + (0.1 - 0.24) = 2.5 - 0.14 = 2.36
    assert result2.easiness_factor < 2.5, f"quality=3 时 EF 应略下降，实际 {result2.easiness_factor}"

    print("  [OK] 低 quality 时 EF 下降")


def test_sm2_ef_increases_with_high_quality():
    """高 quality 时 EF 应增加"""
    result = calculate_sm2(quality=5, interval=1, repetition=0, easiness_factor=2.5)
    # quality=5: EF' = 2.5 + (0.1 - 0) = 2.6
    assert result.easiness_factor > 2.5, f"quality=5 时 EF 应增加，实际 {result.easiness_factor}"
    assert result.easiness_factor == 2.6, f"quality=5 时 EF 应为 2.6，实际 {result.easiness_factor}"

    print("  [OK] 高 quality 时 EF 增加")


def test_sm2_next_review_at():
    """next_review_at 应为当前时间 + interval 天"""
    from datetime import datetime, timezone, timedelta

    before = datetime.now(timezone.utc)
    result = calculate_sm2(quality=5, interval=1, repetition=0, easiness_factor=2.5)
    after = datetime.now(timezone.utc)

    # 首次正确，interval=1，next_review_at 应为约1天后
    expected_min = before + timedelta(days=1)
    expected_max = after + timedelta(days=1)
    assert expected_min <= result.next_review_at <= expected_max, \
        f"next_review_at 不在预期范围内: {result.next_review_at}"

    print("  [OK] next_review_at 正确计算")


def test_sm2_consecutive_correct_sequence():
    """连续正确5次的完整序列"""
    interval = 1
    repetition = 0
    ef = 2.5

    expected_intervals = [1, 6]  # 前两次固定

    for i in range(5):
        result = calculate_sm2(quality=5, interval=interval, repetition=repetition, easiness_factor=ef)
        print(f"    第{i+1}次: interval={result.interval}, repetition={result.repetition}, EF={result.easiness_factor}")

        if i < 2:
            assert result.interval == expected_intervals[i], \
                f"第{i+1}次 interval 应为 {expected_intervals[i]}，实际 {result.interval}"

        interval = result.interval
        repetition = result.repetition
        ef = result.easiness_factor

    # 5次连续正确后，interval 应该较大
    assert interval >= 15, f"5次连续正确后 interval 应 >= 15，实际 {interval}"
    assert repetition == 5

    print("  [OK] 连续正确5次序列正确")


def test_sm2_consecutive_failure_sequence():
    """连续失败3次应始终 interval=1"""
    interval = 1
    repetition = 0
    ef = 2.5

    for i in range(3):
        result = calculate_sm2(quality=1, interval=interval, repetition=repetition, easiness_factor=ef)
        assert result.interval == 1, f"第{i+1}次失败后 interval 应为1，实际 {result.interval}"
        assert result.repetition == 0, f"第{i+1}次失败后 repetition 应为0，实际 {result.repetition}"
        interval = result.interval
        repetition = result.repetition
        ef = result.easiness_factor

    print("  [OK] 连续失败3次 interval 始终为1")


def test_quality_choice():
    """选择题评分映射"""
    # 完全匹配
    q = quality_from_answer("choice", "A. 选项1", "A. 选项1")
    assert q == 5, f"选择题完全匹配应为5，实际 {q}"

    # 不匹配
    q2 = quality_from_answer("choice", "B. 选项2", "A. 选项1")
    assert q2 == 1, f"选择题不匹配应为1，实际 {q2}"

    # 大小写不敏感
    q3 = quality_from_answer("choice", "a. test", "A. Test")
    assert q3 == 5, f"选择题大小写不敏感应为5，实际 {q3}"

    print("  [OK] 选择题评分映射")


def test_quality_fill_blank():
    """填空题评分映射"""
    # 完全匹配
    q = quality_from_answer("fill_blank", "机器学习", "机器学习")
    assert q == 5, f"填空题完全匹配应为5，实际 {q}"

    # 部分匹配
    q2 = quality_from_answer("fill_blank", "机器", "机器学习")
    assert q2 >= 3, f"填空题部分匹配应 >=3，实际 {q2}"

    # 不匹配
    q3 = quality_from_answer("fill_blank", "深度学习", "机器学习")
    assert q3 <= 3, f"填空题不匹配应 <=3，实际 {q3}"

    print("  [OK] 填空题评分映射")


def test_quality_short_answer():
    """简答题评分映射"""
    # 高度匹配
    q = quality_from_answer(
        "short_answer",
        "劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议",
        "劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议"
    )
    assert q >= 4, f"简答题高度匹配应 >=4，实际 {q}"

    # 部分匹配（关键词较少）
    q2 = quality_from_answer(
        "short_answer",
        "劳动合同是关于劳动关系的协议",
        "劳动合同是劳动者与用人单位确立劳动关系、明确双方权利和义务的协议"
    )
    assert 1 <= q2 <= 4, f"简答题部分匹配应在1-4之间，实际 {q2}"

    # 完全不匹配
    q3 = quality_from_answer("short_answer", "不知道", "劳动合同是劳动者与用人单位确立劳动关系的协议")
    assert q3 <= 2, f"简答题不匹配应 <=2，实际 {q3}"

    print("  [OK] 简答题评分映射")


def test_quality_empty_answer():
    """空答案应返回0"""
    q = quality_from_answer("choice", "", "A. 选项1")
    assert q == 0, f"空答案应为0，实际 {q}"

    q2 = quality_from_answer("choice", "  ", "A. 选项1")
    assert q2 == 0, f"空白答案应为0，实际 {q2}"

    print("  [OK] 空答案评分为0")


def main():
    print("\n" + "=" * 60)
    print("第8周 SM-2 算法单元测试")
    print("=" * 60)

    tests = [
        test_sm2_quality_5_increases_interval,
        test_sm2_quality_low_resets,
        test_sm2_ef_minimum,
        test_sm2_ef_decreases_with_low_quality,
        test_sm2_ef_increases_with_high_quality,
        test_sm2_next_review_at,
        test_sm2_consecutive_correct_sequence,
        test_sm2_consecutive_failure_sequence,
        test_quality_choice,
        test_quality_fill_blank,
        test_quality_short_answer,
        test_quality_empty_answer,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {test.__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
