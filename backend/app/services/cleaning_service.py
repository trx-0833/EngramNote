"""
笔记清洗服务模块

本模块提供笔记内容的规则化清洗和文本分块功能，是 AI 清洗管道的核心组件。
清洗管道的目标是生成一份干净、无冗余的笔记副本，**不总结、不润色**。

主要职责：
- 规则化清洗：去除空行、页眉页脚、去除水印等
- 改写类规则（B1，2026-10-21）：公式简化、标题层级重排、多余空格压缩
- 文本分块：将长文本按段落边界分割为重叠块，用于后续嵌入和去重
- 清洗副本生成：根据去重结果标记重复块，生成清洗后的 Markdown

设计决策：
- **默认仅做"减法"**（去除冗余），不做"加法"（不添加内容）
- ⚠️ **改写类规则是有开关的例外**（B1）：`clean_simplify_formula` /
  `clean_normalize_heading_levels` / `clean_normalize_spacing` 三条会**改变字符**，
  而本模块此前长期承诺「仅做减法、不改变原意」。它们由用户在
  `使用时的想法.txt` 第 5 条明确要求并单独拍板，因此：
    - 各自是独立开关（`config.py`），公式简化**默认关**（它改正文语义）；
    - 三条都**只作用于普通文本行** —— 代码块 / 数学块 / 行内代码 / 行内公式内部不碰；
    - 公式简化只认"简单公式"（命令走白名单，结构字符一出现就整条放弃）。
  这份"承诺变更"如实记在这里，而不是让旧表述留在文件头与实现矛盾
  （本仓库判据：代码实测 > 文档，见 `AGENTS.md` §1）。
- 重复块用 HTML 注释标记而非删除，用户可随时恢复
- 分块时保留重叠区域，避免语义在边界处断裂
- 所有清洗操作记录统计信息，便于用户审阅
"""

import difflib
import logging
import re
from typing import Dict, List, Optional, Tuple

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ============================================================
# 规则化清洗
# ============================================================

# 常见页眉页脚模式（可扩展）
_HEADER_FOOTER_PATTERNS = [
    re.compile(r"^第\s*\d+\s*页\s*$", re.MULTILINE),           # "第 1 页"
    re.compile(r"^Page\s*\d+\s*$", re.MULTILINE | re.IGNORECASE),  # "Page 1"
    re.compile(r"^\s*\d+\s*$", re.MULTILINE),                   # 独立数字行（页码）
    re.compile(r"^<!--\s*page\s*=\s*\d+\s*-->\s*$", re.MULTILINE),  # MinerU 页码标签
]

# 水印关键词（可配置扩展）
# _WATERMARK_KEYWORDS = [
#     "水印", "watermark", "样本", "SAMPLE", "草稿", "DRAFT",
#     "仅供内部使用", "内部资料", "CONFIDENTIAL",
# ]
_WATERMARK_KEYWORDS = ["仅供内部使用", "内部资料", "CONFIDENTIAL", ]


# ============================================================
# 改写类规则（B1，2026-10-21）—— ⚠️ 与"仅做减法"的旧承诺不同
# ============================================================
#
# ## 为什么会有改写
#
# 用户在 `使用时的想法.txt` 第 5 条点名了三件事：
#   ① `$6.34m \times 61.95m$` 换成 `6.34m*61.95m`（方便编辑）
#   ② 标题层级要严谨（不能大标题小标题都是 `##`）
#   ③ 数字、字母前后各空一格的，取消那个空格
#
# 走查报告 §5 实测：**一条都没做**，且对 Markdown 输入逐字节空转（711 → 711）。
#
# ## 边界（三条，都必须守住）
#
# 1. **只作用于普通文本行** —— 代码块（```）、数学块（`$$`）、行内代码
#    （`` `…` ``）、行内公式（`$…$`）内部一律不碰；
# 2. **公式简化只认"简单公式"** —— 命令走**白名单**，遇到任何不认识的
#    结构（`\frac` / `\int` / 希腊字母 / 上下标 / 矩阵…）**整条公式原样保留**。
#    宁可漏改，不可改错：改错一个公式比不改严重得多；
# 3. **不做减法以外的语义改动** —— 不改措辞、不加内容、不重排段落。

#: 行内公式 `$…$` 的匹配（不跨行；`$$` 块由调用方的状态机先排除）
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)")

#: 行内代码 `` `…` ``（公式/空格规则都要跳过它内部）
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

#: 简单公式允许出现的 LaTeX 命令 → 纯文本写法
#:
#: 这是**白名单**：公式里出现任何**不在此表**的反斜杠命令，整条公式不改。
_SIMPLE_LATEX_COMMANDS = {
    r"\times": "*",
    r"\cdot": "*",
    r"\div": "/",
    r"\pm": "±",
    r"\leq": "<=",
    r"\geq": ">=",
    r"\neq": "!=",
    r"\approx": "≈",
    r"\left": "",
    r"\right": "",
}

#: 简单公式里**不允许**出现的字符（出现即放弃简化）
#:
#: 覆盖：上下标（`^` / `_`）、花括号分组（`{` / `}`）、以及已经写成 unicode 的
#: 数学符号（求和/连乘/积分/根号/无穷/±≤≥≠≈）—— 那些不是"数字字母的四则运算"。
#:
#: ⚠️ **反斜杠不在这里**。第一版把它写进了这个字符类，结果**一条公式都简化不了**：
#: LaTeX 命令本身就是用反斜杠写的，而白名单里的 `\times` 正是要替换掉的东西。
#: 正确的顺序是"**先做白名单替换，再看还剩不剩反斜杠**"（见 `simplify_inline_math`）。
_LATEX_NOT_SIMPLE_RE = re.compile(r"[\^_{}]|[∑∏∫√∞±≤≥≠≈]")

#: 允许出现在简单公式里的字符（其余一律放弃简化）
_SIMPLE_MATH_CHARS_RE = re.compile(r"^[0-9A-Za-z\s.,+\-*/=()<>%:;'\"°]+$")


def simplify_inline_math(line: str) -> Tuple[str, int]:
    """把**简单**行内公式改写成纯文本写法，返回 (新行, 改写条数)

    ## 判据（全部满足才改）

    1. 是行内公式 `$…$`（不是 `$$` 块）；
    2. 里面**没有** `\\` / `^` / `_` / `{` / `}`（即没有命令、上下标、分组）；
    3. 去掉白名单命令替换后，剩下的字符**只有** `0-9A-Za-z`、空格、
       以及 `.,+-*/=()<>%:;'"°` —— 也就是说它是"数字/字母的四则运算"，
       不是需要 KaTeX 才能读的数学表达。

    改写只做两件事：白名单命令 → 纯文本符号；去掉 `$` 与命令两侧多余空格。

    ## 为什么这么保守

    公式是**用户要照着算/照着抄**的东西。把它改错（例如把 `\\frac{a}{b}`
    拍平成 `a/b` 丢了分组，或把 `x^2` 变成 `x2`）比不改严重得多 ——
    而清洗副本同时是"可编辑的 Markdown"与"公式的来源"。所以命令走白名单、
    结构字符一出现就整条放弃。
    """
    changed = 0

    def _replace(match: re.Match) -> str:
        nonlocal changed
        body = match.group(1)

        # 判据 2：上下标 / 花括号分组 / unicode 数学符号 → 放弃
        if _LATEX_NOT_SIMPLE_RE.search(body):
            return match.group(0)

        # 判据 3：**先**做白名单命令替换，**再**看还剩不剩反斜杠。
        # 顺序反了会把白名单命令本身也当成"不认识的命令"（见
        # `_LATEX_NOT_SIMPLE_RE` 的说明）。
        simplified = body
        for cmd, plain in _SIMPLE_LATEX_COMMANDS.items():
            # 命令后面可能跟空格（`\times 61`），替换时一并吃掉一个空格
            simplified = re.sub(re.escape(cmd) + r"\s*", plain, simplified)

        # 替换后若还残留反斜杠（不认识的命令）→ 放弃
        if "\\" in simplified:
            return match.group(0)

        simplified = simplified.strip()
        if not simplified or not _SIMPLE_MATH_CHARS_RE.match(simplified):
            return match.group(0)

        # 收掉运算符两侧的空格：用户点名的目标形态是 `6.34m*61.95m`
        # （`$6.34m \times 61.95m$` 换掉之后会剩下 `6.34m *61.95m`，
        #   那个空格正是 LaTeX 为了排版加的，对"照着抄/照着算"没有意义）。
        # 只收 `* / + - = < >` 这几个**明确是运算符**的字符两侧；
        # `,` 后面的空格保留（`a, b` 是正常排版）。
        simplified = re.sub(r"\s*([*/+=<>])\s*", r"\1", simplified)
        # `-` 单独处理：只在**两侧都有内容且不是数字正负号**时收空格。
        # `a - b` → `a-b`；但 `5 -3` 这种（负号）不动，避免把减法读成负数。
        simplified = re.sub(r"(?<=[\w)])\s+-\s+(?=[\w(])", "-", simplified)
        # 括号内侧的空格（`( a + b )` → `(a+b)`）
        simplified = re.sub(r"\(\s+", "(", simplified)
        simplified = re.sub(r"\s+\)", ")", simplified)
        simplified = re.sub(r"\s{2,}", " ", simplified).strip()

        # 与原文等价就不算改写（避免把 stats 记成"改过"）
        if f"${simplified}$" == match.group(0):
            return match.group(0)

        changed += 1
        return simplified

    return _INLINE_MATH_RE.sub(_replace, line), changed


#: 数字/字母与中文之间的**多余**空格（≥2 个才动，见下）
#:
#: 为什么是 `{2,}` 而不是 `+`：用户的原话是"数字、字母，**前后各空一格**的
#: 识别内容，可以取消其空格"。而"各空一格"在产物里表现为**两个**空格
#: （`10  台`：MinerU 给的空格 + 原文的空格）。压到一个空格是**去掉多余的那一个**，
#: 不是"删掉所有空格"—— 后者会把 `220 kV` 变成 `220kV`，那是另一种改写。
_MULTI_SPACE_RE = re.compile(r" {2,}")

#: 中文字符（用于判断空格是不是"中英之间"的那个）
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def normalize_spacing(line: str) -> Tuple[str, int]:
    """压缩**普通文本行**里多余的空格，返回 (新行, 改写处数)

    ## 做什么 / 不做什么

    - **做**：把连续 ≥2 个半角空格压成 1 个（`10  台` → `10 台`）。
      这是用户点名的"前后各空一格"的实际形态。
    - **不做**：删掉单个空格。`220 kV` 里的那一个是**正确**的排版，
      删掉会变成 `220kV`（那是"改写"而不是"去掉多余的"）。
    - **不做**：动行首缩进（Markdown 的缩进有语义：列表、代码块）。
      所以只处理 `line.lstrip()` 之后的部分，缩进原样保留。

    ## 边界

    行内代码与行内公式内部不碰 —— 那里的空格可能是内容的一部分
    （`` `print("a  b")` ``、`$a  b$`）。

    ⚠️ 判据里**没有**"必须挨着中文"这一条。第一版想加上"只在数字/字母与中文
    之间压空格"，但那样会漏掉 `价格  10 元` 这类**纯多余空格**，而用户的原话是
    "数字、字母，前后各空一格的识别内容，可以取消其空格" ——
    他要的就是把**多余的那一个**去掉，与前后是不是中文无关。
    真正需要保护的是行内代码与行内公式（上面那条），不是"中文邻接"。
    """
    indent_len = len(line) - len(line.lstrip(" "))
    indent = line[:indent_len]
    body = line[indent_len:]
    if not body:
        return line, 0

    # 把行内代码/公式挖出来占位，处理完再放回去（避免动它们内部的空格）
    placeholders: List[str] = []

    def _stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        # 用不可能出现在正文里的哨兵（含 \x00）
        return f"\x00{len(placeholders) - 1}\x00"

    stashed = _INLINE_CODE_RE.sub(_stash, body)
    stashed = _INLINE_MATH_RE.sub(_stash, stashed)

    fixed, count = _MULTI_SPACE_RE.subn(" ", stashed)

    # 放回占位
    def _restore(match: re.Match) -> str:
        return placeholders[int(match.group(1))]

    fixed = re.sub(r"\x00(\d+)\x00", _restore, fixed)
    if count == 0:
        return line, 0
    return indent + fixed, count


#: Markdown ATX 标题：`## 标题`（`#` 数量 = 层级；`###` 起是 3 个以上）
_ATX_HEADING_RE = re.compile(r"^(#{1,6})(\s+)(.*)$")

#: 标题里开头的编号：`1.2`、`1.2.3`、`一、`、`（1）`、`第 1 章`…
_HEADING_NUMBER_RE = re.compile(
    r"^\s*(?:"
    r"(?P<dotted>\d+(?:\.\d+)*)[.、)）]?\s*"     # 1.2 / 1.2.3 / 1)
    r"|第\s*(?P<cn_num>[0-9一二三四五六七八九十百]+)\s*[章节篇部分]\s*"
    r"|(?P<paren>[（(]\s*\d+\s*[)）])\s*"
    r")"
)


def infer_heading_level(title: str) -> Optional[int]:
    """从标题文本的编号推断**建议的**层级（推不出来时返回 None）

    ## 判据

    编号的**点数**决定层级：`1` → 2 级、`1.2` → 3 级、`1.2.3` → 4 级
    （1 级留给文档标题）。中文编号（`第一章`）→ 2 级，`（1）` → 3 级。

    ## 为什么返回 None 而不是猜一个

    "标题层级严谨化"这条规则**没有唯一正确答案**：`## 概述` 该是 2 级还是 3 级，
    取决于它在文档里的位置，而单看一行标题推不出来。
    所以这个函数只对**带编号**的标题下结论 —— 那些是有客观依据的；
    没有编号的一律 `None`（调用方保持原样）。

    这与本模块其它规则的取舍一致：**宁可漏改，不可改错**。
    """
    match = _HEADING_NUMBER_RE.match(title)
    if not match:
        return None
    if match.group("dotted"):
        depth = len(match.group("dotted").split("."))
        # `1` → 2 级；`1.2` → 3 级；`1.2.3` → 4 级；再深也不超过 6
        return min(1 + depth, 6)
    if match.group("cn_num"):
        return 2
    if match.group("paren"):
        return 3
    return None


def normalize_heading_levels(text: str) -> Tuple[str, int]:
    """按标题里的编号重排 ATX 层级，返回 (新文本, 改写行数)

    ## 只改**层级**，不改标题文字、不改行序

    用户的原话是"不能大标题是 `##` 内容、小标题也是 `##` 内容，
    这样标题层级就没法区分了"。所以本函数只把 `#` 的**数量**按编号对齐，
    标题文字与顺序一个字符都不动。

    ## 为什么不动代码块里的 `#`

    `# 注释` 在 shell/Python 片段里是注释，不是标题。逐行状态机跟踪
    ``` 围栏 —— 与 `clean_rules` 同一套判据。
    """
    lines = text.split("\n")
    in_fence = False
    changed = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        match = _ATX_HEADING_RE.match(line)
        if not match:
            continue
        hashes, gap, title = match.groups()
        # 只处理"像标题"的行：标题文字不能为空、不能是纯符号
        if not title.strip():
            continue
        target = infer_heading_level(title)
        if target is None or target == len(hashes):
            continue
        lines[i] = "#" * target + gap + title
        changed += 1

    return "\n".join(lines), changed

# 全角标点 → 半角标点映射（暂不启用：可能破坏 Markdown 中的数学公式）
# 保留定义以便后续需要时启用
# _FULLWIDTH_TO_HALFWIDTH = {
#     "\u3000": " ",   # 全角空格 → 半角空格
#     "，": "，",       # 中文逗号保留
#     "。": "。",       # 中文句号保留
#     "；": "；",       # 中文分号保留
#     "：": "：",       # 中文冒号保留
#     "！": "！",       # 中文感叹号保留
#     "？": "？",       # 中文问号保留
#     "（": "（",       # 中文括号保留
#     "）": "）",       # 中文括号保留
# }

# OCR 常见错字修正表（高频且置信度极高的修正）
_OCR_CORRECTIONS = {
    "l0": "10", "O0": "00", "rn": "m",  # 常见 OCR 混淆
}


def clean_rules(text: str) -> Tuple[str, Dict[str, int]]:
    """
    对 Markdown 文本进行规则化清洗

    清洗规则（仅做减法，不改变原意）：
    1. 去除多余空行（连续 2+ 空行压缩为 1 个空行）
    2. 去除页眉页脚模式（页码、重复页眉等）
    3. 去除水印文字（可配置的水印关键词列表）
    4. 去除行首行尾多余空白

    Args:
        text: 原始 Markdown 文本

    Returns:
        Tuple[str, Dict[str, int]]: (清洗后的文本, 清洗统计信息)
        统计信息包含各规则的应用次数
    """
    stats = {
        "empty_lines_removed": 0,
        "headers_footers_removed": 0,
        "watermarks_removed": 0,
        "whitespace_trimmed": 0,
    }

    if not text or not text.strip():
        return text, stats

    # 逐行清洗并跟踪代码块/数学块状态。
    # 旧实现直接对全文做正则 sub，页码/数字行/水印规则会误删
    # 围栏代码块内的纯数字行（编号示例 1/2/3）、公式独立数字行等合法正文，见 docs/decisions.md#F-30。
    lines = text.split("\n")
    result_lines = []
    in_code_block = False
    in_math_block = False

    for line in lines:
        stripped = line.strip()

        # 代码块 fence 切换（```）
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue

        # 数学块切换（$$ 独占行；单行 $$...$$ 不切换状态）
        if stripped.startswith("$$"):
            if stripped.endswith("$$") and len(stripped) > 2:
                result_lines.append(line)
                continue
            in_math_block = not in_math_block
            result_lines.append(line)
            continue

        # 代码块/数学块内的行：不应用页眉页脚与水印规则，原样保留
        if in_code_block or in_math_block:
            result_lines.append(line)
            continue

        # 1. 页眉页脚模式（仅普通文本行）
        is_header_footer = False
        for pattern in _HEADER_FOOTER_PATTERNS:
            if pattern.match(stripped):
                is_header_footer = True
                stats["headers_footers_removed"] += 1
                break
        if is_header_footer:
            continue

        # 2. 水印文字（仅普通文本行，且为短行避免误删正文）
        is_watermark = False
        for keyword in _WATERMARK_KEYWORDS:
            if keyword in line and len(stripped) < 30:
                is_watermark = True
                stats["watermarks_removed"] += 1
                break
        if is_watermark:
            continue

        result_lines.append(line)

    result = "\n".join(result_lines)

    # 3. 去除多余空行（连续 2+ 空行压缩为 1 个空行）
    multi_blank_pattern = re.compile(r"\n{3,}")
    multi_blank_matches = multi_blank_pattern.findall(result)
    stats["empty_lines_removed"] = len(multi_blank_matches)
    result = multi_blank_pattern.sub("\n\n", result)

    # 4. 统一标点（暂不启用：可能破坏 Markdown 中的数学公式）
    # for fullwidth, halfwidth in _FULLWIDTH_TO_HALFWIDTH.items():
    #     result = result.replace(fullwidth, halfwidth)

    # 5. 去除行首行尾多余空白（保留缩进，仅去除纯空白行）
    lines = result.split("\n")
    trimmed_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行保留
            trimmed_lines.append("")
        elif stripped == line.rstrip() and line == line.lstrip():
            # 行首行尾无多余空白
            trimmed_lines.append(line)
        else:
            # 保留缩进（行首空格/tab），仅去除行尾空白
            trimmed = line.rstrip()
            if trimmed != line:
                stats["whitespace_trimmed"] += 1
            trimmed_lines.append(trimmed)
    result = "\n".join(trimmed_lines)

    # 6. 去除文件末尾多余空行
    result = result.rstrip("\n") + "\n"

    # ---- 7. 改写类规则（B1，2026-10-21）----
    #
    # ⚠️ 位置刻意放在**减法规则之后**：先去掉页眉页脚/水印/多余空行，
    # 再改写剩下的正文 —— 顺序反了会把即将被删掉的行也改一遍（stats 会虚高）。
    #
    # ⚠️ 这三条与上面的规则不同：它们**改变字符**，而清洗长期承诺
    # "仅做减法、不改变原意"。所以两条开关都在 `config.py` 里显式声明，
    # 且公式简化**默认关**。逐行状态机（代码块 / 数学块）在这里**重新走一遍** ——
    # 上面那个循环的 `in_code_block` / `in_math_block` 已经结束，
    # 而这三条规则各自需要知道"这一行在不在块里"。
    #
    # 统计键与上面那批并列，前端「清洗统计」面板不需要改形状即可展示
    # （新增的键是纯增量）。
    stats.setdefault("formula_simplified", 0)
    stats.setdefault("spacing_normalized", 0)
    stats.setdefault("heading_levels_fixed", 0)

    # 7a. 标题层级（整篇一起看：需要跟踪围栏状态）
    if settings.clean_normalize_heading_levels:
        result, n_heading = normalize_heading_levels(result)
        stats["heading_levels_fixed"] = n_heading

    # 7b/7c. 逐行：公式简化 + 空格压缩（都要跳过代码块/数学块）
    out_lines = []
    in_code = False
    in_math = False
    for line in result.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            out_lines.append(line)
            continue
        if stripped.startswith("$$"):
            if not (stripped.endswith("$$") and len(stripped) > 2):
                in_math = not in_math
            out_lines.append(line)
            continue
        if in_code or in_math:
            out_lines.append(line)
            continue

        new_line = line
        if settings.clean_simplify_formula:
            new_line, n_formula = simplify_inline_math(new_line)
            stats["formula_simplified"] += n_formula
        if settings.clean_normalize_spacing:
            new_line, n_space = normalize_spacing(new_line)
            stats["spacing_normalized"] += n_space
        out_lines.append(new_line)
    result = "\n".join(out_lines)

    return result, stats


def apply_line_range_marks(
    text: str,
    duplicates: List[Dict],
) -> Tuple[str, int]:
    """按**行范围**给重复段落加注释标记（与块索引那条路径并存）

    ## 为什么需要"按行范围"这条路

    `generate_clean_copy` 标记重复靠的是 `block_index` → 重新分块查行范围。
    而段落级去重（`find_duplicate_paragraphs`）与块级去重**不是同一个坐标系**
    （段落 vs 500 字检索块），所以它的结果不能混进同一次调用；
    而"跑两次 `generate_clean_copy`"也不行 —— 第二次会重新输出整篇，
    把第一次的标记覆盖掉。

    段落级的结果**自带** `start_line` / `end_line`（它就是这么产出的），
    所以这条路直接用行范围，不需要任何坐标翻译。

    ## 不重复标记

    已经在 `<!-- duplicate: … -->` 块里的行**不再包一层**（否则嵌套注释会让
    Markdown 渲染出乱码）。判据是"这一行是否落在某个已标记区间内"。

    Args:
        text: 已有块级标记的清洗副本
        duplicates: 段落级重复列表，每项含 `start_line` / `end_line` /
            `block_index` / `similarity`

    Returns:
        (新文本, 实际标记的条数)
    """
    if not duplicates:
        return text, 0

    lines = text.split("\n")

    # 先算出"已经被块级标记覆盖"的行区间，避免嵌套
    already: List[Tuple[int, int]] = []
    open_at: Optional[int] = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("<!-- duplicate:") and open_at is None:
            open_at = i
        elif stripped.startswith("<!-- /duplicate -->") and open_at is not None:
            already.append((open_at, i))
            open_at = None

    def _covered(start: int, end: int) -> bool:
        return any(start <= b and a <= end for a, b in already)

    # 收集要标记的区间（按起始行降序插入，这样前面的插入不影响后面的行号）
    targets: List[Tuple[int, int, Dict]] = []
    for d in duplicates:
        start = int(d.get("start_line") or 0)
        end = int(d.get("end_line") or start)
        if end < start:
            start, end = end, start
        if start < 0 or end >= len(lines):
            continue
        if _covered(start, end):
            continue
        targets.append((start, end, d))

    targets.sort(key=lambda t: t[0], reverse=True)

    marked = 0
    for start, end, d in targets:
        lines.insert(end + 1, "<!-- /duplicate -->")
        lines.insert(
            start,
            f"<!-- duplicate: block_{d.get('block_index')} "
            f"similarity={float(d.get('similarity') or 0):.2f} -->",
        )
        marked += 1

    return "\n".join(lines), marked


def generate_clean_copy(
    original_text: str,
    cleaned_text: str,
    duplicate_blocks: List[Dict],
    total_blocks: Optional[int] = None,
    granularity: str = "chunk",
) -> Tuple[str, Dict[str, int]]:
    """
    根据去重结果生成清洗副本

    对于被标记为重复的块，用 HTML 注释包裹，用户可随时恢复。
    保留首个出现的重复块，其余标记为重复。

    注意：分块（`segment_with_offsets`）产出的 chunk 行范围可能因 overlap
    而重叠，因此不能按 chunk 遍历输出行（会导致重叠行重复输出），
    改为逐行遍历，每行只输出一次。

    Args:
        original_text: 原始 Markdown 文本
        cleaned_text: 经过规则清洗后的 Markdown 文本
        duplicate_blocks: 重复块列表，每个包含：
            - block_index: 块序号
            - duplicate_of: 首次出现的块序号
            - similarity: 相似度分数
        total_blocks: 分块总数。**调用方已经算过就务必传进来** ——
            它与 `metadata.total_chunks` 同源，两个字段因此不可能打架；
            不传则本函数在有重复块时自行重新分块得出（见下方 2026-10-21 的说明）。
        granularity: `"chunk"`（默认）或 `"paragraph"` —— **必须与检测那一侧的
            粒度一致**。`duplicate_blocks` 里的 `block_index` 是检测时的块索引，
            而"检索块"与"段落"不是同一个坐标系；传错会让标记打在错误的行上
            （且不会报错，只是标错）。
    Returns:
        Tuple[str, Dict[str, int]]: (清洗副本文本, 清洗统计信息)
    """
    from .markdown_segmenter import segment_with_offsets, to_retrieval_chunks

    stats = {
        "duplicate_blocks_marked": 0,
        "total_blocks": 0,
        #: 去重用的粒度（`chunk` = 检索块 / `paragraph` = 空行分界的段落）
        #:
        #: 它存在的唯一理由是**让界面能如实描述**：改造前界面上那句
        #: 「本次未检测到重复块」在按 500 字块去重时是**静默说谎** ——
        #: 段落级的重复根本不在它的判据里。有了这个字段，文案可以写成
        #: "按段落/按 500 字块"。
        "granularity": "chunk",
    }

    if not cleaned_text:
        return cleaned_text, stats

    # ⚠️ 2026-10-21 修（真实用户走查发现）：
    #    `total_blocks` 原先**只在"有重复块"的分支里**被赋值（原来那行
    #    `stats["total_blocks"] = len(chunks)` 在下面的分块之后）。于是
    #    **没有重复块的文档 —— 也就是绝大多数文档 —— 返回的 copy_stats 里
    #    `total_blocks` 恒为 0**，与同一份 metadata 里的 `total_chunks` 自相矛盾。
    #    实测：一篇 6 块的笔记 `total_chunks: 6` / `copy_stats.total_blocks: 0`，
    #    而界面「总分块: 6」用的是前者，所以用户看不出、接口却在说谎。
    #    现在先把值落下来，再走后续分支。
    if total_blocks is not None:
        stats["total_blocks"] = int(total_blocks)

    # 如果没有重复块，直接返回清洗后的文本
    #
    # ⚠️ `granularity` 必须**在早退之前**就按调用方给的粒度写进 stats：
    # 界面那句"未检测到重复"要说清是**按什么粒度**找的。第一版把它放在
    # 早退之后，于是"没有重复"时永远报 `chunk` —— 而"没有重复"恰恰是
    # 那句文案出现的**唯一**场景，等于这个字段在最需要它的时候失效。
    stats["granularity"] = "paragraph" if granularity == "paragraph" else "chunk"
    if not duplicate_blocks:
        return cleaned_text, stats

    # 构建需要标记的块索引集合
    duplicate_indices = set()
    duplicate_map = {}
    for dup in duplicate_blocks:
        idx = dup["block_index"]
        duplicate_indices.add(idx)
        duplicate_map[idx] = dup
        stats["duplicate_blocks_marked"] += 1

    # 重新分块，获取行范围信息
    #
    # 阶段 2.1：改用 markdown_segmenter 的统一分块。这里必须与
    # `clean_tasks` 的分块**完全一致**（同一个函数、同一个 chunk_size），
    # 否则 block_index 对不上，标记的重复块就不是检测到的那一块。
    # 曾经的 `split_into_chunks` 与 markdown_segmenter 是两套独立实现，
    # 同一文档会产生不同的块边界 —— 这正是 S-3 那类缺陷的温床。
    #
    # ⚠️ 2026-10-22（B1 第 ④ 项）：分块粒度必须与**检测时的粒度一致**。
    #
    # `duplicate_blocks` 里的 `block_index` 是"检测那一侧"的块索引：
    #   - `granularity="chunk"`（默认）：按检索块（`settings.chunk_size`）检出来的；
    #   - `granularity="paragraph"`：按**段落**（空行分界）检出来的
    #     （`find_duplicate_paragraphs`）。
    #
    # 两套索引**不是同一个坐标系**。所以这里必须按同一个粒度重新分块，
    # 否则 `block_index` 会指到错误的块上 —— 症状是"标记出来的重复块
    # 不是检测到的那一块"，而且**不会报错**（只是标错行）。
    if granularity == "paragraph":
        chunks = segment_paragraphs(cleaned_text)
    else:
        chunks = to_retrieval_chunks(
            segment_with_offsets(cleaned_text, settings.chunk_size)
        )
    # 调用方传了就用它的（与 metadata.total_chunks 同源，保证两字段一致）；
    # 没传才用本地重新分块的结果。
    if total_blocks is None:
        stats["total_blocks"] = len(chunks)

    if not chunks:
        return cleaned_text, stats

    lines = cleaned_text.split("\n")

    # 构建"行 → 所属 chunk"映射
    # 如果一行同时被 duplicate 和 non-duplicate chunk 覆盖，
    # 优先视为 non-duplicate（保留首次出现，不标记为重复）
    line_in_non_dup: set[int] = set()
    line_to_dup_info: Dict[int, Dict] = {}  # line_idx -> dup_info

    for chunk in chunks:
        chunk_index = chunk["index"]
        for line_idx in range(chunk["start_line"], chunk["end_line"] + 1):
            if chunk_index in duplicate_indices:
                if line_idx not in line_to_dup_info:
                    line_to_dup_info[line_idx] = duplicate_map[chunk_index]
            else:
                line_in_non_dup.add(line_idx)

    # 仅当一行只被 duplicate chunk 覆盖时，才标记为 duplicate
    duplicate_lines: set[int] = set()
    for line_idx in line_to_dup_info:
        if line_idx not in line_in_non_dup:
            duplicate_lines.add(line_idx)

    # 逐行输出，每行只输出一次
    result_lines = []
    in_duplicate = False

    for line_idx in range(len(lines)):
        is_dup = line_idx in duplicate_lines

        if is_dup and not in_duplicate:
            dup_info = line_to_dup_info[line_idx]
            # 注释标记使用重复块自身的 block_index（与前端传参、
            # metadata duplicates_detail 的 block_index 一致），
            # 旧实现误用 duplicate_of（保留块 index）导致恢复/删除无法匹配（见 docs/decisions.md#F-11）
            result_lines.append(
                f"<!-- duplicate: block_{dup_info['block_index']} "
                f"similarity={dup_info['similarity']:.2f} -->"
            )
            in_duplicate = True

        if not is_dup and in_duplicate:
            result_lines.append("<!-- /duplicate -->")
            in_duplicate = False

        result_lines.append(lines[line_idx])

    if in_duplicate:
        result_lines.append("<!-- /duplicate -->")

    return "\n".join(result_lines), stats


def restore_block(clean_text: str, block_index: int) -> str:
    """
    恢复被标记为重复的块

    移除指定块的 duplicate 注释标记，使其内容正常显示。

    Args:
        clean_text: 清洗后的 Markdown 文本
        block_index: 要恢复的块序号

    Returns:
        str: 恢复后的 Markdown 文本
    """
    # 查找匹配的 duplicate 注释块
    pattern = re.compile(
        rf"<!-- duplicate: block_{block_index} similarity=[\d.]+ -->\n(.*?)\n<!-- /duplicate -->",
        re.DOTALL,
    )
    # 移除注释标记，保留内容
    result = pattern.sub(r"\1", clean_text)
    return result


def delete_block(clean_text: str, block_index: int) -> str:
    """
    彻底删除被标记为重复的块

    连同内容和注释标记一起删除。

    Args:
        clean_text: 清洗后的 Markdown 文本
        block_index: 要删除的块序号

    Returns:
        str: 删除后的 Markdown 文本
    """
    # 查找匹配的 duplicate 注释块并整体删除
    pattern = re.compile(
        rf"<!-- duplicate: block_{block_index} similarity=[\d.]+ -->\n.*?\n<!-- /duplicate -->\n*",
        re.DOTALL,
    )
    result = pattern.sub("", clean_text)
    return result


# ============================================================
# 无模型兜底去重（嵌入模型不可用时的降级方案）
# ============================================================

def _normalize_for_dedup(text: str) -> str:
    """归一化文本用于去重比较：压缩连续空白并去除首尾空白、忽略大小写"""
    return " ".join(text.split()).lower()


def _text_similarity(a: str, b: str, threshold: float) -> float:
    """
    基于标准库的文本相似度

    精确相等返回 1.0；否则用 difflib.SequenceMatcher 计算相似度。
    先用 quick_ratio（ratio 的快速上界，O(n) 代价）做预过滤，
    只有可能达到阈值的块对才执行精确的 ratio 计算，控制最坏情况开销。

    Args:
        a: 归一化后的文本 A
        b: 归一化后的文本 B
        threshold: 相似度阈值

    Returns:
        float: 相似度，低于阈值直接返回 0.0（避免无谓计算）
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # autojunk=False：默认的 autojunk 会把出现频率高的字符（中文高频字如"的/了/。"）
    # 当作垃圾丢弃，导致中文文本相似度被严重低估（实测 2% 差异的文本 ratio 仅 0.5）
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    # quick_ratio 是 ratio 的快速上界：上界都达不到阈值，真实值必然低于阈值
    if matcher.quick_ratio() < threshold:
        return 0.0
    return matcher.ratio()


#: 段落级去重里"一个段落块"的长度上限
#:
#: ⚠️ **不能靠 `segment_with_offsets(text, 1)` 拿段落** —— 实测那样会得到
#: **65 个"段落"**（5 行文本），因为 `limit` 小于块长度时，分块器会把**每个块
#: 按字符拆开**（`markdown_segmenter.py:662-669` 的"超限块必须拆开"）。
#: 于是 `#`、`。` 这种单字符块互相相似度 1.0，去重结果里塞满垃圾。
#:
#: 所以段落级自己走**块级**切分：`_split_blocks_with_offsets` 拿块与偏移，
#: 每个块自成一个段落，再翻译成行范围。超长块（> 此上限）仍然按安全边界拆，
#: 避免一个 5000 字的块把两处重复藏在一起。
PARAGRAPH_MAX_CHARS = 800


def segment_paragraphs(text: str) -> List[Dict]:
    """把文本切成**段落级**块（以**空行**为界，超长段再按安全边界拆）

    返回的字典与 `to_retrieval_chunks(...)` 同形（含 `index` / `content` /
    `start_line` / `end_line` / `char_start` / `char_end`），所以下游的
    `find_duplicates_lightweight` 与 `generate_clean_copy` 都能直接吃。

    ## 为什么不用 `segment_with_offsets(text, 1)`（第一版就是这么写的）

    实测那样会得到 **65 个"段落"**（对 5 行文本），因为 `limit` 一旦小于块长度，
    分块器会把**每个块按字符拆开**（`markdown_segmenter.py:662-669`
    "超限块必须拆开"）。于是 `#`、`。` 这种单字符块互相相似度 1.0，
    去重结果里塞满垃圾（实测 34 条"重复"，全是标点）。

    ## 判据：空行分界

    "段落"在 Markdown 里就是**空行分隔的文本块** —— 这也是用户口语里的段落。
    所以先按空行切成段；段内若还超过 `PARAGRAPH_MAX_CHARS`，再按安全边界
    （换行/句末标点）拆，避免一个 5000 字的段把两处重复藏在一起。

    ## 不变量

    `text[char_start:char_end] == content` —— 与检索分块同一条不变量
    （它在开发中被违反过两次，各 1318 个分段，见 `docs/overhaul-plan.md` 附录 L.2）。
    按行边界构造区间，这条不变量由构造保证。
    """
    if not text or not text.strip():
        return []

    lines = text.split("\n")
    # 每行首字符在原文中的下标（与 `_split_blocks_with_offsets` 同一手法）
    line_starts: List[int] = []
    _off = 0
    for line in lines:
        line_starts.append(_off)
        _off += len(line) + 1  # +1 为 "\n"

    def _line_of(char_idx: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= char_idx:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # 按空行切段：连续非空行算一段
    spans: List[Tuple[int, int]] = []
    cur_start_line: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip():
            if cur_start_line is None:
                cur_start_line = i
        elif cur_start_line is not None:
            spans.append((cur_start_line, i - 1))
            cur_start_line = None
    if cur_start_line is not None:
        spans.append((cur_start_line, len(lines) - 1))

    # 超长段再拆（按字符找安全边界，仍落在行内）
    final_spans: List[Tuple[int, int]] = []
    for start_line, end_line in spans:
        seg = "\n".join(lines[start_line : end_line + 1])
        if len(seg) <= PARAGRAPH_MAX_CHARS:
            final_spans.append((start_line, end_line))
            continue
        # 逐行累积，超过上限就封口（行是天然的安全边界）
        chunk_start = start_line
        acc = 0
        for ln in range(start_line, end_line + 1):
            add = len(lines[ln]) + (1 if ln > chunk_start else 0)
            if acc and acc + add > PARAGRAPH_MAX_CHARS:
                final_spans.append((chunk_start, ln - 1))
                chunk_start = ln
                acc = len(lines[ln])
            else:
                acc += add
        final_spans.append((chunk_start, end_line))

    out: List[Dict] = []
    for start_line, end_line in final_spans:
        char_start = line_starts[start_line]
        char_end = line_starts[end_line] + len(lines[end_line])
        content = text[char_start:char_end]
        if not content.strip():
            continue
        out.append({
            "index": len(out),
            "content": content,
            "char_start": char_start,
            "char_end": char_end,
            "start_line": start_line,
            "end_line": end_line,
            "char_count": len(content),
        })

    # 自证不变量（与 `index_note_chunks` 同一条判据）：切片必须逐字等于 content。
    # 违反时**返回空列表**而不是返回错位的块 —— 错位的块会让下游把
    # 重复标记打在错误的行上，而那种错误是静默的。
    for p in out:
        if text[p["char_start"] : p["char_end"]] != p["content"]:
            logger.error(
                "段落切分不变量被破坏（返回空列表，不做段落级去重）: "
                "index=%s chars=%s-%s",
                p["index"], p["char_start"], p["char_end"],
            )
            return []

    return out


def find_duplicate_paragraphs(
    cleaned_text: str,
    chunk_duplicates: List[Dict],
    threshold: float = 0.0,
) -> List[Dict]:
    """**段落级**去重（B1 第 ④ 项）：找出"整段重复"的那些块

    ## 它补的是什么缺口

    检索分块是 `chunk_size=500`（`config.py`），于是一段 100 字的重复文字
    会和它前后文**落在同一个 500 字块里** —— 块与块的相似度因此远低于阈值，
    检测不到。走查报告 §5 实测：样本原文里刻意重复的段落**仍出现 2 次**，
    而界面上却显示「本次未检测到重复块」。

    **这是"静默说谎"**：用户看到的是"没有重复"，事实是"我们按 500 字找的、
    找不到段落级的重复"。两个修法各做一半 —— 检测放到段落级（本函数），
    文案如实描述粒度（前端）。

    ## 为什么用**文本**相似度而不是嵌入

    段落级重复绝大多数是**逐字重复**（页眉残留、复制粘贴、MinerU 把同一段
    切到相邻页）。嵌入模型在 500 字块上算的向量对 100 字段落本来就不敏感，
    而且这一步要能在**没有模型**的降级路径（`dedup_mode=lightweight`）里也工作。
    所以走 `find_duplicates_lightweight` 那条纯文本路径。

    ## 与块级结果的关系：**并列，不覆盖**

    块级去重能发现"大段改写式重复"（语义相同、用词不同），段落级能发现
    "短段逐字重复"。两者找到的是不同的东西，所以结果是**并集**：
    以段落块的 `start_line` 为键去重（同一段落块只报一次）。

    Args:
        cleaned_text: 规则清洗后的 Markdown（与块级去重同一份输入）
        chunk_duplicates: 块级去重的结果（只用来判断"这个行范围已经报过了"）
        threshold: 相似度阈值，0 表示用配置默认值

    Returns:
        List[Dict]: 与块级同形的重复列表（`block_index` / `duplicate_of` /
        `similarity`），**外加** `start_line` / `end_line` ——
        因为段落块的索引与检索块的索引不是同一套坐标，下游必须按行范围定位。
    """
    paragraphs = segment_paragraphs(cleaned_text)
    if len(paragraphs) < 2:
        return []

    dupes = find_duplicates_lightweight(paragraphs, threshold=threshold)
    if not dupes:
        return []

    # 已经由块级去重覆盖过的行范围：不重复报（否则界面上同一条重复会出现两次，
    # 一次按 500 字块、一次按段落 —— 用户看到的是"重复数虚高"）
    covered: List[Tuple[int, int]] = []
    for d in chunk_duplicates:
        rng = d.get("line_range")
        if rng:
            covered.append((int(rng[0]), int(rng[1])))

    by_index = {p["index"]: p for p in paragraphs}
    out: List[Dict] = []
    for d in dupes:
        para = by_index.get(d["block_index"])
        if para is None:
            continue
        start = int(para.get("start_line") or 0)
        end = int(para.get("end_line") or 0)
        if any(start <= c_end and c_start <= end for c_start, c_end in covered):
            continue
        out.append({
            **d,
            "start_line": start,
            "end_line": end,
            "granularity": "paragraph",
        })
    return out


def find_duplicates_by_embedding(
    chunks: List[Dict],
    embeddings: List[List[float]],
    threshold: float = 0.0,
) -> List[Dict]:
    """基于嵌入向量的块级去重（阶段 2.4 收尾：取代 Chroma 往返）

    ## 为什么把这段逻辑从 `VectorStore.find_duplicates` 搬出来

    原实现把向量写进 Chroma，随后 `find_duplicates` 再 `collection.get()` 把
    **全部向量读回来**，然后在 Python 里做两两循环 —— 它**并没有用到向量库的
    检索能力**（不是 HNSW 近邻查询，就是两个嵌套 for）。也就是说整趟
    Chroma 往返只是为了把刚算好的向量存起来又取回来。

    改为直接消费 `clean_tasks` 里已经算好的 `embeddings`：**数学完全相同**
    （同一个 `compute_similarity`、同样的两两循环、同样的 overlap 跳过规则），
    但省掉一次写库 + 一次读库，也让清洗流程不再依赖 Chroma。

    ## 去重策略（与 `find_duplicates_lightweight` / 旧 VectorStore 一致）

    - 保留首次出现的块（`block_index` 较小者）
    - 跳过行范围有重叠的块对（重叠是分块策略的正常结果，非真正重复）
    - 已被标记为重复的块不再作为"首次出现"，也不重复标记
    - 按相似度降序返回

    ## 阈值口径（一个既有风险，如实标注）

    `compute_similarity` 返回余弦相似度 ∈ [-1,1]，而
    `settings.similarity_threshold`（默认 0.92）的注释写的是"去重相似度阈值"，
    同时又被 `find_duplicates_lightweight` 用作**文本**相似度阈值。
    两个不同量纲共用一个配置值，属于既有设计问题（§2.1 S-3 那一类）。
    这里保持与旧实现**相同的取值**，不擅自调整 —— 换阈值会改变写入的
    clean.md 内容，必须单独评估。

    Args:
        chunks: 分块列表（需含 `index` 与 `start_line`/`end_line`）
        embeddings: 与 `chunks` **一一对应**的向量列表
        threshold: 相似度阈值，0 表示用配置默认值

    Returns:
        List[Dict]: 重复块列表（`block_index` / `duplicate_of` / `similarity`）
    """
    if not chunks or not embeddings or len(chunks) != len(embeddings):
        # 长度不一致说明调用方传错了 —— 宁可不做去重，也不要错配向量与文本
        if chunks and embeddings and len(chunks) != len(embeddings):
            logger.warning(
                "去重跳过：chunks(%d) 与 embeddings(%d) 数量不一致",
                len(chunks), len(embeddings),
            )
        return []

    threshold = threshold or settings.similarity_threshold
    n = len(chunks)
    if n < 2:
        return []

    from .embedding_service import EmbeddingService

    duplicates = []
    already_duplicate = set()

    for i in range(n):
        if chunks[i]["index"] in already_duplicate:
            continue
        i_start = chunks[i].get("start_line", 0)
        i_end = chunks[i].get("end_line", 0)

        for j in range(i + 1, n):
            if chunks[j]["index"] in already_duplicate:
                continue
            # 跳过行范围有重叠的块对
            j_start = chunks[j].get("start_line", 0)
            j_end = chunks[j].get("end_line", 0)
            if i_start <= j_end and j_start <= i_end:
                continue

            similarity = EmbeddingService.compute_similarity(embeddings[i], embeddings[j])
            if similarity >= threshold:
                duplicates.append({
                    "block_index": chunks[j]["index"],
                    "duplicate_of": chunks[i]["index"],
                    "similarity": similarity,
                })
                already_duplicate.add(chunks[j]["index"])

    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return duplicates


def find_duplicates_lightweight(
    chunks: List[Dict],
    threshold: float = 0.0,
) -> List[Dict]:
    """
    无模型兜底去重：基于归一化文本相似度查找重复块

    嵌入模型不可用（如内存不足导致加载失败）时的降级方案，仅使用标准库，
    内存占用可忽略。语义与 VectorStore.find_duplicates 保持一致：
    - 保留首次出现的块，后续相似块标记为重复
    - 跳过行范围有重叠的块对（重叠是分块策略的正常结果，非真正重复）
    - 按相似度降序排列

    Args:
        chunks: 分块列表，每个包含 index、content、start_line、end_line
        threshold: 相似度阈值，0 表示使用配置默认值

    Returns:
        List[Dict]: 重复块列表，每个包含：
            - block_index: 重复块的索引
            - duplicate_of: 首次出现的块索引
            - similarity: 相似度分数
    """
    threshold = threshold or settings.similarity_threshold

    n = len(chunks)
    if n < 2:
        return []

    normalized = [_normalize_for_dedup(chunk["content"]) for chunk in chunks]

    duplicates = []
    already_duplicate = set()  # 已被标记为重复的块索引

    for i in range(n):
        if i in already_duplicate:
            continue  # 已被标记为重复的块不再作为"首次出现"

        for j in range(i + 1, n):
            if j in already_duplicate:
                continue  # 已被标记为重复的块跳过

            # 跳过行范围有重叠的块对（重叠是分块策略的正常结果，非真正重复）
            i_start = chunks[i].get("start_line", 0)
            i_end = chunks[i].get("end_line", 0)
            j_start = chunks[j].get("start_line", 0)
            j_end = chunks[j].get("end_line", 0)
            if i_start <= j_end and j_start <= i_end:
                continue

            similarity = _text_similarity(normalized[i], normalized[j], threshold)
            if similarity >= threshold:
                # j 是 i 的重复块
                duplicates.append({
                    "block_index": chunks[j]["index"],
                    "duplicate_of": chunks[i]["index"],
                    "similarity": similarity,
                })
                already_duplicate.add(j)

    # 按相似度降序排列
    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return duplicates
