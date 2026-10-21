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
import type { ReactNode } from 'react';
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
import { useTranslation } from '../../i18n';

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
  /**
   * 导出笔记正文为 `.md`（想法 4「设计导出功能」）
   *
   * 导出哪一版由 `viewMode` 决定（原始版 / 清洗版），所以这个回调**不带参数** ——
   * 判断留在 `useNoteActions` 里，页头只负责"点一下"。
   */
  onExport: () => void;
  /** 导出进行中（按钮禁用 + 文案变化） */
  exporting: boolean;
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
  /**
   * 页头最下方的内容（2026-10-18 新增）
   *
   * 目前唯一的调用方是 `NoteDetail.tsx` 传进来的**元信息条**
   * （`NotePropertyRail`）。它原本是主内容左侧那条常驻竖轨，用户实测反馈
   * "占地方、滚动不腾位"，于是回到页头。
   *
   * 为什么用 `children` 而不是让页头直接 import 那个组件：
   * 元信息条要用到 `note` 与 `onRoleUpdated`，而 `onRoleUpdated` 的落点是
   * **页面**的 `setNote`（局部合并）—— 页头自己接这个回调会把它变成
   * "页头管数据"，与它现在"只收 props、只渲染"的角色不符。
   * 用 `children` 让页面继续持有那条数据流，页头只管**放在哪**。
   */
  children?: ReactNode;
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
  onExport,
  exporting,
  navigate,
  children,
}: NoteDetailHeaderProps) {
  const { t } = useTranslation();
  const isProcessing = PROCESSING_STATUSES.includes(note.status);

  return (
    <header style={{ marginBottom: 'var(--space-lg)' }}>
      {/* 标题行。2026-10-19：标题**独占一行**，不再与按钮组抢宽度。
          这两层骨架（以及窄屏那两条）已随组件搬进 NoteDetailHeader.module.css
          —— 类名哈希后写在 responsive.css 里的选择器会永远选不中（5.6 序 8）。 */}
      <div className={styles.noteDetailHeader}>
        {/* ⚠️ 这里**只换量尺、不换骨架**（visual-refactor-plan 批次 C1）：
            这一页的 h1 是**实体标题** —— 显示的是笔记自己的名字，不是页面名；
            而页头骨架（`.noteDetailHeader` / `.noteDetailActions` 以及它们的
            768px"按钮撑满"与 480px"一行两个"）承载的是
            **8 个操作按钮的窄屏行为**，是 5.6 序 8 专门搬进模块、并且被
            `verify-built-css.mjs` 的切片标记盯着的既有结论。
            把整行换成 `<PageHeader actions={…}>` 会顺手把那一整套窄屏行为
            改成 PageHeader 自己的"768px 换行"（按钮不再按 50% 撑开）——
            那是 C1 范围之外的版式改动。
            所以：`<PageHeader>` 在这里只负责"1.5rem 衬线 600"这一件事
            （原字号本就 1.5rem，观感不变），`spacing="none"` 把与下方内容的
            间距留给 `.noteDetailHeader` 自己的 `margin-bottom`。

            ⚠️ 2026-10-19 起这一层**不再**用 `space-between` 把按钮推到右边：
            那正是"标题被压成两行"的成因（见 module.css 文件头的实测表）。
            现在它只包标题，按钮是它下面的兄弟。 */}
        <PageHeader title={note.title} spacing="none" />
      </div>

      {/* 视图切换 tab（批次 E2 建，2026-10-19 **换了位置**）
          —— 行为与文案一字未变：仍是 `ViewModeTabs` 的
          「原始版 / 清洗版 / 对比视图」三个按钮、同样的禁用条件、
          同样由 `NoteDetail` 的 `viewMode` 驱动。
          `.viewTabs` 只改这一处的观感（透明底 + 底线 + 金色指示线），
          `segment-control` / `segment-btn` 两个全局类本身一字未改，
          学习评估页仍在用它们原来的样子。

          ## 为什么从"页头最上面"挪到"标题下面"

          用户实测反馈：**「放在左上角让人搞不懂其具体的功能」**。
          实测证实了那个观感 —— tab 条**容器占满整行**（1600 下 1296px），
          而**内容只有 188px 贴在左边缘**，右侧 1108px 全空；
          三个按钮分别是 42 / 42 / 56px 宽。也就是说它是一条通栏底线下面
          挂着三个孤立的小按钮，悬在标题、按钮、元信息**之上**，
          没有任何视觉分组或标签说明它跟下面哪一块有关。

          挪到标题**正下方**之后归属关系直接读得出来：它们控制的是
          **这一篇笔记**用哪一版显示。页头顺序于是变成
          「叫什么 → 用哪一版看 → 能对它做什么 → 它是什么」，
          前两步都是"这个实体"，后两步是"对它做什么/它是什么"。

          ⚠️ 刻意**没有**加「查看：」之类的可见标签：那是改文案
          （`ViewModeTabs` 的字一个都没动过），而且加标签会让这一行
          变成"标签 + 控件"的两段式，比现在的"三个 tab 贴着标题"更碎。
          若挪位之后仍然读不懂，那才轮到考虑加标签。 */}
      <div className={styles.viewTabs}>
        <ViewModeTabs
          viewMode={viewMode}
          onViewModeChange={onViewModeChange}
          canShowClean={canShowClean}
          canShowDiff={canShowDiff}
        />
      </div>

      {/* 操作工具栏：**按视觉重量分两级**（2026-10-19 第三次改版）
          
          ## 为什么分级（人眼核对真实页面后定的）
          
          改之前 9 个按钮是**同一套白底描边、同一字号**，横排一行：
          编辑 / 版本历史 / 导出 / 立即复习 / 学习评估 / AI预处理 /
          取消审阅 / 删除 / 返回。于是出现三个问题：
          
          1. **没有主次** —— 工具栏是页头里最密的东西，9 个等重方框把标题压住了；
          2. **两个深蓝按钮并列**（「立即复习」与「AI预处理」）—— 主操作不唯一，
             眼睛不知道该落哪；
          3. **「删除」是整页最响的元素** —— 通栏红底白字，比任何操作都显眼。
             破坏性操作不该是页头的视觉焦点。
          
          ## 分级规则
          
          | 级 | 成员 | 样式 | 理由 |
          |---|---|---|---|
          | **主操作** | 立即复习 | `.btn-primary`（深墨色实心） | 这一页唯一的主动作是"去学习"。它是**唯一**的实心按钮 |
          | 辅助操作 | 编辑 / 版本历史 / 导出 / 学习评估 / AI预处理 / 取消审阅 / 返回 | `.btn-ghost`（无边框，悬停才浮起） | 偶尔用一次。让工具栏**退到背景**，标题重新成为焦点 |
          | 破坏性 | 删除 | `.btn-danger-outline`（描边危险色） | 仍清楚可辨（`--color-error` 白底 5.44:1，见 `components.css:128`），但不再是页头最响的东西 |
          
          `.btn-ghost` 与 `.btn-danger-outline` 都是**既有的全局类**
          （`components.css:131` / `:141`），本轮没有新增任何按钮样式。
          
          ## 布局：一个容器、两组、同一行
          
          ```
          [编辑 版本历史 导出 学习评估 AI预处理 取消审阅 删除 返回]   [立即复习]
           ← 辅助组（幽灵，靠左）                                    主操作（实心，最右）→
          ```
          
          ⚠️ **第一版不是这样的**：那时两组各占一行（主操作单独一行靠左），
          人眼核对截图后发现「立即复习」孤零零挂在左边、辅助组在右边，
          中间一大片空 —— 横向断裂比"两组同行"更乱。所以合成一行，
          主操作用 `margin-left: auto` 推到最右。
          
          ## 窄屏：上下两行，且不再出现"一个按钮撑满整行"
          
          ≤768px 时容器改竖排（辅助组在上、主操作在下）。
          原来那条 `flex: 1 1 auto` 在"一行只剩一个按钮"时会让它**撑满整行**
          —— 实测 768px 下「返回」就是一条 1472px 宽的通栏大按钮，
          看起来像个主操作。现在按钮按内容宽度，不再拉伸。 */}

      <div className={styles.noteDetailToolbar}>
        {/* ① 辅助操作组：幽灵样式，退到背景。
            ⚠️ DOM 顺序是**读屏的朗读顺序**：先"有什么可做"，最后才落到
            "去做"（主操作在下面那个 div 里）。视觉上主操作被推到最右
            （`margin-left: auto`），正好是阅读的终点。 */}
        <div className={styles.noteDetailActions}>
          {editMode === 'view' && (
            <button
              className="btn btn-ghost"
              onClick={onEnterEdit}
              disabled={viewMode === 'original' || isProcessing}
              title={
                viewMode === 'original'
                  ? t('noteDetail.editBlockedOriginal')
                  : isProcessing
                    ? t('noteDetail.editBlockedProcessing')
                    : t('noteDetail.editHint')
              }
            >
              <Icon name="edit" size={16} />
              {t('noteDetail.edit')}
            </button>
          )}
          <button
            className="btn btn-ghost"
            onClick={onOpenVersionHistory}
            disabled={['uploading', 'converting'].includes(note.status)}
            title={t('noteDetail.viewVersionHistory')}
          >
            {t('noteDetail.versionHistory')}
          </button>
          {/* 导出（想法 4「设计导出功能」）。
              禁用条件与 `title` 文案：
              - **处理中**（`isProcessing`）：正文还没生成或正在改写，导出来是半成品；
              - **对比视图**（`viewMode === 'diff'`）：那是"两版并排看"，
                不是某一个版本，没有"这一版"可导。要导请先切到具体某一版；
              - 其余情况都可导 —— 包括**尚未清洗**的笔记（导原始版）。
                清洗版不可用时**不禁用按钮**：此时 `viewMode` 必然是 `original`
                （`ViewModeTabs` 的 `canShowClean` 已经挡住了切过去），
                所以用户能导的正是他手上有的那一版。
                真正没有正文时后端回 `NOTE_EXPORT_EMPTY`，由 toast 透传文案。 */}
          <button
            className="btn btn-ghost"
            onClick={onExport}
            disabled={isProcessing || viewMode === 'diff' || exporting}
            title={
              isProcessing
                ? t('noteDetail.exportBlockedProcessing')
                : viewMode === 'diff'
                  ? t('noteDetail.exportBlockedDiff')
                  : viewMode === 'original'
                    ? t('noteDetail.exportOriginalHint')
                    : t('noteDetail.exportCleanHint')
            }
          >
            {exporting ? t('noteDetail.exporting') : t('noteDetail.export')}
          </button>
          {(note.note_role === 'material' || !note.note_role) && (
            <button
              className="btn btn-ghost"
              onClick={() => navigate(`/assessment?noteId=${note.id}`)}
            >
              {t('noteDetail.assessment')}
            </button>
          )}
          {note.note_role === 'personal_note' && (
            <button className="btn btn-ghost" onClick={onManageLinks}>
              {t('noteDetail.manageLinks')}
            </button>
          )}
          {(note.status === 'cleaned' ||
            note.status === 'learning_failed' ||
            note.status === 'archived') && (
            <button className="btn btn-ghost" onClick={onStartLearning}>
              {t('noteDetail.aiPreprocess')}
            </button>
          )}
          {(note.status === 'cleaned' ||
            note.status === 'learning_failed' ||
            note.status === 'converted' ||
            note.status === 'archived') && (
            <button className="btn btn-ghost" onClick={onArchive}>
              {note.status === 'archived' ? t('noteDetail.unarchive') : t('noteDetail.archive')}
            </button>
          )}
          <button className="btn btn-danger-outline" onClick={onDelete}>
            <Icon name="delete" size={16} />
            {t('noteDetail.delete')}
          </button>
          <button className="btn btn-ghost" onClick={() => navigate('/notes')}>
            {t('noteDetail.back')}
          </button>
        </div>

        {/* ② 主操作组：唯一的实心按钮，被推到最右。
            条件不成立时这个 div 是空的，`.noteDetailPrimaryActions:empty`
            会把它整个 `display: none`（否则 `justify-content: space-between`
            会把它当成第二个 flex 项，把辅助组挤到左边、右边留一片空）。 */}
        <div className={styles.noteDetailPrimaryActions}>
          {(note.status === 'archived' || note.status === 'learning') && hasQuizItems && (
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/review/quick/${note.id}`)}
            >
              {t('noteDetail.reviewNow')}
            </button>
          )}
        </div>
      </div>

      {/* 元信息条（2026-10-18 起从 `children` 进来）。
          批次 E2 曾把这一整块搬去主内容左侧当竖轨（DOM 上从
          `<header>` 的第二个子块变成 `<aside aria-label="笔记元信息">`），
          2026-10-18 用户实测反馈"占地方、滚动不腾位"后搬回页头，
          但**排版是横排**（不再横占满行、也不再竖占一列）。

          2026-10-19：DOM 顺序从"标题与按钮之间"挪到**工具栏之后**
          （视觉位置本来就是最后，所以观感不变）。理由是语义顺序 ——
          人读一页是「叫什么 → 是什么 → 能对它做什么」，改之前按钮夹在
          标题与元信息中间，把"这个实体是什么"切成了两半。

          容器从 `<aside aria-label>` 改成了 `<ul aria-label>`（见
          `NotePropertyRail.tsx` 的语义对照表）：它不再是"与主内容并列的
          侧轨"，`complementary` 这个地标语义已经名不副实；
          `<ul>` 是原生元素，读屏会直接报"列表，7 项"。 */}
      {children}

      {/* 索引未建好的提示（B2 的"可见"那半，2026-10-22）

          ## 它说的是什么

          清洗路径**刻意不写向量**（要加载 4.3GB 模型，不能放在高并发的用户路径
          里），补嵌入由 Beat 的 `backfill_embeddings` 周期补齐。在那之前这篇
          笔记**只被 BM25 检索到** —— 换个说法/同义的提问会答不出或答歪。

          ## 为什么必须在这里出现

          此前只有两处能看到这件事：就绪探针里的一个计数（**运维视角**，
          用户不会去读；那条端点的名字刻意不写在这里 —— 有守卫禁止前端源码
          出现它，见 `backend/tests/test_ready_endpoint.py` 的
          `test_no_frontend_source_references_ready`），以及问答时的 SSE
          `meta.retrieval_status`（**要先提一个问题**才会出现）。
          也就是说"不提问就不知道" —— 而用户不知道就会以为
          "这篇资料问不出来"是系统坏了。

          ## 判据与措辞

          - `undefined` / `null`：还没统计（笔记还没清洗完）⇒ **不显示**。
            "没测到"不能长得像"有问题"（与后端那个 `Optional[int]` 同一条原则）。
          - `0`：全部已建索引 ⇒ **正常，不显示**。这是绝大多数情况，
            常驻一条"0 个待建"只会变成噪音。
          - `> 0`：显示，并说明**会自愈**（周期任务会补）而不是"出错了"。
            用户需要的不是焦虑，是"要不要做什么" —— 答案通常是"不用，
            等一会儿"，那就直说。 */}
      {typeof note.pending_embedding_chunks === 'number' && note.pending_embedding_chunks > 0 && (
        <p
          style={{
            color: 'var(--color-text-secondary)',
            fontSize: '0.8125rem',
            margin: 'var(--space-sm) 0 0',
          }}
        >
          {t('noteDetail.indexPending', { count: note.pending_embedding_chunks })}
        </p>
      )}

      {/* 错误信息提示 + 重试按钮。⚠️ 留在页头最下方：它是"这一页现在出问题了"，
          与"这条笔记是什么"（上面的元信息条）不是一类信息。 */}
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
            {t('noteDetail.errorPrefix')}: {note.error_message}
          </p>
          {note.status === 'failed' && (
            <RetryConvertButton noteId={noteId} onRetried={onRetryConverted} />
          )}
        </div>
      )}
    </header>
  );
}
