#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chunk 向量重嵌入（overhaul-plan 阶段 2.2′ B 半）
===============================================

把 `chunks` 表里的每个 chunk 用一个**统一模型**重新编码，写入
`embedding` / `embedding_model` / `embedding_dim` / `has_embedding`。

## 为什么必须重做（不是"可选优化"）

实测当前向量索引处于**分裂状态**（附录 N）：

| collection 标记的模型 | 向量数 | 维度 | 运行时能否检索 |
|---|---|---|---|
| `BAAI/bge-small-zh-v1.5` | 509 | 512 | ❌ 维度不匹配 |
| （无模型记录） | 477 | 1024 | ⚠️ 靠异常兜底 |
| `BAAI/bge-m3` | 204 | 1024 | ✅ |

运行时只加载一个模型，因此 **1190 条里只有 681 条（57%）可检索**。
"保持现状"不是一个选项 —— 现状就是 43% 的向量取不到。

## 为什么按 chunk 逐条落库、且支持续跑

重嵌入需要加载 `bge-m3`（4356MB）。本机可用内存虽然充裕，
但**中途失败会留下半新半旧的索引**，那种状态比"完全没有向量"更难排查
（部分 chunk 是新模型、部分是旧模型，余弦相似度无法跨模型比较）。

因此本脚本：

1. **只处理 `has_embedding = 0` 的行** —— 已嵌入的行在续跑时自动跳过
2. **每个批次单独提交** —— 中断只影响当前批次，不产生"半行"
3. **每批检查可用内存** —— 低于安全线主动中止并报告进度，而不是等 OOM
4. **向量与模型名、维度一同写入** —— 维度不一致时 `unpack_vector` 返回 None，
   宁可"没有向量"也不给一个无意义的数字（它会静默参与排序）

## 用法

    python scripts/embed_chunks.py --check          # 只看内存与待处理量
    python scripts/embed_chunks.py --batch-size 8   # 小批量（默认）
    python scripts/embed_chunks.py --limit 50       # 先试 50 条
    python scripts/embed_chunks.py                  # 全部（可反复运行续跑）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

#: 编码开始前要求的最低可用内存（GB）。
#:
#: bge-m3 权重约 4.36GB，加载后还要留出激活与 Python 堆的空间。
#: 5.0 是在本机实测后的保守值：低于它时宁可不动手 ——
#: 编码到一半 OOM 会留下部分新、部分旧的向量，而那种混合索引
#: 无法跨行比较相似度（见模块 docstring）。
MIN_FREE_GB_BEFORE = 5.0

#: 编码过程中每批检查的安全线。低于它立即停止（已写入的批次仍然有效）
MIN_FREE_GB_DURING = 0.6


def _free_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return float("inf")


def _log(msg: str) -> None:
    print(msg, flush=True)


async def run(args) -> int:
    from sqlalchemy import func, select, update

    from app import database as db_mod
    from app.config import get_settings
    from app.models.chunk import Chunk, pack_vector

    settings = get_settings()
    await db_mod.init_db()
    session_factory = db_mod.get_session_factory()

    # ---- 1. 待处理量与内存检查 ----
    async with session_factory() as db:
        pending = (await db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.has_embedding.is_(False))
        )).scalar() or 0
        done = (await db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.has_embedding.is_(True))
        )).scalar() or 0

    free = _free_gb()
    _log(f"配置模型        : {settings.embedding_model}")
    _log(f"待嵌入 chunk    : {pending}（已嵌入 {done}）")
    _log(f"可用内存        : {free:.2f} GB（开始前要求 ≥ {MIN_FREE_GB_BEFORE} GB）")

    if pending == 0:
        _log("没有待嵌入的 chunk（全部已完成）。")
        return 0
    if args.check:
        _log("（--check 模式，未做任何改动）")
        return 0
    if free < MIN_FREE_GB_BEFORE:
        _log(f"可用内存不足（{free:.2f} GB < {MIN_FREE_GB_BEFORE} GB）。"
             f"请关闭占内存的程序后重试 —— 编码中途 OOM 会留下混合索引。")
        return 2

    # ---- 2. 加载模型 ----
    from app.services.embedding_service import EmbeddingService

    service = EmbeddingService()
    _log("加载嵌入模型 …（首次较慢，bge-m3 约 4.3GB）")
    probe = service.encode(["预检测试"])
    model_name = service.loaded_model_name
    dim = len(probe[0]) if probe else 0
    _log(f"模型已加载      : {model_name}  维度={dim}")
    _log(f"加载后可用内存  : {_free_gb():.2f} GB")

    if not model_name or not dim:
        _log("模型未成功加载，中止。")
        return 2
    if settings.embedding_model and model_name != settings.embedding_model:
        _log(f"⚠️ 实际加载的是 {model_name}，与配置的 {settings.embedding_model} 不一致。"
             f"这通常是内存不足触发了降级 —— 继续会把降级模型写进库，故中止。")
        return 2

    # ---- 3. 分批编码并落库 ----
    batch_size = max(1, args.batch_size)
    limit = args.limit or 10 ** 9
    written = 0
    batches = 0

    while written < limit:
        take = min(batch_size, limit - written)

        # 只取未嵌入的行；每批重新查询，天然支持续跑
        async with session_factory() as db:
            rows = list((await db.execute(
                select(Chunk.id, Chunk.content)
                .where(Chunk.has_embedding.is_(False))
                .order_by(Chunk.note_id, Chunk.index)
                .limit(take)
            )).all())

        if not rows:
            break

        ids = [r.id for r in rows]
        texts = [r.content or "" for r in rows]
        vectors = service.encode(texts)

        async with session_factory() as db:
            for cid, vec in zip(ids, vectors, strict=True):
                if vec is None:
                    continue
                await db.execute(
                    update(Chunk).where(Chunk.id == cid).values(
                        embedding=pack_vector(vec),
                        embedding_model=model_name,
                        embedding_dim=len(vec),
                        has_embedding=True,
                    )
                )
            # 每批单独提交：中断只影响当前批次
            await db.commit()

        written += len(ids)
        batches += 1
        if batches % 5 == 0 or written >= pending:
            _log(f"  已写入 {written}/{min(pending, limit)}   "
                 f"可用内存 {_free_gb():.2f} GB")

        if _free_gb() < MIN_FREE_GB_DURING:
            _log(f"可用内存降至 {_free_gb():.2f} GB（安全线 {MIN_FREE_GB_DURING} GB），"
                 f"主动停止。已写入 {written} 条**有效**，重跑本脚本可继续。")
            return 3

    _log("")
    _log(f"完成: 本次写入 {written} 条向量（模型 {model_name}，{dim} 维）")

    # ---- 4. 收尾自检 ----
    async with session_factory() as db:
        still = (await db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.has_embedding.is_(False))
        )).scalar() or 0
        models = list((await db.execute(
            select(Chunk.embedding_model, func.count())
            .where(Chunk.has_embedding.is_(True))
            .group_by(Chunk.embedding_model)
        )).all())
        dims = list((await db.execute(
            select(Chunk.embedding_dim, func.count())
            .where(Chunk.has_embedding.is_(True))
            .group_by(Chunk.embedding_dim)
        )).all())

    _log(f"未嵌入剩余      : {still}")
    _log(f"向量按模型分布  : {models}")
    _log(f"向量按维度分布  : {dims}")
    if still:
        _log("（未全部完成，重跑本脚本可继续）")
        return 0
    if len(models) > 1 or len(dims) > 1:
        _log("⚠️ 库中存在多个模型/维度的向量 —— 这正是要消除的状态。")
        return 1
    _log("单一模型、单一维度，2.2′ 验收第 2 条达成。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Chunk 向量重嵌入（阶段 2.2′ B 半）")
    ap.add_argument("--batch-size", type=int, default=8, help="每批编码条数（默认 8）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少条")
    ap.add_argument("--check", action="store_true", help="只检查，不做改动")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
