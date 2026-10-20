import { useTranslation } from '../../i18n';
/**
 * @file 项目卡片里的行内重命名编辑区
 * @description 自 `pages/Projects.tsx` 的 `renderCard` 拆分（overhaul-plan 5.5），
 * **只搬不改**：名称与描述两个输入框（名称那个不带额外样式，与卡片头部的同名输入框
 * 绑同一份草稿）、保存/取消两个按钮的类名与文案均逐字保留。
 */
interface ProjectRenameFormProps {
  name: string;
  description: string;
  onChangeName: (value: string) => void;
  onChangeDescription: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

export default function ProjectRenameForm({
  name,
  description,
  onChangeName,
  onChangeDescription,
  onSave,
  onCancel,
}: ProjectRenameFormProps) {
  const { t } = useTranslation();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <input
        value={name}
        onChange={(e) => onChangeName(e.target.value)}
        placeholder={t('projectPage.namePlaceholderShort')}
      />
      <textarea
        value={description}
        onChange={(e) => onChangeDescription(e.target.value)}
        placeholder={t('projectPage.descriptionLabel')}
        rows={2}
        style={{ resize: 'vertical' }}
      />
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          className="btn btn-primary"
          style={{ fontSize: '0.8rem', padding: '6px 14px' }}
          onClick={onSave}
        >
          {t('projectPage.save')}
        </button>
        <button
          className="btn btn-secondary"
          style={{ fontSize: '0.8rem', padding: '6px 14px' }}
          onClick={onCancel}
        >
          {t('projectPage.cancel')}
        </button>
      </div>
    </div>
  );
}
