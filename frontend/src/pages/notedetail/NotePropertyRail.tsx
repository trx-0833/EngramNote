/**
 * @file 笔记详情页的元信息竖轨（批次 E2）
 * @description 借鉴表 `docs/visual-symbol-research.md` §C3「笔记详情」行的
 * **Memos property rail**：元信息收进主内容左侧一条**窄竖轨**，
 * 让主内容区保持纯净。
 *
 * ## 它装的是什么（逐字搬来，不是重写）
 *
 * 这一块原本是 `NoteDetailHeader.tsx` 里的「笔记元信息标签行」——
 * 来源类型徽章 / 所属项目标签 / 处理状态 / **笔记角色下拉** /
 * 页数 / 文件大小 / 创建时间。搬过来之后：
 *
 * | 项 | 搬动前 | 搬动后 |
 * |---|---|---|
 * | 容器 | `<header>` 里的第二个 `<div>`（横排、`gap: --space-md`） | `<aside aria-label="笔记元信息">`（竖排、`gap: --space-sm`） |
 * | 每一项的类名 | `badge badge-<type>` / `badge` / `statusClass(...)` | **一字未变** |
 * | 每一项的文案 | 原样 | **一字未变** |
 * | 原生控件 | `<select aria-label="笔记角色">` | **同名同角色**（仍是 `combobox`，`aria-label` 仍是「笔记角色」） |
 * | 回调 | `onRoleUpdated`（`NoteDetailHeader` 接） | `onRoleUpdated`（本组件接）—— 仍是 `updateNoteRole` → `setNote` 局部合并 |
 *
 * 也就是说：**读屏/键盘看到的东西一个都没变**，变的只有它们在版面上的位置
 * 与排列方向。
 *
 * ## 为什么是 `<aside>` 而不是 `<div>`
 *
 * 它是与主内容并列的一条侧轨，`aside` 是这件事的标准元素。实测过 axe 的
 * `landmark-complementary-is-top-level`：`<main>` 内的 `complementary`
 * 是**明确豁免**的（`axe.js` 的 `landmarkIsTopLevelEvaluate` 里有
 * `!(role === 'main' && nodeRole === 'complementary')`），所以它不会
 * 变成一条新的 best-practice 违规。`aria-label` 给这条轨一个可跳转的名字。
 *
 * ## 角色下拉：外观仍全部在样式表里
 *
 * 它是本页唯一一个**会改数据**的原生表单控件。原先住在
 * `NoteDetailHeader.module.css` 的 `.roleSelect`（含"白内圈 + 墨色外圈"两层
 * 焦点环、以及为什么必须用 `box-shadow` 而不是 `outline-offset` 的完整论证）
 * —— 那一段随控件整体搬到 `NotePropertyRail.module.css`，值一个字没改。
 * `--note-role-bg` 仍由 tsx 传进去（两个色值是角色语义的一部分，留在 tsx 可见）。
 */
import type { CSSProperties } from 'react';
import { updateNoteRole, type NoteDetail } from '../../api/client';
import { useToast } from '../../components/Toast';
import { formatDateTime } from '../../utils/datetime';
import { statusClass, statusLabels } from '../../utils/labels';
import styles from './NotePropertyRail.module.css';

interface NotePropertyRailProps {
  /** 笔记详情（与页面同一个对象，未做拷贝） */
  note: NoteDetail;
  /**
   * 局部合并角色更新结果
   *
   * 阶段 5.1 / S2：`note_role` 在契约里**带默认值**（`material`）→ 生成类型里
   * 是必填 `string`，"可能是 undefined"这个假设不成立，因此去掉 `| undefined`。
   */
  onRoleUpdated: (noteRole: string) => void;
}

/** 笔记详情的元信息竖轨 */
export default function NotePropertyRail({ note, onRoleUpdated }: NotePropertyRailProps) {
  const toast = useToast();

  return (
    <aside className={styles.rail} aria-label="笔记元信息">
      <span className={`badge badge-${note.source_type}`}>{note.source_type.toUpperCase()}</span>
      {/* 所属项目标签：色值与 NotesList 的同一枚标签保持一致
          （`--color-primary-soft` 在本项目未定义，实际落到兜底 #eef2ff；
          而兜底前景 #2563eb 与它只有 4.62:1，12px 小字压在门槛线上 ——
          改用同色系的 #1b4fbf，5.49:1。两处必须一起改，否则同一枚标签
          在两个页面上是两个颜色）。 */}
      {note.project_names?.map((name) => (
        <span
          key={name}
          className="badge"
          style={{ backgroundColor: 'var(--color-primary-soft, #eef2ff)', color: '#1b4fbf' }}
        >
          {name}
        </span>
      ))}
      <span className={statusClass(note.status)}>{statusLabels[note.status] || note.status}</span>
      {/* 笔记角色：本页唯一会写数据的原生控件。
          - 可访问名用 `aria-label`（竖轨是一列标签，插一个可见 <label>
            会多出一行文字，而这一批不改文案）；
          - 外观在 NotePropertyRail.module.css：内联样式的权重高于任何
            选择器，`outline: 'none'` 留在 tsx 里的话，样式表中的
            `:focus-visible` 焦点环**永远不会生效**（模块文件头有完整说明）；
          - 底色随角色变，通过 `--note-role-bg` 传进去，两个色值仍在 tsx 里可见。 */}
      <select
        className={styles.roleSelect}
        aria-label="笔记角色"
        value={note.note_role || 'material'}
        onChange={async (e) => {
          try {
            const updated = await updateNoteRole(note.id, e.target.value);
            onRoleUpdated(updated.note_role);
          } catch (err) {
            toast.error(err instanceof Error ? err.message : '更新角色失败');
          }
        }}
        style={
          {
            // #316fd8 白底白字 4.78:1、#6d28d9 为 7.10:1（原先 #3b82f6 只有 3.68:1）
            '--note-role-bg': note.note_role === 'personal_note' ? '#6d28d9' : '#316fd8',
          } as CSSProperties
        }
      >
        <option value="material">学习资料</option>
        <option value="personal_note">我的笔记</option>
      </select>
      {note.page_count && <span>{note.page_count} 页</span>}
      <span>{(note.file_size / 1024).toFixed(0)} KB</span>
      <span>创建于 {formatDateTime(note.created_at)}</span>
    </aside>
  );
}
