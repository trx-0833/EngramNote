/**
 * @file 问答历史的三个"会出现在文案里的数字"（2026-11-13）
 *
 * ## 为什么单列一个文件
 *
 * 计划 §5.4 要求三句提示里的数字**来自配置而不是写死在文案里**：
 * 调了 `qa_context_max_turns` / 保留期 / 备份份数之后，界面上的 6 轮、90 天、
 * 2 周必须跟着变，否则文案就变成"看起来像事实的旧信息"。
 *
 * 前端的数字来源有一层现实约束：**没有任何接口暴露这三个值**
 * （它们只影响后端行为，不影响任何响应体的形状）。所以这里做**镜像常量**，
 * 并用一条后端用例盯住"这里与 `backend/app/config.py` 的默认值一致"——
 * 改了后端默认值却忘了改这里，CI 会红（`tests/test_qa_history_copy_matches_config.py`）。
 *
 * 之所以不为此新增一个 `/api/qa/config` 端点：为一个纯展示用的数字多一处契约、
 * 多一次请求、多一条"接口挂了文案就空"的失败面，不划算。
 */
export const QA_LIMITS = {
  /** 追问时最多带几轮历史 —— 镜像 `Settings.qa_context_max_turns` */
  contextTurns: 6,
  /** 问答历史保留天数 —— 镜像 `Settings.qa_history_retention_days` */
  retentionDays: 90,
  /** 数据库备份保留周数 —— 镜像 `Settings.backup_keep`（14 份 ≈ 2 周） */
  backupWeeks: 2,
} as const;
