/**
 * @file 学习目标页面
 * @description 学习目标管理页面，支持：
 * 1. 查看当前激活的目标列表（带进度条、剩余天数、归档/删除操作）
 * 2. 查看已归档目标（可折叠）
 * 3. 新建目标（弹窗式表单：名称、类型、目标掌握度、截止日期）
 * 4. 目标归档与删除（带二次确认）
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { getGoals, createGoal, archiveGoal, deleteGoal, type LearningGoal } from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import Icon from '../components/Icon';
// 页面标题（visual-refactor-plan 批次 C1）：本页原先内联写 2rem，
// 统一进组件后是 1.5rem；页头那一行（标题 + 按钮、窄屏换行）也由它承担
import PageHeader from '../components/PageHeader';
import ConfirmDialog from '../components/ConfirmDialog';
// 对话框基座（visual-refactor-plan 批次 D2）：新建目标弹窗的遮罩 / 面板 / Esc 由它渲染
import Dialog from '../components/Dialog';
import { useToast } from '../components/Toast';

/** 目标类型：每日 / 每周 */
type GoalType = 'daily' | 'weekly';

/** 创建表单数据结构 */
interface CreateFormState {
  name: string;
  type: GoalType;
  target_mastery: number;
  deadline: string;
}

/** 创建表单的初始值 */
const INITIAL_FORM: CreateFormState = {
  name: '',
  type: 'daily',
  target_mastery: 80,
  deadline: '',
};

export default function LearningGoals() {
  const toast = useToast();
  /** 激活中的学习目标列表 */
  const [goals, setGoals] = useState<LearningGoal[]>([]);
  /** 已归档的学习目标列表 */
  const [archivedGoals, setArchivedGoals] = useState<LearningGoal[]>([]);
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true);
  /** 错误信息 */
  const [error, setError] = useState('');
  /** 是否显示新建表单 */
  const [showCreateForm, setShowCreateForm] = useState(false);
  /** 新建表单数据 */
  const [createForm, setCreateForm] = useState<CreateFormState>(INITIAL_FORM);
  /** 表单字段错误（用于校验提示） */
  const [formError, setFormError] = useState('');
  /** 提交中状态（防止重复提交） */
  const [submitting, setSubmitting] = useState(false);
  /** 是否展开已归档区域 */
  const [showArchived, setShowArchived] = useState(false);
  /**
   * 待删除的目标 ID（`null` = 删除确认框关着）。
   *
   * 批次 D3：原来是同步的 `confirm('确定删除此学习目标？此操作不可恢复。')`。
   */
  const [pendingDeleteGoalId, setPendingDeleteGoalId] = useState<string | null>(null);

  /** 拉取激活与归档两组目标数据 */
  async function fetchGoals() {
    setLoading(true);
    setError('');
    try {
      const [activeRes, archivedRes] = await Promise.all([
        getGoals('active').catch(() => null),
        getGoals('archived').catch(() => null),
      ]);
      if (activeRes) setGoals(activeRes.goals);
      if (archivedRes) setArchivedGoals(archivedRes.goals);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchGoals();
  }, []);

  /**
   * 提交新建目标表单
   * 校验通过后调用 createGoal，成功后刷新列表并关闭弹窗
   */
  async function handleCreate() {
    setFormError('');
    // 名称校验：1-200 字符
    const name = createForm.name.trim();
    if (!name) {
      setFormError('请输入目标名称');
      return;
    }
    if (name.length > 200) {
      setFormError('目标名称不能超过 200 个字符');
      return;
    }
    // 掌握度校验：0-100
    if (createForm.target_mastery < 0 || createForm.target_mastery > 100) {
      setFormError('目标掌握度需在 0-100 之间');
      return;
    }

    setSubmitting(true);
    try {
      await createGoal({
        name,
        type: createForm.type,
        target_mastery: createForm.target_mastery,
        // 截止日期为空字符串时不传，后端存为 null
        deadline: createForm.deadline || undefined,
      });
      // 重置表单并关闭
      setCreateForm(INITIAL_FORM);
      setShowCreateForm(false);
      // 刷新两组列表
      await fetchGoals();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  }

  /**
   * 归档目标
   * @param goalId - 目标 ID
   * @param e - 鼠标事件，用于阻止冒泡
   */
  async function handleArchive(goalId: string, e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await archiveGoal(goalId);
      await fetchGoals();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '归档失败');
    }
  }

  /**
   * 删除目标（批次 D3：二次确认由 `ConfirmDialog` 承担）。
   *
   * 点「删除」只打开确认框，`e.stopPropagation()` 原样保留 —— 原来那一行
   * 在 `confirm()` **之前**，现在仍在同一个位置（同一条点击路径）。
   */
  function handleDelete(goalId: string, e: React.MouseEvent) {
    e.stopPropagation();
    setPendingDeleteGoalId(goalId);
  }

  /**
   * 真正执行删除（原来这段紧跟在同步的 `confirm()` 之后，现在由确认框的
   * `onConfirm` 调用 —— 逐字保留，含 `fetchGoals()` 刷新与失败提示）
   */
  async function performDelete(goalId: string) {
    try {
      await deleteGoal(goalId);
      await fetchGoals();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败');
    }
  }

  /** 取消新建，重置表单 */
  const handleCancelCreate = useCallback(() => {
    setCreateForm(INITIAL_FORM);
    setFormError('');
    setShowCreateForm(false);
  }, []);

  /**
   * ⚠️ 墓碑（批次 D2）：这里原来挂着**一份 document 级的 Esc 监听**
   * （a11y-audit 的键盘可达性一轮补的：`useEffect` + `document.addEventListener
   * ('keydown', …)` 调 `handleCancelCreate`）。弹窗外壳换成 `<Dialog>` 基座后
   * **整段已删除** —— Esc 由基座在遮罩的 `onKeyDown` 里统一处理。
   *
   * **不要加回来**：同一个 Esc 有两套来源时，"哪一套先生效"取决于事件路径
   * （基座挂在遮罩上、这份挂在 `document` 上），不一致时的症状只在真实浏览器里
   * 才看得见 —— 理由与 `Dialog.tsx` 文件头"为什么焦点陷阱是一个 `onKeyDown`
   * 而不是 document 上的监听器"逐字相同。
   */

  return (
    <div className="page-enter">
      {/* 页头：标题 + 新建按钮（批次 C1：2rem → 1.5rem；
          窄屏换行原由全局 `.page-header-row` 负责，现随组件进模块） */}
      <PageHeader
        title="学习目标"
        actions={
          <button className="btn btn-primary" onClick={() => setShowCreateForm(true)}>
            新建目标
          </button>
        }
      />

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
              action={
                <button className="btn btn-primary" onClick={() => setShowCreateForm(true)}>
                  新建目标
                </button>
              }
            />
          ) : (
            <section style={{ marginBottom: 'var(--space-xl)' }}>
              <h2
                className="heading-serif"
                style={{ fontSize: '1.25rem', marginBottom: 'var(--space-md)' }}
              >
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
                onClick={() => setShowArchived((s) => !s)}
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
                        <span
                          style={{
                            marginLeft: 'var(--space-sm)',
                            fontSize: '0.8rem',
                            color: 'var(--color-text-secondary)',
                          }}
                        >
                          {goal.type === 'daily' ? '每日' : '每周'} · 目标 {goal.target_mastery}%
                        </span>
                      </div>
                      <button
                        className="btn btn-danger"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => handleDelete(goal.id, e)}
                        aria-label={`删除 ${goal.name}`}
                      >
                        {/* 批次 B3：「删除」此前是全站纯文字按钮 —— 补 `delete` 图标
                            （`aria-label` 保留，图标本身是装饰性的 aria-hidden） */}
                        <Icon name="delete" size={16} />
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

      {/* 新建目标弹窗（批次 D2：遮罩 / 面板 / `role="dialog"` + `aria-modal` +
          `aria-labelledby` / Esc / 焦点陷阱 / body 滚动锁全部交给 `<Dialog>` 基座）。
          ── a11y-audit 的键盘可达性一轮（这一处此前**没有任何场景**覆盖，
             所以先补 `learning-goals-create` 场景再改，见 e2e/a11y.spec.ts）──
          那时补的四件事现在的归属：
            1. `role="dialog"` —— 基座给；
            2. `aria-modal="true"` —— 基座给；
            3. 对话框的可访问名 —— 基座给：`title="新建学习目标"` 渲染成面板顶部的
               `h2`，并用 `useId()` 生成 `id` 接到 `aria-labelledby` 上，
               所以原来写死的 `id="learning-goal-create-title"` 连同那枚 `<h3>`
               一起删掉了（信息一个字没少，`<h2>` 与 `<h3>` 都不跳级）；
            4. 四个 `<label>` 与输入框的 `htmlFor`/`id` —— **本文件保留**，逐字未动：
               那是内容层的标签关联，与弹窗外壳无关。
          `autoFocus` 本来就在名称输入框上（打开即聚焦），保持不变 ——
          基座的初始焦点取面板里第一个可聚焦元素，也正好是它。 */}
      <Dialog
        open={showCreateForm}
        onClose={handleCancelCreate}
        title="新建学习目标"
        footer={
          <>
            <button
              className="btn btn-secondary"
              onClick={handleCancelCreate}
              disabled={submitting}
            >
              取消
            </button>
            <button className="btn btn-primary" onClick={handleCreate} disabled={submitting}>
              {submitting ? '创建中...' : '创建'}
            </button>
          </>
        }
      >
        {/* 名称输入 */}
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <label
            htmlFor="learning-goal-create-name"
            style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}
          >
            目标名称
          </label>
          <input
            id="learning-goal-create-name"
            type="text"
            className="input"
            placeholder="例如：掌握第一章核心概念"
            value={createForm.name}
            onChange={(e) => setCreateForm((prev) => ({ ...prev, name: e.target.value }))}
            maxLength={200}
            autoFocus
          />
        </div>

        {/* 类型选择 */}
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <label
            htmlFor="learning-goal-create-type"
            style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}
          >
            目标类型
          </label>
          <select
            id="learning-goal-create-type"
            className="input"
            value={createForm.type}
            onChange={(e) =>
              setCreateForm((prev) => ({ ...prev, type: e.target.value as GoalType }))
            }
          >
            <option value="daily">每日目标</option>
            <option value="weekly">每周目标</option>
          </select>
        </div>

        {/* 目标掌握度 */}
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <label
            htmlFor="learning-goal-create-mastery"
            style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}
          >
            目标掌握度 (%)
          </label>
          <input
            id="learning-goal-create-mastery"
            type="number"
            className="input"
            min={0}
            max={100}
            value={createForm.target_mastery}
            onChange={(e) =>
              setCreateForm((prev) => ({ ...prev, target_mastery: Number(e.target.value) }))
            }
          />
        </div>

        {/* 截止日期 */}
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <label
            htmlFor="learning-goal-create-deadline"
            style={{ display: 'block', marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}
          >
            截止日期（可选）
          </label>
          <input
            id="learning-goal-create-deadline"
            type="date"
            className="input"
            value={createForm.deadline}
            onChange={(e) => setCreateForm((prev) => ({ ...prev, deadline: e.target.value }))}
          />
        </div>

        {/* 表单错误提示 */}
        {formError && (
          <p
            style={{
              color: 'var(--color-error)',
              fontSize: '0.875rem',
              marginBottom: 'var(--space-sm)',
            }}
          >
            {formError}
          </p>
        )}
      </Dialog>

      {/* 删除目标的确认框（批次 D3）：文案逐字保留原来的 `confirm()` 参数。
          `onConfirm` 先关框再执行（见 `ConfirmDialog` 文件头），取消什么都不做。 */}
      <ConfirmDialog
        open={pendingDeleteGoalId !== null}
        title="确定删除此学习目标？"
        message="此操作不可恢复。"
        confirmText="删除"
        danger
        onConfirm={() => {
          const goalId = pendingDeleteGoalId;
          setPendingDeleteGoalId(null);
          if (goalId === null) return;
          void performDelete(goalId);
        }}
        onCancel={() => setPendingDeleteGoalId(null)}
      />
    </div>
  );
}

/**
 * 单个目标卡片
 * 展示名称、类型徽章、进度条、目标掌握度、剩余天数与操作按钮
 */
interface GoalCardProps {
  goal: LearningGoal;
  onArchive: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
}

function GoalCard({ goal, onArchive, onDelete }: GoalCardProps) {
  // 进度百分比，后端可能不返回，默认为 0
  const progress = goal.progress_percentage ?? 0;
  // 剩余天数（依赖 deadline 变化才重算；useMemo 内读时钟属"剩余天数"展示的合理非纯场景，
  // 每次 deadline 变化时重算即可，不追求渲染纯函数）
  const daysRemaining = useMemo(() => {
    if (!goal.deadline) return null;
    // eslint-disable-next-line react-hooks/purity -- 剩余天数必须读取当前时钟
    return Math.ceil((new Date(goal.deadline).getTime() - Date.now()) / 86400000);
  }, [goal.deadline]);
  // 关联笔记数（scope_notes 可能为 null）
  const noteCount = goal.scope_notes?.length ?? 0;

  return (
    <article className="card card-hover" style={{ padding: 'var(--space-lg)' }}>
      {/* 顶部：名称 + 类型徽章 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 'var(--space-md)',
        }}
      >
        <div style={{ flex: 1 }}>
          <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>{goal.name}</h3>
          <div
            style={{
              display: 'flex',
              gap: 'var(--space-sm)',
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            {/* 类型徽章：每日=蓝色，每周=紫色 */}
            <span
              style={{
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: '0.75rem',
                color: '#fff',
                background: goal.type === 'daily' ? 'var(--color-primary)' : '#6d28d9',
              }}
            >
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
              <span
                style={{
                  fontSize: '0.8rem',
                  color: daysRemaining < 0 ? 'var(--color-error)' : 'var(--color-text-secondary)',
                }}
              >
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
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: '0.8rem',
            color: 'var(--color-text-secondary)',
            marginBottom: 'var(--space-xs)',
          }}
        >
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
          {/* 批次 B3：同上一处（已归档目标的删除按钮） */}
          <Icon name="delete" size={16} />
          删除
        </button>
      </div>
    </article>
  );
}
