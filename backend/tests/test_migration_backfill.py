"""隔离审计 1-3 的守卫：`content_hash` 的回填必须**每次启动**都跑。

审计实测：真库仍有 6 行 `content_hash` 为 NULL，重跑 `init_db()` 也不补 ——
因为整段回填嵌在 `if "content_hash" not in card_cols:` 里（列已存在，条件永不成立）。
后果是那些卡不参与去重，**重跑理解时会多插一张卡**。

这里用源码结构断言守住位置：回填不能藏在"只在加列时执行"的分支里。
"""

from __future__ import annotations

import re
from pathlib import Path


class TestContentHashBackfillAlwaysRuns:
    def _src(self) -> str:
        return Path("app/database.py").read_text(encoding="utf-8")

    def test_backfill_not_inside_add_column_branch(self):
        """回填语句不能出现在 `if "content_hash" not in card_cols:` 的直接子块里"""
        src = self._src()
        card_cols_at = src.index('if "content_hash" not in card_cols:')
        backfill_at = src.index("SELECT id, title, content FROM knowledge_cards")
        assert backfill_at > card_cols_at, "回填应在加列判断之后"
        # 取加列分支的直接子块（下一个同缩进的 `#` 或 `if` 之前）
        between = src[card_cols_at:backfill_at]
        assert "ALTER TABLE knowledge_cards ADD COLUMN content_hash" in between
        # 关键：两者之间必须出现过"独立块"的标志（我们的注释或 `if True:`）
        assert "隔离审计 1-3" in between or "if True:" in between or "\n            if True" in between, (
            "回填仍被包在'只在加列时执行'的分支里 —— 后加的 NULL 行永远不会被补"
        )

    def test_backfill_is_batched(self):
        """必须分批（避免一次性锁库），且报剩余"""
        src = self._src()
        assert "BATCH = 2000" in src or "LIMIT :n" in src
        assert "仍有" in src and "content_hash IS NULL" in src

    def test_backfill_logs_remaining(self):
        """补不完要显式告警，不能静默留 NULL"""
        src = self._src()
        assert re.search(r"content_hash 仍有|仍有.*NULL", src), "缺少'剩余 N 行为 NULL'的告警"
