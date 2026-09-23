/**
 * @file 笔记详情页头部：顶部视图切换 tab、标题与操作按钮、错误提示
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 标签顺序、禁用/显示条件、`title` 提示文案、inline 样式均与拆分前一致。
 *
 * ## 批次 E2（visual-refactor-plan §6 · 借鉴表 §C3「笔记详情」行）
 *
 * 本文件在两处**原位**调整，页头骨架（`.noteDetailHeader` /
 * `.noteDetailActions` 与它们那 5 条窄屏规则）**一个字没动**：
 *
 * 1. `<ViewModeTabs>` 从页头**末尾**提到页头**最前面**，并加一层
 *    `.viewTabs` 包装把它从"分段控件"改成顶部 tab 条（Obsidian 的侧栏标签页
 *    思路）。tab 的取值、顺序、禁用条件、文案仍由 `ViewModeTabs.tsx` +
 *    `NoteDetail` 的 `viewMode` state 驱动 —— 只换了位置与外观。
 * 2. 「笔记元信息标签行」（来源类型徽章 / 项目标签 / 状态 / 笔记角色下拉 /
 *    页数 / 大小 / 创建时间）**整体搬到 `NotePropertyRail.tsx`**，
 *    成为主内容左侧那条窄竖轨。搬走的是这一整块 + 它唯一的控件
 *    （角色下拉）与 `onRoleUpdated` 这一个回调；搬法逐字照抄，
 *    连 `aria-label="笔记角色"` 与 `--note-role-bg` 的传值方式都没变。
 *    角色下拉的样式（含那一圈两层焦点环）随之搬到
 *    `NotePropertyRail.module.css`，原处留了墓碑注释。
 */
import type { NoteDetail, RetryConvertOutcome } from '../../api/client';
import Icon from '../../components/Icon';
// 页面标题（visual-refactor-plan 批次 C1）：本页的 h1 是**实体标题**
// （笔记自己的名字，长度不可控），所以只把量尺换成 <PageHeader>，
// 页头骨架与它那 5 条窄屏规则仍留在下面的组件模块里 —— 见调用点的说明
import PageHeader from '../../components/PageHeader';
import RetryConvertButton from './RetryConvertButton';
import ViewModeTabs from './ViewModeTabs';
import styles from './NoteDetailHeader.module.css';
import type { EditMode, ViewMode } from './types';

/** 处理中（不可编辑）的笔记状态 */
const PROCESSING_STATUSES = ['uploading', 'converting', 'cleaning', 'learning'];

/**
 * 页头 props。
 *
 * ⚠️ 批次 E2 之后这里**不再有** `onRoleUpdated`：「笔记角色」下拉整块搬进了
 * `NotePropertyRail.tsx`，回调随之改由它接 —— 数据流方向没变，仍然是
 * `NoteDetail` → 子组件的 `onRoleUpdated` → `setNote` 局部合并。
 */
interface NoteDetailHeaderProps {
  /** 笔记详情 */
  note: NoteDetail;
  /** 笔记 ID（重试转换用；与 note.id 同源） */
  noteId: string | undefined;
  /** 当前视图模式 */
  viewMode: ViewMode;
  /** 当前编辑模式（编辑中隐藏"编辑"按钮） */
  editMode: EditMode;
  /** 是否有可立即复习的题目 */
  hasQuizItems: boolean;
  /** 是否可切换到清洗版 */
  canShowClean: boolean;
  /** 是否可切换到对比视图 */
  canShowDiff: boolean;
  /** 切换视图模式 */
  onViewModeChange: (mode: ViewMode) => void;
  /** 进入编辑模式 */
  onEnterEdit: () => void;
  /** 触发 AI 预处理（开始学习） */
  onStartLearning: () => void;
  /** 归档 / 取消归档 */
  onArchive: () => void;
  /** 打开删除确认弹窗 */
  onDelete: () => void;
  /** 打开版本历史面板 */
  onOpenVersionHistory: () => void;
  /** 打开关联资料管理弹窗 */
  onManageLinks: () => void;
  /**
   * 局部合并重试转换结果
   *
   * 阶段 5.1 / S2：与 `RetryConvertButton` 共用 `retryConvert` 的生成返回类型，
   * 不再手抄第二份 `{ status: string; error_message: string | null }`。
   */
  onRetryConverted: (next: RetryConvertOutcome) => void;
  /** 跳转路由 */
  navigate: (path: string) => void;
}

/** 笔记详情页头部区域 */
export default function NoteDetailHeader({
  note,
  noteId,
  viewMode,
  editMode,
  hasQuizItems,
  canShowClean,
  canShowDiff,
  onViewModeChange,
  onEnterEdit,
  onStartLearning,
  onArchive,
  onDelete,
  onOpenVersionHistory,
  onManageLinks,
  onRetryConverted,
  navigate,
}: NoteDetailHeaderProps) {
  const isProcessing = PROCESSING_STATUSES.includes(note.status);

  return (
    <header style={{ marginBottom: 'var(--space-lg)' }}>
      {/* 顶部视图切换 tab（批次 E2）——位置与外观变了，**行为与文案没变**：
          仍是 ViewModeTabs 的「原始版 / 清洗版 / 对比视图」三个按钮、
          同样的禁用条件、同样由 NoteDetail 的 viewMode 驱动。
          `.viewTabs` 只改这一处的观感（透明底 + 底线 + 金色指示线），
          `segment-control` / `segment-btn` 两个全局类本身一字未改，
          学习评估页仍在用它们原来的样子。 */}
      <div className={styles.viewTabs}>
        <ViewModeTabs
          viewMode={viewMode}
          onViewModeChange={onViewModeChange}
          canShowClean={canShowClean}
          canShowDiff={canShowDiff}
        />
      </div>

      {/* 标题 + 操作按钮组：窄屏下改为上下排列、按钮换行。
          这两层骨架（以及窄屏那 3 条）已随组件搬进 NoteDetailHeader.module.css
          —— 类名哈希后写在 responsive.css 里的选择器会永远选不中（5.6 序 8）。 */}
      <div className={styles.noteDetailHeader}>
        {/* ⚠️ 这里**只换量尺、不换骨架**（visual-refactor-plan 批次 C1）：
            这一页的 h1 是**实体标题** —— 显示的是笔记自己的名字，不是页面名；
            而页头骨架（`.noteDetailHeader` / `.noteDetailActions` 以及它们的
            768px"上下排列 + 按钮换行 + 按钮撑满"与 480px"一行两个"）承载的是
            **8 个操作按钮的窄屏行为**，是 5.6 序 8 专门搬进模块、并且被
            `verify-built-css.mjs` 的切片标记盯着的既有结论。
            把整行换成 `<PageHeader actions={…}>` 会顺手把那一整套窄屏行为
            改成 PageHeader 自己的"768px 换行"（按钮不再上下排列、
            也不再按 50% 撑开）—— 那是 C1 范围之外的版式改动。
            所以：`<PageHeader>` 在这里只负责"1.5rem 衬线 600"这一件事
            （原字号本就 1.5rem，观感不变），`spacing="none"` 把与下方内容的
            间距留给 `.noteDetailHeader` 自己的 `margin-bottom`。
            批次 E2 也不动它：8 个操作按钮仍在这一层里。 */}
        <PageHeader title={note.title} spacing="none" />
        <div className={styles.noteDetailActions}>
          {editMode === 'view' && (
            <button
              className="btn btn-secondary"
              onClick={onEnterEdit}
              disabled={viewMode === 'original' || isProcessing}
              title={
                viewMode === 'original'
                  ? '原始版不可编辑，请在清洗版中编辑'
                  : isProcessing
                    ? '处理中，暂不可编辑'
                    : '编辑笔记内容'
              }
            >
              <Icon name="edit" size={16} />
              编辑
            </button>
          )}
          <button
            className="btn btn-secondary"
            onClick={onOpenVersionHistory}
            disabled={['uploading', 'converting'].includes(note.status)}
            title="查看版本历史"
          >
            版本历史
          </button>
          {(note.status === 'archived' || note.status === 'learning') && hasQuizItems && (
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/review/quick/${note.id}`)}
            >
              立即复习
            </button>
          )}
          {(note.note_role === 'material' || !note.note_role) && (
            <button
              className="btn btn-secondary"
              onClick={() => navigate(`/assessment?noteId=${note.id}`)}
            >
              学习评估
            </button>
          )}
          {note.note_role === 'personal_note' && (
            <button className="btn btn-secondary" onClick={onManageLinks}>
              管理关联资料
            </button>
          )}
          {(note.status === 'cleaned' ||
            note.status === 'learning_failed' ||
            note.status === 'archived') && (
            <button className="btn btn-primary" onClick={onStartLearning}>
              AI预处理
            </button>
          )}
          {(note.status === 'cleaned' ||
            note.status === 'learning_failed' ||
            note.status === 'converted' ||
            note.status === 'archived') && (
            <button className="btn btn-secondary" onClick={onArchive}>
              {note.status === 'archived' ? '取消审阅' : '审阅'}
            </button>
          )}
          <button className="btn btn-danger" onClick={onDelete}>
            <Icon name="delete" size={16} />
            删除
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/notes')}>
            返回
          </button>
        </div>
      </div>

      {/* 元信息标签行**整块搬到 `NotePropertyRail.tsx`**（批次 E2）：
          它原本横在标题与正文之间，现在收进主内容左侧那条窄竖轨。
          DOM 上它从 `<header>` 的第二个子块变成了 `<aside aria-label="笔记元信息">`
          的内容 —— 里面每一项的类名、文案、顺序、可访问名逐字未变
          （唯一的原生控件「笔记角色」下拉仍是 `aria-label="笔记角色"`，
          仍是 `combobox`）。 */}

      {/* 错误信息提示 + 重试按钮。⚠️ 留在页头：它是"这一页现在出问题了"，
          与"这条笔记是什么"（竖轨里的元信息）不是一类信息。 */}
      {note.error_message && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-sm)',
            marginTop: 'var(--space-sm)',
          }}
        >
          <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.875rem', margin: 0 }}>
            错误: {note.error_message}
          </p>
          {note.status === 'failed' && (
            <RetryConvertButton noteId={noteId} onRetried={onRetryConverted} />
          )}
        </div>
      )}
    </header>
  );
}
