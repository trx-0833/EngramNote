/**
 * @file 单个项目卡片（项目管理是破坏性操作最集中的地方）
 * @description 自 `pages/Projects.tsx` 的 `renderCard(p, index)` 拆分（overhaul-plan 5.5），
 * **只搬不改**：卡片类名（含 `stagger-${(index % 5) + 1}` 入场动画）、重命名态与展示态的
 * 互斥、`note_count ?? 0`（缺字段显示 0 而不是字面量 undefined）、重命名时隐藏描述、
 * 展开按钮的箭头与文案、以及"移出笔记"的确认与不跳转全部逐字保留。
 *
 * 卡片自身不持有状态：草稿（重命名/展开详情/扫描结果/候选笔记）都在 `pages/projects/`
 * 的 hooks 里，与拆分前同源。
 *
 * 5.10 移动端补丁：卡片的两行按钮都靠**内联样式**排版、没有类名，所以 responsive.css
 * 改不到它们 —— 320px 视口下五个按钮会把卡片撑出横向滚动。补的是 `flexWrap: 'wrap'`
 * （四处：头部行、头部按钮组、底部行、底部按钮组；宽屏无溢出可换，像素不变）。
 */
import type {
  Note,
  NoteInFolder,
  Project,
  ProjectDetail,
  ScanImportResponse,
} from '../../api/client';
import Icon from '../../components/Icon';
import AddNotesPanel from './AddNotesPanel';
import ProjectNotesList from './ProjectNotesList';
import ProjectRenameForm from './ProjectRenameForm';
import ScanResultPanel from './ScanResultPanel';
import { unwrapProjectNotes } from './helpers';
// 本卡片私有样式（visual-refactor-plan 批次 E8）：计数 chip 的两个颜色
import styles from './ProjectCard.module.css';

interface ProjectCardProps {
  project: Project;
  /** 列表下标，仅用于入场动画的 stagger 类 */
  index: number;
  /** 行内重命名草稿（存在即处于重命名态） */
  rename: { name: string; description: string } | undefined;
  /** 展开后的项目详情（未展开为 undefined） */
  detail: ProjectDetail | null | undefined;
  isScanning: boolean;
  scanResult: ScanImportResponse | null | undefined;
  /** 添加笔记面板是否开在本卡片上（同时只开一个） */
  addPanelOpen: boolean;
  candidateNotes: Note[];
  addSearch: string;
  addError: string;
  adding: boolean;
  selectedNoteIds: string[];
  onStartRename: () => void;
  onChangeRenameName: (value: string) => void;
  onChangeRenameDescription: (value: string) => void;
  onSaveRename: () => void;
  onCancelRename: () => void;
  onDelete: () => void;
  onScan: () => void;
  onToggleExpand: () => void;
  onOpenAddPanel: () => void;
  onChangeAddSearch: (value: string) => void;
  onToggleSelectNote: (id: string) => void;
  onConfirmAdd: () => void;
  onCloseAddPanel: () => void;
  onRemoveNote: (note: NoteInFolder) => void;
}

export default function ProjectCard({
  project: p,
  index,
  rename,
  detail,
  isScanning,
  scanResult,
  addPanelOpen,
  candidateNotes,
  addSearch,
  addError,
  adding,
  selectedNoteIds,
  onStartRename,
  onChangeRenameName,
  onChangeRenameDescription,
  onSaveRename,
  onCancelRename,
  onDelete,
  onScan,
  onToggleExpand,
  onOpenAddPanel,
  onChangeAddSearch,
  onToggleSelectNote,
  onConfirmAdd,
  onCloseAddPanel,
  onRemoveNote,
}: ProjectCardProps) {
  const isRenaming = !!rename;
  const isExpanded = !!detail;
  // 详情里的 notes 与 `/projects` 走同一道拆包判据：缺字段/包装对象都不该让 `notes.map` 白屏。
  // 但"还没展开"必须排除在判据之外：没有详情就是没有笔记列表，把 `undefined` 喂给
  // unwrapProjectNotes 会把每一次列表渲染都报成契约漂移。
  const notes: NoteInFolder[] = detail ? unwrapProjectNotes(detail.notes) : [];

  return (
    <div
      /* 批次 E8：色条从**顶部**挪到**左侧**（`docs/visual-symbol-research.md` §C3
         的「项目卡左侧色条」，Trilium 的类型色条做法，与笔记列表 E2 的 3px 左色条同一套）。
         原来是在这里写内联 `borderTop: '3px solid var(--color-primary)'` ——
         内联样式压得过一切，也就等于把这条视觉规则钉死在 tsx 里（改不了 hover、
         改不了窄屏）。改用**已有的**全局类 `.card-accent-left`（与 `.card` 的
         `border` 简写竞争已经在 `verify-built-css.mjs` 的 `CROSS_CLASS_SHORTHAND_RULES`
         里登记过，见 `card × card-accent-left`），与 Dashboard / TodayLearn /
         ReminderBanner 四处完全同形。 */
      className={`card card-hover card-accent-left fade-in stagger-${(index % 5) + 1}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        padding: 20,
      }}
    >
      {/* 项目头：名称 + 笔记数 */}
      {/* flexWrap：窄屏（~320px）时右侧两个按钮整组换到第二行，而不是把卡片撑出横向滚动 */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          {isRenaming ? (
            <input
              value={rename.name}
              onChange={(e) => onChangeRenameName(e.target.value)}
              placeholder="项目名称"
              style={{ width: '100%', fontWeight: 600 }}
              autoFocus
            />
          ) : (
            /* `h2` 而不是 `h3`（a11y-audit **F-20**）：这一页的标题是 h1「项目」
               （`ProjectsHeader`），卡片标题是它的**直接下级区块** ——
               中间不存在第三级，写成 h3 就是 h1 → h3 跳级。
               提升为 h2 之后大纲是 h1 → h2（每张卡片），层级是完整的，
               而且**没有新起任何名字、没有多任何一行文字**（区块名就是卡片标题）。
               字号 1.05rem 与字重 700 本来就显式钉着，所以**一个像素都没动**。 */
            <h2
              style={{
                fontSize: '1.05rem',
                fontWeight: 700,
                margin: 0,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {p.name}
            </h2>
          )}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginTop: 6,
              fontSize: '0.75rem',
              color: 'var(--color-text-tertiary)',
            }}
          >
            {/* 计数 chip（批次 E8）：`.badge` 提供胶囊基线，本模块只补"浅墨底 + 墨蓝字"
                两个颜色 —— 这两个属性 `.badge` 一个都没声明，所以没有同权重竞争。 */}
            <span className={`badge ${styles.projectNoteCountChip}`}>
              {p.note_count ?? 0} 篇笔记
            </span>
          </div>
        </div>
        {/* 操作按钮（flexShrink:0 让它们不被标题压缩；因此必须能换行，否则窄屏溢出） */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, flexShrink: 0 }}>
          <button
            className="btn btn-ghost"
            title="从已有笔记中选择并添加到本项目"
            onClick={onOpenAddPanel}
            style={{ fontSize: '0.8rem', padding: '4px 10px' }}
          >
            {/* 批次 B3：「添加」此前是全站纯文字按钮 —— 补一枚加号图标 */}
            <Icon name="add" size={16} />
            添加笔记
          </button>
          <button
            className="btn btn-ghost"
            title="扫描导入 source/ 目录中的新文件"
            onClick={onScan}
            disabled={isScanning}
            style={{ fontSize: '0.8rem', padding: '4px 10px' }}
          >
            {isScanning ? '扫描中…' : '扫描导入'}
          </button>
        </div>
      </div>

      {/* 描述 */}
      {!isRenaming && p.description && (
        <p
          style={{
            fontSize: '0.85rem',
            color: 'var(--color-text-secondary)',
            margin: 0,
            lineHeight: 1.6,
          }}
        >
          {p.description}
        </p>
      )}

      {/* 重命名编辑区 */}
      {isRenaming && (
        <ProjectRenameForm
          name={rename.name}
          description={rename.description}
          onChangeName={onChangeRenameName}
          onChangeDescription={onChangeRenameDescription}
          onSave={onSaveRename}
          onCancel={onCancelRename}
        />
      )}

      {/* 扫描结果 */}
      {scanResult && <ScanResultPanel result={scanResult} />}

      {/* 添加笔记面板 */}
      {addPanelOpen && (
        <AddNotesPanel
          candidates={candidateNotes}
          search={addSearch}
          error={addError}
          adding={adding}
          selectedNoteIds={selectedNoteIds}
          onChangeSearch={onChangeAddSearch}
          onToggleSelect={onToggleSelectNote}
          onConfirm={onConfirmAdd}
          onClose={onCloseAddPanel}
        />
      )}

      {/* 底部操作行：这行没有类名（样式全在内联），所以窄屏的换行也只能在这里加。
          不加 flexWrap 时 320px 视口上「查看笔记（N）＋重命名＋删除」会横向溢出卡片 */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          borderTop: '1px solid var(--color-border-light)',
          paddingTop: 10,
          marginTop: 'auto',
        }}
      >
        <button
          className="btn btn-ghost"
          style={{ fontSize: '0.8rem', padding: '4px 8px' }}
          onClick={onToggleExpand}
        >
          {/* 批次 B3：`▶` 换 `<Icon name="chevron" />`，旋转过渡仍由 `.collapse-arrow` 提供 */}
          <span className={`collapse-arrow ${isExpanded ? 'collapse-arrow-open' : ''}`}>
            <Icon name="chevron" size={16} />
          </span>
          {isExpanded ? '收起笔记' : `查看笔记（${p.note_count ?? 0}）`}
        </button>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.8rem', padding: '4px 8px' }}
            onClick={onStartRename}
          >
            重命名
          </button>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.8rem', padding: '4px 8px', color: 'var(--color-error)' }}
            onClick={onDelete}
          >
            {/* 批次 B3：「删除」此前是全站纯文字按钮 —— 补 `delete`（带叉的浅桶，动作）
                而不是 `trash`（回收站，位置）。两者怎么区分见 icons/delete.tsx 文件头。 */}
            <Icon name="delete" size={16} />
            删除
          </button>
        </div>
      </div>

      {/* 笔记列表 */}
      {isExpanded && <ProjectNotesList notes={notes} onRemoveNote={onRemoveNote} />}
    </div>
  );
}
