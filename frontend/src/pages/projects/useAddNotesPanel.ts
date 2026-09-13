/**
 * @file 项目页的「添加笔记」面板状态：候选笔记加载、勾选、提交
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 候选笔记只列「尚未归属本项目」的（否则同一篇笔记会被反复添加）、
 * 面板同时只打开一个、打开与关闭都会重置勾选/搜索/错误、
 * 提交成功后关闭面板并刷新列表（展开态还要刷新详情，否则被移出的笔记还挂在页面上）。
 */
import { useState } from 'react'
import {
  addNotesToProject,
  getNotes,
  type Note,
  type Project,
} from '../../api/client'

interface UseAddNotesPanelOptions {
  /** 刷新项目列表（笔记归属变化会影响 note_count） */
  loadProjects: () => Promise<void>
  /** 若该项目处于展开状态，同步刷新它的笔记列表 */
  refreshExpandedDetail: (p: Project) => Promise<void>
}

export function useAddNotesPanel({
  loadProjects,
  refreshExpandedDetail,
}: UseAddNotesPanelOptions) {
  // 添加笔记面板（同时只打开一个）
  const [addPanelProject, setAddPanelProject] = useState<Project | null>(null)
  const [candidateNotes, setCandidateNotes] = useState<Note[]>([])
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([])
  const [addSearch, setAddSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')

  /** 打开添加笔记面板：加载候选笔记（不属于当前项目的笔记） */
  async function openAddPanel(p: Project) {
    setAddPanelProject(p)
    setSelectedNoteIds([])
    setAddSearch('')
    setAddError('')
    try {
      // 拉取全部笔记（分页上限 999），过滤出尚未打上当前项目标签的作为候选
      const data = await getNotes(1, 999, undefined, undefined)
      setCandidateNotes(data.items.filter((n) => !n.project_ids?.includes(p.id)))
    } catch (err) {
      console.error('加载候选笔记失败:', err)
      setAddError('加载候选笔记失败，请稍后重试')
      setCandidateNotes([])
    }
  }

  /** 关闭添加笔记面板 */
  function closeAddPanel() {
    setAddPanelProject(null)
    setCandidateNotes([])
    setSelectedNoteIds([])
    setAddSearch('')
    setAddError('')
  }

  /** 勾选/取消勾选候选笔记 */
  function toggleSelectNote(id: string) {
    setSelectedNoteIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  /** 确认添加选中笔记到项目 */
  async function confirmAdd(p: Project) {
    if (selectedNoteIds.length === 0) {
      setAddError('请先勾选要添加的笔记')
      return
    }
    setAdding(true)
    setAddError('')
    try {
      await addNotesToProject(p.id, selectedNoteIds)
      closeAddPanel()
      await loadProjects()
      await refreshExpandedDetail(p)
    } catch (err) {
      console.error('添加笔记失败:', err)
      setAddError('添加笔记失败，请稍后重试')
    } finally {
      setAdding(false)
    }
  }

  return {
    addPanelProject,
    candidateNotes,
    selectedNoteIds,
    addSearch,
    setAddSearch,
    adding,
    addError,
    openAddPanel,
    closeAddPanel,
    toggleSelectNote,
    confirmAdd,
  }
}
