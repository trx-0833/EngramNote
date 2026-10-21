"""业务日日期串的口径守卫（2026-10-21）

## 这个守卫针对什么

真实用户走查在 `/today` 页面上看到 **「今日报告 (2026-10-20)」，而当天是 10-21**。
根因不是"时区没处理"，而是**处理了一半**：

    today_start = today_start_utc(now)            # 返回 **UTC 时刻**（正确）
    today_str   = today_start.strftime("%Y-%m-%d")  # 却按 UTC 取日期（错）

北京 2026-10-21 00:00 == UTC 2026-10-20 16:00，所以日期串必然是"昨天"。
东八区**每天都触发**，不是偶发。

同一个文件里的周趋势更严重：建键用 `local_day_start_utc(day).strftime(...)`
（业务日起点所在的 UTC 日），查找用 `(now - timedelta(days=i)).strftime(...)`
（UTC 日）—— 两套口径永不相等，`daily_map.get()` 恒 miss，**七根柱子全是 0**。

## 守卫策略

纯函数断言 + **源码级反模式检测**。后者是关键：只要有人再写
`local_day_start_utc(...).strftime(...)` 或 `today_start_utc(...).strftime(...)`，
就会红 —— 这两个写法看起来都"很时区正确"，靠 review 是拦不住的。
"""

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.utils.timeutil import (
    BUSINESS_TZ,
    business_date_str,
    local_day_start_utc,
    recent_business_day_ranges,
    today_start_utc,
)

BACKEND = Path(__file__).resolve().parents[1]

#: 走查当天的真实时刻：北京 2026-10-21 17:31 == UTC 09:31
NOW = datetime(2026, 10, 21, 9, 31, tzinfo=timezone.utc)


class TestBusinessDateStr:
    def test_reproduces_the_original_bug(self):
        """把当时的错误写法钉在这里：它就是差一天"""
        wrong = local_day_start_utc(NOW).strftime("%Y-%m-%d")
        right = business_date_str(NOW)
        assert wrong == "2026-10-20", "旧写法的结果变了？说明前提假设需要重看"
        assert right == "2026-10-21"
        assert wrong != right, "旧写法与新写法必须不同 —— 相同就说明这个 bug 不存在"

    def test_utc_evening_belongs_to_next_business_day(self):
        """UTC 16:00 之后已经是北京的第二天"""
        assert business_date_str(datetime(2026, 10, 20, 15, 59, tzinfo=timezone.utc)) == "2026-10-20"
        assert business_date_str(datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)) == "2026-10-21"

    def test_naive_datetime_is_treated_as_utc(self):
        """SQLite 取出来的历史行是 naive 的，按 UTC 解释（与 timeutil._as_utc 一致）"""
        assert business_date_str(datetime(2026, 10, 21, 9, 31)) == "2026-10-21"

    def test_matches_the_business_timezone_directly(self):
        assert business_date_str(NOW) == NOW.astimezone(BUSINESS_TZ).strftime("%Y-%m-%d")


class TestRecentBusinessDayRanges:
    def test_returns_requested_count_ending_today(self):
        days = recent_business_day_ranges(7, NOW)
        assert len(days) == 7
        assert days[-1][0] == "2026-10-21", "最后一格必须是今天"
        assert days[0][0] == "2026-10-15"

    def test_dates_are_consecutive_with_no_gap_or_duplicate(self):
        """在业务时区里逐日回退 —— 不能重复也不能跳过"""
        labels = [d[0] for d in recent_business_day_ranges(14, NOW)]
        assert len(set(labels)) == 14, f"日期串有重复：{labels}"
        parsed = [datetime.strptime(x, "%Y-%m-%d") for x in labels]
        for a, b in zip(parsed, parsed[1:], strict=False):
            assert (b - a).days == 1, f"日期不连续：{a} → {b}"

    def test_each_range_is_exactly_24h_and_starts_at_business_midnight(self):
        for label, start, end in recent_business_day_ranges(7, NOW):
            assert end - start == timedelta(days=1)
            local = start.astimezone(BUSINESS_TZ)
            assert (local.hour, local.minute, local.second) == (0, 0, 0)
            assert local.strftime("%Y-%m-%d") == label, (
                f"区间起点 {local} 与标签 {label} 不是同一个业务日"
            )

    def test_key_and_range_come_from_the_same_source(self):
        """结构性命中：用 ranges 建键、用 ranges 查找，必须全部命中

        这正是周趋势原先失败的地方 —— 建键与查找出自两套口径。
        """
        days = recent_business_day_ranges(7, NOW)
        built = {label: {"total": i + 1, "correct": i + 1} for i, (label, _s, _e) in enumerate(days)}
        looked_up = [built.get(label, {"total": 0, "correct": 0}) for label, _s, _e in days]
        assert all(v["total"] > 0 for v in looked_up), "有格子查不到 —— 口径又打架了"

    def test_boundary_near_business_midnight(self):
        """北京 00:30 时，"今天"是新的一天，且区间不重复"""
        edge = datetime(2026, 10, 20, 16, 30, tzinfo=timezone.utc)  # 北京 10-21 00:30
        days = recent_business_day_ranges(3, edge)
        assert [d[0] for d in days] == ["2026-10-19", "2026-10-20", "2026-10-21"]


class TestNoHalfConvertedTimezonePattern:
    """源码级反模式：`<某 UTC 时刻>.strftime("%Y-%m-%d")`

    这两个函数**都返回 UTC 时刻**，名字里又带 today/local_day，
    看起来完全像"业务日的零点"，于是很容易顺手 strftime —— 必须机器拦。
    """

    ANTI_PATTERNS = (
        re.compile(r"today_start_utc\s*\([^)]*\)\s*\.strftime\s*\("),
        re.compile(r"local_day_start_utc\s*\([^)]*\)\s*\.strftime\s*\("),
    )

    def test_report_service_has_no_half_converted_date_labels(self):
        src = (BACKEND / "app" / "services" / "report_service.py").read_text(encoding="utf-8")
        # 注释里会引用这些写法解释历史，所以先剥掉注释再判
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
        code_only = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("#")
        )
        offenders = [p.pattern for p in self.ANTI_PATTERNS if p.search(code_only)]
        assert not offenders, (
            "report_service.py 又出现「UTC 时刻直接 strftime」的写法：\n  "
            + "\n  ".join(offenders)
            + "\n请改用 timeutil.business_date_str() / recent_business_day_ranges()。"
        )

    def test_no_module_derives_a_business_date_by_formatting_a_utc_instant(self):
        """全后端扫一遍：这两个函数的结果不得直接 strftime 成日期"""
        offenders = []
        for path in (BACKEND / "app").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            code_only = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("#")
            )
            for pattern in self.ANTI_PATTERNS:
                if pattern.search(code_only):
                    offenders.append(f"{path.relative_to(BACKEND)}")
        assert not offenders, (
            "这些文件把 UTC 时刻直接格式化成了日期串：\n  "
            + "\n  ".join(sorted(set(offenders)))
            + "\n正确做法：business_date_str(dt)。"
        )

    def test_today_start_utc_is_still_the_right_tool_for_ranges(self):
        """反模式的另一半：它**该**被用于比较区间（不是日期串）"""
        start = today_start_utc(NOW)
        assert start.tzinfo is not None
        assert start.astimezone(BUSINESS_TZ).strftime("%H:%M:%S") == "00:00:00"
        assert start < NOW, "今日起点必须早于当前时刻"
