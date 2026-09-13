/**
 * @file 项目页的数据层：列表加载、重命名、删除、展开详情、扫描导入
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 失败文案（如「加载项目列表失败，请稍后重试」「项目名称不能为空」）、
 * `console.error` 的日志点、`window.confirm` 的确认文案、以及
 * "删除项目只移除标签、笔记与文件保留"的语义全部逐字保留。
 *
 * 破坏性操作（删除项目 / 移出笔记）的二次确认在各自的处理器里，
 * 取消时**不发请求**；扫描导入只在 `imported > 0` 时刷新列表。
 */
import { useEffect, useState } from 'react'
import {
  deleteProject,
  getProjectDetail,
  getProjects,
  removeNoteFromProject,
  scanProject,
  updateProject,
  type NoteInFolder,
  type Project,
  type ProjectDetail,
  type ScanImportResponse,
} from '../../api/client'
import { unwrapProjects } from './helpers'

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 重命名（行内编辑）
  const [renaming, setRenaming] = useState<Record<string, { name: string; description: string }>>({})

  // 展开的笔记列表
  const [expanded, setExpanded] = useState<Record<string, ProjectDetail | null>>({})

  // 扫描导入
  const [scanning, setScanning] = useState<Record<string, boolean>>({})
  const [scanResults, setScanResults] = useState<Record<string, ScanImportResponse | null>>({})

  /** 加载项目列表 */
  async function loadProjects() {
    setLoading(true)
    setError('')
    try {
      const data = await getProjects()
      // 契约漂移兜底见 unwrapProjects：/projects 可能回 204/空体或 {items:[...]} 包装
      setProjects(unwrapProjects(data))
    } catch (err) {
      console.error('加载项目列表失败:', err)
      setError('加载项目列表失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadProjects()
  }, [])

  /** 开始重命名 */
  function startRename(p: Project) {
    setRenaming((prev) => ({ ...prev, [p.id]: { name: p.name, description: p.description ?? '' } }))
  }

  /** 更新行内编辑草稿（名称或描述） */
  function updateRenameField(p: Project, field: 'name' | 'description', value: string) {
    setRenaming((prev) => ({ ...prev, [p.id]: { ...prev[p.id], [field]: value } }))
  }

  /** 提交重命名 */
  async function handleRename(p: Project) {
    const edit = renaming[p.id]
    if (!edit) return
    const name = edit.name.trim()
    if (!name) {
      setError('项目名称不能为空')
      return
    }
    try {
      await updateProject(p.id, edit.name.trim(), edit.description.trim() || undefined)
      setRenaming((prev) => {
        const next = { ...prev }
        delete next[p.id]
        return next
      })
      await loadProjects()
    } catch (err) {
      console.error('重命名项目失败:', err)
      setError('重命名项目失败，请稍后重试')
    }
  }

  /** 取消重命名 */
  function cancelRename(p: Project) {
    setRenaming((prev) => {
      const next = { ...prev }
      delete next[p.id]
      return next
    })
  }

  /** 删除项目（只删标签，笔记与文件保留） */
  async function handleDelete(p: Project) {
    if (!window.confirm(`确定删除项目「${p.name}」？删除仅移除该项目标签，关联笔记与文件都会保留。`)) {
      return
    }
    try {
      await deleteProject(p.id)
      await loadProjects()
    } catch (err) {
      console.error('删除项目失败:', err)
      setError('删除项目失败，请稍后重试')
    }
  }

  /** 展开/收起笔记列表 */
  async function toggleExpand(p: Project) {
    if (expanded[p.id]) {
      setExpanded((prev) => {
        const next = { ...prev }
        delete next[p.id]
        return next
      })
      return
    }
    try {
      const detail = await getProjectDetail(p.id)
      setExpanded((prev) => ({ ...prev, [p.id]: detail }))
    } catch (err) {
      console.error('加载项目详情失败:', err)
      setError('加载项目笔记失败，请稍后重试')
    }
  }

  /** 扫描收件箱 source/ 目录并打上当前项目标签 */
  async function handleScan(p: Project) {
    setScanning((prev) => ({ ...prev, [p.id]: true }))
    setScanResults((prev) => ({ ...prev, [p.id]: null }))
    try {
      const result = await scanProject(p.id)
      setScanResults((prev) => ({ ...prev, [p.id]: result }))
      // 有新导入时刷新项目笔记数
      if (result.imported > 0) {
        await loadProjects()
      }
    } catch (err) {
      console.error('扫描导入失败:', err)
      setError('扫描导入失败，请确认后端服务可用后重试')
    } finally {
      setScanning((prev) => {
        const next = { ...prev }
        delete next[p.id]
        return next
      })
    }
  }

  /** 若项目处于展开状态，重新拉取详情以同步笔记列表 */
  async function refreshExpandedDetail(p: Project) {
    if (!expanded[p.id]) return
    try {
      const detail = await getProjectDetail(p.id)
      setExpanded((prev) => ({ ...prev, [p.id]: detail }))
    } catch (err) {
      console.error('刷新项目详情失败:', err)
    }
  }

  /** 将笔记移出项目（破坏性操作：先 confirm，取消则不发请求） */
  async function handleRemoveNote(p: Project, n: NoteInFolder) {
    if (!window.confirm(`确定将笔记「${n.title}」移出项目「${p.name}」？`)) {
      return
    }
    try {
      await removeNoteFromProject(p.id, n.id)
      await loadProjects()
      await refreshExpandedDetail(p)
    } catch (err) {
      console.error('移出笔记失败:', err)
      setError('移出笔记失败，请稍后重试')
    }
  }

  /** 关闭页面级错误提示 */
  function clearError() {
    setError('')
  }

  return {
    projects,
    loading,
    error,
    clearError,
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
  }
}
