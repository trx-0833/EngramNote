/**
 * @file 项目页底部的使用说明（常驻，不随列表状态消失）
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 * （批次 B3：`\u{1F4D6}` 📖 换成 `<Icon name="book" />`，正文逐字未动。）
 */
import Icon from '../../components/Icon';
import { useTranslation } from '../../i18n';

export default function ProjectsUsageNotes() {
  const { t } = useTranslation();
  return (
    <div
      className="card"
      style={{
        marginTop: 24,
        background: 'var(--color-bg)',
        border: '1px dashed var(--color-border)',
        fontSize: '0.85rem',
        color: 'var(--color-text-secondary)',
        lineHeight: 1.8,
      }}
    >
      <strong
        style={{
          color: 'var(--color-text)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        <Icon name="book" size={16} />
        {t('projectPage.usageTitle')}
      </strong>
      <ol style={{ margin: '8px 0 0 20px', padding: 0 }}>
        <li>{t('projectPage.usageTagOnly')}</li>
        <li>
          {t('projectPage.usageAllFiles')}{' '}
          <code style={{ color: 'var(--color-primary)' }}>source/</code>.
        </li>
        <li>
          {t('projectPage.usageCopyHint')}{' '}
          <code style={{ color: 'var(--color-primary)' }}>source/</code>{' '}
          {t('projectPage.usageScanHint')}
        </li>
        <li>{t('projectPage.usageUploadHint')}</li>
      </ol>
    </div>
  );
}
