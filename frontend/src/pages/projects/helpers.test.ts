/**
 * @file 项目页纯辅助的单元测试：列表归一化的"容忍 + 可见"，以及文件大小格式化的语义
 *
 * 三组断言，对应三件容易做错的事：
 *
 * 1. **形状漂移必须既容忍又报出来**（overhaul-plan AZ.7）：`/projects` 与 `/projects/{id}`
 *    声明返回数组，被包成 `{items:[…]}` 时数据其实在 —— 照常渲染 + 上报漂移；
 *    真正非数组才降级为空列表 + 上报。`(c) 正常响应不报` 与 `(a)(b)` 同等重要：
 *    一个总在响的提示等于没有提示。
 * 2. `/notes` 是**分页对象**、不是包装对象：`items` 是约定字段，正常分页响应**不许**报漂移
 *    （报错了就等于每次打开"添加笔记"面板都弹一条无用提示）。
 * 3. `formatSize(0)`：0 字节是**真实大小**，`—` 只能留给"没有值/不是有效数字"。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Note, NoteInFolder, Project } from '../../api/client'
import {
  resetContractDriftNotices,
  subscribeContractDrift,
  type ContractDriftEvent,
} from '../contractDrift'
import { formatSize, unwrapCandidateNotes, unwrapProjectNotes, unwrapProjects } from './helpers'

function makeProject(over: Partial<Project> = {}): Project {
  return {
    id: 'p-1',
    user_id: 'u-1',
    name: 'Transformer 论文精读',
    description: '把注意力机制相关的论文读一遍',
    note_count: 2,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    ...over,
  }
}

function makeNoteInFolder(over: Partial<NoteInFolder> = {}): NoteInFolder {
  return {
    id: 'n-1',
    title: 'Attention Is All You Need',
    source_type: 'pdf',
    status: 'cleaned',
    file_size: 2048,
    created_at: '2026-09-01T10:00:00Z',
    ...over,
  }
}

function makeNote(over: Partial<Note> = {}): Note {
  return {
    id: 'n-1',
    user_id: 'u-1',
    title: 'Attention Is All You Need',
    source_type: 'pdf',
    status: 'cleaned',
    file_size: 1024,
    page_count: 15,
    error_message: null,
    trashed_at: null,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    ...over,
  }
}

// ── 漂移事件的收集：订阅是唯一的观察点 ──
let drifts: ContractDriftEvent[] = []
let unsubscribe: (() => void) | null = null

beforeEach(() => {
  // 去重键是模块级状态，会跨用例存活：不清就会让后面的用例"提示没出现"假红
  resetContractDriftNotices()
  drifts = []
  unsubscribe = subscribeContractDrift((event) => drifts.push(event))
})

afterEach(() => {
  unsubscribe?.()
  unsubscribe = null
})

describe('unwrapProjects', () => {
  it('(a) 包装载荷：拆包后照常返回项目，并报出 wrapper 漂移', () => {
    const project = makeProject()

    expect(unwrapProjects({ items: [project] })).toEqual([project])
    expect(drifts).toHaveLength(1)
    expect(drifts[0]).toMatchObject({ kind: 'wrapper', source: 'GET /projects' })
  })

  it('(b) 非数组载荷：归一成空列表，并报出 non-array 漂移', () => {
    expect(unwrapProjects({})).toEqual([])
    expect(drifts).toHaveLength(1)
    expect(drifts[0]).toMatchObject({ kind: 'non-array', source: 'GET /projects' })
  })

  it('(b) 204/空响应体（undefined）同样算非数组，不能当成"正常的空列表"', () => {
    expect(unwrapProjects(undefined)).toEqual([])
    expect(drifts).toHaveLength(1)
    expect(drifts[0].kind).toBe('non-array')
  })

  it('(c) 正常数组：原样返回，一条漂移都不报', () => {
    const project = makeProject()

    expect(unwrapProjects([project])).toEqual([project])
    expect(drifts).toEqual([])
  })

  it('(c) 正常的空数组（真的没有项目）不报 —— "空"不是漂移', () => {
    expect(unwrapProjects([])).toEqual([])
    expect(drifts).toEqual([])
  })

  it('去重：列表被反复重拉（每次写操作后都会重拉）也只上报一次', () => {
    for (let i = 0; i < 6; i += 1) {
      unwrapProjects({ items: [] })
    }

    expect(drifts).toHaveLength(1)
  })
})

describe('unwrapProjectNotes', () => {
  it('(a) 包装载荷：拆包后照常返回笔记，并报出 wrapper 漂移', () => {
    const note = makeNoteInFolder()

    expect(unwrapProjectNotes({ items: [note] })).toEqual([note])
    expect(drifts).toHaveLength(1)
    expect(drifts[0]).toMatchObject({ kind: 'wrapper', source: 'GET /projects/{id}' })
  })

  it('(b) notes 字段缺失（undefined）时归一成空列表并报出非数组', () => {
    expect(unwrapProjectNotes(undefined)).toEqual([])
    expect(drifts).toHaveLength(1)
    expect(drifts[0].kind).toBe('non-array')
  })

  it('(c) 正常数组：原样返回，一条漂移都不报', () => {
    const note = makeNoteInFolder()

    expect(unwrapProjectNotes([note])).toEqual([note])
    expect(drifts).toEqual([])
  })
})

describe('unwrapCandidateNotes（/notes 是分页对象，items 是约定字段）', () => {
  it('(c) 正常分页响应**不是**漂移：items 就在约定位置，一条都不报', () => {
    const note = makeNote()

    const result = unwrapCandidateNotes({ items: [note], total: 1, page: 1, page_size: 999 })

    expect(result).toEqual([note])
    expect(drifts).toEqual([])
  })

  it('(b) items 缺失/类型不对：归一成空候选并报出 items 字段（原来会抛错）', () => {
    expect(unwrapCandidateNotes({ total: 0 })).toEqual([])
    expect(unwrapCandidateNotes({ items: {} })).toEqual([])
    expect(unwrapCandidateNotes(undefined)).toEqual([])

    expect(drifts).toHaveLength(1) // 同一处漂移只报一次
    expect(drifts[0]).toMatchObject({ kind: 'non-array', source: 'GET /notes', field: 'items' })
  })
})

describe('formatSize', () => {
  it('0 字节是真实大小，不是"未知大小"', () => {
    expect(formatSize(0)).toBe('0 B')
  })

  it('B / KB / MB 三个档位的换算不变', () => {
    expect(formatSize(1)).toBe('1 B')
    expect(formatSize(1023)).toBe('1023 B')
    expect(formatSize(1024)).toBe('1.0 KB')
    expect(formatSize(2048)).toBe('2.0 KB')
    expect(formatSize(3 * 1024 * 1024)).toBe('3.0 MB')
  })

  it('只有"没有值 / 不是有效数字"才显示 —', () => {
    expect(formatSize(null)).toBe('—')
    expect(formatSize(undefined)).toBe('—')
    expect(formatSize(Number.NaN)).toBe('—')
  })
})
