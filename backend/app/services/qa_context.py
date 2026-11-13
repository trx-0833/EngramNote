"""
多轮问答的上下文组装（docs/qa-history-plan.md §4）

本模块是**纯函数层**：不碰数据库、不调 LLM、不读配置（参数由调用方传入）。
这样它能在毫秒级单测里穷举预算与截断边界 —— 而这些边界恰恰是"多轮"最容易坏的地方。

## 三件必须做对的事

1. **只带成功的轮次**（`status='ok'`）：失败轮次没有答案；中止轮次只有半截答案。
   把半截答案当事实继承，比不给上下文更糟（计划 §4.4）。
2. **预算三条同时生效**：轮数上限、单条答案截断、总字符上限；超出从**最旧**开始丢。
3. **system 里必须写入优先级**："历史只用于理解指代，事实以本轮片段为准" ——
   多轮最大的风险是"上一轮说错了，这一轮继续错"。

## 为什么答案要截断到"开头几句"就够

指代消解需要的是"上一轮在说哪个对象"，而答案的前几句几乎总在定义那个对象；
把整篇答案（尤其带表格的）搬进上下文，会把预算吃光，反而挤掉本轮真正的检索片段。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

#: 组装时最多带几轮（调用方通常传 `settings.qa_context_max_turns`）
DEFAULT_MAX_TURNS = 6
#: 单条历史答案截断长度
DEFAULT_ANSWER_CHARS = 400
#: 历史消息总字符上限
DEFAULT_MAX_CHARS = 4000

#: 追加到 system 提示词末尾的硬指令（**不要删**：它是抑制"沿着错误继续推"的唯一手段）
MULTITURN_SYSTEM_RULE = (
    "\n\n关于历史对话：下面的 assistant 消息来自本次会话的前几轮。"
    "它们**只用于理解用户当前在指代什么**；任何事实性结论必须以本轮提供的参考资料为准，"
    "两者冲突时以参考资料为准。若历史与资料矛盾，请明确指出并采用资料的说法。"
)

#: 检索查询词里，历史问题最多取多少字（避免把整段旧问题拼进查询把向量检索带偏）
RETRIEVAL_PREV_MAX_CHARS = 120


def _clip(text: str, limit: int) -> str:
    """按字符截断并标记省略（中文没有空格，按词切开会切在词中间）"""
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…（略）"


def build_retrieval_query(prev_question: Optional[str], current_question: str) -> str:
    """追问时的检索查询词 = 上一问 + 当前问（计划 §4.3）

    ## 为什么不是"只用当前问"

    "那它的寿命呢？"直接拿去检索几乎必然检索不到 —— 主语在上文里。
    拼接是**零额外 LLM 调用**的近似消解：确定性、可测试、且落库后能归因。

    ## 为什么不无限拼接整段历史

    拼得越长，查询向量的语义越糊（等于把两个话题平均）。所以只取**上一问**，
    且截到 `RETRIEVAL_PREV_MAX_CHARS` 字。
    """
    current = (current_question or "").strip()
    prev = _clip(prev_question or "", RETRIEVAL_PREV_MAX_CHARS)
    if not prev or prev in current:
        return current
    if not current:
        return prev
    return f"{prev} {current}"


def usable_history(turns: Sequence[Tuple[str, str, str]]) -> List[Tuple[str, str]]:
    """从轮次三元组 `(question, answer, status)` 里挑出**可进上下文**的问答对

    只保留 `status == 'ok'` 且答案非空者。返回按时间升序的 `(question, answer)`。
    """
    pairs: List[Tuple[str, str]] = []
    for question, answer, status in turns:
        if status != "ok":
            continue
        if not (answer or "").strip():
            continue
        pairs.append((question or "", answer or ""))
    return pairs


def assemble_messages(
    *,
    system_prompt: str,
    current_user_content: str,
    history: Sequence[Tuple[str, str]] = (),
    max_turns: int = DEFAULT_MAX_TURNS,
    answer_chars: int = DEFAULT_ANSWER_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> List[Dict[str, str]]:
    """拼出送给模型的 `messages`

    形状（计划 §4.2）：

    ```
    [system]   system_prompt (+ MULTITURN_SYSTEM_RULE，仅当带了历史)
    [user]     Q_{k-n}  … assistant A_{k-n}
    …
    [user]     Q_{k-1}  … assistant A_{k-1}
    [user]     current_user_content（= 检索片段 + 当前问题）
    ```

    ## 裁剪顺序（三条预算）

    1. 只取最后 `max_turns` 轮；
    2. 每条答案 `_clip` 到 `answer_chars`；
    3. 若累计字符超过 `max_chars`，从**最旧**开始丢，直到放得下（或丢光）。

    "从最旧丢"而不是"从最新丢"：越近的上下文越可能与当前问题相关。
    """
    limit_turns = max(0, int(max_turns))
    kept = list(history)[-limit_turns:] if limit_turns else []

    # 先按单条答案截断，再算总预算（截断会改变总量，顺序反了会多丢几轮）
    clipped: List[Tuple[str, str]] = [(q, _clip(a, int(answer_chars))) for q, a in kept]

    def total_chars(pairs: Sequence[Tuple[str, str]]) -> int:
        return sum(len(q) + len(a) for q, a in pairs)

    budget = max(0, int(max_chars))
    while clipped and total_chars(clipped) > budget:
        clipped.pop(0)

    messages: List[Dict[str, str]] = []
    system = system_prompt or ""
    if clipped:
        system = f"{system}{MULTITURN_SYSTEM_RULE}"
    messages.append({"role": "system", "content": system})
    for question, answer in clipped:
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer})
    messages.append({"role": "user", "content": current_user_content})
    return messages


def context_turns_used(
    *,
    history: Sequence[Tuple[str, str]],
    max_turns: int = DEFAULT_MAX_TURNS,
    answer_chars: int = DEFAULT_ANSWER_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> int:
    """本轮**实际**带上了几轮（落库到 `qa_turns.context_turns`）

    为什么单独算一遍而不用 `len(history)`：预算裁剪之后真正进模型的是更少的轮次，
    而排障时要知道的是**实际值**（"配置说带 6 轮、库里记着 2 轮"才是可诊断的信号）。
    """
    limit_turns = max(0, int(max_turns))
    kept = list(history)[-limit_turns:] if limit_turns else []
    clipped: List[Tuple[str, str]] = [(q, _clip(a, int(answer_chars))) for q, a in kept]
    budget = max(0, int(max_chars))
    while clipped and sum(len(q) + len(a) for q, a in clipped) > budget:
        clipped.pop(0)
    return len(clipped)
