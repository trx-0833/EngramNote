# 提示词三版对照报告（A2 + A3′）

> **状态**：2026-10-22 · **小样阶段结论**（3 篇 × 3 版，其中 1 篇因缺陷失败 ⇒ 有效样本 **2 篇**）。
> **数据来源**：`backend/data/_uxwalk/rerun/all-runs.json`（隔离栈实跑产物，逐轮落盘）。
> **未做**：全量三版重跑（31 篇）。本报告是"值不值得烧全量"的依据，不是最终结论。
> **真库零写入**：全部在副本 `backend/data/_uxwalk/` 上跑，真库 `data/db/engramnote.db` 未改动。

## 0. 一句话结论

**v1 确实坏（100% 四选一、100% 简单）；v2 修好了题型但把难度从"全简单"整体推成"全中等"；
v3 最好 —— 题型保持三类，且是唯一产出 `hard` 的版本。建议全量采用 v3。**

## 1. 原始数据

同两篇笔记 × 三版提示词（同一副本、同一模型 `deepseek-v4-flash`）：

| 笔记 | 卡片 | 版本 | 题目 | 题型分布 | 难度分布 | tokens |
|---|---|---|---|---|---|---|
| `sample-气系统运行技术标准` | 45 | **v1** | 45 | `choice: 45` | `easy: 45` | 0（全缓存） |
| | | **v2** | 30 | `choice: 12` `fill_blank: 10` `short_answer: 8` | `medium: 30` | 6,891 |
| | | **v3** | 30 | `choice: 3` `fill_blank: 16` `short_answer: 11` | `medium: 30` | 10,079 |
| `拉哇变电站柴油发电机运行技术标准学习笔记` | 162 | **v1** | 30 | `choice: 30` | `easy: 30` | 64,837 |
| | | **v2** | 30 | `choice: 13` `fill_blank: 12` `short_answer: 5` | `medium: 30` | 16,836 |
| | | **v3** | 30 | `choice: 16` `fill_blank: 8` `short_answer: 6` | `easy: 16` `medium: 13` `hard: 1` | 16,540 |

**额度消耗：三版合计 115,183 tokens**（v1 64,837 / v2 23,727 / v3 26,619）。
v1 偏贵是因为它含**首次理解**；v2/v3 的理解全部命中 `llm_cache`
（提示词版本只影响**出题**、不影响理解），所以边际成本只是出题。

## 2. 三条结论

### 2.1 v1 确实坏 —— 计划的判断被实测证实

75 道题（两篇合计）**全部**是 `choice`，难度**全部** `easy`。
这与计划 §2.3 的根因判断一致：「题型与难度写死在提示词里，
`understand_tasks.py:324` 也不传参数 → 现在重跑一万遍还是『简单四选一』」。

计划里「**不修就白跑**」这句话因此成立：直接全量重跑 v1 只会再生产一遍
全 choice/easy 的题。

### 2.2 v2 修好了题型，但**难度过校正**

v2 让三类题型都出现了（`fill_blank` / `short_answer` 从 0 变成 10–12 道），
这是决定性的改善。

**但难度从「全 easy」整体摆到「全 medium」** —— 两篇笔记都是 `medium: 30`，
**零多样性**。也就是说 v2 把"写死简单"换成了"写死中等"，
问题形态变了但没消失。

⚠️ 这一条**只有在实测数据里才看得见**：只看"有没有出现 medium/hard"
（计划 §A2 的验收口径）会判 v2 通过 —— 因为它确实产出了 `medium`。
是"分布"而不是"是否出现"暴露了过校正。

### 2.3 v3 最好 —— 唯一产出 `hard` 的版本

| 维度 | v1 | v2 | v3 |
|---|---|---|---|
| 题型多样性 | ❌ 1 种 | ✅ 3 种 | ✅ 3 种 |
| 难度多样性 | ❌ 1 档 | ❌ 1 档 | 🟡 **2 篇里 1 篇有 3 档** |
| 产出 `hard` | ❌ | ❌ | ✅（`拉哇…柴油发电机` 1 道） |
| 四选一占比 | 100% | 40% / 43% | **10% / 53%** |

v3 在 `sample-气系统` 上把四选一压到 **3/30（10%）**，`fill_blank` 成主力（16 道）——
这是三版里最接近"按比例分布"的一版。

## 3. 必须一起看的限制

1. **有效样本只有 2 篇**。第 3 篇（`35kV&10kV 配电装置运行技术标准`）在三版下
   **全部失败**，原因是 `POST /understanding/{id}/start` 的一个真实缺陷 ——
   见 §4。所以"v3 更好"是 **n=2** 的结论。
2. **难度多样性在 v3 下也不普遍**：2 篇里只有 1 篇出现 3 档，
   另一篇仍是 100% `medium`。**不要**据此承诺"v3 一定产出三档难度"。
3. **v1 那篇 45 道题的 `tokens=0`** 是缓存命中（同版本重跑免费），
   它的分布仍是 v1 的真实产出，但**不代表** v1 的典型成本。
4. 单篇题目数在 v2/v3 下都是 30 —— 而 v1 有 45 与 30 两种。
   **题目总数不完全由版本决定**，还受笔记长度与分批策略影响，
   不能把"题目变少"归因给 v3。

## 4. ★ 顺带发现的真实缺陷（阻塞 3 篇笔记）

**现象**：`POST /api/understanding/{note_id}/start` 返回 **500**，
三版重跑该笔记**每次都失败**。

**根因**（`data/_uxwalk/logs/errors.log`，request_id `a4867668942f4599959d64789a1d1407`）：

```
type=IntegrityError | detail=(sqlite3.IntegrityError) FOREIGN KEY constraint failed
[SQL: DELETE FROM knowledge_cards WHERE knowledge_cards.note_id = ?]
```

重跑流程（`backend/app/api/understanding.py:165-190`）按 **`note_id`** 删题目：

```python
await db.execute(sql_delete(QuizItem).where(QuizItem.note_id == note_id))   # :188
await db.execute(sql_delete(KnowledgeCard).where(KnowledgeCard.note_id == note_id))  # :189
```

但 `quiz_items.card_id → knowledge_cards.id` 是外键，而**题目可以引用别的笔记的卡片**。
这类跨笔记题目按 `note_id` 删不到，于是删卡片时撞外键。

**取证**：

| 项 | 实测 |
|---|---|
| 该笔记的卡片被**别的笔记**的题目引用 | **2 行**（笔记 `04ff65bc`「拉哇水电站…500kV…」的题引用了本篇的卡） |
| 全库这种跨笔记题目 | **8 条** |
| 受影响的"被引用笔记" | **3 篇**（这 3 篇重跑都会 500） |
| `quiz_items.card_id` 可否置空 | **NOT NULL** ⇒ 不能靠置空绕过 |

**这是产品语义问题，需要拍板**：修它必然要动**另一篇笔记**的数据。
三个选项见 §5，我没有自行选择 —— 按 `AGENTS.md` §6「改产品语义 / 删数据必须停下来问」。

## 5. 全量重跑前要定的事

| # | 问题 | 选项 |
|---|---|---|
| 1 | **跨笔记题目的外键缺陷怎么修？** | (a) 删掉那些跨笔记题目（**别的笔记会少题**）<br>(b) 把 `quiz_items.card_id` 改成可空 + 迁移，重跑时置空（**保留题、断链**）<br>(c) 重跑前先把这些题的 `card_id` 指向的卡片复制一份（**最复杂，不动别的笔记**） |
| 2 | **全量用哪一版？** | 建议 **v3**（§2.3）。但若要保守，v2 也能解决"全是四选一" |
| 3 | **全量范围** | 副本 31 篇全跑，还是排除已知会失败的 3 篇？ |

## 6. 复现方式

```powershell
cd backend
# 只在副本上跑；脚本内置硬守卫，指向真库直接退出
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" scripts\rerun_understanding.py `
    --tmp data/_uxwalk --min-cards 10 --limit 3 --versions 1,2,3
```

产物：`backend/data/_uxwalk/rerun/snapshot-v{1,2,3}.json`（逐版）与 `all-runs.json`（合计）。
