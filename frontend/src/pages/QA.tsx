/**
 * @file 智能问答页面
 * @description 基于 RAG 的智能问答，支持跨笔记检索和引用来源展示
 * 使用 SSE 流式响应实现实时答案展示，首字到达前显示"AI 正在思考..."
 *
 * ## 批次 E7：引用来源改成常驻右栏
 *
 * 借鉴表 §C3「问答」行引的是 Khoj 的 **Reference Panel**：引用来源做**常驻右栏**，
 * 不做事后展开的折叠角标。理由是这一页除了提问之外唯一的操作就是"顺着引用跳原文"，
 * 把入口藏在一次点击之后等于把它降级成彩蛋。
 *
 * 两处取舍值得记下来：
 *
 * 1. **右栏只跟最新一轮**。多轮对话里每一轮的引用属于那一轮，所以卡片内那份
 *    **原样保留** —— 右栏是"不用滚动就能看到当前引用"的增强，不是替代。
 *    窄屏没有右栏时，卡片内那份就是唯一的来源入口。
 * 2. **两处共用同一个 `SourceLinks`**。"带定位信息才能跳转"这条判据如果各写一遍，
 *    迟早有一边会忘记（那会让用户跳到笔记开头，以为引用就是开头那段）。
 *
 * ## 计划里还有一条本批**没做**
 *
 * 「输入框带检索范围（全部/当前笔记/当前项目）」—— **没有 API 支撑**：
 * `askQuestionStream(question, signal)` 只收这两个参数（`api/client.ts:471`），
 * 契约里也没有对应的范围字段（`schema.ts` 里的 `scope_notes` / `scope_folders`
 * 是**学习目标**的字段，与问答无关）。给后端加字段属于接口改动，
 * 按 AGENTS.md §4 不擅自做，留给用户决策。
 *
 * ## 2026-11-13 独立审计批次（本页承担的部分）
 *
 * 四条结论落在这一页上，共同病因都是**状态被丢掉了，界面只能猜**：
 *
 * | 编号 | 症状 | 这里怎么治 |
 * |---|---|---|
 * | M1 | 抽屉只拉 50 条、且不过滤 `surface` | 请求显式带 `surface: 'global'` + 后端上限 `page_size: 100` |
 * | M3 | 追问的会话已被删 → 一行红字永久卡住，再问还是 404 | 认 `QA_SESSION_NOT_FOUND`：清 `?s=`、清空轮次、`toast` 告知 |
 * | M4 | 失败/中止的轮次渲染成「AI 正在思考...」，永不结束 | `QARecord` 带上 `status` / `errorCode`，按三态渲染 |
 * | m1 | `meta.history_used` 的语义（已改成"实际带了几轮"） | **显示逻辑不变**：仍只在 `> 0` 时提示，只是数字更准 |
 *
 * 逐条的"为什么"写在对应代码旁边，这里只做索引 —— 抽屉的 M2/m4/m5/m6 在
 * `pages/qa/QAHistoryDrawer.tsx`，本文件只负责透传 `total`（见那里的文件头）。
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
// `useNavigate` 随引用来源那一行的改动一起去掉了：那一行现在是 `<Link to>`，
// 不再需要"用 JS 跳转"（见引用列表的注释）。
import { Link } from 'react-router-dom';
import { askQuestionStream, type AnswerSource } from '../api/client';
import { getQaSession, getQaSessions, isSessionGoneError, type QATurnItem } from '../api/qaHistory';
import { parseSSEStream } from '../utils/sse';
import { retrievalNotice } from '../utils/retrievalNotice';
import { useThrottledStream } from '../hooks/useStreamAnswer';
import EmptyState from '../components/EmptyState';
import Icon from '../components/Icon';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变。
// 导航名「问答」与页内标题「智能问答」的不一致属于产品语义，本批只登记不改
import PageHeader from '../components/PageHeader';
// 本页私有样式（overhaul-plan 5.6 第三批）：`.qa-*` 从 `src/styles/learning.css` 拆出，
// 连同被哈希的 `@keyframes slideUp`（改名 `qaSlideUp`）—— 见 QA.module.css 文件头
import styles from './QA.module.css';
import { useTranslation } from '../i18n';
import { useUrlState } from '../hooks/useUrlState';
import { useToast } from '../components/Toast';
import { QA_LIMITS } from '../constants/qaHistory';
import QAHistoryDrawer from './qa/QAHistoryDrawer';

interface QARecord {
  question: string;
  answer: string;
  sources: AnswerSource[];
  provider: string;
  retrievalStatus?: string;
  /**
   * 这一轮在后端的落库状态（`qa_turns.status`：`ok` / `failed` / `aborted`）。
   *
   * 审计 M4：载入历史时原来把它丢了，于是"失败"与"中止"的轮次和"首字还没到"
   * 长得**一模一样**（`answer` 都是空串），渲染只能落到兜底的「AI 正在思考...」。
   * 可这类轮次是**终态**，再也不会有 token 进来 —— 那行提示就永远停在那儿。
   * 带上它才可能把三态分开（见下面回答渲染的三段分支）。
   */
  status?: string;
  /** 失败原因码（`qa_turns.error_code`）；正常与中止的历史轮次为 `null` */
  errorCode?: string | null;
}

/**
 * 引用来源列表（批次 E7 抽出，右栏与卡片内共用）
 *
 * 抽出来的唯一理由是**判据只能有一份**：`canJump` 那三条判断决定了
 * "这条引用能不能跳到原文那一段"，各写一遍的话迟早有一边会忘记，
 * 而忘记的后果是"跳到笔记开头"—— 用户会以为引用就是开头那段，比不给跳转更容易误导。
 */
function SourceLinks({ sources }: { sources: AnswerSource[] }) {
  const { t } = useTranslation();
  return (
    <>
      {sources.map((source, sIdx) => {
        // 阶段 2.7：带定位信息才能跳到原文那一段。
        // 缺 char_start/char_end 时**不提供跳转** —— 跳到笔记开头
        // 会让用户以为"引用就是开头那段"，比不给跳转更容易误导。
        const canJump =
          typeof source.char_start === 'number' &&
          typeof source.char_end === 'number' &&
          source.char_end > source.char_start;
        const params = new URLSearchParams();
        if (canJump) {
          // view=clean：chunk 偏移是基于 clean 副本算的，
          // 若页面显示 original 副本，偏移对不上，会高亮错位置
          params.set('view', 'clean');
          params.set('cs', String(source.char_start));
          params.set('ce', String(source.char_end));
        }
        const href = params.toString()
          ? `/notes/${source.note_id}?${params}`
          : `/notes/${source.note_id}`;
        // 引用编号与回答里的 [N] 对应（后端保证同一次遍历产出）
        //
        // ⚠️ 这里原来是 `div[onClick]`（没有 role/tabIndex）—— **键盘到不了**，
        // 而"点引用跳原文"是这一页除了提问之外唯一的操作。
        // 现在它是真 `<Link>`：可右键、可新标签页、可被读屏当"链接"列出、
        // Tab 一次即达（与 F-17 的卡片标题链接同形）。
        // 下划线**刻意保留**（全局默认）：它是正文块里的一行链接，
        // 属于 F-13 那一类（链接必须不只靠颜色与周围文字区分）。
        // `canJump=false` 时仍然给链接（只是没有定位参数）——
        // 原来它也是可点的，行为逐字不变。
        return (
          <Link
            key={source.chunk_id || sIdx}
            to={href}
            title={canJump ? t('qa.jumpToSource') : t('qa.noLocation')}
            style={{
              display: 'block',
              fontSize: '0.8rem',
              color: canJump ? 'var(--color-primary)' : 'var(--color-text-secondary)',
              marginBottom: '2px',
            }}
          >
            {/* 批次 B3：`\u{1F4C4}` 📄 换 `<Icon name="file" />` ——
                emoji 是彩色的，夹在 `[1] 笔记标题` 这样一行 0.8rem 的
                引用里比标题还抢眼。
                `verticalAlign: middle`：图标**内联**在原来那个 emoji 的位置，
                整行的 `display: block` 与后续 `heading_path` / 「{t('qa.noLocationShort')}」
                的拼接方式一字未改（改成 flex 会把这些文本节点拆成多个 flex 项，
                中间凭空多出 gap）。 */}
            [{sIdx + 1}] <Icon name="file" size={16} style={{ verticalAlign: 'middle' }} />{' '}
            {source.note_title}
            {source.heading_path
              ? ` > ${source.heading_path}`
              : source.chapter_title
                ? ` > ${source.chapter_title}`
                : ''}
            {!canJump && <span style={{ fontSize: '0.7rem' }}>{t('qa.noLocationShort')}</span>}
          </Link>
        );
      })}
    </>
  );
}

export default function QA() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState<QARecord[]>([]);
  /** 本轮实际参考了几轮上文（来自 SSE `meta.history_used`；0 时不显示提示） */
  const [historyUsed, setHistoryUsed] = useState(0);
  /** 历史抽屉 */
  const [drawerOpen, setDrawerOpen] = useState(false);
  /**
   * 当前会话 id —— **存在 URL 上**（`?s=`）：
   * 刷新/分享/返回本页都回到同一个会话；"新建对话"就是把它清空。
   * 与笔记/卡片/题库三处的做法一致（见 hooks/useUrlState）。
   */
  const [sessionId, setSessionId] = useUrlState('s', '');

  /**
   * 历史会话列表
   *
   * `staleTime` 用 0：历史列表的变化来自"刚问完一轮"或"删除"，
   * 都不是高频轮询能解决的，靠 `invalidateQueries` 显式失效更准确。
   *
   * 两个参数都不是随手写的（审计 M1）：
   * - `page_size: 100` 是后端允许的上限（更大的值会被截断到 100，写 100 才是
   *   "一次拿满"）。原来写 50，第 51 条以后的会话在抽屉里**凭空消失**，
   *   而抽屉又没有任何"还有更多"的提示 —— 用户只会以为历史被吞了；
   * - `surface: 'global'` 必须显式传。不传后端不做过滤，笔记浮层
   *   （`NoteAskPanel`，`surface: 'note'`）里问的会话会混进这里的列表，
   *   点开是一条上下文对不上、正文里没有笔记引用的对话。
   */
  const sessionsQuery = useQuery({
    queryKey: ['qa', 'sessions'],
    queryFn: ({ signal }) => getQaSessions({ page_size: 100, surface: 'global' }, { signal }),
  });
  const sessions = sessionsQuery.data?.items ?? [];

  // 持有当前流式请求的 AbortController 与 reader，用于新问题中止旧流、停止生成与卸载清理
  const abortRef = useRef<AbortController | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);

  // 流式 token 的节流累积器：把每个 token 的 setState 合并为按间隔批量刷新，
  // 避免长回答时"每个 token 全量重算 Markdown"造成的 O(n²) 卡顿（§2.8 F-10）
  const { push: pushDelta, flush: flushDelta, reset: resetDelta } = useThrottledStream();

  // 组件卸载时中止进行中的流式请求并取消读取
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      const reader = readerRef.current;
      readerRef.current = null;
      abortRef.current = null;
      if (reader) {
        void reader.cancel().catch(() => {});
      }
    };
  }, []);

  /**
   * 载入 `?s=` 指向的会话（刷新 / 分享 / 从历史抽屉点开）
   *
   * 三条与"后端可能已删"有关的处理：
   * 1. 会话已消失 → 清掉 `?s=`、提示"已开始新对话"，而不是让页面空着；
   * 2. 载入的轮次按**倒序**塞进 `history`（页面约定 `history[0]` 是最新一轮，
   *    见 `handleAsk` 里的 `[tempRecord, ...prev]`）；
   * 3. 载入期间不覆盖用户正在进行的流式回答（`streaming` 时跳过）。
   *
   * ⚠️ 两个"防死循环"的细节（2026-11-13 实测踩过：测试挂死 10 分钟不走）：
   * - `loadedSessionRef` 记住**已经载入过的会话**：`setHistory` 会触发重渲染，
   *   而重渲染若再次进入本 effect，就会"取详情 → setState → 再取详情"无限循环；
   * - `toast` / `t` 全部走 ref：`useToast()` 在没有 Provider 时每次渲染都返回
   *   **新对象**（测试环境正是如此），把它们放进依赖等于依赖"每次渲染"。
   */
  const loadedSessionRef = useRef<string | null>(null);
  /**
   * **已确认被删掉**的会话 id（404 `QA_SESSION_NOT_FOUND`）。
   *
   * 审计 M3 的收尾：光把 `?s=` 清掉不够。本 effect 的依赖里有 `setSessionId`，
   * 而它来自 `useUrlState`，**身份不保证稳定** —— 依赖一变，effect 就会再跑一遍；
   * 那时 `loadedSessionRef` 也刚被清空，"已载入"标记一点也挡不住，
   * 于是"刚判定删除、URL 上的 `?s=` 还没清掉"那一帧会再发一次详情请求，
   * 把旧轮次拉回一个已经清空的页面（实测踩过：页面自己长回历史记录）。
   * 所以判定删除后先把 id 记进这里，载入 effect 见到它就再不重试。
   */
  const goneSessionsRef = useRef<Set<string>>(new Set());
  const toastRef = useRef(toast);
  const tRef = useRef(t);
  useEffect(() => {
    toastRef.current = toast;
    tRef.current = t;
  });

  useEffect(() => {
    if (!sessionId || streaming) return;
    // 已经判定"这条会话被删了"的 id 不再重试（审计 M3）：本 effect 的依赖含
    // `setSessionId`（`useUrlState` 的身份不保证稳定），可能重复运行；只靠下面
    // 那个"已载入"标记挡不住"刚判定删除、URL 还没清掉"那一帧把旧轮次拉回来。
    if (goneSessionsRef.current.has(sessionId)) return;
    if (loadedSessionRef.current === sessionId) return;
    // 先记账再发请求：请求失败时也不该在下一次渲染里重试成一个死循环
    loadedSessionRef.current = sessionId;
    let cancelled = false;
    (async () => {
      try {
        const detail = await getQaSession(sessionId);
        if (cancelled) return;
        const records: QARecord[] = (detail.turns ?? [])
          .map((turn: QATurnItem) => ({
            question: turn.question,
            answer: turn.answer ?? '',
            sources: (turn.sources ?? []) as AnswerSource[],
            provider: turn.provider ?? '',
            retrievalStatus: turn.retrieval_status ?? '',
            // 审计 M4：`status` / `error_code` 必须一路带到渲染层，
            // 否则失败与中止的轮次只能落到兜底的「AI 正在思考...」
            status: turn.status,
            errorCode: turn.error_code ?? null,
          }))
          .reverse();
        setHistory(records);
        setHistoryUsed(0);
      } catch (err) {
        if (cancelled) return;
        if (isSessionGoneError(err)) {
          // 先把 id 记进"已删除"集合，再清状态：否则下面几个 setState 触发的
          // 重渲染会让 effect 在 URL 还没清掉的那一帧重新拉一次详情（见 ref 的说明）
          goneSessionsRef.current.add(sessionId);
          loadedSessionRef.current = null;
          setSessionId('');
          setHistory([]);
          toastRef.current.info(tRef.current('qa.sessionGone'));
          return;
        }
        setError(err instanceof Error ? err.message : tRef.current('qa.historyLoadFailed'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, streaming, setSessionId]);

  /** 「新建对话」：清空 URL 上的会话与当前轮次（下一问自动开新会话） */
  function handleNewChat() {
    stopActiveStream();
    // 与载入 effect 的判据统一成字符串：`''`（没有会话）而不是 `null`
    // （审计 M3 的余波 —— 载入 effect 依赖不保证稳定，`null` 与 `''` 各写一半时
    // 任何一个"清空"路径都可能漏掉一种写法，导致标记看似清空其实还在比对）
    loadedSessionRef.current = '';
    setSessionId('');
    setHistory([]);
    setHistoryUsed(0);
    setError('');
  }

  /** 从抽屉打开一条历史会话：换会话前先停掉正在生成的流 */
  function handleOpenSession(nextSessionId: string) {
    stopActiveStream();
    setDrawerOpen(false);
    setError('');
    setSessionId(nextSessionId);
  }

  /** 中止当前流式请求（新问题发起前 / 点击停止生成时调用） */
  function stopActiveStream() {
    abortRef.current?.abort();
    const reader = readerRef.current;
    readerRef.current = null;
    abortRef.current = null;
    if (reader) {
      void reader.cancel().catch(() => {});
    }
  }

  function handleStop() {
    stopActiveStream();
  }

  async function handleAsk() {
    if (!question.trim()) return;
    // 新问题发起前中止上一个未完成的流，避免旧 token 污染新答案
    stopActiveStream();
    // 清空上一轮可能残留的节流缓冲
    resetDelta();

    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setStreaming(true);
    setError('');
    const currentQuestion = question.trim();

    // 创建一个临时记录，答案会逐步填充
    const tempRecord: QARecord = {
      question: currentQuestion,
      answer: '',
      sources: [],
      provider: '',
    };
    setHistory((prev) => [tempRecord, ...prev]);
    setQuestion('');

    // 标记是否已收到首个 token，用于切换"思考中"与"流式渲染"状态
    let firstTokenReceived = false;
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;

    try {
      const stream = await askQuestionStream(
        currentQuestion,
        controller.signal,
        sessionId || undefined,
      );
      // askQuestionStream 返回 ReadableStream，直接获取读取器
      const r = stream.getReader();
      if (!r) {
        throw new Error('无法读取流式响应');
      }
      reader = r;
      readerRef.current = reader;

      // 用共享的规范 SSE 解析器替换原先内联的手写解析：
      // 手写版只保留最后一行 data:、不识别 \r\n、且 JSON.parse 无保护
      // （一个截断分片就会终结整段回答）。见 utils/sse.ts 的说明。
      //
      // ⚠️ 传的是**上面取好的读取器**，不是 `stream` 本身：
      // 读取器是独占的，把已经 `getReader()` 过的流再交给解析器会被第二次
      // `getReader()` 拒绝（`... only accept readable streams that are not yet
      // locked to a reader`）—— 这里此前传的就是 `stream`，于是**每一次提问都
      // 直接失败**（2026 覆盖轮把 `qa` 接进审计场景时第一次真的问了一次才暴露，
      // 见 docs/a11y-audit.md §10）。传读取器同时也保住了 `stopActiveStream`
      // 手里的 `readerRef`。
      await parseSSEStream(
        reader,
        {
          onEvent: (eventType, data) => {
            const payload = (data ?? {}) as Record<string, unknown>;

            if (eventType === 'meta') {
              // 会话 id（首问时由服务端新建并回传）与「本轮参考了几轮上文」
              const nextSessionId = String(payload.session_id || '');
              if (nextSessionId && nextSessionId !== sessionId) {
                setSessionId(nextSessionId);
              }
              // 审计 m1：后端已把 `history_used` 改成「**实际**带了几轮」
              // （此前报的是请求的轮数，追问一次之后数字会虚高）。前端显示逻辑
              // 照旧 —— 只在 `> 0` 时提示，改的只是数字本身的含义，不是这里。
              setHistoryUsed(Number(payload.history_used || 0));
              // 首事件：记录检索降级状态，供渲染降级提示
              setHistory((prev) => {
                if (prev.length === 0) return prev;
                const updated = [...prev];
                updated[0] = {
                  ...updated[0],
                  retrievalStatus: String(payload.retrieval_status || ''),
                };
                return updated;
              });
              return;
            }

            if (eventType === 'token') {
              // 首个 token 到达时，切换出"思考中"状态
              if (!firstTokenReceived) {
                firstTokenReceived = true;
                setLoading(false);
              }
              // 节流提交：把 token 累积到缓冲区，按间隔批量刷新。
              // 原先每个 token 一次 setState + 一次全文 Markdown 重算，
              // 长回答时是 O(n²)（见 §2.8 F-10）。
              pushDelta(String(payload.content || ''), (delta) => {
                setHistory((prev) => {
                  if (prev.length === 0) return prev;
                  const updated = [...prev];
                  updated[0] = { ...updated[0], answer: updated[0].answer + delta };
                  return updated;
                });
              });
              return;
            }

            if (eventType === 'sources') {
              setHistory((prev) => {
                if (prev.length === 0) return prev;
                const updated = [...prev];
                updated[0] = {
                  ...updated[0],
                  sources: (payload.sources as AnswerSource[]) || [],
                  provider: String(payload.provider || ''),
                };
                return updated;
              });
              return;
            }

            if (eventType === 'done') {
              flushDelta((delta) => {
                setHistory((prev) => {
                  if (prev.length === 0) return prev;
                  const updated = [...prev];
                  updated[0] = { ...updated[0], answer: updated[0].answer + delta };
                  return updated;
                });
              });
              return;
            }

            if (eventType === 'error') {
              throw new Error(String(payload.message || '流式响应错误'));
            }
          },
          onParseError: (raw, err) => {
            // 坏事件跳过而非中断整段回答
            console.warn('[QA] 无法解析的 SSE 事件已跳过:', raw.slice(0, 120), err);
          },
        },
        controller.signal,
      );
      // 部分实现不发 done 事件，流自然结束也要提交剩余文本
      flushDelta((delta) => {
        setHistory((prev) => {
          if (prev.length === 0) return prev;
          const updated = [...prev];
          updated[0] = { ...updated[0], answer: updated[0].answer + delta };
          return updated;
        });
      });
    } catch (err) {
      if (controller.signal.aborted) {
        // 用户主动停止：保留已生成文本；若尚无任何文本则移除空气泡
        if (!firstTokenReceived) {
          setHistory((prev) => (prev.length > 0 ? prev.slice(1) : prev));
        }
      } else if (isSessionGoneError(err)) {
        // 审计 M3：追问的会话在别处被删了（另一个标签页、或抽屉里的「清空全部」），
        // 后端回 404 `QA_SESSION_NOT_FOUND`。原来这一支落到下面的 `setError`：
        // 页面上留一行红字，而 `?s=` 仍指着一条不存在的会话 —— 再问一次还是 404，
        // 除了手动点「新建对话」没有出路。按"会话已消失"处理：
        // 1. 记进 `goneSessionsRef`，挡住载入 effect 把旧轮次拉回来（见那里的注释）；
        // 2. 清掉 `?s=` 和当前轮次，下一问自动开新会话；
        // 3. 用一个 `toast` 说明发生了什么 —— 内容凭空消失必须给解释。
        // 注意这里**不清空** `error`：`handleAsk` 开头已经清过一次，
        // 而这一支压根没往 `error` 里写过东西。
        goneSessionsRef.current.add(sessionId);
        loadedSessionRef.current = '';
        setSessionId('');
        setHistory([]);
        setHistoryUsed(0);
        toastRef.current.info(tRef.current('qa.sessionGone'));
      } else {
        setError(err instanceof Error ? err.message : t('qa.failed'));
        // 如果首字未到，移除空白的临时记录，避免列表中出现空白气泡
        if (!firstTokenReceived) {
          setHistory((prev) => (prev.length > 0 ? prev.slice(1) : prev));
        }
      }
    } finally {
      // 仅当 ref 仍指向当前请求时才清理，避免误清新流
      if (abortRef.current === controller) abortRef.current = null;
      if (readerRef.current === reader) readerRef.current = null;
      setLoading(false);
      setStreaming(false);
      // 刚问完一轮 / 被中止：历史列表的标题、轮次数与排序都可能变了，
      // 显式失效比等 staleTime 更准（用户点开抽屉时看到的就是最新的）
      void queryClient.invalidateQueries({ queryKey: ['qa', 'sessions'] });
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  }

  /**
   * 右栏的来源 = **最新一轮**（`history[0]` 就是最新，见 handleAsk 里的
   * `[tempRecord, ...prev]`）。历史轮次的引用仍在各自卡片里，不在这里重复列。
   */
  const latestSources = history[0]?.sources ?? [];

  return (
    <div className="page-enter">
      {/* 页面标题（批次 C1：统一进 <PageHeader>，字号本就 1.5rem） */}
      <PageHeader
        title={t('qa.title')}
        actions={
          <>
            <button type="button" className="btn btn-secondary" onClick={handleNewChat}>
              {t('qa.newChat')}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setDrawerOpen(true)}>
              {t('qa.history')}
            </button>
          </>
        }
      />

      {/* 两栏骨架（批次 E7）：左对话流 / 右常驻引用栏。窄屏收成一栏，见模块 CSS */}
      <div className={styles.qaLayout}>
        <div className={styles.qaMain}>
          {/* 输入区域 */}
          <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
            <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
              <input
                type="text"
                className="input"
                placeholder={t('qa.inputPlaceholder')}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={streaming}
                aria-label={t('qa.inputLabel')}
                aria-describedby="qa-multiturn-hint"
                style={{ flex: 1 }}
              />
              {streaming ? (
                <button className="btn btn-danger" onClick={handleStop}>
                  {t('qa.stop')}
                </button>
              ) : (
                <button className="btn btn-primary" onClick={handleAsk} disabled={!question.trim()}>
                  {t('qa.ask')}
                </button>
              )}
            </div>
            {/*
              ★ 追问提示（计划 §5.4 第 1 句）。

              数字来自 `QA_LIMITS.contextTurns`（镜像后端 `qa_context_max_turns`），
              由一条后端用例盯着两处一致 —— 文案里写死 6 会在调参后变成谎言。
              `aria-describedby` 指向它：读屏用户在输入框上就能听到"追问会带上下文"。
            */}
            <p id="qa-multiturn-hint" className={styles.qaHint}>
              {t('qa.multiturnHint', { turns: QA_LIMITS.contextTurns })}
            </p>
            {/* 本轮实际参考了几轮（history_used > 0 才显示：首轮提示"参考了 0 轮"是噪音） */}
            {historyUsed > 0 && (
              <p className={styles.qaContextUsed}>{t('qa.contextUsed', { count: historyUsed })}</p>
            )}
            {error && (
              <p
                style={{
                  color: 'var(--color-error)',
                  marginTop: 'var(--space-sm)',
                  fontSize: '0.875rem',
                }}
              >
                {error}
              </p>
            )}
          </div>

          {/* 问答历史 */}
          {history.length === 0 ? (
            <EmptyState message={t('qa.empty')} description={t('qa.emptyHint')} />
          ) : (
            history.map((record, idx) => {
              // 第一条记录且处于"思考中"阶段（loading=true 且尚未收到任何 token）
              const isThinking = idx === 0 && loading && record.answer === '';
              return (
                // key 必须**稳定**：原实现把 answer 的前 20 字符拼进 key，
                // 于是答案每增长约 20 字符 key 就变化一次，React 会把整条记录
                // （问题气泡 + AI 卡片 + 引用列表）卸载重建 —— 焦点、文本选区、
                // 滚动位置全部丢失，并成倍放大流式渲染开销（§2.8 F-10）。
                // question + idx 在本列表内已唯一且不随流式内容变化。
                <div key={`${record.question}#${idx}`} style={{ marginBottom: 'var(--space-md)' }}>
                  {/* 问题 */}
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'flex-end',
                      marginBottom: 'var(--space-sm)',
                    }}
                  >
                    <div className={styles.qaUserBubble}>{record.question}</div>
                  </div>
                  {/* 回答 */}
                  <div className={`card ${styles.qaAiCard}`}>
                    {isThinking ? (
                      // 思考中状态：首字到达前显示
                      <div
                        style={{
                          color: 'var(--color-text-secondary)',
                          fontStyle: 'italic',
                          lineHeight: 1.8,
                        }}
                      >
                        {t('qa.thinking')}
                      </div>
                    ) : record.answer ? (
                      // 流式渲染：答案实时增长
                      <div style={{ lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{record.answer}</div>
                    ) : record.status === 'failed' ? (
                      // 审计 M4 之一：**失败**的终态轮次。没有这一支时它落到最后的
                      // 兜底分支，显示「AI 正在思考...」—— 可它永远等不到 token 了。
                      // 带上 `error_code` 才有可查的线索；旧数据的 code 可能为空。
                      //
                      // 这两支**排在 `record.answer` 之后**是刻意的：失败/中止也可能
                      // 留下半截回答，把正文换成一句状态会把用户已经看到的文字弄丢。
                      // 它们只接管"没有正文的终态"—— 那正是原来永远停在「思考中」的那类。
                      <div className={styles.qaTurnFailed}>
                        {record.errorCode
                          ? t('qa.turnFailed', { code: record.errorCode })
                          : t('qa.turnFailedUnknown')}
                      </div>
                    ) : record.status === 'aborted' ? (
                      // 审计 M4 之二：**主动中止**的终态轮次。与"失败"分开措辞：
                      // 失败是系统的问题，中止是用户自己的操作，混成一句话会让
                      // 用户以为"我没停过，它怎么失败了"。
                      <div className={styles.qaTurnFailed}>{t('qa.turnAborted')}</div>
                    ) : (
                      <div
                        style={{
                          color: 'var(--color-text-secondary)',
                          fontStyle: 'italic',
                          lineHeight: 1.8,
                        }}
                      >
                        {t('qa.thinking')}
                      </div>
                    )}
                    {/* 检索降级提示：向量检索没命中（`hybrid`）或向量服务不可用（`bm25_only`）
                        都该如实告诉用户 —— 修复前只提示后者，而实际发生的是前者
                        （见 utils/retrievalNotice.ts 与 overhaul-plan 附录 BN.4） */}
                    {retrievalNotice(record.retrievalStatus) && (
                      <p
                        style={{
                          fontSize: '0.75rem',
                          color: 'var(--color-warning)',
                          marginTop: 'var(--space-sm)',
                        }}
                      >
                        {retrievalNotice(record.retrievalStatus)}
                      </p>
                    )}
                    {/* 引用来源（批次 E7：这一份**保留**，右栏是增强不是替代 —— 见文件头） */}
                    {record.sources.length > 0 && (
                      <div
                        style={{
                          marginTop: 'var(--space-md)',
                          paddingTop: 'var(--space-sm)',
                          borderTop: '1px solid var(--color-border)',
                        }}
                      >
                        <p
                          style={{
                            fontSize: '0.75rem',
                            color: 'var(--color-text-secondary)',
                            marginBottom: 'var(--space-xs)',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 4,
                          }}
                        >
                          {/* 批次 B3：引用来源补 `quote` 图标（一对上引号） */}
                          <Icon name="quote" size={16} />
                          {t('qa.sources')}:
                        </p>
                        <SourceLinks sources={record.sources} />
                      </div>
                    )}
                    {record.provider && (
                      <p
                        style={{
                          fontSize: '0.7rem',
                          color: 'var(--color-text-secondary)',
                          marginTop: 'var(--space-xs)',
                          textAlign: 'right',
                        }}
                      >
                        {t('qa.poweredBy', {
                          provider: record.provider === 'glm' ? 'GLM' : 'DeepSeek',
                        })}
                      </p>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* ── 常驻引用栏（批次 E7，Khoj 的 Reference Panel 做法）── */}
        <aside className={styles.qaRail} aria-label={t('qa.sources')}>
          <p className={styles.qaRailTitle}>
            <Icon name="quote" size={16} />
            {latestSources.length > 0
              ? t('qa.sourcesCount', { count: latestSources.length })
              : t('qa.sources')}
          </p>
          {latestSources.length > 0 ? (
            <div className={styles.qaRailList}>
              <SourceLinks sources={latestSources} />
            </div>
          ) : (
            <p className={styles.qaRailEmpty}>{t('qa.sourcesRailHint')}</p>
          )}
        </aside>
      </div>

      <QAHistoryDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        sessions={sessions}
        loading={sessionsQuery.isLoading}
        error={
          sessionsQuery.error instanceof Error
            ? sessionsQuery.error.message
            : sessionsQuery.error
              ? t('qa.historyLoadFailed')
              : undefined
        }
        activeSessionId={sessionId || undefined}
        // 审计 m6：服务端总数要透传给抽屉 —— 它据此判断"还有更早的对话没显示"。
        // 不能用 `sessions.length`：只拉一页时列表看起来"就是全部"，
        // 而这里 `page_size` 已经是 100（见 sessionsQuery 的注释），
        // 超过 100 条的用户否则永远不会知道历史被截断了。
        total={sessionsQuery.data?.total ?? sessions.length}
        onOpenSession={handleOpenSession}
        onChanged={() => {
          void queryClient.invalidateQueries({ queryKey: ['qa', 'sessions'] });
        }}
        retentionDays={QA_LIMITS.retentionDays}
        backupWeeks={QA_LIMITS.backupWeeks}
      />
    </div>
  );
}
