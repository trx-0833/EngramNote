# `backend/data/` 数据盘点与删除决策报告（D1）

> **状态：过程记录（2026-10-22 的一次只读盘点）。**
> 不是活文档 —— 数字是那一天的实测值，会随使用变化。
> 需要更新时**重跑盘点脚本**，不要手改数字。

## 0. 这份报告是什么、不是什么

**是**：`backend/data/` 下每一样东西的「谁在引用 / 删了会怎样 / 怎么恢复」，
以及一份「现在就能删 / 要先确认 / 不确定 → 不动」的三档清单。

**不是**：删除指令。**本报告没有删除、移动或修改任何一个字节**
（生成它只用 `os.walk` + `stat`）。`docs/agent-workstream-plan.md` §5 第 6 条
已拍板：**D1 只产出清单，任何删除动作都要另开一轮单独批、单独备份**。

⚠️ 这条约束不是形式主义：`AGENTS.md` §6 记着一条历史事故 ——
**「清理副本却删掉真身」**。本报告第 4 节专门标了"副本"与"真身"的区分方法。

---

## 1. 实测总览（2026-10-22）

盘点根目录 `backend/data/`，**总计 6.8 GB / 约 1000 个文件**：

| 条目 | 大小 | 文件数 | 最近修改 | 性质 |
|---|---:|---:|---|---|
| `models/` | **4.4 GB** | 42 | 2026-08-13 | 🟢 **在用**（嵌入模型缓存） |
| `mineru/` | **2.0 GB** | 17 | 2026-10-21 | 🟠 **当前不用**（本地 MinerU 模型） |
| `_uxwalk/` | 155.2 MB | 184 | 2026-10-21 | 🟠 隔离副本（A2/A3′ 重跑用） |
| `storage/` | 116.7 MB | 132 | 2026-10-20 | 🔴 **绝不能删**（用户文件） |
| `chroma/` | 53.9 MB | 457 | 2026-09-11 | 🟠 **历史数据**（已无代码引用） |
| `db/` | 45.3 MB | 7 | 2026-10-21 | 🔴 **绝不能删**（主库） |
| `_a1p0_probe/` | 34.9 MB | 3 | 2026-10-22 | 🟢 本轮工作的探针产物 |
| `logs/` | 18.0 MB | 9 | 2026-10-22 | 🟠 运行日志 |
| `_understand_test_tmp/` | 300 KB | 1 | 2026-08-15 | 🟢 历史临时目录 |
| `celery/` | 2 KB | 10 | 2026-10-22 | 🟢 运行期（broker） |
| `tmp/` | 1 KB | 132 | 2026-10-22 | 🟢 运行期（上传暂存） |
| `engramnote.db` | **0 B** | 1 | 2026-06-27 | 🟢 空壳残留 |
| `tmp_test/` | 0 B | 0 | — | 🟢 空目录 |

最大的 10 个文件里有 **4.2 GB 是同两份 bge-m3 权重**（见 §2.1）。

---

## 2. 逐项：引用者 / 删除影响 / 恢复方式

### 2.1 `models/` —— 4.4 GB 🟢 **在用，但内部有约 2.1 GB 重复候选**

**引用者**：`app/services/embedding_service.py:271-275`
（`snapshot_download(cache_dir=...)` 下载到本项目 `data/models/`，避免占 C 盘）。
嵌入模型是**检索与去重的核心**，`AGENTS.md` §3 明确它必须隔离在异步 worker 进程。

**删除影响**：删掉 ⇒ 下次启动 worker 时**重新下载 bge-m3（约 2.1 GB）**。
不丢数据，但要等下载，且离线环境下直接不可用。

**⚠️ 实测发现一处可疑重复**：

| 文件 | 大小 |
|---|---:|
| `models/Xorbits/bge-m3/pytorch_model.bin` | 2.1 GB |
| `models/Xorbits/bge-m3/model.safetensors` | 2.1 GB |

同一份权重存了两种格式。`transformers` 现代版本**优先加载 `.safetensors`**，
所以 `.bin` 很可能是纯冗余。**但本报告不主张删它** ——
判据是"加载器是否只用 safetensors"，那需要读当时的 `transformers` 版本行为，
而"看起来没人用"正是历史上删错的那一类。**归入第 5 节「要先确认」**。

**恢复方式**：删了由 `snapshot_download` 自动重下；或从备份复制。
**回滚命令**（如果要删）：无（下载即恢复）。

---

### 2.2 `mineru/` —— 2.0 GB 🟠 **当前不用，但切回本地解析就要它**

**引用者**：`backend/.env:55` 的 `MINERU_HOME=D:\engramnote\backend\data\mineru`，
配合 `MINERU_BACKEND=vlm-http-client`（`:52`）。

**关键判据**：`vlm-http-client` = **走云端**，本地模型**不被加载**。
所以这 2.0 GB 现在是闲置的。

**删除影响**：把 `MINERU_BACKEND` 切回 `pipeline`（本地解析）时，
4.x 会报「模型未就绪」，需要重新下载（约 2.0 GB，且要能访问模型源）。
**当前功能不受影响** —— PDF 转换走 `mineru.net` 云端 API。

**⚠️ 计划里的数字已过时**：`agent-workstream-plan.md:396` 写的是
「约 **4.6 GB** 闲置本地 MinerU 模型」，实测**只有 2.0 GB** ——
因为 `docs/mineru-backend-decision.md:17` 记着「已回收 6.66 GB」。
**以本报告的实测值为准**（`AGENTS.md` §1：代码/实测 > 文档）。

**恢复方式**：重新下载（`MinerU-4_models_onnx` 约 0.9 GB +
`MinerU2.5-Pro-2605-1.2B-GGUF` 约 2.1 GB，见 `mineru-backend-decision.md:63-64`）。

---

### 2.3 `_uxwalk/` —— 155.2 MB 🟠 **隔离副本，A2/A3′ 重跑要用**

**引用者**：
- `agent-workstream-plan.md:464`（决策 1：A2/A3 在副本上重跑）
- `agent-workstream-plan.md:537`（`ENGRAMNOTE_E2E_TMP` 指向它）
- `docs/graph-relation-methods.md:23`（§0/§2 的分布统计就跑在它上面）

**内容**：`celery/` `db/` `logs/` `mineru_probe/` `vault/` + 9 个一次性探针脚本
（`_analyze.py` / `_graphdiag.py` / `_probe.py` / `_sse.py` / `_understand_quality.py` 等）。

**删除影响**：**A2+A3′ 的副本重跑还没做**（见进度台账）——
删了它，那条工作流就得先重建隔离栈。
**它不在任何生产路径上**（`.gitignore:63` 覆盖，且服务不会连它）。

**恢复方式**：`ENGRAMNOTE_E2E_TMP='...\_uxwalk' ENGRAMNOTE_E2E_KEEP='1'`
跑一次 `_e2e_full_runner.py` 即重建（plan §6.2）。

---

### 2.4 `storage/` —— 116.7 MB 🔴 **绝不能删**

**引用者**：`app/config.py:35` `STORAGE_DIR = DATA_DIR / "storage"`；
上传/读取正文都走它（对象存储在本地文件系统上的落点）。

**删除影响**：**用户笔记的原始文件与清洗副本全在这里**。
删了 = 用户数据丢失，且**数据库里仍有指向它们的路径**（变成一整片
"笔记都在但每一篇都打不开"）。

⚠️ 这正是 `AGENTS.md` §6 警告的那个形态。**任何涉及 storage 的清理都要
先确认目标不是真身**（判据见 §4）。

**恢复方式**：只能从备份恢复 —— `data/backup/` 的周快照
（`maintenance_tasks.storage_snapshot`）或数据库备份里**不含**这些文件。
**没有它就真的没了。**

---

### 2.5 `chroma/` —— 53.9 MB 🟠 **历史数据，已无代码引用**

**实测判据（这一条是本轮最确凿的）**：

```
$ grep -r "import chromadb\|from chromadb" backend/app/    → 无结果
$ python -c "from app.config import get_settings; print(hasattr(get_settings(),'chroma_dir'))"
  has chroma_dir attr: False
```

`app/config.py:327-335` 自己写着：

> ── 已移除：`chroma_dir`（阶段 2.4 收尾，2026-09-11）──
> 原先指向 Chroma 持久化目录（`data/chroma/`）。检索与去重都已不再使用
> …磁盘上的 `data/chroma/`（54MB）已是历史数据，可删除

**114 个子目录**（每个约 214–414 KB）是 114 篇笔记的向量 collection，
向量已经搬进 `chunks` 表的 `embedding` 列。

**删除影响**：**无功能影响**（无代码读它）。唯一的"损失"是历史向量的原始副本
（本来也没有回滚用途 —— 重新嵌入即可重建）。

**恢复方式**：不需要。真要重建，重跑 `scripts/embed_chunks.py`。

---

### 2.6 `db/` —— 45.3 MB 🔴 **绝不能删**

| 文件 | 大小 | 说明 |
|---|---:|---|
| `engramnote.db` | 32.68 MB | **主库**（SQLite 单写者，见 `docs/sqlite-single-writer.md`） |
| `engramnote.db-wal` | 0.84 MB | WAL 日志 —— **删它会丢最近未 checkpoint 的写入** |
| `engramnote.db-shm` | 0.03 MB | 共享内存索引，**删了可能损坏库** |
| `engramnote.db.bak-20260815-200246` | 1.15 MB | 2026-08-15 的备份 |
| `engramnote.db.bak-merge-20260926-145145` | 10.52 MB | 2026-09-26 合并前备份 |
| `.schema-lock` | 0 B | 迁移锁 |
| `cleanup-notes-n1-n2.log` | 40 KB | 历史清理日志 |

**引用者**：`app/config.py:37` `DB_DIR = DATA_DIR / "db"`；`database_url` 由它派生。

**删除影响**：删主库 = 全部数据没了。**删 `-wal` / `-shm` 比删主库更危险** ——
它们看起来像"临时文件"，但 WAL 里可能有尚未 checkpoint 的事务。

**恢复方式**：`data/backup/` 的日备份（`maintenance_tasks.backup_database`，
Beat 每天 03:30）+ 周恢复演练（`restore_drill`）。

---

### 2.7 `_a1p0_probe/` —— 34.9 MB 🟢 **本轮工作的探针产物（我自己造的）**

**来源**：第 8 轮做 A1 P0 验收时，`_agenttmp/quota/a1p0_accept.py` 把真库
复制到这里跑 5 轮关系建议（避免写真库）。

**引用者**：**只有那个探针脚本**（`_agenttmp/quota/a1p0_accept.py`，gitignored）。
**删除影响**：无。它是**已经用完的**一次性副本。

**恢复方式**：重跑那个脚本即重建。
⚠️ 但它是**副本**，删它不影响真库（`db/engramnote.db`）—— 这一点在删除前
**必须再确认一次**（§4 的方法）。

---

### 2.8 `logs/` —— 18.0 MB 🟠 运行日志

**引用者**：`app/config.py:864` `get_logs_dir()` = `DATA_DIR / "logs"`。

**删除影响**：丢掉历史排障信息，**不影响功能**。
**恢复方式**：不可恢复（但日志本来就没有长期保留价值）。

---

### 2.9 运行期小目录 / 空壳

| 条目 | 大小 | 引用者 | 删除影响 |
|---|---:|---|---|
| `celery/` | 2 KB | `app/config.py:882` `get_celery_broker_url()` = `DATA_DIR/celery/broker` | ⚠️ **broker 目录，删了会丢排队中的任务**（见下） |
| `tmp/` | 1 KB | `app/config.py:40` `TMP_UPLOAD_DIR` | 只影响进行中的上传（会重传） |
| `engramnote.db`（根目录） | **0 B** | 无（真库在 `db/`） | 无 —— 6 月 27 日的空壳残留 |
| `tmp_test/` | 0 B | 无（空目录） | 无 |
| `_understand_test_tmp/` | 300 KB | 无（8 月 15 日的历史临时目录） | 无 |

**`celery/` 要单独说清**：它 2 KB 但**不是**"几乎空的所以能删"。
它是文件系统 broker，里面可能有**已投递未消费**的消息；
删掉等于丢弃排队中的任务（`config.py:871-882` 专门写了这条依赖）。
**归入「要先确认」**。

---

## 3. 附带发现：一个**坏掉的脚本**（不在 `data/` 里，但盘点时撞到）

`backend/scripts/cleanup_test_data.py` **在 import 阶段就崩**：

```
AttributeError: 'Settings' object has no attribute 'chroma_dir'
  at scripts/cleanup_test_data.py:43
```

**根因**：`chroma_dir` 在阶段 2.4 已被移除（`app/config.py:327`），
但这个脚本还在读 `settings.chroma_dir`。

**两条附加事实**：
1. 它被 **`.gitignore:136` 忽略**（`git check-ignore` 命中），
   `git ls-files` 也查不到 —— 它是**本机文件，不在仓库里**；
2. 它带 `--apply`，是**能删数据的维护脚本**。

**为什么本报告只记不修**：修它需要先决定"chroma 清理那一整段还要不要"
（chroma 已无代码引用，那段是死代码）。而**让一个删数据的脚本重新能跑**
不属于"只读盘点"的范围 —— 要单独批。

**另有一处文档指引过时**（与本条相邻，可顺手修）：
`backend/tests/测试账号信息.md:35` 写的是 `python e2e_cleanup.py`，
而实测该脚本在 `backend/scripts/dev/e2e_cleanup.py`（**路径少了 `scripts/dev/`**）。
照文档做的人会得到 "can't open file"。这是纯文档错误，已在本轮修正。

---

## 4. 「副本 vs 真身」的区分方法（删除前必须先做这一步）

`AGENTS.md` §6 的警告针对的正是"以为在清副本、实际删了真身"。**三个判据，缺一不可**：

1. **看绝对路径**，不看相对路径 —— `data/db/engramnote.db` 是**真身**；
   `data/_uxwalk/db/engram.db`、`data/_a1p0_probe/db/engramnote.db` 是**副本**。
   （注意副本的文件名可能不同：`engram.db` vs `engramnote.db`。）
2. **看服务是否连着它** —— 真身是 `settings.database_url` 指向的那个；
   用 `python -c "from app.config import get_settings; print(get_settings().database_url)"`
   打印出来逐字比对。
3. **确认备份存在且可读** —— `db/` 有 `data/backup/` 日备；
   `storage/` **没有**随库备份（要单独快照）。

---

## 5. 三档清单

### 🟢 现在就能删（无引用、无功能影响）

| 目标 | 大小 | 为什么安全 |
|---|---:|---|
| `data/_a1p0_probe/` | 34.9 MB | **我自己第 8 轮造的探针副本**，唯一引用是 gitignored 的一次性脚本 |
| `data/engramnote.db`（根目录那个 0 字节文件） | 0 B | 空壳残留，无任何引用；真库在 `db/` |
| `data/tmp_test/` | 0 B | 空目录 |
| `data/_understand_test_tmp/` | 300 KB | 8 月 15 日的历史临时目录，无引用 |

合计约 **35 MB**。⚠️ 即便如此，**仍然要另开一轮单独批**（见 §0）。

### 🟠 要先确认（有引用或有余量，但判据清楚）

| 目标 | 大小 | 先确认什么 |
|---|---:|---|
| `data/chroma/` | 53.9 MB | 已确认无代码引用（§2.5）。**删前再跑一次 grep 确认没有回退** |
| `data/mineru/` | 2.0 GB | 确认 `.env:52` 仍是 `vlm-http-client`；切回 `pipeline` 就要重下 2.0 GB |
| `data/logs/` | 18.0 MB | 确认不需要历史排障信息（或先留最近 7 天） |
| `data/celery/` | 2 KB | **先确认没有排队任务**（它是 broker，不是垃圾目录） |
| `models/Xorbits/bge-m3/pytorch_model.bin` | 2.1 GB | 确认加载器只用 `.safetensors`。**"看起来重复"不等于"可以删"** |
| `db/*.bak-*`（两个备份） | 11.7 MB | 确认 `data/backup/` 里有更新的备份 |

合计潜在 **~4.2 GB**，其中 `mineru/` 与 `.bin` 那两份占了大头。

### 🔴 不确定 → 不动

| 目标 | 大小 | 为什么不动 |
|---|---:|---|
| `data/storage/` | 116.7 MB | **用户文件**。没有它就真的没了（备份不覆盖它） |
| `data/db/engramnote.db` + `-wal` + `-shm` | 33.6 MB | **主库**。`-wal`/`-shm` 看着像临时文件，实则含未 checkpoint 事务 |
| `data/_uxwalk/` | 155.2 MB | **A2/A3′ 重跑还没做**，删了要重建隔离栈。等那条工作流跑完再说 |

---

## 6. 如果要执行删除：必须的前置动作（本报告不执行）

1. **停服务**（uvicorn + worker + beat）—— 对着正在写的 SQLite 删文件是最危险的形态；
2. **先备份到 `data/backup/pre_d1_{时间戳}/`**（`cleanup_test_data.py` 的 `--apply`
   也是这个顺序，见该脚本 `:11`）；
3. **按 §4 三个判据逐项复核绝对路径**；
4. **逐项删、逐项验**，不要一次性 `Remove-Item -Recurse` 多个目标；
5. 删完跑一次 `/ready` + 一次真实问答（证明检索链路没被碰）。

**回滚命令**（按档位）：

```powershell
# 🟢 档：无状态，重建即可
# _a1p0_probe → 重跑 _agenttmp/quota/a1p0_accept.py
# 空壳/空目录 → 无可回滚内容

# 🟠 档：
# chroma    → 重跑 python scripts/embed_chunks.py（向量重建）
# mineru    → 重新下载模型（约 2.0 GB）
# logs      → 不可恢复
# celery    → 不可恢复（排队任务丢失）

# 🔴 档（不该走到这里；真走了只能靠备份）：
# db        → 从 data/backup/ 的日备恢复
# storage   → 从 data/backup/ 的存储快照恢复
```

---

## 7. 复现这份盘点的命令

本报告的数字来自一个**只读**脚本（`os.walk` + `stat`，无写操作）。
它放在 `_agenttmp/quota/d1_inventory.py`（gitignored，属一次性探针）：

```
cd backend
python ../_agenttmp/quota/d1_inventory.py
```

要更新这份报告就重跑它，**不要手改 §1 的数字** —— 手改的数字会与本报告
其余各节的引用关系脱节，而那种脱节没人会发现。
