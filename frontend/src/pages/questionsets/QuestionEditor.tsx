/**
 * 题目行内编辑 / 删除（B5，2026-10-22）
 *
 * ## 为什么需要它
 *
 * 改造前题目**只有 GET**（卡片有 PUT/DELETE，题目没有）。后果：模型出一道
 * 坏题时用户**没有任何入口**修它，只能整篇"重新理解"（那会删掉该笔记的
 * 全部卡片/题目/复习记录）。而 A2/A3′ 重跑之后题库会整批换掉 ——
 * 没有审核入口就无从对比新旧、也无从留下好的那几道。
 *
 * ## 为什么放在**展开的答案区**里，而不是每一行都放按钮
 *
 * 表格里一行一题，每题都挂两个按钮会让每行宽度翻倍（这个页面是 5 列 +
 * 操作列的密集表）。而用户判断"这道题要不要改"恰恰要先看答案 ——
 * 他展开答案区时就已经在读了，操作跟着出现在那里最省事。
 *
 * ## 选项用"一行一个"的 textarea，而不是动态增删的输入框列表
 *
 * 与库里的存储形态一致（`options` 是 JSON 字符串数组），也和模型产出的
 * 格式一致（`A.甲` / `B.乙`）。动态列表要多处理"删到只剩一个怎么办"、
 * "焦点跑到哪"这类细节，而收益只是少按一次回车。
 *
 * ## 保存是 **PUT 全量替换**
 *
 * 四个字段一起提交，`explanation` 清空就是清空 —— 没有"没传 vs 显式 null"
 * 的二义性（B3 那个坑的反面）。
 */
import { useState } from 'react';

import { deleteQuestion, updateQuestion, type QuizItem } from '../../api/client';
import { useConfirm } from '../../components/ConfirmProvider';
import { useToast } from '../../components/Toast';
import { useTranslation } from '../../i18n';
import styles from './QuestionEditor.module.css';

/** 把库里存的 `options`（JSON 字符串数组）解析成多行文本 */
export function optionsToText(options: string | null | undefined): string {
  if (!options) return '';
  try {
    const parsed = JSON.parse(options);
    if (Array.isArray(parsed)) {
      return parsed.map((v) => String(v)).join('\n');
    }
  } catch {
    // 库里可能存着历史脏数据（不是 JSON）—— 原样当一行文本，
    // 而不是把它吞掉（吞掉之后再保存就把用户看不到的内容删了）
    return String(options);
  }
  return '';
}

/** 多行文本 → 选项数组（丢掉纯空白行） */
export function textToOptions(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/**
 * 编辑态的初始值。
 *
 * 单独抽出来是为了让"打开编辑器"是一个**纯函数**，用例可以直接验
 * 而不必渲染整个页面。
 */
export function draftFromQuestion(q: QuizItem) {
  return {
    question: q.question ?? '',
    answer: q.answer ?? '',
    optionsText: optionsToText(q.options as string | null | undefined),
    explanation: q.explanation ?? '',
  };
}

export function QuestionEditor({
  question,
  onSaved,
  onCancel,
}: {
  question: QuizItem;
  /** 保存成功后回调（页面据此重新拉列表 / 就用返回的行替换） */
  onSaved: (updated: QuizItem) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const confirm = useConfirm();

  const [draft, setDraft] = useState(() => draftFromQuestion(question));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const isChoice = question.question_type === 'choice';

  function set<K extends keyof typeof draft>(key: K, value: string) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    const q = draft.question.trim();
    const a = draft.answer.trim();
    if (!q || !a) {
      // 空的题干/答案是死题 —— 前端先挡一道，后端 schema 也会 422。
      // 两处都挡是刻意的：前端给出可读的中文提示，后端保证"绕过界面也进不来"。
      setError(t('questionEditor.required'));
      return;
    }
    const options = isChoice ? textToOptions(draft.optionsText) : [];
    if (isChoice && options.length < 2) {
      // 少于 2 个选项就无法区分对错（后端同样会 400，理由一致）
      setError(t('questionEditor.optionsRequired'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      const updated = await updateQuestion(question.id, {
        question: q,
        answer: a,
        // 非选择题不传选项：后端会存成 null（存了但永远不读的字段是噪音）
        options: isChoice ? options : null,
        // PUT 语义下 `null` = **清空解析**（不是"没传"）
        explanation: draft.explanation.trim() || null,
      });
      toast.success(t('questionEditor.saved'));
      onSaved(updated as QuizItem);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('shared.saveFailed'));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    const ok = await confirm({
      title: t('questionEditor.deleteConfirm'),
      // 删除是**不可撤销**的，且会连带清掉这道题的复习记录与复习状态。
      // 这句话必须说出来 —— 否则用户以为只是把它从列表里藏起来。
      message: t('questionEditor.deleteConfirmDetail'),
      confirmText: t('questionEditor.delete'),
      danger: true,
    });
    if (!ok) return;
    setSaving(true);
    setError('');
    try {
      await deleteQuestion(question.id);
      toast.success(t('questionEditor.deleted'));
      onSaved(null as unknown as QuizItem);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('shared.saveFailed'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.editor} data-testid="question-editor">
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}

      <label className={styles.field}>
        <span className={styles.label}>{t('questionEditor.questionLabel')}</span>
        <textarea
          className={styles.textarea}
          rows={2}
          value={draft.question}
          onChange={(e) => set('question', e.target.value)}
        />
      </label>

      {isChoice && (
        <label className={styles.field}>
          <span className={styles.label}>{t('questionEditor.optionsLabel')}</span>
          <textarea
            className={styles.textarea}
            rows={4}
            value={draft.optionsText}
            onChange={(e) => set('optionsText', e.target.value)}
          />
          <span className={styles.hint}>{t('questionEditor.optionsHint')}</span>
        </label>
      )}

      <label className={styles.field}>
        <span className={styles.label}>{t('questionEditor.answerLabel')}</span>
        <textarea
          className={styles.textarea}
          rows={2}
          value={draft.answer}
          onChange={(e) => set('answer', e.target.value)}
        />
      </label>

      <label className={styles.field}>
        <span className={styles.label}>{t('questionEditor.explanationLabel')}</span>
        <textarea
          className={styles.textarea}
          rows={2}
          value={draft.explanation}
          onChange={(e) => set('explanation', e.target.value)}
        />
      </label>

      <div className={styles.actions}>
        <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? t('shared.saving') : t('questionEditor.save')}
        </button>
        <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={saving}>
          {t('questionEditor.cancel')}
        </button>
        {/* 删除放在最右，且是 danger 样式 —— 它是这一组里唯一不可撤销的动作 */}
        <button
          type="button"
          className={`btn btn-danger ${styles.deleteBtn}`}
          onClick={handleDelete}
          disabled={saving}
        >
          {t('questionEditor.delete')}
        </button>
      </div>
    </div>
  );
}
