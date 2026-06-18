/**
 * @file 统一标签映射
 * @description 集中管理各页面共用的标签映射，消除重复定义
 */

/** 来源类型到中文标签的映射 */
export const sourceTypeLabels: Record<string, string> = {
  pdf: 'PDF', image: '图片', docx: 'Word', pptx: 'PPT', xlsx: 'Excel', audio: '音频', video: '视频',
}

/** 笔记状态到中文标签的映射 */
export const statusLabels: Record<string, string> = {
  uploading: '上传中', converting: '转换中', converted: '已完成',
  cleaning: '清洗中', cleaned: '已清洗', cleaning_failed: '清洗失败',
  learning: '学习中', learning_failed: '学习失败', archived: '已审阅', failed: '失败',
}

/** 知识卡片类型到中文标签的映射 */
export const cardTypeLabels: Record<string, string> = {
  concept: '概念', formula: '公式', qa: '问答', definition: '定义',
}

/** 知识卡片类型到颜色的映射 */
export const cardTypeColors: Record<string, string> = {
  concept: '#3b82f6', formula: '#8b5cf6', qa: '#10b981', definition: '#f59e0b',
}

/** 题目类型到中文标签的映射 */
export const questionTypeLabels: Record<string, string> = {
  choice: '选择题', fill_blank: '填空题', short_answer: '简答题',
}

/** 题目类型到颜色的映射 */
export const questionTypeColors: Record<string, string> = {
  choice: '#3b82f6', fill_blank: '#8b5cf6', short_answer: '#10b981',
}

/** 难度到中文标签的映射 */
export const difficultyLabels: Record<string, string> = {
  easy: '简单', medium: '中等', hard: '困难',
}

/** 难度到颜色的映射 */
export const difficultyColors: Record<string, string> = {
  easy: '#10b981', medium: '#f59e0b', hard: '#ef4444',
}
