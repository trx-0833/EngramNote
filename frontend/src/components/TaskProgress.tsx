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
import { useCallback, useEffect, useRef, useState } from 'react';
import { cancelTask, isTerminal, listNoteTasks, type TaskRun } from '../api/tasks';

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

export default function TaskProgress({
  noteId,
  fallbackText = '处理中，请稍候...',
  onCancelled,
  allowCancel = true,
}: Props) {
  const [run, setRun] = useState<TaskRun | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [cancelNotice, setCancelNotice] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  // 用 ref 保存定时器与"是否已卸载"，避免卸载后 setState
  const timerRef = useRef<number | null>(null);
  const aliveRef = useRef(true);

  const poll = useCallback(async () => {
    try {
      const data = await listNoteTasks(noteId, 5);
      if (!aliveRef.current) return;
      setUnavailable(false);
      // 取最新的一条：列表已按最新在前排序
      const latest = data.items[0] ?? null;
      setRun(latest);
      return latest;
    } catch {
      // 进度是辅助信息：失败就退回静态文案，不向用户报错
      if (aliveRef.current) setUnavailable(true);
      return null;
    }
  }, [noteId]);

  useEffect(() => {
    aliveRef.current = true;
    let stopped = false;

    const tick = async () => {
      const latest = await poll();
      if (stopped) return;
      // 终态或查不到任务：停止轮询（页面主数据由父组件自己的轮询负责）
      if (latest && !isTerminal(latest.status)) {
        timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    void tick();

    return () => {
      stopped = true;
      aliveRef.current = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [poll]);

  const handleCancel = async () => {
    if (!run || cancelling) return;
    setCancelling(true);
    try {
      const result = await cancelTask(run.task_id);
      setCancelNotice(
        result.terminated
          ? '已取消。'
          : '已请求取消：任务会在下一个阶段边界退出（正在进行的这一步不会中断）。',
      );
      onCancelled?.();
    } catch (err) {
      setCancelNotice(err instanceof Error ? `取消失败：${err.message}` : '取消失败，请重试。');
    } finally {
      setCancelling(false);
    }
  };

  // 接口不可用、或还没有任何任务记录 → 静态文案（绝不因此让页面报错）
  if (unavailable || !run) {
    return (
      <p style={{ color: 'var(--color-warning)', margin: 0 }} data-testid="task-progress-fallback">
        {fallbackText}
      </p>
    );
  }

  const percent = Math.round(Math.max(0, Math.min(1, run.progress)) * 100);
  const terminal = isTerminal(run.status);
  const stageText = run.stage || (terminal ? '已结束' : '处理中');

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
            {cancelling ? '正在取消...' : '取消任务'}
          </button>
        )}
        {run.attempt > 1 && (
          <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.85rem' }}>
            第 {run.attempt}/{run.max_attempts} 次尝试
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
