#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡片向量补算（关系判定提速的前提）

把还没有向量的知识卡片编码并写进 `knowledge_cards` 的
`embedding` / `embedding_model` / `embedding_dim` / `has_embedding` 四列。

## 为什么需要它

关系建议原本**每次点击都现算**向量：200 张 73 秒、全量 1181 张 **275 秒**，
而 `POST /graph/suggest` 的 Celery `task.get` 超时是 **180 秒** ——
于是 `MAX_CARDS_FOR_SUGGEST = 200` 这个上限**是被超时逼出来的**，
而它正是"1061 张卡片一条边都没有"的主因（**904 张根本进不了候选池**）。

向量存下来之后，每次点击只做 numpy：1181² 余弦矩阵实测 **10 ms**。

## 它**不**做什么

不重算已有向量。嵌入模型换了要重算时，把 `has_embedding` 置回 False
（或按 `embedding_model` 过滤）另行处理 —— 重算全量是 275 秒，
不该混进"补缺失"这条日常路径。

## 口径

调用的是**应用自己的 Celery 任务** `app.tasks.embedding_tasks.embed_cards`
（在**本进程内**直接执行，不需要 worker 在跑），因此：

- 编码文本口径与 `graph_service._build_card_embedding_text` 一致（任务内部 import 它）
- 打包方式与 `chunks` 表一致（复用 `models/chunk.py` 的 `pack_vector`）

脚本自己不另写一套编码或打包 —— 分叉了就会产生"库里存的向量与现算的不是同一个东西"。

## 用法

    python scripts/embed_cards.py --check              # 只看还差多少张
    python scripts/embed_cards.py                      # 补到没有为止（全量约 275 秒 + 模型加载）
    python scripts/embed_cards.py --limit 50           # 只补 50 张
    python scripts/embed_cards.py --batch-size 100     # 调小单批（内存紧时用）

退出码：0 = 跑完了；2 = **检查自身空转**（库读不到 / 没有待补的卡 / 用户不存在）。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "engramnote.db"
)


def _fail_vacuous(problem: str) -> int:
    print("!" * 72)
    print(f"[ERROR] {problem}")
    print("        退出码 2 = 检查自身空转（0 才代表「跑完了」）。")
    print("!" * 72)
    return 2


def _db_path() -> str:
    """库文件路径

    ## ⚠️ 必须用 `get_database_url()`，不能读 `settings.database_url`

    `Settings.database_url` 是个**默认空串的字段**（`app/config.py:104`），
    只在用户显式配置了连接串时才有值；实际的库路径由
    `Settings.get_database_url()`（`app/config.py:725`）按 `DB_DIR` 算出来。

    本脚本原先三处都写成 `get_settings().database_url` —— 于是 url 是空串、
    `path` 也是空串，`os.path.exists("")` 为假，脚本直接以
    「库不存在：」退出码 2 结束。**功能完全不可用**，而报错信息里
    库名是空的、看不出原因（2026-10-20 实测踩到）。

    其他脚本的写法是对的，见 `scripts/migrate_project_tags.py:101`。
    """
    from app.config import get_settings

    url = get_settings().get_database_url()
    return url.split("///")[-1] if "///" in url else url


def _pending(user_id: str) -> int:
    """还差多少张（同步 sqlite 只读，避免为了一个计数拉起整个 ORM 栈）"""
    import sqlite3

    con = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT count(*) FROM knowledge_cards c LEFT JOIN notes n ON n.id = c.note_id "
            "WHERE c.user_id = ? AND (n.trashed_at IS NULL) AND (c.has_embedding = 0 OR c.has_embedding IS NULL)",
            (user_id,),
        ).fetchone()[0]
    finally:
        con.close()


def _resolve_user(user_id: str) -> str:
    import sqlite3

    con = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    try:
        if user_id:
            row = con.execute(
                "SELECT user_id FROM knowledge_cards WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
            return row[0] if row else ""
        row = con.execute(
            "SELECT user_id FROM knowledge_cards GROUP BY user_id "
            "ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else ""
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="给知识卡片补嵌入向量（写入 knowledge_cards）")
    ap.add_argument("--user-id", default="", help="用户 ID（默认取卡片最多的那个）")
    ap.add_argument("--batch-size", type=int, default=200, help="单批处理多少张（默认 200）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少张（0 = 不限）")
    ap.add_argument("--check", action="store_true", help="只检查，不做改动")
    args = ap.parse_args()

    path = _db_path()
    if not os.path.exists(path):
        return _fail_vacuous(f"库不存在：{path}")

    user_id = _resolve_user(args.user_id)
    if not user_id:
        return _fail_vacuous("找不到任何有卡片的用户 —— 库是空的，或 --user-id 写错了")

    pending = _pending(user_id)
    print(f"用户 {user_id} ｜ 待补向量 {pending} 张")
    if pending == 0:
        print("没有需要补的卡片 —— 检查自身空转，退出码 2")
        return 2
    if args.check:
        print("（--check：只看不做）")
        return 0

    from app.tasks.embedding_tasks import embed_cards

    budget = args.limit if args.limit > 0 else pending
    done = 0
    round_no = 0
    while done < budget:
        take = min(args.batch_size, budget - done)
        round_no += 1
        print(f"\n--- 第 {round_no} 批（最多 {take} 张）---")
        # 直接调用任务函数：Celery 任务的 `__call__` 会在**本进程内**执行，
        # 不需要 worker 在跑（脚本本来就是为了"没有 worker 时也能补"）
        result = embed_cards(user_id, take)
        print(f"    扫描 {result['scanned']} ｜ 写入 {result['embedded']} "
              f"｜ 失败 {result['failed']} ｜ 剩余 {result['remaining']}")
        if result["scanned"] == 0:
            break
        done += result["embedded"]
        if result["embedded"] == 0:
            print("    本批一张都没写进去 —— 停止，避免死循环")
            return 1

    left = _pending(user_id)
    print(f"\n完成：本次写入约 {done} 张 ｜ 仍待补 {left} 张")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
