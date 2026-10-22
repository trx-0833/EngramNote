/**
 * @file 任务进度组件（overhaul-plan 阶段 5.11）
 * @description 取代"正在转换中，请稍候..."这类静态文案：展示**真实进度条 +
 *   阶段名 + 取消按钮**。
 *
 * ## 为什么值得单独做一个组件
 *
 * 后端从阶段 1′ 起就写好了完整的进度契约（`progress` / `stage` / `message` /
 * 心跳 / 取消），Celery 任务里也逐阶段上报（"正在抽取知识点"这种可读阶段名
 * 是专门为展示准备的）。但前端一次都没消费过 —— 于是一整套可观测性
 * 只存在于日志和数据库里，用户看到的仍然是一个转圈。
 *
 * ## 三条刻意的行为
 *
 * 1. **进度是装饰，不是正确性**：接口失败时不报错、不阻塞，退回静态文案。
 *    让"看不到进度"变成"页面打不开"是本末倒置（页面的主数据由别的接口提供）。
 * 2. **终态即停止轮询**：任务结束（成功/失败/取消/僵尸）后不再请求 ——
 *    否则一个开着的页面会永远每 2 秒打一次接口。
 * 3. **取消成功 ≠ 已停止**：文件 broker 无法强杀执行中的任务，
 *    后端如实返回 `terminated=false`，界面也必须如实说
 *    "已请求取消，任务会在下一个阶段边界退出"，而不是显示"已取消"。
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { cancelTask, isTerminal, listNoteTasks, type TaskRun } from '../api/tasks';
import { useTranslation } from '../i18n';

/** 轮询间隔：进度条不需要更实时，2 秒足够让用户看到"在动" */
const POLL_INTERVAL_MS = 2000;

interface Props {
  noteId: string;
  /** 静态兜底文案（接口不可用、或还没有任务记录时显示） */
  fallbackText?: string;
  /** 取消成功后的回调（父组件可据此重新拉取笔记状态） */
  onCancelled?: () => void;
  /**
   * 是否提供"取消任务"按钮（默认 true）
   *
   * 在**已经有产品级停止入口**的面板里（如清洗面板的"停止清洗"，
   * 它的语义是把笔记标记为 cleaning_failed、与"取消 Celery 任务"并不相同）
   * 应当传 false：两个功能不同但看起来一样的按钮，用户无法判断该点哪个。
   */
  allowCancel?: boolean;
}

/**
 * 任务列表查询的 key 前缀（迁移 5.2 / C1）
 *
 * 完整 key 是 `[...NOTE_TASKS_QUERY_KEY, noteId]` —— 前缀单独导出，
 * 这样"失效这一篇笔记的任务"与"失效所有任务"都能表达。
 */
export const NOTE_TASKS_QUERY_KEY = ['noteTasks'] as const;

export default function TaskProgress({
  noteId,
  fallbackText,
  onCancelled,
  allowCancel = true,
}: Props) {
  const { t } = useTranslation();
  // ⚠️ `fallbackText` 的默认值**不能**写在参数解构里
  // （原来写的是 `fallbackText = '处理中，请稍候...'`，改文案时自然想写成
  // `= t('shared.taskProcessing')`）—— 解构默认值在**组件体外**求值，
  // 取不到 hook，语言切换时它也不会变。改成体内 `??` 才是对的。
  const fallback = fallbackText ?? t('shared.taskProcessing');
  const [cancelNotice, setCancelNotice] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  /**
   * 任务进度轮询（迁移 5.2 / C1，2026-10-22）
   *
   * ## 停止条件与原来逐字同义
   *
   * `refetchInterval` 用**函数形式**：返回 `false` 即停止。
   * 原实现是"终态或查不到任务就停"（`setTimeout` 递归里那句
   * `if (latest && !isTerminal(latest.status))`），这里一一对应。
   *
   * ## `retry: false` 是**这一条 query 的覆盖**
   *
   * 原实现 `catch` 后直接退回静态文案、不重试。生产默认是 `retry: 1`，
   * 而这是个 **2 秒一次**的高频轮询 —— 每次失败多打一次请求会让行为明显变吵。
   * 覆盖只作用于这一条，全局默认值没动。
   *
   * ## 卸载后不再 setState
   *
   * 原来靠 `aliveRef` + `timerRef` 手工管理；现在由 query 负责
   * （它不会在卸载后把结果写进已卸载组件的状态）。两个 ref 因此都删掉了。
   */
  const { data, isError: unavailable } = useQuery({
    queryKey: [...NOTE_TASKS_QUERY_KEY, noteId],
    queryFn: ({ signal }) => listNoteTasks(noteId, 5, { signal }),
    retry: false,
    refetchInterval: (query) => {
      // 取最新的一条：列表已按最新在前排序
      const latest = query.state.data?.items[0];
      // 终态或查不到任务：停止轮询（页面主数据由父组件自己的轮询负责）
      if (!latest || isTerminal(latest.status)) return false;
      return POLL_INTERVAL_MS;
    },
  });

  const run: TaskRun | null = data?.items[0] ?? null;

  const handleCancel = async () => {
    if (!run || cancelling) return;
    setCancelling(true);
    try {
      const result = await cancelTask(run.task_id);
      setCancelNotice(
        result.terminated ? t('shared.taskCancelled') : t('shared.taskCancelRequested'),
      );
      onCancelled?.();
    } catch (err) {
      setCancelNotice(
        err instanceof Error
          ? t('misc.taskCancelFailedWithReason', { reason: err.message })
          : t('shared.taskCancelFailed'),
      );
    } finally {
      setCancelling(false);
    }
  };

  // 接口不可用、或还没有任何任务记录 → 静态文案（绝不因此让页面报错）
  if (unavailable || !run) {
    return (
      <p style={{ color: 'var(--color-warning)', margin: 0 }} data-testid="task-progress-fallback">
        {fallback}
      </p>
    );
  }

  const percent = Math.round(Math.max(0, Math.min(1, run.progress)) * 100);
  const terminal = isTerminal(run.status);
  const stageText =
    run.stage || (terminal ? t('shared.taskFinished') : t('shared.taskProcessingShort'));

  return (
    <div data-testid="task-progress" style={{ textAlign: 'left' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ color: 'var(--color-text-secondary)' }} data-testid="task-progress-stage">
          {run.message || stageText}
        </span>
        <span style={{ color: 'var(--color-text-secondary)' }} data-testid="task-progress-percent">
          {percent}%
        </span>
      </div>

      <div
        className="progress-bar"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center', marginTop: 10 }}>
        {!terminal && allowCancel && (
          <button className="btn btn-secondary" onClick={handleCancel} disabled={cancelling}>
            {cancelling ? t('shared.taskCancelling') : t('shared.taskCancel')}
          </button>
        )}
        {run.attempt > 1 && (
          <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.85rem' }}>
            {t('misc.taskAttempt', { attempt: run.attempt, max: run.max_attempts })}
          </span>
        )}
        {terminal && run.status === 'failed' && run.error && (
          <span style={{ color: 'var(--color-danger)', fontSize: '0.85rem' }}>{run.error}</span>
        )}
      </div>

      {cancelNotice && (
        <p
          style={{ color: 'var(--color-text-secondary)', marginTop: 8, fontSize: '0.9rem' }}
          data-testid="task-cancel-notice"
        >
          {cancelNotice}
        </p>
      )}
    </div>
  );
}
