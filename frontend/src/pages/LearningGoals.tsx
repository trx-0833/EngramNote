/**
 * @file 学习目标页面
 * @description 学习目标管理页面，支持：
 * 1. 查看当前激活的目标列表（带进度条、剩余天数、归档/删除操作）
 * 2. 查看已归档目标（可折叠）
 * 3. 新建目标（弹窗式表单：名称、类型、目标掌握度、截止日期）
 * 4. 目标归档与删除（带二次确认）
 */
import { useEffect, useState } from 'react'
import {
  getGoals, createGoal, archiveGoal, deleteGoal,
  type LearningGoal,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'

/** 目标类型：每日 / 每周 */
type GoalType = 'daily' | 'weekly'

/** 创建表单数据结构 */
interface CreateFormState {
  name: string
  type: GoalType
  target_mastery: number
  deadline: string
}

/** 创建表单的初始值 */
const INITIAL_FORM: CreateFormState = {
  name: '',
  type: 'daily',
  target_mastery: 80,
  deadline: '',
}

export default function LearningGoals() {
  /** 激活中的学习目标列表 */
  const [goals, setGoals] = useState<LearningGoal[]>([])
  /** 已归档的学习目标列表 */
  const [archivedGoals, setArchivedGoals] = useState<LearningGoal[]>([])
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true)
  /** 错误信息 */
  const [error, setError] = useState('')
  /** 是否显示新建表单 */
  const [showCreateForm, setShowCreateForm] = useState(false)
  /** 新建表单数据 */
  const [createForm, setCreateForm] = useState<CreateFormState>(INITIAL_FORM)
  /** 表单字段错误（用于校验提示） */
  const [formError, setFormError] = useState('')
  /** 提交中状态（防止重复提交） */
  const [submitting, setSubmitting] = useState(false)
  /** 是否展开已归档区域 */
  const [showArchived, setShowArchived] = useState(false)

  /** 拉取激活与归档两组目标数据 */
  async function fetchGoals() {
    setLoading(true)
    setError('')
    try {
      const [activeRes, archivedRes] = await Promise.all([
        getGoals('active').catch(() => null),
        getGoals('archived').catch(() => null),
      ])
      if (activeRes) setGoals(activeRes.goals)
      if (archivedRes) setArchivedGoals(archivedRes.goals)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchGoals()
  }, [])

  /**
   * 提交新建目标表单
   * 校验通过后调用 createGoal，成功后刷新列表并关闭弹窗
   */
  async function handleCreate() {
    setFormError('')
    // 名称校验：1-200 字符
    const name = createForm.name.trim()
    if (!name) {
      setFormError('请输入目标名称')
      return
    }
    if (name.length > 200) {
      setFormError('目标名称不能超过 200 个字符')
      return
    }
    // 掌握度校验：0-100
    if (createForm.target_mastery < 0 || createForm.target_mastery > 100) {
      setFormError('目标掌握度需在 0-100 之间')
      return
    }

    setSubmitting(true)
    try {
      await createGoal({
        name,
        type: createForm.type,
        target_mastery: createForm.target_mastery,
        // 截止日期为空字符串时不传，后端存为 null
        deadline: createForm.deadline || undefined,
      })
      // 重置表单并关闭
      setCreateForm(INITIAL_FORM)
      setShowCreateForm(false)
      // 刷新两组列表
      await fetchGoals()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  /**
   * 归档目标
   * @param goalId - 目标 ID
   * @param e - 鼠标事件，用于阻止冒泡
   */
  async function handleArchive(goalId: string, e: React.MouseEvent) {
    e.stopPropagation()
    try {
      await archiveGoal(goalId)
      await fetchGoals()
    } catch (err) {
      alert(err instanceof Error ? err.message : '归档失败')
    }
  }

  /**
   * 删除目标（带二次确认）
   * @param goalId - 目标 ID
   * @param e - 鼠标事件，用于阻止冒泡
   */
  async function handleDelete(goalId: string, e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirm('确定删除此学习目标？此操作不可恢复。')) return
    try {
      await deleteGoal(goalId)
      await fetchGoals()
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  /** 取消新建，重置表单 */
  function handleCancelCreate() {
    setCreateForm(INITIAL_FORM)
    setFormError('')
    setShowCreateForm(false)
  }

  return (
    <div className="page-enter">
      {/* 页头：标题 + 新建按钮 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-lg)' }}>
        <h1 className="heading-serif gradient-text" style={{ fontSize: '2rem' }}>
          学习目标
        </h1>
        <button className="btn btn-primary" onClick={() => setShowCreateForm(true)}>
          新建目标
        </button>
      </div>

      {error && <ErrorDisplay message={error} onRetry={fetchGoals} />}

      {/* 激活目标列表 */}
      {loading ? (
        <LoadingSpinner />
      ) : (
        <>
          {goals.length === 0 && !error ? (
            <EmptyState
              message="还没有学习目标"
              description="创建你的第一个学习目标，开始追踪学习进度"
              action={<button className="btn btn-primary" onClick={() => setShowCreateForm(true)}>新建目标</button>}
            />
          ) : (
            <section style={{ marginBottom: 'var(--space-xl)' }}>
              <h2 className="heading-serif" style={{ fontSize: '1.25rem', marginBottom: 'var(--space-md)' }}>
                进行中的目标
              </h2>
              <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
                {goals.map((goal) => (
                  <GoalCard
                    key={goal.id}
                    goal={goal}
                    onArchive={(e) => handleArchive(goal.id, e)}
                    onDelete={(e) => handleDelete(goal.id, e)}
                  />
                ))}
              </div>
            </section>
          )}

          {/* 已归档目标（可折叠） */}
          {archivedGoals.length > 0 && (
            <section>
              <button
                className="btn btn-secondary"
                style={{ marginBottom: 'var(--space-md)' }}
                onClick={() => setShowArchived(s => !s)}
                aria-expanded={showArchived}
              >
                {showArchived ? '收起' : '展开'}已归档目标 ({archivedGoals.length})
              </button>
              {showArchived && (
                <div className="card" style={{ padding: 'var(--space-md)' }}>
                  {archivedGoals.map((goal) => (
                    <div
                      key={goal.id}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: 'var(--space-sm) 0',
                        borderBottom: '1px solid var(--color-border)',
                      }}
                    >
                      <div>
                        <span style={{ fontWeight: 500 }}>{goal.name}</span>
                        <span style={{ marginLeft: 'var(--space-sm)', fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                          {goal.type === 'daily' ? '每日' : '每周'} · 目标 {goal.target_mastery}%
                        </span>
                      </div>
                      <button
                        className="btn btn-danger"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => handleDelete(goal.id, e)}
                        aria-label={`删除 ${goal.name}`}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}
        </>
      )}

      {/* 新建目标弹窗 */}
      {showCreateForm && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={handleCancelCreate}
        >
          <div
            className="card"
            style={{ width: '90%', maxWidth: 480, padding: 'var(--space-lg)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 'var(--space-md)' }}>新建学习目标</h3>

            {/* 名称输入 */}
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <label style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}>
                目标名称
              </label>
              <input
                type="text"
                className="input"
                placeholder="例如：掌握第一章核心概念"
                value={createForm.name}
                onChange={(e) => setCreateForm(prev => ({ ...prev, name: e.target.value }))}
                maxLength={200}
                autoFocus
              />
            </div>

            {/* 类型选择 */}
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <label style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}>
                目标类型
              </label>
              <select
                className="input"
                value={createForm.type}
                onChange={(e) => setCreateForm(prev => ({ ...prev, type: e.target.value as GoalType }))}
              >
                <option value="daily">每日目标</option>
                <option value="weekly">每周目标</option>
              </select>
            </div>

            {/* 目标掌握度 */}
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <label style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}>
                目标掌握度 (%)
              </label>
              <input
                type="number"
                className="input"
                min={0}
                max={100}
                value={createForm.target_mastery}
                onChange={(e) => setCreateForm(prev => ({ ...prev, target_mastery: Number(e.target.value) }))}
              />
            </div>

            {/* 截止日期 */}
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <label style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}>
                截止日期（可选）
              </label>
              <input
                type="date"
                className="input"
                value={createForm.deadline}
                onChange={(e) => setCreateForm(prev => ({ ...prev, deadline: e.target.value }))}
              />
            </div>

            {/* 表单错误提示 */}
            {formError && (
              <p style={{ color: 'var(--color-error)', fontSize: '0.875rem', marginBottom: 'var(--space-sm)' }}>
                {formError}
              </p>
            )}

            {/* 操作按钮 */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-sm)' }}>
              <button className="btn btn-secondary" onClick={handleCancelCreate} disabled={submitting}>
                取消
              </button>
              <button className="btn btn-primary" onClick={handleCreate} disabled={submitting}>
                {submitting ? '创建中...' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * 单个目标卡片
 * 展示名称、类型徽章、进度条、目标掌握度、剩余天数与操作按钮
 */
interface GoalCardProps {
  goal: LearningGoal
  onArchive: (e: React.MouseEvent) => void
  onDelete: (e: React.MouseEvent) => void
}

function GoalCard({ goal, onArchive, onDelete }: GoalCardProps) {
  // 进度百分比，后端可能不返回，默认为 0
  const progress = goal.progress_percentage ?? 0
  // 剩余天数
  const daysRemaining = ((): number | null => {
    if (!goal.deadline) return null
    return Math.ceil((new Date(goal.deadline).getTime() - Date.now()) / 86400000)
  })()
  // 关联笔记数（scope_notes 可能为 null）
  const noteCount = goal.scope_notes?.length ?? 0

  return (
    <article className="card card-hover" style={{ padding: 'var(--space-lg)' }}>
      {/* 顶部：名称 + 类型徽章 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-md)' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>{goal.name}</h3>
          <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center', flexWrap: 'wrap' }}>
            {/* 类型徽章：每日=蓝色，每周=紫色 */}
            <span style={{
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: '0.75rem',
              color: '#fff',
              background: goal.type === 'daily' ? 'var(--color-primary)' : '#6d28d9',
            }}>
              {goal.type === 'daily' ? '每日' : '每周'}
            </span>
            {/* 目标掌握度 */}
            <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              目标 {goal.target_mastery}%
            </span>
            {/* 关联笔记数 */}
            <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              关联笔记 {noteCount} 篇
            </span>
            {/* 截止日期 / 剩余天数 */}
            {daysRemaining !== null && (
              <span style={{
                fontSize: '0.8rem',
                color: daysRemaining < 0 ? 'var(--color-error)' : 'var(--color-text-secondary)',
              }}>
                {daysRemaining < 0
                  ? '已过期'
                  : daysRemaining === 0
                    ? '今日截止'
                    : `剩余 ${daysRemaining} 天`}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 进度条 */}
      <div style={{ marginBottom: 'var(--space-md)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-xs)' }}>
          <span>学习进度</span>
          <span>{progress}%</span>
        </div>
        <div className="progress-bar">
          <div
            className="progress-bar-fill"
            style={{ width: `${Math.min(Math.max(progress, 0), 100)}%` }}
          />
        </div>
      </div>

      {/* 操作按钮 */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-sm)' }}>
        <button
          className="btn btn-secondary"
          style={{ fontSize: '0.75rem', padding: '4px 8px' }}
          onClick={onArchive}
          aria-label={`归档 ${goal.name}`}
        >
          归档
        </button>
        <button
          className="btn btn-danger"
          style={{ fontSize: '0.75rem', padding: '4px 8px' }}
          onClick={onDelete}
          aria-label={`删除 ${goal.name}`}
        >
          删除
        </button>
      </div>
    </article>
  )
}
