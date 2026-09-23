/**
 * @file 项目页面
 * @description 项目作为纯标签（note_projects 多对多）管理笔记归属的项目页面。
 *
 * 功能：
 * 1. 创建项目 — 纯标签，不生成物理目录
 * 2. 重命名 / 删除项目（删除只移除标签，笔记与文件保留）
 * 3. 查看项目下的笔记列表
 * 4. 扫描导入 — 用户手动把文件拷入收件箱 source/ 目录后，点击扫描将其识别为笔记并打上本项目标签
 *
 * overhaul-plan 5.5：本文件已拆到 300 行以下，只保留**编排**——列表/重命名/删除/展开/扫描
 * 归 `pages/projects/useProjects`，添加笔记面板归 `useAddNotesPanel`，卡片与各子面板
 * 归 `pages/projects/` 下的组件。拆分是**纯提取**：`renderCard(p, index)` 的 JSX、
 * 加载/空列表两种状态的文案、以及破坏性操作的确认逻辑都一字未动。
 */
import Icon from '../components/Icon';
import NewProjectForm from './projects/NewProjectForm';
import ProjectCard from './projects/ProjectCard';
import ProjectsErrorBanner from './projects/ProjectsErrorBanner';
import ProjectsHeader from './projects/ProjectsHeader';
import ProjectsUsageNotes from './projects/ProjectsUsageNotes';
import { useAddNotesPanel } from './projects/useAddNotesPanel';
import { useProjects } from './projects/useProjects';

export default function Projects() {
  const {
    projects,
    loading,
    error,
    clearError,
    renameError,
    clearRenameError,
    renaming,
    expanded,
    scanning,
    scanResults,
    loadProjects,
    startRename,
    updateRenameField,
    handleRename,
    cancelRename,
    handleDelete,
    toggleExpand,
    handleScan,
    refreshExpandedDetail,
    handleRemoveNote,
  } = useProjects();

  // 添加笔记面板（候选笔记 / 勾选 / 提交）
  const addNotes = useAddNotesPanel({
    loadProjects,
    refreshExpandedDetail,
  });

  return (
    <div className="page-enter">
      {/* 页面头部 */}
      <ProjectsHeader />

      {error && <ProjectsErrorBanner error={error} onDismiss={clearError} />}

      {/* 重命名校验（"项目名称不能为空"）：与页面级失败分成两条，
          点掉其中一条不会连带清掉另一条（原来共用一个 error 槽位就会） */}
      {renameError && (
        <ProjectsErrorBanner
          error={renameError}
          onDismiss={clearRenameError}
          dismissLabel="关闭重命名提示"
        />
      )}

      {/* 新建项目 */}
      <NewProjectForm onCreated={loadProjects} />

      {/* 项目列表 */}
      {loading ? (
        <div className="state-container">
          <div className="spinner" />
          <p className="state-message">正在加载项目…</p>
        </div>
      ) : projects.length === 0 ? (
        <div className="state-container">
          {/* 批次 B3：`\u{1F4C2}` 📂 换 `<Icon name="folder" />`。
              原来靠内联 `fontSize: 40` 把 emoji 撑到"插画尺寸"，
              而图标只允许 16 / 20 / 24 三档 —— 这里取最大档 24。
              `.state-icon` 自带 `opacity: 0.7`，那一条不动。 */}
          <div className="state-icon">
            <Icon name="folder" size={24} />
          </div>
          <p className="state-message">还没有项目</p>
          <p className="state-description">
            点击上方「新建项目」创建第一个项目（纯标签，不生成文件夹）。
          </p>
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            // min(320px, 100%)：可用宽度不足 320px 时不再撑出横向滚动。
            // 批次 C3：原为 340px —— 而知识卡片页（`KnowledgeCards.tsx`）用的是 320px，
            // 两个"自适应卡片网格"的阈值不一致，同一类数据在相邻两页每行能放几张卡都不同。
            // 统一到更窄的那档（320px），窄屏下更稳。
            gridTemplateColumns: 'repeat(auto-fill, minmax(min(320px, 100%), 1fr))',
            gap: 16,
            alignItems: 'stretch',
          }}
        >
          {projects.map((p, i) => (
            <ProjectCard
              key={p.id}
              project={p}
              index={i}
              rename={renaming[p.id]}
              detail={expanded[p.id]}
              isScanning={!!scanning[p.id]}
              scanResult={scanResults[p.id]}
              addPanelOpen={addNotes.addPanelProject?.id === p.id}
              candidateNotes={addNotes.candidateNotes}
              addSearch={addNotes.addSearch}
              addError={addNotes.addError}
              adding={addNotes.adding}
              selectedNoteIds={addNotes.selectedNoteIds}
              onStartRename={() => startRename(p)}
              onChangeRenameName={(value) => updateRenameField(p, 'name', value)}
              onChangeRenameDescription={(value) => updateRenameField(p, 'description', value)}
              onSaveRename={() => handleRename(p)}
              onCancelRename={() => cancelRename(p)}
              onDelete={() => handleDelete(p)}
              onScan={() => handleScan(p)}
              onToggleExpand={() => toggleExpand(p)}
              onOpenAddPanel={() => addNotes.openAddPanel(p)}
              onChangeAddSearch={addNotes.setAddSearch}
              onToggleSelectNote={addNotes.toggleSelectNote}
              onConfirmAdd={() => addNotes.confirmAdd(p)}
              onCloseAddPanel={addNotes.closeAddPanel}
              onRemoveNote={(note) => handleRemoveNote(p, note)}
            />
          ))}
        </div>
      )}

      {/* 使用说明 */}
      <ProjectsUsageNotes />
    </div>
  );
}
