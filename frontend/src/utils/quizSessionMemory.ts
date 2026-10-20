/**
 * @file 刷题会话的进度记忆（想法 8 阶段 3）
 *
 * ## 它解决什么
 *
 * 一次会话可能几十道题，中途刷新（F5、误触、切标签页被回收）会**从头开始** ——
 * 已经做过的题要重做一遍，而"做到第几题"这件事完全丢失。长会话里这很恼人。
 *
 * ## 键的形状：`scope` + **题目 id 指纹**
 *
 * ```
 * engramnote:quiz:v1:<scope>:<题目 id 的哈希>
 * ```
 *
 * 为什么要带 id 指纹，而不是只按 `scope`（页面 + 笔记）存：
 *
 * 到期队列**会变**。今天做的题明天不再到期，`/review/due` 返回的就是另一批。
 * 若只按 scope 存，第二天打开会拿"昨天的第 7 题"去对"今天的第 7 题" ——
 * **位置对上了、题不是同一道**，用户会看到自己的答案出现在一道没做过的题上。
 * 那是比"从头开始"严重得多的错误。
 *
 * 带上指纹后，题目集合一变键就变，旧记录自然不被读取（并由 TTL / 上限淘汰）。
 * 代价是每次装载多算一次哈希 —— 50 个 uuid 拼接后取个简单哈希，可忽略。
 *
 * ## 与 `utils/scrollMemory.ts` 同一套降级约定
 *
 * `localStorage` 的每一次访问都可能抛（Safari 隐私模式、配额写满、企业策略
 * 禁用存储）。这些**不是**本功能的失败场景，而是"这次不记忆"而已：
 * 用户顶多回到刷新前的观感（从头开始），那是**功能没生效**，不是**功能坏了**。
 * 所以每个出口都包 `try/catch`，且**不弹提示、不写 console** ——
 * 一个记不住进度的小功能没有资格打扰用户。
 */
import type { SubmitAnswerResponse } from '../api/client';

/** localStorage 键前缀。带版本号：将来改存储形状时旧键自然失效，而不是被误读 */
const KEY_PREFIX = 'engramnote:quiz:v1:';

/**
 * 记忆的有效期：12 小时。
 *
 * 为什么比 `scrollMemory` 的 30 天短得多：那个记的是"读到哪"，跨月仍有意义；
 * 这个记的是"这一轮做到哪"，而**每日额度是当天清零的** ——
 * 隔天恢复一轮"昨天的刷题"会让用户看到一堆已经不再到期的题，
 * 且"今日已完成"的数字与页面上的进度对不上。12 小时覆盖"今天这一轮"足够。
 */
const TTL_MS = 12 * 60 * 60 * 1000;

/**
 * 最多保留多少条会话记录。
 *
 * 为什么要上限：键按"题目集合指纹"分，做题越多键越多。每条记录最多几十 KB
 * （含判分结果），20 条约几百 KB，对 5MB 配额没有压力，同时覆盖
 * "最近在做的几轮"这个真实集合。
 */
const MAX_ENTRIES = 20;

/** 单条记录里的一道题（只存恢复 UI 必需的四项） */
export interface QuizSessionRecordItem {
  /** 题目 id。恢复时与当前题目**逐一对齐**，不匹配就整条丢弃 */
  id: string;
  userAnswer: string;
  submitted: boolean;
  /** 判分结果；未提交为 null */
  result: SubmitAnswerResponse | null;
}

/** 一条会话记录 */
export interface QuizSessionRecord {
  /** 当前题下标 */
  currentIndex: number;
  items: QuizSessionRecordItem[];
  /** 本次会话已答 / 正确（恢复后汇总页的数字要接得上） */
  sessionTotal: number;
  sessionCorrect: number;
  /** 写入时间戳（ms）。用于 TTL 判定与超限淘汰 */
  at: number;
}

/** 本模块的存储入口。抽成函数是为了让测试能替换它，也为了让 `try/catch` 只有一处 */
function storage(): Storage | null {
  try {
    // 隐私模式下 `localStorage` 这个 getter 本身就可能抛
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * 由题目 id 列表算一个短指纹（FNV-1a 变体）
 *
 * 不追求抗碰撞 —— 它的用途是"题目集合变了就别恢复"，不是安全边界。
 * 32 位足以让"顺序或成员变了"必然换一个键。
 */
function fingerprint(ids: string[]): string {
  let h = 0x811c9dc5;
  const joined = ids.join('\u0001');
  for (let i = 0; i < joined.length; i++) {
    h ^= joined.charCodeAt(i);
    // 乘 16777619，用移位避免 32 位溢出后的精度问题
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h.toString(36);
}

/** 拼接某个会话的键 */
function keyFor(scope: string, ids: string[]): string {
  return `${KEY_PREFIX}${scope}:${fingerprint(ids)}`;
}

/** 本模块管理的键前缀（用于超限淘汰时只清自己的，不碰别的功能） */
function scopePrefix(scope: string): string {
  return `${KEY_PREFIX}${scope}:`;
}

/** 判断一个解析出来的值是不是合法记录 */
function isRecord(v: unknown): v is QuizSessionRecord {
  if (typeof v !== 'object' || v === null) return false;
  const r = v as Record<string, unknown>;
  if (typeof r.currentIndex !== 'number' || !Number.isInteger(r.currentIndex)) return false;
  if (typeof r.sessionTotal !== 'number' || typeof r.sessionCorrect !== 'number') return false;
  if (typeof r.at !== 'number') return false;
  if (!Array.isArray(r.items)) return false;
  return r.items.every((it) => {
    if (typeof it !== 'object' || it === null) return false;
    const o = it as Record<string, unknown>;
    return (
      typeof o.id === 'string' &&
      typeof o.userAnswer === 'string' &&
      typeof o.submitted === 'boolean'
    );
  });
}

/**
 * 淘汰同一 scope 下的旧记录，只留最近 `MAX_ENTRIES` 条
 *
 * 只清本 scope 的键：别的页面（别的 scope）有自己的预算，
 * 不能因为这一页做得多就把它们的记录挤掉。
 */
function prune(store: Storage, scope: string): void {
  const prefix = scopePrefix(scope);
  const entries: Array<{ key: string; at: number }> = [];
  for (let i = 0; i < store.length; i++) {
    const key = store.key(i);
    if (!key || !key.startsWith(prefix)) continue;
    let at = 0;
    try {
      const parsed: unknown = JSON.parse(store.getItem(key) ?? 'null');
      if (isRecord(parsed)) at = parsed.at;
    } catch {
      // 解析不了就当作最旧，优先淘汰
    }
    entries.push({ key, at });
  }
  if (entries.length <= MAX_ENTRIES) return;
  entries
    .sort((a, b) => a.at - b.at)
    .slice(0, entries.length - MAX_ENTRIES)
    .forEach(({ key }) => {
      try {
        store.removeItem(key);
      } catch {
        /* 静默：清不掉就算了 */
      }
    });
}

/**
 * 保存进度
 *
 * @param scope - 会话标识（页面 + 笔记），例如 `review` / `quick:<noteId>`
 * @param ids - 当前题目 id 列表（顺序敏感，进指纹）
 * @param record - 要保存的状态（不含 `at`，由本函数补）
 */
export function saveQuizSession(
  scope: string,
  ids: string[],
  record: Omit<QuizSessionRecord, 'at'>,
): void {
  if (ids.length === 0) return;
  const store = storage();
  if (!store) return;
  try {
    const payload: QuizSessionRecord = { ...record, at: Date.now() };
    store.setItem(keyFor(scope, ids), JSON.stringify(payload));
    prune(store, scope);
  } catch {
    // 配额写满 / 被策略禁止 —— 静默降级为"这次不记忆"，见文件头
  }
}

/**
 * 读取进度
 *
 * @returns 命中的记录；题目集合不匹配、已过期、或存储不可用时返回 `null`
 */
export function loadQuizSession(
  scope: string,
  ids: string[],
  now: number = Date.now(),
): QuizSessionRecord | null {
  if (ids.length === 0) return null;
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(keyFor(scope, ids));
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return null;
    // TTL：过期就顺手删掉，不留垃圾
    if (now - parsed.at > TTL_MS) {
      try {
        store.removeItem(keyFor(scope, ids));
      } catch {
        /* 静默 */
      }
      return null;
    }
    // 下标越界（题目变少了）当作没有记忆 —— 否则会停在空白页
    if (parsed.currentIndex < 0 || parsed.currentIndex >= parsed.items.length) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** 清除某个会话的进度（做完整轮、或"再复习一轮"时调用） */
export function clearQuizSession(scope: string, ids: string[]): void {
  if (ids.length === 0) return;
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(keyFor(scope, ids));
  } catch {
    /* 静默 */
  }
}

/** 供测试使用：本模块管理的键前缀 */
export const QUIZ_SESSION_KEY_PREFIX = KEY_PREFIX;
/** 供测试使用：记忆有效期（ms） */
export const QUIZ_SESSION_TTL_MS = TTL_MS;
