/**
 * @file 复习提醒横幅组件
 * @description 自包含的提醒组件，负责：
 * 1. 检测浏览器通知权限，未开启时展示开启按钮
 * 2. 权限开启后每 10 分钟轮询 getReminders() 获取最新待复习数
 * 3. 当有待复习题目时展示横幅，支持跳转至复习页
 * 4. 在非静默时段通过桌面通知提醒用户
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { getReminders } from '../api/client';
import { useToast } from '../components/Toast';
import {
  requestNotificationPermission,
  showNotification,
  isInQuietHours,
} from '../utils/notifications';
import { useTranslation } from '../i18n';

/** 轮询间隔：10 分钟 */
const POLL_INTERVAL_MS = 600000;

/**
 * 提醒数据查询的 key（迁移 5.2 / C1）
 *
 * 这个 key 没有参数维度（提醒是按用户聚合的），所以直接用常量而不是数组拼接。
 */
export const REMINDERS_QUERY_KEY = ['reminders'] as const;

export default function ReminderBanner() {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  /** 当前通知权限状态 */
  const [permission, setPermission] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied',
  );
  /** 用户是否手动关闭了开启提示横幅 */
  const [dismissed, setDismissed] = useState(false);
  /** 已通知过的 due_count 快照，避免重复弹通知 */
  const lastNotifiedDueRef = useRef<number>(0);

  /**
   * 提醒数据轮询（迁移 5.2 / C1，2026-10-22）
   *
   * ## 三个被删掉的东西
   *
   * - `pollOnce()`：取数归 queryFn、弹通知归下面的 effect；
   * - `timerRef`：query 自己管定时器；
   * - **`polling` 状态**：实测 grep 的 4 处命中**全在 `startPolling`/`stopPolling`
   *   内部**，外部没有任何消费者 —— 它一直是个没人读的死状态，顺手删掉。
   *
   * ## `retry: false` 是这一条 query 的覆盖
   *
   * 原实现 `catch {}` 静默失败、**不重试**。生产默认 `retry: 1` 会让这个
   * 高频轮询每次失败多打一次请求。覆盖只作用于这一条。
   */
  const { data: reminders = null } = useQuery({
    queryKey: REMINDERS_QUERY_KEY,
    queryFn: ({ signal }) => getReminders({ signal }),
    // 只在已授权时轮询（原来那个 `if (permission === 'granted') startPolling()`）
    enabled: permission === 'granted',
    refetchInterval: POLL_INTERVAL_MS,
    retry: false,
  });

  /**
   * 取得数据后判断是否需要弹桌面通知
   *
   * ## 为什么这段是 effect 而不是 queryFn 的一部分
   *
   * 它是**对新数据做出的副作用**（弹系统通知），不是"派生状态" ——
   * 放在 effect 里是正确位置，`set-state-in-effect` 那条规则针对的是
   * "在 effect 里同步 setState 来镜像 props/state"，与这里无关。
   *
   * 逻辑与原来的 `pollOnce` **逐字相同**：
   * 仅按 `due_count` 值变化去重（`docs/decisions.md#F-25`）、
   * 静默时段不弹（`isInQuietHours()`）、清零后重置快照。
   */
  useEffect(() => {
    if (!reminders) return;
    if (reminders.due_count > 0 && reminders.due_count !== lastNotifiedDueRef.current) {
      // 静默时段不弹通知
      if (!isInQuietHours()) {
        showNotification(
          t('reminder.title'),
          t('reminder.dueCount', { count: reminders.due_count }),
        );
        // 记录本次已通知的 due_count，值变化才再次通知
        lastNotifiedDueRef.current = reminders.due_count;
      }
    } else if (reminders.due_count === 0) {
      // 待复习清零后重置快照，下次到来时再次通知
      lastNotifiedDueRef.current = 0;
    }
  }, [reminders, t]);

  /**
   * 处理t('reminder.enable')按钮点击
   * 请求权限，成功则启动轮询并发送成功通知
   */
  async function handleEnable() {
    const granted = await requestNotificationPermission();
    if (granted) {
      setPermission('granted');
      // 立即弹出成功通知
      showNotification(t('reminder.enabledTitle'), t('reminder.enabledBody'));
    } else {
      setPermission('denied');
      toast.error(t('reminder.permissionDenied'));
    }
  }

  /** 跳转至复习页 */
  function goToReview() {
    navigate('/review');
  }

  // 已被用户关闭且未授权：不渲染
  if (dismissed && permission !== 'granted') return null;

  // 权限未授予：展示开启横幅
  if (permission !== 'granted') {
    return (
      <div
        className="card card-accent-gold"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--space-md)',
          marginBottom: 'var(--space-lg)',
          padding: 'var(--space-md) var(--space-lg)',
        }}
      >
        <div style={{ flex: 1 }}>
          <p style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
            {t('reminder.desktopHint')}
          </p>
          <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
            {t('reminder.algorithmHint')}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <button className="btn btn-primary" onClick={handleEnable}>
            {t('reminder.enable')}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => setDismissed(true)}
            aria-label={t('reminder.dismissLabel')}
          >
            {t('reminder.later')}
          </button>
        </div>
      </div>
    );
  }

  // 权限已授予但尚无数据：不展示
  if (!reminders) return null;

  // 全部为 0：不展示横幅
  if (
    reminders.due_count === 0 &&
    reminders.due_in_1h_count === 0 &&
    reminders.weak_point_count === 0
  ) {
    return null;
  }

  // 构造文案
  const messages: string[] = [];
  if (reminders.due_count > 0) {
    messages.push(t('reminder.dueCount', { count: reminders.due_count }));
  }
  if (reminders.due_in_1h_count > 0) {
    messages.push(t('reminder.dueInOneHour', { count: reminders.due_in_1h_count }));
  }
  if (reminders.weak_point_count > 0) {
    messages.push(t('reminder.weakPoints', { count: reminders.weak_point_count }));
  }

  return (
    <div
      className="card card-accent-left"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 'var(--space-md)',
        marginBottom: 'var(--space-lg)',
        padding: 'var(--space-md) var(--space-lg)',
      }}
    >
      <div style={{ flex: 1 }}>
        {messages.map((msg, i) => (
          <p
            key={i}
            style={{
              fontWeight: i === 0 ? 600 : 400,
              fontSize: i === 0 ? '1rem' : '0.85rem',
              color: i === 0 ? 'var(--color-text)' : 'var(--color-text-secondary)',
              marginBottom: i < messages.length - 1 ? 'var(--space-xs)' : 0,
            }}
          >
            {msg}
          </p>
        ))}
      </div>
      {reminders.due_count > 0 && (
        <button className="btn btn-primary" onClick={goToReview}>
          {t('reminder.goReview')}
        </button>
      )}
    </div>
  );
}
