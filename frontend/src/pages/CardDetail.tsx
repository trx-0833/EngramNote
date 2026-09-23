/**
 * @file 知识卡片详情页面
 * @description 展示单张知识卡片的完整内容、原始出处和关联题目，支持编辑和删除
 */
import { useCallback, useEffect, useState } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  getKnowledgeCard,
  updateKnowledgeCard,
  deleteKnowledgeCard,
  getQuestions,
  type KnowledgeCard,
  type QuizItem,
} from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorDisplay from '../components/ErrorDisplay';
import Icon from '../components/Icon';
// 页面标题（visual-refactor-plan 批次 C1）：本页的 h1 是**实体标题**
// （卡片自己的名字），字号本就 1.5rem，用 <PageHeader> 只是让它与全站
// 页面标题共用同一把量尺 —— 见下方调用点的说明
import PageHeader from '../components/PageHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import { cardTypeLabels, difficultyLabels, questionTypeLabels } from '../utils/labels';
import { useToast } from '../components/Toast';

export default function CardDetail() {
  const toast = useToast();
  const { cardId } = useParams<{ cardId: string }>();
  const navigate = useNavigate();
  const [card, setCard] = useState<KnowledgeCard | null>(null);
  const [questions, setQuestions] = useState<QuizItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAnswer, setShowAnswer] = useState<Record<string, boolean>>({});

  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  /** 删除确认框是否打开（批次 D3：原来是同步的 `confirm()`，改成对话框后由 state 承载） */
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const fetchCard = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getKnowledgeCard(cardId!);
      setCard(data);
      setEditTitle(data.title);
      setEditContent(data.content);
      const qData = await getQuestions(1, 20, data.note_id);
      const related = qData.items.filter((q) => q.card_id === cardId);
      setQuestions(related);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [cardId]);

  useEffect(() => {
    if (!cardId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchCard();
  }, [cardId, fetchCard]);

  async function handleSave() {
    if (!card) return;
    try {
      await updateKnowledgeCard(card.id, { title: editTitle, content: editContent });
      await fetchCard(); // 重新获取完整数据，避免 note_title 丢失
      setEditing(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    }
  }

  /**
   * 真正执行删除（批次 D3：原来这段紧跟在同步的 `confirm()` 之后，
   * 现在由确认框的 `onConfirm` 调用 —— 逐字保留，包括失败提示）
   */
  async function performDelete() {
    if (!card) return;
    try {
      await deleteKnowledgeCard(card.id);
      navigate('/cards');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败');
    }
  }

  /** 点「删除」：先开确认框，不碰数据 */
  function handleDelete() {
    if (!card) return;
    setConfirmingDelete(true);
  }

  function handleCancelEdit() {
    if (!card) return;
    setEditTitle(card.title);
    setEditContent(card.content);
    setEditing(false);
  }

  /** 安全解析题目选项 JSON */
  function parseOptions(optionsStr: string | null): string[] {
    if (!optionsStr) return [];
    try {
      const parsed = JSON.parse(optionsStr);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  if (loading) return <LoadingSpinner />;
  if (error || !card) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorDisplay message={error || '卡片不存在'} />
        <button className="btn btn-secondary" onClick={() => navigate('/cards')}>
          返回列表
        </button>
      </div>
    );
  }

  return (
    <div className="page-enter">
      {/* 标题 + 操作按钮行（批次 C1：统一进 <PageHeader>。
          窄屏换行原由全局 `.page-header-row` 负责，现随组件进模块）。
          ⚠️ 这里的标题是**实体标题** —— 显示的是这张知识卡片自己的名字，
          不是页面名（页面名是列表页的「知识卡片」）。本批不改这个语义：
          `card.title` 仍然是这一页的 `<h1>`，只是把量尺换成统一的那把
          （原字号 1.5rem + 衬线，**观感不变**）。 */}
      <PageHeader
        title={card.title}
        actions={
          <>
            {editing ? (
              <>
                <button className="btn btn-primary" onClick={handleSave}>
                  保存
                </button>
                <button className="btn btn-secondary" onClick={handleCancelEdit}>
                  取消
                </button>
              </>
            ) : (
              <>
                {/* 批次 B3：「编辑 / 删除」此前是全站纯文字按钮 —— 各补一枚图标 */}
                <button className="btn btn-secondary" onClick={() => setEditing(true)}>
                  <Icon name="edit" size={16} />
                  编辑
                </button>
                <button className="btn btn-danger" onClick={handleDelete}>
                  <Icon name="delete" size={16} />
                  删除
                </button>
              </>
            )}
            <button className="btn btn-secondary" onClick={() => navigate('/cards')}>
              返回
            </button>
          </>
        }
      />

      {/* 来源笔记链接：独立卡片（提升后的核心卡片）显示徽章，悬挂引用显示灰色占位 */}
      {card.note_id ? (
        <p style={{ marginBottom: 'var(--space-sm)', fontSize: '0.875rem' }}>
          来源笔记：
          {card.note_title === '已删除的笔记' ? (
            <span style={{ color: 'var(--color-text-secondary)' }}>[已删除的笔记]</span>
          ) : (
            /* ⚠️ 这里原来是 `span[onClick]`（没有 role/tabIndex）—— **键盘到不了**。
               现在它是真 `<Link>`（Tab 一次即达、可右键、可新标签页）。
               下划线**刻意保留**（全局默认，base.css）：它就在一行文字里
               （「来源笔记：<标题>」），属于 F-13 那一类 —— 链接必须不只靠颜色
               与周围文字区分，`link-in-text-block` 会判它（card-detail 场景有门禁）。 */
            <Link to={`/notes/${card.note_id}`} style={{ color: 'var(--color-primary)' }}>
              {card.note_title || '查看笔记'}
            </Link>
          )}
        </p>
      ) : (
        <p style={{ marginBottom: 'var(--space-sm)', fontSize: '0.875rem' }}>
          来源：
          <span
            style={{
              padding: '2px 8px',
              background: 'var(--color-accent-light)',
              // 压在 `--color-accent-light`（rgba(201,169,89,.12) 叠白 = #f9f5eb）
              // 上的是 0.75rem 的文字，`--color-accent`（#c9a959）在那里只有
              // **2.07:1**（要求 4.5:1）—— 与 a11y-audit 的 F-19/F-33 是同一个
              // "金色压浅底"的洞，只是这次不在 labels.ts 的颜色表里，是内联的。
              // `#7d6417` 是同一支金压深后的取值：对该底色 5.19:1、对白底 5.66:1。
              color: '#7d6417',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.75rem',
            }}
          >
            独立卡片
          </span>
        </p>
      )}

      {/* 卡片信息 */}
      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        <div
          style={{
            display: 'flex',
            gap: 'var(--space-sm)',
            marginBottom: 'var(--space-md)',
            fontSize: '0.875rem',
            color: 'var(--color-text-secondary)',
          }}
        >
          <span className="badge">{cardTypeLabels[card.card_type] || card.card_type}</span>
          {card.chapter_title && <span>章节: {card.chapter_title}</span>}
        </div>
        {editing ? (
          <div>
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
                display: 'block',
                marginBottom: 'var(--space-xs)',
              }}
            >
              标题
            </label>
            <input
              className="input"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              style={{ width: '100%', marginBottom: 'var(--space-md)' }}
            />
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
                display: 'block',
                marginBottom: 'var(--space-xs)',
              }}
            >
              内容
            </label>
            <textarea
              className="input"
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              style={{ width: '100%', minHeight: '200px', resize: 'vertical' }}
            />
          </div>
        ) : (
          <div style={{ lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{card.content}</div>
        )}
      </div>

      {/* 章节摘要。
          `h2` 而不是 `h3`：这一页的页面标题是卡片标题（h1），下面是**三个平级的
          区块**（章节摘要 / 原始出处 / 关联题目）—— 中间本来就不存在第三级，
          写成 h3 会让大纲变成 h1 → h3 跳级（axe 的 heading-order）。
          字号与字重都显式钉着（`fontSize: '1rem'` / `fontWeight: 600`），
          所以改级别**一个像素都没动** —— 这与已修的 F-14/F-15（`CardFace` 的
          h3→h2）、F-18（笔记列表卡片标题 h3→h2）是同一个做法。 */}
      {card.summary && !editing && (
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
            章节摘要
          </h2>
          <p style={{ color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>{card.summary}</p>
        </div>
      )}

      {/* 原始出处（与「章节摘要」同级，见上面的说明） */}
      {card.source_text && !editing && (
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
            原始出处
          </h2>
          <blockquote
            style={{
              borderLeft: '3px solid var(--color-primary)',
              paddingLeft: 'var(--space-md)',
              color: 'var(--color-text-secondary)',
              lineHeight: 1.6,
              margin: 0,
            }}
          >
            {card.source_text}
          </blockquote>
        </div>
      )}

      {/* 关联题目（同上：与「章节摘要」同级的区块标题） */}
      {questions.length > 0 && !editing && (
        <div className="card">
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>
            关联题目 ({questions.length})
          </h2>
          {questions.map((q, idx) => (
            <div
              key={q.id}
              style={{
                padding: 'var(--space-md)',
                borderBottom: idx < questions.length - 1 ? '1px solid var(--color-border)' : 'none',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: 'var(--space-sm)',
                  marginBottom: 'var(--space-sm)',
                  fontSize: '0.75rem',
                }}
              >
                <span className="badge">
                  {questionTypeLabels[q.question_type] || q.question_type}
                </span>
                <span className="badge">{difficultyLabels[q.difficulty] || q.difficulty}</span>
              </div>
              <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>
                {idx + 1}. {q.question}
              </p>
              {q.options &&
                parseOptions(q.options).map((opt, i) => (
                  <p key={i} style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                    {opt}
                  </p>
                ))}
              <button
                className="btn btn-secondary"
                style={{ fontSize: '0.8rem' }}
                onClick={() => setShowAnswer((prev) => ({ ...prev, [q.id]: !prev[q.id] }))}
              >
                {showAnswer[q.id] ? '隐藏答案' : '显示答案'}
              </button>
              {showAnswer[q.id] && (
                <div
                  style={{
                    marginTop: 'var(--space-sm)',
                    padding: 'var(--space-sm)',
                    background: 'var(--color-surface)',
                    borderRadius: '4px',
                  }}
                >
                  <p style={{ color: 'var(--color-success)', fontWeight: 500 }}>答案: {q.answer}</p>
                  {q.explanation && (
                    <p
                      style={{
                        fontSize: '0.875rem',
                        color: 'var(--color-text-secondary)',
                        marginTop: 'var(--space-xs)',
                      }}
                    >
                      解析: {q.explanation}
                    </p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 删除确认框（批次 D3）：文案逐字保留原来 `confirm()` 的那一句。
          `onConfirm` 先关框再执行（见 `ConfirmDialog` 文件头），取消则什么都不做。
          执行期间**故意不加** loading —— 原来 `confirm()` 之后那段也没有。 */}
      <ConfirmDialog
        open={confirmingDelete}
        title="确定删除此知识卡片？"
        message="将同时删除关联的练习题目、复习记录和知识图谱关系。此操作不可恢复。"
        confirmText="删除"
        danger
        onConfirm={() => {
          setConfirmingDelete(false);
          void performDelete();
        }}
        onCancel={() => setConfirmingDelete(false)}
      />
    </div>
  );
}
