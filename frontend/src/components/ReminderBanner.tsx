/**
 * @file 复习提醒横幅组件
 * @description 自包含的提醒组件，负责：
 * 1. 检测浏览器通知权限，未开启时展示开启按钮
 * 2. 权限开启后每 10 分钟轮询 getReminders() 获取最新待复习数
 * 3. 当有待复习题目时展示横幅，支持跳转至复习页
 * 4. 在非静默时段通过桌面通知提醒用户
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getReminders, type ReminderResponse } from '../api/client'
import {
  requestNotificationPermission,
  showNotification,
  isInQuietHours,
} from '../utils/notifications'

/** 轮询间隔：10 分钟 */
const POLL_INTERVAL_MS = 600000

export default function ReminderBanner() {
  const navigate = useNavigate()
  /** 当前通知权限状态 */
  const [permission, setPermission] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied'
  )
  /** 最新提醒数据 */
  const [reminders, setReminders] = useState<ReminderResponse | null>(null)
  /** 是否正在轮询 */
  const [polling, setPolling] = useState(false)
  /** 用户是否手动关闭了开启提示横幅 */
  const [dismissed, setDismissed] = useState(false)
  /** 已通知过的 due_count 快照，避免重复弹通知 */
  const lastNotifiedDueRef = useRef<number>(0)
  /** 轮询定时器引用 */
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  /**
   * 拉取一次提醒数据
   * 取得数据后判断是否需要弹桌面通知
   */
  async function pollOnce() {
    try {
      const res = await getReminders()
      setReminders(res)
      // F-25 修复：仅按 due_count 值变化去重。
      // 旧实现依赖 getNotifiedQuizIds().length===0，markNotified 写入后 length 恒非 0，
      // 导致同一会话内 due_count 从 5→8 等变化不再二次通知（整个会话只弹一次）。
      if (res.due_count > 0 && res.due_count !== lastNotifiedDueRef.current) {
        // 静默时段不弹通知
        if (!isInQuietHours()) {
          showNotification('复习提醒', `你有 ${res.due_count} 个题目待复习`)
          // 记录本次已通知的 due_count，值变化才再次通知
          lastNotifiedDueRef.current = res.due_count
        }
      } else if (res.due_count === 0) {
        // 待复习清零后重置快照，下次到来时再次通知
        lastNotifiedDueRef.current = 0
      }
    } catch {
      // 静默失败，不打扰用户
    }
  }

  /** 启动轮询：立即拉一次 + 定时拉取 */
  function startPolling() {
    if (polling) return
    setPolling(true)
    pollOnce()
    timerRef.current = setInterval(pollOnce, POLL_INTERVAL_MS)
  }

  /** 停止轮询并清理定时器 */
  function stopPolling() {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    setPolling(false)
  }

  // 组件挂载时根据权限状态决定是否启动轮询
  useEffect(() => {
    if (permission === 'granted') {
      startPolling()
    }
    return () => stopPolling()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permission])

  /**
   * 处理"开启"按钮点击
   * 请求权限，成功则启动轮询并发送成功通知
   */
  async function handleEnable() {
    const granted = await requestNotificationPermission()
    if (granted) {
      setPermission('granted')
      // 立即弹出成功通知
      showNotification('复习提醒已开启', '将在有待复习题目时提醒你')
    } else {
      setPermission('denied')
      alert('通知权限已被拒绝，请在浏览器设置中开启')
    }
  }

  /** 跳转至复习页 */
  function goToReview() {
    navigate('/review')
  }

  // 已被用户关闭且未授权：不渲染
  if (dismissed && permission !== 'granted') return null

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
            开启桌面通知，及时获取复习提醒
          </p>
          <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
            基于 FSRS 算法的间隔重复，不错过任何最佳复习时机
          </p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <button className="btn btn-primary" onClick={handleEnable}>
            开启
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => setDismissed(true)}
            aria-label="关闭提醒"
          >
            稍后
          </button>
        </div>
      </div>
    )
  }

  // 权限已授予但尚无数据：不展示
  if (!reminders) return null

  // 全部为 0：不展示横幅
  if (reminders.due_count === 0 && reminders.due_in_1h_count === 0 && reminders.weak_point_count === 0) {
    return null
  }

  // 构造文案
  const messages: string[] = []
  if (reminders.due_count > 0) {
    messages.push(`你有 ${reminders.due_count} 个题目待复习`)
  }
  if (reminders.due_in_1h_count > 0) {
    messages.push(`其中 ${reminders.due_in_1h_count} 个将在 1 小时内到期`)
  }
  if (reminders.weak_point_count > 0) {
    messages.push(`薄弱知识点: ${reminders.weak_point_count} 个`)
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
          去复习
        </button>
      )}
    </div>
  )
}
