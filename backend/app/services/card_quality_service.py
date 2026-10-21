# -*- coding: utf-8 -*-
"""
卡片质量打分器 + 低分卡报告（A4，**只标记不删除**）
=====================================================

## 这一批**不做**什么（先写清楚，免得被误读）

`docs/agent-workstream-plan.md` §5 第 6 条已拍板：**A4 只产出报告与标记，
不删卡、不合并、不改复习历史**。真去重合并会动产品语义
（`review_states` 会变孤儿行），**要另开一轮单独批**。
所以本模块**没有任何写操作**：它只读库、只返回数据。

## 为什么是**规则**打分，而不是逐卡 LLM 打分

计划里 A4 的原方案是"3469 张卡逐卡 LLM 打分（信息量 / 自足性 / 可出题性）"，
额度一栏写的是"**是**（3469 次调用，量不小）"。本轮先做**规则版**，理由是实测：

| 实测（真库只读） | 数字 |
|---|---|
| 卡片内容平均长度 | **49.5 字符**（P25=27 / P50=40 / P90=89） |
| 内容 ≤ 30 字符 | **1133 / 3469（32.7%）** |
| 内容 ≤ 20 字符 | 367 / 3469（10.6%） |
| 标题重复种数 / 涉及卡片 | **72 种 / 187 张（5.4%）** |
| `card_type='formula'` 却**没有任何数学符号** | 3 / 46 |

也就是说"卡片太短、只是把标题说了一遍"这个失效模式**在字符数上就能筛出来**，
而 3469 次 LLM 调用是**几十万 token 级**的开销。规则打分能先把这批挑出来，
让 LLM 那条路只花在**规则筛不动的**（内容够长但没信息量）那些卡上。

## ⚠️ 这是**启发式**，不是质量判据（本模块最重要的一条免责）

规则打分**读不懂语义**。它判不了"这段话对不对"、
也判不了"这张卡对这个用户有没有用"。它能判的只有三件**可观测**的事：
**太短**、**只是把标题抄一遍**、**类型与内容不符**。

所以报告里每条理由都必须带**证据**（字符数 / 标题与内容的相同部分），
让人能一眼否掉误判 —— 而不是给一个 0-100 的分让人无从反驳。
`score` 只用于**排序**（先看最可疑的），不用于任何自动动作。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ===========================================================================
# 阈值：全部按真库分位数标定（见模块说明里的实测表）
# ===========================================================================

#: 内容短到"几乎不可能有信息量"的字符数（真库 P25 = 27）
SUSPICIOUS_CHARS = 30
#: 内容短到"基本等于标题"的字符数（真库 P10 = 20）
SEVERE_CHARS = 20
#: 自足性判据：内容里出现的标题字符比例达到这个值就算"只是把标题说了一遍"
#:
#: ⚠️ **必须是 1.0**（而不是 0.8 之类的"高比例"）：实测 0.80-0.83 那一档里
#: 既有"该判"的（`泄洪放空洞检修门配置` → `泄洪放空洞设1孔检修门。` 里
#: "1孔"是信息）也有"不该判"的，**没有可用的分界**。
#: 详见 `score_card` 里那张实测表 —— 那个取舍是调过两次才定下来的。
TITLE_ECHO_RATIO = 1.0
#: 公式卡必须出现数学符号（真库 3/46 没有）
_MATH_SYMBOL_RE = re.compile(r"[=+\-*/^√∑∫×÷≈≤≥<>]|\d")
#: 纯占位/元数据型内容的**锚定形状**（"见下表"这类自指）
#:
#: ## 为什么是"锚定形状表"而不是一条正则
#:
#: 这一条改了三版，每版都被实测反例打回：
#:
#: | 版本 | 判据 | 被什么打回 |
#: |---|---|---|
#: | v1 | 正则 `^(见\|参见\|…)` | `"见证了我国水电…"` 被误判成自指 |
#: | v2 | `^(见\|…)` + 指代物 `(表\|图\|附录)` | `"见下表。"` 漏判 —— 中间隔着方位词"下" |
#: | v3（现在） | 归一化后**逐条比对锚定形状** | —— |
#:
#: 逐条比对的好处是**每条都能读懂、能增删、能被用例一条条钉住**；
#: 而一条越来越长的正则到最后没人敢改（上面两版的教训）。
#: 代价是覆盖不了"见附件三"这类长尾 —— 那正是**宁可漏判**的方向。
_META_PLACEHOLDERS = (
    "见下表", "见上表", "见下图", "见上图", "见表", "见图",
    "参见附录", "详见附录", "参见", "详见", "如图", "如下",
    "如上", "同上", "略", "无", "待补", "待补充", "暂无", "略去",
)
#: 归一化时丢掉的字符（占位符判断不在乎标点与空白）
_META_STRIP_RE = re.compile(r"[\s。．.，,；;：:、（）()]")


def _is_meta_only(content: str) -> bool:
    """内容是不是"只有指向、没有实质"（见 `_META_PLACEHOLDERS` 的来历）

    判据是**归一化后逐条比对**：内容去掉标点空白之后，
    要么**就是**某个占位形状，要么**以它开头**（`"见下表。参数略。"` 这种）。
    """
    normalized = _META_STRIP_RE.sub("", content or "")
    if not normalized:
        return False
    return any(
        normalized == p or normalized.startswith(p) for p in _META_PLACEHOLDERS
    )
#: 中文停用词（算"标题回响"时剔掉，避免"的/与/和"这类字把比例抬高）
_STOP_CHARS = set("的了和与及或在是为对把被这那其之也都很")


def _strip_function_chars(text: str) -> set:
    """去掉虚词后剩下的字符集（"标题回响"的判据用）

    ## 为什么要剔虚词**和**判断词

    实测的两个反例（真库实例）：

    - `'泄洪放空洞洞型'` → `'泄洪放空洞为有压洞。'`
      —— 内容**补充了信息**（"有压洞"），但"泄洪放空洞"这五个字逐字重复，
      回响比例 0.83 ⇒ 第一版把它误判成"只是把标题说了一遍"。
      剔掉判断词"为"之后，比例降到 0.67，不再误判。
    - `'变压器急救负载运行时需加强巡视'` → 同名内容 + 句号
      —— 剔掉虚词后仍然是 13/13 = 1.0，**照旧被判出来**。

    所以要剔的是"两类文本都会用到的**结构字**"，保留的是**实词**：
    剔得不够会误判（第一个反例），剔得太多会漏判（第二个正例）。
    停用词表刻意**小**：只放结构字，不放任何实词。
    """
    return {ch for ch in (text or "") if ch.strip() and ch not in _STOP_CHARS}


@dataclass
class CardQuality:
    """一张卡的打分结果（**只读数据**，没有任何副作用）"""

    card_id: str
    title: str
    card_type: str
    note_id: str
    char_count: int
    #: 越低越可疑。**只用于排序**，不是质量结论
    score: int
    #: 命中的规则名（稳定标识，便于按规则分组统计）
    reasons: List[str] = field(default_factory=list)
    #: 每条理由的**证据**（人能据此否掉误判）
    evidence: str = ""
    #: 内容摘要（报告里给人看的，截断到 80 字）
    excerpt: str = ""

    @property
    def severe(self) -> bool:
        """是否属于"看着就该动"的那一档（报告里优先列）"""
        return "too_short_severe" in self.reasons or "title_echo" in self.reasons


#: 规则 → 扣分权重。**权重只影响排序**，不影响"列不列进清单"。
#:
#: 为什么权重差这么大：`title_echo` 与 `too_short_severe` 是"这张卡几乎没内容"，
#: 而 `type_mismatch` 只是"类型标错了"（内容可能有用）。两者混在一个分数里
#: 会让人误以为后者也该删。
_RULE_WEIGHTS: Dict[str, int] = {
    "title_echo": 40,          # 内容只是把标题说了一遍
    "too_short_severe": 35,    # <= 20 字符
    "too_short": 20,           # <= 30 字符
    "meta_only": 25,           # "见下表"这类自指，没有实质内容
    "type_mismatch": 10,       # formula 卡里没有数学符号
    "no_title": 15,            # 标题为空
}


def _title_echo_ratio(title: str, content: str) -> float:
    """内容里"来自标题"的字符占比（0-1）

    ## 为什么要剔停用词

    "变压器急救负载运行时需加强巡视" 与它的内容 "变压器急救负载运行时需加强巡视。"
    去掉句号后**逐字相同** ⇒ 比例 1.0（这是真库里的实例）。
    但若标题是"冷备用状态的定义"，内容里出现"的"字不该算"回响" ——
    剔掉停用词才能让这个比例反映**实词**的重合。

    ## 为什么用"标题字符集"而不是"最长公共子串"

    子串更严格（能排除"撞字但不撞词"），但它会把
    "阀门操作术语" → "阀门：开启、关闭。"（真库实例）判成低回响 ——
    而那张卡的问题恰恰是"内容只是把标题里的词拆开重复了一遍"。
    用字符集重合度 + 长度惩罚，这两类都能覆盖。
    """
    t = _strip_function_chars(title)
    c = _strip_function_chars(content)
    if not t:
        return 0.0
    if not c:
        return 1.0  # 内容为空 ⇒ 完全"没补充"
    hit = sum(1 for ch in t if ch in c)
    return hit / len(t)


def score_card(row: Dict) -> CardQuality:
    """给一张卡打分

    Args:
        row: 至少含 `id` / `title` / `content` / `card_type` / `note_id`

    Returns:
        CardQuality（`score` 越低越可疑；`reasons` 为空表示**三条规则都没命中**，
        **不等于"这张卡是好的"** —— 规则读不懂语义，见模块说明的免责）
    """
    title = (row.get("title") or "").strip()
    content = (row.get("content") or "").strip()
    card_type = (row.get("card_type") or "").strip()

    char_count = len(content)
    reasons: List[str] = []
    score = 100

    if not title:
        reasons.append("no_title")
        score -= _RULE_WEIGHTS["no_title"]

    if char_count <= SEVERE_CHARS:
        reasons.append("too_short_severe")
        score -= _RULE_WEIGHTS["too_short_severe"]
    elif char_count <= SUSPICIOUS_CHARS:
        reasons.append("too_short")
        score -= _RULE_WEIGHTS["too_short"]

    if _is_meta_only(content):
        reasons.append("meta_only")
        score -= _RULE_WEIGHTS["meta_only"]

    # 「只是把标题说了一遍」：短 **且** 完全由标题的字组成。
    #
    # ## 判据为什么收得这么紧（实测调过两次）
    #
    # 真库实例的实测比例（去掉虚词后）：
    #
    # | 标题 → 内容 | 比例 | 该不该判 |
    # |---|---|---|
    # | `渗漏集水井为无压井` → 同名 + 句号 | **1.00** | ✅ 该 |
    # | `各部分温度监视` → `监视各部分温度正常。` | **1.00** | ✅ 该（"正常"是套话） |
    # | `变压器急救负载运行时需加强巡视` → 同名 + 句号 | **1.00** | ✅ 该 |
    # | `泄洪放空洞检修门配置` → `泄洪放空洞设1孔检修门。` | 0.80 | ❌ 不该（"1孔"是信息） |
    # | `泄洪放空洞洞型` → `泄洪放空洞为有压洞。` | 0.83 | ❌ 不该（"有压洞"是信息） |
    # | `档位调整后测直流电阻要求` → `档位调整后应测直流电阻。` | 0.83 | ❌ 不该 |
    #
    # 0.80 与 1.00 之间**没有可用的分界**（0.83 那一档里既有该判的也有不该判的），
    # 所以判据只能取 **== 1.0**（内容完全由标题的字构成）。代价是漏掉
    # "标题 + 一句套话"那一类（如 `各部分温度监视` → `…温度正常。` 幸好仍被
    # `too_short*` 接住），换来的是**不误报**。
    #
    # 长度守卫同理收得紧（`<= len(title) + 6`）：内容比标题长出一大截时，
    # 就算字都来自标题也更可能是**展开**而不是重复。
    if title and char_count <= SUSPICIOUS_CHARS * 2:
        ratio = _title_echo_ratio(title, content)
        if ratio >= 1.0 and char_count <= len(title) + 6:
            reasons.append("title_echo")
            score -= _RULE_WEIGHTS["title_echo"]

    if card_type == "formula" and not _MATH_SYMBOL_RE.search(content):
        reasons.append("type_mismatch")
        score -= _RULE_WEIGHTS["type_mismatch"]

    # 分数只用于**排序**，所以下限截到 0 就够了（不设上限：命中越多排得越前）
    score = max(0, score)

    return CardQuality(
        card_id=str(row.get("id") or ""),
        title=title,
        card_type=card_type,
        note_id=str(row.get("note_id") or ""),
        char_count=char_count,
        score=score,
        reasons=reasons,
        evidence=(
            f"{char_count} 字符"
            + (f"；标题 {len(title)} 字符" if title else "；标题为空")
        ),
        excerpt=content[:80] + ("…" if char_count > 80 else ""),
    )


def build_report(rows: List[Dict], *, only_suspicious: bool = True) -> Dict:
    """把一批卡片行变成报告数据

    Returns:
        `{"total": n, "flagged": n, "by_reason": {规则: 条数}, "cards": [...]}`
        `cards` 按 `(score 升序, char_count 升序, card_id)` 排序 ——
        **最可疑的先看**，且顺序确定（同一份输入每次得到同样的报告）。
    """
    scored = [score_card(r) for r in rows]
    flagged = [c for c in scored if c.reasons]

    by_reason: Dict[str, int] = {}
    for c in flagged:
        for name in c.reasons:
            by_reason[name] = by_reason.get(name, 0) + 1

    cards = flagged if only_suspicious else scored
    cards = sorted(cards, key=lambda c: (c.score, c.char_count, c.card_id))

    return {
        "total": len(scored),
        "flagged": len(flagged),
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "cards": cards,
    }


def render_markdown(report: Dict, *, top: int = 40, duplicate_section: Optional[Dict] = None) -> str:
    """把报告渲染成 markdown（**给人看的**，不是给程序读的）

    ⚠️ 报告开头必须写清"这是启发式筛查、没有删任何东西" ——
    否则一份名叫《低分卡清单》的文档被当成"待删清单"是迟早的事
    （`AGENTS.md` §6 记过「清理副本却删掉真身」那类事故）。
    """
    lines = [
        "# 卡片质量筛查报告（A4）",
        "",
        "> ⚠️ **本报告只做启发式筛查，没有删除、没有合并、没有改动任何数据。**",
        "> 判据是**字符数与标题回响**，它**读不懂语义** —— 判不了「这段话对不对」、",
        "> 也判不了「这张卡对这个用户有没有用」。每条都带证据，请据此逐条否掉误判。",
        "",
        "## 总览",
        "",
        f"- 参与打分的卡片：**{report['total']}**",
        f"- 命中至少一条规则的：**{report['flagged']}**"
        f"（{report['flagged'] / report['total'] * 100:.1f}%）" if report["total"] else "- 参与打分的卡片：0",
        "",
    ]
    if report["by_reason"]:
        lines += ["### 按规则分布", "", "| 规则 | 条数 | 含义 |", "|---|---|---|"]
        meanings = {
            "title_echo": "内容只是把标题说了一遍（几乎没补充信息）",
            "too_short_severe": f"内容 ≤ {SEVERE_CHARS} 字符",
            "too_short": f"内容 ≤ {SUSPICIOUS_CHARS} 字符",
            "meta_only": "内容是「见下表」这类自指，没有实质内容",
            "type_mismatch": "`card_type='formula'` 但内容里没有数学符号",
            "no_title": "标题为空",
        }
        for name, count in report["by_reason"].items():
            lines.append(f"| `{name}` | {count} | {meanings.get(name, '')} |")
        lines.append("")

    lines += [f"## 最可疑的 {min(top, len(report['cards']))} 张", ""]
    if not report["cards"]:
        lines += ["（没有命中任何规则的卡片）", ""]
    else:
        lines += [
            "| 分数 | 标题 | 内容 | 证据 | 命中规则 |",
            "|---|---|---|---|---|",
        ]
        for c in report["cards"][:top]:
            title = c.title or "（无标题）"
            excerpt = c.excerpt.replace("|", "\\|")
            lines.append(
                f"| {c.score} | {title} | {excerpt} | {c.evidence} "
                f"| {', '.join(c.reasons)} |"
            )
        lines.append("")

    if duplicate_section:
        lines += [
            "## 重复标题清单（与 `report_duplicate_cards.py` 同判据）",
            "",
            f"- 重复标题种数：**{duplicate_section.get('title_groups', 0)}**",
            f"- 涉及卡片：**{duplicate_section.get('cards_involved', 0)}**",
            "",
            "⚠️ 重复**标题**不等于重复**卡片**（同一个标题在不同标准里可能是不同条款）。",
            "真要合并必须先看 `report_duplicate_cards.py` 的语义分类输出。",
            "",
        ]

    lines += [
        "## 下一步（都不在本报告的范围内）",
        "",
        "1. **人工过一遍上面那些「证据」** —— 误判直接划掉；",
        "2. 真正需要重写的卡片，走「重新理解」而不是手改；",
        "3. **合并/删除卡片要另开一轮单独批** —— 它会动 `review_states`",
        "   （复习历史会变孤儿行），属产品语义变更。",
        "",
    ]
    return "\n".join(lines)
