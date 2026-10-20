/**
 * @file 笔记详情页的元信息条
 * @description
 * 批次 E2 时它是主内容**左侧**一条常驻竖轨（Memos property rail，220px、
 * `position: sticky`）。2026-10-18 用户实测反馈改回**横排一条**：
 *
 * > 笔记内容页的这些信息，在左侧位置有点占地方，上下滑动窗口时，
 * > 不会腾出它占用的这一列，因此重新排列这段信息。
 *
 * 也就是说 sticky 那一列**整篇笔记都在占着 220px**，正文被永久挤到右边。
 * 而窄屏档（≤768px）本来就把它渲染成"正文上方的一横排"—— 现在等于
 * **把窄屏那套排版推广到所有宽度**，版式反而更统一。
 *
 * ## 它装的是什么（逐字搬来，不是重写）
 *
 * 来源类型徽章 / 所属项目标签 / 处理状态 / **笔记角色下拉** /
 * 页数 / 文件大小 / 创建时间。从批次 E2 至今，每一项的类名、文案、顺序、
 * 可访问名**一字未变**（唯一的原生控件「笔记角色」下拉仍是
 * `aria-label="笔记角色"`，仍是 `combobox`）。
 *
 * ## 语义：从 `<aside aria-label>` 改成 `<ul>`
 *
 * 批次 E2 用 `<aside aria-label="笔记元信息">` 是因为它当时是**与主内容并列的
 * 一条侧轨**（`aside`/`complementary` 正是这件事的标准元素，当时还实测过
 * axe 的 `landmark-complementary-is-top-level` 在 `<main>` 内是豁免的）。
 *
 * 现在它不再是"旁"的内容，而是**标题下面的一条元信息**，`complementary`
 * 这个地标语义已经名不副实。改用 `<ul>`/`<li>`：
 *
 * | | `<aside aria-label>` | `<ul>` |
 * |---|---|---|
 * | 语义 | complementary 地标（"与主内容相关但可独立"） | 一列条目 —— 正是元信息 |
 * | 屏幕阅读器 | "补充信息 地标，笔记元信息" | "列表，7 项"（读屏直接报**项数**） |
 * | 依赖 | 依赖 `aria-label` 才有名字 | 原生元素，**不依赖 ARIA** |
 * | 列表语义 | 无 | 有（"第 3 项，共 7 项"可跳读） |
 *
 * `aria-label="笔记元信息"` **保留**：给这个列表一个名字，读屏会读成
 * "笔记元信息 列表"。它不再是地标，但名字仍然有用。
 *
 * ## 角色下拉：外观仍全部在样式表里
 *
 * 它是本页唯一一个**会改数据**的原生表单控件。原先住在
 * `NoteDetailHeader.module.css` 的 `.roleSelect`（含"白内圈 + 墨色外圈"两层
 * 焦点环、以及为什么必须用 `box-shadow` 而不是 `outline-offset` 的完整论证）
 * —— 那一段随控件整体搬进 `NotePropertyRail.module.css`，值一个字没改。
 * `--note-role-bg` 仍由 tsx 传进去（两个色值是角色语义的一部分，留在 tsx 可见）。
 */
import type { CSSProperties } from 'react';
import { updateNoteRole, type NoteDetail } from '../../api/client';
import { useToast } from '../../components/Toast';
import { formatDate } from '../../utils/datetime';
import { statusClass, useLabels } from '../../utils/labels';
import styles from './NotePropertyRail.module.css';
import { useTranslation } from '../../i18n';

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

/**
 * 笔记详情的元信息条
 *
 * ⚠️ 组件名与文件名仍叫 `Rail`（竖轨），因为改名要同时动
 * `NoteDetail.tsx` 的 import、`NotePropertyRail.module.css` 的文件名与
 * 若干注释 —— 那是"搬家"，与本次"改版式"是两件事，不混在一起。
 * 名字与现状的偏差已在此处写明，不留给下一个人去猜。
 */
export default function NotePropertyRail({ note, onRoleUpdated }: NotePropertyRailProps) {
  const { t } = useTranslation();
  const { statusLabels } = useLabels();
  const toast = useToast();

  return (
    <ul className={styles.rail} aria-label={t('noteAnnotations.railLabel')}>
      <li>
        <span className={`badge badge-${note.source_type}`}>{note.source_type.toUpperCase()}</span>
      </li>
      {/* 所属项目标签：色值与 NotesList 的同一枚标签保持一致
          （`--color-primary-soft` 在本项目未定义，实际落到兜底 #eef2ff；
          而兜底前景 #2563eb 与它只有 4.62:1，12px 小字压在门槛线上 ——
          改用同色系的 #1b4fbf，5.49:1。两处必须一起改，否则同一枚标签
          在两个页面上是两个颜色）。 */}
      {note.project_names?.map((name) => (
        <li key={name}>
          <span
            className="badge"
            style={{ backgroundColor: 'var(--color-primary-soft, #eef2ff)', color: '#1b4fbf' }}
          >
            {name}
          </span>
        </li>
      ))}
      <li>
        <span className={statusClass(note.status)}>{statusLabels[note.status] || note.status}</span>
      </li>
      {/* 笔记角色：本页唯一会写数据的原生控件。
          - 可访问名用 `aria-label`（这一条是标签，插一个可见 <label>
            会多出一段文字，而这一批不改文案）；
          - 外观在 NotePropertyRail.module.css：内联样式的权重高于任何
            选择器，`outline: 'none'` 留在 tsx 里的话，样式表中的
            `:focus-visible` 焦点环**永远不会生效**（模块文件头有完整说明）；
          - 底色随角色变，通过 `--note-role-bg` 传进去，两个色值仍在 tsx 里可见。 */}
      <li>
        <select
          className={styles.roleSelect}
          aria-label={t('noteAnnotations.roleLabel')}
          value={note.note_role || 'material'}
          onChange={async (e) => {
            try {
              const updated = await updateNoteRole(note.id, e.target.value);
              onRoleUpdated(updated.note_role);
            } catch (err) {
              toast.error(
                err instanceof Error ? err.message : t('noteAnnotations.roleUpdateFailed'),
              );
            }
          }}
          style={
            {
              // #316fd8 白底白字 4.78:1、#6d28d9 为 7.10:1（原先 #3b82f6 只有 3.68:1）
              '--note-role-bg': note.note_role === 'personal_note' ? '#6d28d9' : '#316fd8',
            } as CSSProperties
          }
        >
          <option value="material">{t('labels.noteRole.material')}</option>
          <option value="personal_note">{t('labels.noteRole.personal_note')}</option>
        </select>
      </li>
      {/* 规格 + 日期合成**一项**（2026-10-19 第三次改版）。
          
          ## 为什么合并
          
          人眼核对真实页面（1600 / 1280 / 768 三档截图）后定的：原来
          「9 页」「3004 KB」「创建于 2026/8/28 GMT+8 19:47:18」是**三个并列项**，
          与徽章、状态、下拉框同样字号、同样视觉重量，于是这一行读起来
          是七段等重的碎片；而末尾那段时间戳（`GMT+8 19:47:18`）是整行最长的
          一段，却几乎没人需要"这篇笔记是几点几分导入的"。
          
          合并成一段**三级文字**（`.railMeta`）之后：语义分组清楚了
          （徽章=身份、状态、控件、规格=次级），这一行也短了一大截。
          
          ⚠️ 用 `formatDate`（只到日）而不是 `formatDateTime`：后者会带上
          `GMT+8 19:47:18`。这个函数是 `utils/datetime.ts` 里**既有**的导出，
          本轮没有新增任何工具函数。
          
          ⚠️ `page_count` 为空（音频、Markdown 笔记）时**整段省略**，
          不写"0 页" —— 那会把"没有页数这个概念"说成"零页"。
          与 `page_count` 的真值判断一致（`0` 也不显示，因为 0 页不是有效值）。 */}
      {(note.page_count || note.file_size) && (
        <li className={styles.railMeta}>
          {[
            note.page_count ? t('noteAnnotations.pageCount', { count: note.page_count }) : null,
            note.file_size ? `${(note.file_size / 1024).toFixed(0)} KB` : null,
            t('noteAnnotations.createdAt', { date: formatDate(note.created_at) }),
          ]
            .filter(Boolean)
            .join(' · ')}
        </li>
      )}
    </ul>
  );
}
