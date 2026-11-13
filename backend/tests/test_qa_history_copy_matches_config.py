"""
文案里的三个数字必须与后端配置一致（docs/qa-history-plan.md §5.4 / §8.7）

## 为什么用一条**后端**用例盯一个**前端**常量

三句面向用户的提示里有三个数字：追问带几轮、历史留几天、备份留几周。
计划要求它们"从配置来，不写死在文案里"，但前端的数字没有任何接口可读
（它们只影响后端行为，不影响任何响应体的形状），所以前端做的是
`frontend/src/constants/qaHistory.ts` 里的**镜像常量**。

镜像就有漂移风险：把 `qa_context_max_turns` 从 6 调到 3，界面上仍写着"最近 6 轮"。
这类错误没有任何运行时症状 —— 用户只会觉得"这软件说话不算数"。
所以用一条用例直接读那个 TS 文件、与 `Settings` 的默认值比对：
改了后端默认值却忘了改前端，CI 立刻红。

（放在后端的原因：`Settings` 只在后端可导入；前端那侧没有 `config.py` 的读权限。）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

FRONTEND_CONSTANTS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "constants" / "qaHistory.ts"


def _read_number(source: str, key: str) -> int:
    match = re.search(rf"{key}:\s*(\d+)", source)
    assert match is not None, f"frontend constants 里找不到 `{key}`（文件结构变了？）"
    return int(match.group(1))


@pytest.mark.skipif(not FRONTEND_CONSTANTS.exists(), reason="前端constants 文件不存在（后端单独检出）")
def test_frontend_limits_match_backend_defaults():
    source = FRONTEND_CONSTANTS.read_text(encoding="utf-8")
    defaults = Settings()

    assert _read_number(source, "contextTurns") == defaults.qa_context_max_turns, (
        "前端 `QA_LIMITS.contextTurns` 与 `settings.qa_context_max_turns` 不一致 —— 界面上「追问会带上最近 N 轮」会写错"
    )
    assert _read_number(source, "retentionDays") == defaults.qa_history_retention_days, (
        "前端 `QA_LIMITS.retentionDays` 与 `settings.qa_history_retention_days` 不一致"
    )
    # 备份份数按"份/天"折算成周：14 份 ≈ 2 周（文案里用的是"周"）
    assert _read_number(source, "backupWeeks") == max(1, round(defaults.backup_keep / 7)), (
        "前端 `QA_LIMITS.backupWeeks` 与 `settings.backup_keep / 7` 不一致"
    )
