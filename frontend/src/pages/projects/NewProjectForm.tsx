/**
 * @file 项目页的「新建项目」卡片（折叠态 + 展开态自管状态）
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 名称为空时**在前端拦住**（只提示「请输入项目名称」，不发请求）、
 * 创建成功后清空输入、收起表单并刷新列表（否则页面上看不到新项目）、
 * 失败时报「创建项目失败，请稍后重试」且不收起表单。
 *
 * 表单状态（是否展开、名称、描述、创建中、错误）原本就在页面组件里，
 * 这里放在同一个组件内自管：位置固定、不随列表刷新重挂载，状态生命周期与拆分前一致。
 */
import { useState } from 'react';
import { createProject } from '../../api/client';

interface NewProjectFormProps {
  /** 创建成功后刷新项目列表（`loadProjects`） */
  onCreated: () => Promise<void>;
}

export default function NewProjectForm({ onCreated }: NewProjectFormProps) {
  // 新建项目表单
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState('');

  /** 新建项目 */
  async function handleCreate() {
    const name = newName.trim();
    if (!name) {
      setFormError('请输入项目名称');
      return;
    }
    setCreating(true);
    setFormError('');
    try {
      await createProject(name, newDesc.trim() || undefined);
      setNewName('');
      setNewDesc('');
      setShowForm(false);
      await onCreated();
    } catch (err) {
      console.error('创建项目失败:', err);
      setFormError('创建项目失败，请稍后重试');
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 24, padding: 20 }}>
      {!showForm ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div>
            {/* `h2` 而不是 `h3`（a11y-audit **F-20**）：与 `ProjectCard` 同一次修复 ——
                这一页的 h1 是「项目」，这些卡片标题就是它的直接下级区块。
                字号 1rem / 字重 600 本来就显式钉着，**像素不变**。 */}
            <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>创建新项目</h2>
            <p
              style={{
                margin: '4px 0 0',
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
              }}
            >
              项目为纯标签，创建后不生成物理目录；用标签给笔记打归属
            </p>
          </div>
          <button className="btn btn-primary" onClick={() => setShowForm(true)}>
            ＋ 新建项目
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>新建项目</h2>
          <div>
            <label style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              项目名称
            </label>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="如：Transformer 论文精读"
              autoFocus
            />
          </div>
          <div>
            <label style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              项目描述（可选）
            </label>
            <textarea
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="这个项目是做什么的？"
              rows={2}
              style={{ resize: 'vertical' }}
            />
          </div>
          {formError && (
            <div style={{ color: 'var(--color-error)', fontSize: '0.8rem' }}>{formError}</div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
              {creating ? '创建中…' : '创建项目'}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setShowForm(false);
                setFormError('');
              }}
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
