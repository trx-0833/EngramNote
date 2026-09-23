/**
 * @file 笔记链接关系（关联资料 / 被引用）的页面级 hook
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * effect 依赖数组、`console.error` 文案、toast 文案、接口调用顺序均与拆分前一致。
 */
import { useEffect, useState } from 'react';
import {
  getNoteLinks,
  getNotes,
  updateNoteLinks,
  type Note,
  type NoteLinksResponse,
} from '../../api/client';
import { useToast } from '../../components/Toast';

interface UseNoteLinksOptions {
  /** 当前笔记 ID（未加载完成时为 undefined） */
  noteId: string | undefined;
  /**
   * 笔记角色（material / personal_note）
   *
   * 仅用于"加载链接后是否预填已关联资料"，与拆分前一样**不**进 effect 依赖：
   * role 随 note 变化，effect 已由 note?.id 驱动，补 role 依赖会重复触发。
   */
  noteRole: string | undefined;
}

/**
 * 管理笔记的关联资料：数据加载、管理弹窗状态、保存与悬挂链接清理。
 */
export function useNoteLinks({ noteId, noteRole }: UseNoteLinksOptions) {
  const toast = useToast();

  /** 链接管理相关 state */
  const [noteLinks, setNoteLinks] = useState<NoteLinksResponse | null>(null);
  const [showLinkManager, setShowLinkManager] = useState(false);
  const [linkMaterialIds, setLinkMaterialIds] = useState<string[]>([]);
  const [availableMaterials, setAvailableMaterials] = useState<Note[]>([]);

  // 加载链接关系：当 note 加载完成后获取其关联的资料/被引用笔记
  // note.note_role 决定查询方向,但 role 变化依赖 note 变化,effect 已由 note?.id 驱动,
  // 补 role 依赖会与业务语义重复触发,故豁免 exhaustive-deps
  useEffect(() => {
    if (!noteId) return;
    const loadLinks = async () => {
      try {
        const links = await getNoteLinks(noteId);
        setNoteLinks(links);
        if (noteRole === 'personal_note') {
          setLinkMaterialIds(links.linked_materials.map((m) => m.id));
        }
      } catch (err) {
        console.error('加载链接关系失败:', err);
      }
    };
    loadLinks();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- role 随 note 变化,effect 由 note?.id 驱动
  }, [noteId]);

  /** 打开"管理关联资料"弹窗，加载可关联的学习资料列表 */
  async function handleManageLinks() {
    if (!noteId) return;
    try {
      const data = await getNotes(1, 100, undefined, 'material');
      setAvailableMaterials(data.items || []);
      setShowLinkManager(true);
    } catch {
      toast.error('加载资料列表失败');
    }
  }

  /** 保存关联资料修改 */
  async function handleSaveLinks() {
    if (!noteId) return;
    try {
      const result = await updateNoteLinks(noteId, linkMaterialIds);
      if (result.changed) {
        // 重新加载链接
        const links = await getNoteLinks(noteId);
        setNoteLinks(links);
        toast.success('关联资料已更新');
      } else {
        toast.error('关联资料未变化');
      }
      setShowLinkManager(false);
    } catch {
      toast.error('保存关联失败');
    }
  }

  /** 清理悬挂链接：以当前有效资料全量覆盖，自动剔除资料端为 NULL 的悬挂行 */
  async function handleCleanDanglingLinks() {
    if (!noteId || !noteLinks) return;
    try {
      await updateNoteLinks(
        noteId,
        (noteLinks.linked_materials ?? []).map((m) => m.id),
      );
      const links = await getNoteLinks(noteId);
      setNoteLinks(links);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '清理链接失败');
    }
  }

  return {
    noteLinks,
    showLinkManager,
    setShowLinkManager,
    linkMaterialIds,
    setLinkMaterialIds,
    availableMaterials,
    handleManageLinks,
    handleSaveLinks,
    handleCleanDanglingLinks,
  };
}
