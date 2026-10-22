# -*- coding: utf-8 -*-
"""A2 + A3′ 的重跑驱动器（**只在副本上跑**）

## 这个脚本存在的理由

计划要"三版提示词 × 全量重跑"，而 `backend/scripts/_e2e_full_runner.py` 是给
Playwright 当 `webServer` 用的监督器 —— 它起栈、被探活、然后被 Playwright 杀掉。
重跑需要的是"起栈 → 逐笔记驱动 → 收数据 → 出报告"，所以这里复用它的
**env 约定**（`ENGRAMNOTE_E2E_TMP` 指副本根）并自己按需起停。

## 🔴 硬守卫：指向真库就退出

`POST /understanding/{note_id}/start` 会删掉该笔记的**全部卡片 / 题目 /
复习记录 / 图谱关系**（`backend/app/api/understanding.py:165-190`）。
真库现有 3469 卡片 / 3358 题目 / 604 关系 —— 真跑就是清空重来。

所以脚本第一件事是解析 `--tmp` 下的 db 路径并**逐字比对真库路径**，
相同即 `SystemExit`。**不提供 `--force`**：一个能被绕过的守卫等于没有守卫。

## ⚠️ 三版不能同时存在 —— 必须逐版快照

`start` 会删掉上一篇的产物，所以 v2 会把 v1 的题目删掉。
"三版对照"因此**不能靠事后查库**，必须在每版跑完后**立刻快照**：
把该笔记的 `quiz_items`（题型/难度/prompt_version）与卡片数落盘到 JSON，
再跑下一版。快照是这份对照实验的**原始数据**，丢了就得重花额度。

## 用法

    python scripts/rerun_understanding.py --tmp data/_uxwalk --limit 3 --versions 1,2,3
    python scripts/rerun_understanding.py --tmp data/_uxwalk --versions 1,2,3 --skip-done

产物：`<tmp>/rerun/snapshot-v{N}.json`（逐笔记落盘，中断可续）
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BACKEND_ROOT / "scripts"

#: 真库路径（**守卫的比对基准**）。任何解析结果等于它就退出。
REAL_DB = (BACKEND_ROOT / "data" / "db" / "engramnote.db").resolve()

#: 与 `_e2e_full_runner.py` 的默认值保持一致，可用环境变量覆盖
API_PORT = int(os.environ.get("ENGRAMNOTE_E2E_API_PORT", "4382"))
CONTROL_PORT = int(os.environ.get("ENGRAMNOTE_E2E_CONTROL_PORT", "4383"))
#: 监督器写死的 throwaway 密钥（`_e2e_full_runner.py:324`）—— 本脚本签 token 要用同一个
JWT_SECRET = "e2e-full-throwaway-secret-not-used-anywhere-else-0123456789"

#: 轮询上限：单篇理解 + 出题在 31 篇的库里最慢的实测是分钟级
POLL_TIMEOUT_S = 1800
POLL_INTERVAL_S = 5
#: 网关限流（C-1 记的 10 RPM）⇒ 串行 + 退避
RETRY_MAX = 5
RETRY_BASE_S = 4.0


def _log(msg: str) -> None:
    print(f"[rerun] {msg}", flush=True)


# --------------------------------------------------------------------------
# 守卫
# --------------------------------------------------------------------------

def resolve_copy_db(tmp_root: Path) -> Path:
    """解析副本 db 路径并**硬守卫**

    ## ⚠️ 第一版这个守卫是**空转的**，已改成正向要求

    第一版写的是"解析出的 db == 真库路径就退出"。实测**永远不会触发**：
    真库叫 `data/db/engramnote.db`，而副本叫 `<tmp>/db/engram.db` ——
    **文件名不同**，路径相等这一条不可能成立。一个永不触发的守卫等于没有守卫，
    而它还会让人以为"有保护"。

    改成**正向要求**（比否定式强得多）：目标必须在仓库约定的**可弃副本目录**里，
    即 `data/` 下**以 `_` 开头**的那几个（`_uxwalk` / `_a1p0_probe` /
    `_understand_test_tmp` —— 这是本仓库既有的命名约定）。
    另外显式拒绝真库目录与真库文件名，两层都要过。
    """
    db = (tmp_root / "db" / "engram.db").resolve()
    real_dir = REAL_DB.parent.resolve()

    # ① 显式拒绝真库所在目录（含它下面任何东西）
    if real_dir == db.parent or real_dir in db.parents:
        raise SystemExit(
            f"[rerun] 拒绝执行：目标在真库目录里（{db}）。\n"
            f"        真库目录：{real_dir} —— 重跑会删除卡片/题目/复习记录/图谱关系。"
        )
    # ② 显式拒绝真库文件名
    if db.name == REAL_DB.name:
        raise SystemExit(f"[rerun] 拒绝执行：目标文件名与真库同名（{db.name}）")
    # ③ 正向要求：必须在 `data/` 下以 `_` 开头的可弃副本目录里
    data_dir = (BACKEND_ROOT / "data").resolve()
    if data_dir in db.parents:
        rel = db.parent.relative_to(data_dir)
        top = rel.parts[0] if rel.parts else ""
        if not top.startswith("_"):
            raise SystemExit(
                f"[rerun] 拒绝执行：{db.parent} 不在可弃副本目录里。\n"
                f"        本仓库的约定是副本目录以 `_` 开头（如 data/_uxwalk）。\n"
                f"        真库是 {REAL_DB}，重跑会清空该笔记的全部学习成果。"
            )
    if not db.exists():
        raise SystemExit(f"[rerun] 副本数据库不存在：{db}")
    return db


# --------------------------------------------------------------------------
# 隔离栈
# --------------------------------------------------------------------------

def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_stack(tmp_root: Path, python_exe: str) -> subprocess.Popen:
    """起隔离栈（uvicorn + worker），返回监督器进程"""
    env = dict(os.environ)
    env.update(
        {
            "ENGRAMNOTE_E2E_TMP": str(tmp_root),
            "ENGRAMNOTE_E2E_KEEP": "1",
            "ENGRAMNOTE_E2E_API_PORT": str(API_PORT),
            "ENGRAMNOTE_E2E_CONTROL_PORT": str(CONTROL_PORT),
            "ENGRAMNOTE_E2E_PYTHON": python_exe,
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.Popen(
        [python_exe, str(SCRIPTS_DIR / "_e2e_full_runner.py")],
        cwd=str(BACKEND_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _log(f"已起隔离栈（监督器 pid={proc.pid}），等控制口 {CONTROL_PORT} 就绪…")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() if proc.stdout else b"").decode("utf-8", "replace")
            raise SystemExit(f"[rerun] 监督器提前退出（code={proc.returncode}）：\n{out[-2000:]}")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{CONTROL_PORT}/health", timeout=2
            ) as r:
                if r.status == 200:
                    _log("隔离栈就绪（API + worker 都在）")
                    return proc
        except Exception:
            pass
        time.sleep(2)
    proc.terminate()
    raise SystemExit("[rerun] 等隔离栈就绪超时（300s）")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def api(method: str, path: str, token: str, body: Optional[dict] = None) -> Any:
    """调一次 API，带退避重试（限流/5xx）"""
    url = f"http://127.0.0.1:{API_PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_err: Optional[str] = None
    for attempt in range(RETRY_MAX):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", "replace")
            # 4xx（除 429）是确定性错误，重试无意义
            if e.code != 429 and e.code < 500:
                raise RuntimeError(f"{method} {path} → {e.code} {body_txt[:300]}") from None
            last_err = f"{e.code} {body_txt[:200]}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        wait = RETRY_BASE_S * (2**attempt)
        _log(f"  重试 {attempt + 1}/{RETRY_MAX}（{last_err}），等 {wait:.0f}s")
        time.sleep(wait)
    raise RuntimeError(f"{method} {path} 连续失败：{last_err}")


def mint_token(user_id: str) -> str:
    """用监督器那套 throwaway 密钥签一个 token（只在进程内，不写盘）"""
    os.environ["JWT_SECRET_KEY"] = JWT_SECRET
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.services.auth_service import create_access_token

    return create_access_token(user_id)


# --------------------------------------------------------------------------
# 快照
# --------------------------------------------------------------------------

def snapshot_note(db_path: Path, note_id: str, version: str) -> Dict[str, Any]:
    """直接从副本库里读这篇笔记的产物分布（只读连接）

    ⚠️ **必须在跑下一版之前调用** —— `start` 会删掉这一版的产物。
    """
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        c = con.cursor()
        cards = c.execute(
            "SELECT count(*) FROM knowledge_cards WHERE note_id=?", (note_id,)
        ).fetchone()[0]
        rows = c.execute(
            "SELECT question_type, difficulty, prompt_version FROM quiz_items WHERE note_id=?",
            (note_id,),
        ).fetchall()
        rels = c.execute(
            """SELECT count(*) FROM card_relations r
               WHERE r.card_id_1 IN (SELECT id FROM knowledge_cards WHERE note_id=?)
                  OR r.card_id_2 IN (SELECT id FROM knowledge_cards WHERE note_id=?)""",
            (note_id, note_id),
        ).fetchone()[0]
        return {
            "note_id": note_id,
            "version": version,
            "cards": cards,
            "quizzes": len(rows),
            "relations": rels,
            "by_type": dict(Counter(r[0] for r in rows)),
            "by_difficulty": dict(Counter(r[1] for r in rows)),
            "by_prompt_version": dict(Counter(r[2] for r in rows)),
        }
    finally:
        con.close()


def usage_delta(db_path: Path, since_iso: str) -> Dict[str, Any]:
    """这一版期间新产生的 LLM 调用（用于额度记账）

    ## ⚠️ `since_iso` 必须是 **UTC** —— 第一版用本地时间，结果恒为 0

    `llm_calls.created_at` 由 SQLite 的 `server_default=func.now()` 生成
    （`app/models/base.py:75-78`），而 SQLite 的 `now()` 是 **UTC**。
    第一版传的是 `time.strftime(...)`（本地时间，UTC+8），比真实值**晚 8 小时**
    ⇒ `created_at >= since` 永远不成立 ⇒ 每次快照都记 `tokens=0`。

    实测证据：跑小样时本地是 `11:42`，副本里刚落库的那条是 `03:42` —— 正好差 8 小时。
    """
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        c = con.cursor()
        row = c.execute(
            """SELECT count(*), coalesce(sum(total_tokens),0), coalesce(sum(cached),0)
               FROM llm_calls WHERE created_at >= ?""",
            (since_iso,),
        ).fetchone()
        return {"calls": row[0], "tokens": row[1], "cached_hits": row[2]}
    finally:
        con.close()


def utc_now_iso() -> str:
    """UTC 时间戳（与 `llm_calls.created_at` 同一时区，见 `usage_delta`）"""
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def count_quizzes(db_path: Path, note_id: str) -> int:
    """这篇笔记当前有几道题（用于等 Celery 出题任务落库）"""
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT count(*) FROM quiz_items WHERE note_id=?", (note_id,)
        ).fetchone()[0]
    finally:
        con.close()


def wait_for_questions(
    db_path: Path, note_id: str, *, timeout_s: int = 300, interval_s: int = 3
) -> int:
    """等出题任务落库

    ## 为什么必须等 —— 第一版没等，快照全是 0 题

    `POST /understanding/{note_id}/generate-questions` 是
    **`generate_questions_task.delay(...)` 后立即返回**（`api/understanding.py:774-782`），
    返回体里的 `question_count` 恒为 **0**（字段语义是"已触发"不是"已生成"）。
    第一版紧接着就查库 ⇒ 每次都记到 0 题 ⇒ **小样的全部意义（验证题型分布）
    直接落空**。

    判据用"连续两次计数相同且 > 0"而不是"计数 > 0"：出题是分批写入的，
    只判 > 0 会抓到**半份**结果，而题型分布正是要拿来做结论的东西。
    """
    deadline = time.monotonic() + timeout_s
    last = -1
    stable = 0
    while time.monotonic() < deadline:
        n = count_quizzes(db_path, note_id)
        if n > 0 and n == last:
            stable += 1
            if stable >= 2:
                return n
        else:
            stable = 0
        last = n
        time.sleep(interval_s)
    return max(last, 0)


def generate_questions_with_retry(
    db_path: Path,
    note_id: str,
    version: str,
    token: str,
    *,
    attempts: int = 4,
    wait_s: int = 300,
) -> int:
    """触发出题，并在产出 0 题时**重新入队**

    ## 为什么需要这层重试 —— 实跑踩到

    出题任务会因为**瞬时 DNS 失败**整体失败：
    `[Errno 11001] getaddrinfo failed` ⇒ 网关调用重试 5 次仍失败
    ⇒ `generate_questions_task` 每个批次都失败 ⇒ 该版本产出 **0 道题**。

    实测证据（`data/_uxwalk/logs/errors.log`，2026-10-22 12:13）：同一笔记的
    批次 1/2/3 全部「LLM API 调用失败，重试 5 次后仍出错」。而同期从**新进程**
    连测 30 次 DNS 是 30/30 成功 —— 说明是 worker 进程在负载下的瞬时解析失败，
    不是配置错误。

    ⚠️ 没有这层重试的后果**不是「少几道题」**：`start` 已经删掉了旧题目
    （`api/understanding.py:188`），所以 0 题意味着这篇笔记在这个版本下
    **完全没有产物** —— 而对照实验正是要拿这些产物做分布比较。
    第一版就是这么白等 900s 并记下 0 题的。
    """
    n = 0
    for attempt in range(1, attempts + 1):
        api(
            "POST",
            f"/api/understanding/{note_id}/generate-questions?prompt_version={version}",
            token,
        )
        n = wait_for_questions(db_path, note_id, timeout_s=wait_s)
        if n > 0:
            if attempt > 1:
                _log(f"    出题在第 {attempt} 次尝试后成功：{n} 道")
            return n
        if attempt < attempts:
            _log(f"    ⚠ 出题为 0（第 {attempt}/{attempts} 次），重新入队…")
    _log(f"    ✘ 出题 {attempts} 次尝试后仍为 0 道")
    return n


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def pick_notes(
    db_path: Path,
    owner_id: str,
    limit: Optional[int],
    min_cards: int = 0,
    note_id: str = "",
    only_ids: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """挑要重跑的笔记：**按卡片数升序**取前 N（小样先挑小的，快且省）

    ## 为什么要 `--min-cards`

    实测副本里有 **4 篇 `cleaned` 且 0 卡**的笔记（从未跑过理解）。
    不加下限时 `--limit 3` 会正好挑中它们 —— 那种小样**验证不了任何东西**：
    没有旧产物就无从对比"题型是不是变了"，而小样的全部意义就是
    "先确认提示词真的修好了再烧那 9M"（计划原文：**不修就白跑**）。
    所以小样应当挑**有产物但体量小**的笔记（实测 14 / 23 / 26 卡那几篇）。
    """
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        c = con.cursor()
        sql = """SELECT n.id, n.title, n.status,
                        (SELECT count(*) FROM knowledge_cards k WHERE k.note_id=n.id) AS nc
                 FROM notes n
                 WHERE n.user_id=? AND n.trashed_at IS NULL
                   AND (SELECT count(*) FROM knowledge_cards k WHERE k.note_id=n.id) >= ?
                   AND (? = '' OR n.id = ?)
                 ORDER BY nc ASC"""
        rows = c.execute(sql, (owner_id, min_cards, note_id, note_id)).fetchall()
        out = [{"id": r[0], "title": r[1], "status": r[2], "cards": r[3]} for r in rows]
        # `only_ids`：**在同一批语料上做三版对照**。重跑会改变卡片数，所以
        # "再取前 N 篇"取到的不是同一批 —— 必须显式指定，否则版本差异与
        # 笔记差异混在一起，结论无效。
        if only_ids:
            order = {nid: i for i, nid in enumerate(only_ids)}
            out = [r for r in out if r["id"] in order]
            out.sort(key=lambda r: order[r["id"]])
        return out[:limit] if limit else out
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="A2+A3′ 重跑驱动器（只跑副本）")
    ap.add_argument("--tmp", required=True, help="副本根（含 db/engram.db）")
    ap.add_argument("--owner", default="7775422b-ca78-4389-8bea-051a149e9cad", help="笔记归属用户")
    ap.add_argument("--versions", default="1,2,3", help="要跑的提示词版本，逗号分隔")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 篇（按卡片数升序）")
    ap.add_argument(
        "--min-cards",
        type=int,
        default=0,
        help="只跑卡片数 >= N 的笔记（小样应当设成 10 左右，见 pick_notes 的说明）",
    )
    ap.add_argument("--skip-done", action="store_true", help="快照里已有该版本就跳过")
    ap.add_argument("--note-id", default="", help="只跑这一篇（验证修复 / 补跑个别笔记）")
    ap.add_argument(
        "--note-ids-from",
        default="",
        help="从已有快照 JSON 里读 note_id 列表，只跑这些（保证三版对照是同一批语料）",
    )
    ap.add_argument("--no-stack", action="store_true", help="栈已在跑（不自己起）")
    args = ap.parse_args()

    tmp_root = Path(args.tmp).resolve()
    db_path = resolve_copy_db(tmp_root)
    _log(f"副本数据库：{db_path}")
    _log(f"真库（只用于比对，绝不写入）：{REAL_DB}")

    out_dir = tmp_root / "rerun"
    out_dir.mkdir(parents=True, exist_ok=True)

    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    only_ids: Optional[List[str]] = None
    if args.note_ids_from:
        src = Path(args.note_ids_from)
        assert src.exists(), f"找不到快照文件：{src}"
        snap = json.loads(src.read_text(encoding="utf-8"))
        only_ids = [r["note_id"] for r in snap.get("runs", []) if r.get("note_id")]
        # 只保留成功的那些（失败轮不该进对照组）
        only_ids = [
            r["note_id"] for r in snap.get("runs", []) if r.get("note_id") and "error" not in r
        ]
        _log(f"从 {src.name} 读到 {len(only_ids)} 篇（仅成功轮）")

    notes = pick_notes(
        db_path, args.owner, args.limit, args.min_cards, args.note_id, only_ids
    )
    _log(f"要重跑 {len(notes)} 篇 × {len(versions)} 版 = {len(notes) * len(versions)} 轮")
    for n in notes:
        _log(f"  · {n['title'][:34]:36s} 卡片={n['cards']:4d} 状态={n['status']}")

    stack: Optional[subprocess.Popen] = None
    if not args.no_stack:
        stack = start_stack(tmp_root, sys.executable)

    try:
        token = mint_token(args.owner)
        _log("已签发 token")

        grand: Dict[str, Any] = {"versions": versions, "notes": notes, "runs": []}

        for version in versions:
            snap_path = out_dir / f"snapshot-v{version}.json"
            done_ids = set()
            if args.skip_done and snap_path.exists():
                prev = json.loads(snap_path.read_text(encoding="utf-8"))
                done_ids = {r["note_id"] for r in prev.get("runs", [])}
                grand["runs"].extend(prev.get("runs", []))
                _log(f"v{version}：已有快照，跳过 {len(done_ids)} 篇")

            _log(f"===== 版本 v{version} =====")
            for i, note in enumerate(notes, 1):
                if note["id"] in done_ids:
                    continue
                t0 = time.monotonic()
                started_iso = utc_now_iso()  # ⚠️ UTC：llm_calls.created_at 是 UTC，见 usage_delta
                _log(f"[v{version} {i}/{len(notes)}] {note['title'][:30]} 开始理解…")
                try:
                    api(
                        "POST",
                        f"/api/understanding/{note['id']}/start",
                        token,
                        {"confirm": True},
                    )
                    # 轮询到终态
                    deadline = time.monotonic() + POLL_TIMEOUT_S
                    status = None
                    while time.monotonic() < deadline:
                        st = api("GET", f"/api/understanding/{note['id']}/status", token)
                        status = (st or {}).get("status")
                        if status in ("archived", "cleaned", "learning_failed", "cleaning_failed"):
                            break
                        time.sleep(POLL_INTERVAL_S)
                    _log(f"    理解终态：{status}")
                    if status not in ("archived", "cleaned"):
                        raise RuntimeError(f"理解未成功，终态 {status}")

                    n_q = generate_questions_with_retry(
                        db_path, note["id"], version, token
                    )
                    _log(f"    出题落库：{n_q} 道")
                    snap = snapshot_note(db_path, note["id"], version)
                    snap["usage"] = usage_delta(db_path, started_iso)
                    snap["elapsed_s"] = round(time.monotonic() - t0, 1)
                    snap["title"] = note["title"]
                    grand["runs"].append(snap)
                    _log(
                        f"    ✔ 卡片={snap['cards']} 题目={snap['quizzes']} "
                        f"题型={snap['by_type']} 难度={snap['by_difficulty']} "
                        f"tokens={snap['usage']['tokens']} {snap['elapsed_s']}s"
                    )
                except Exception as e:  # noqa: BLE001
                    _log(f"    ✘ 失败：{e}")
                    grand["runs"].append(
                        {
                            "note_id": note["id"],
                            "title": note["title"],
                            "version": version,
                            "error": str(e),
                        }
                    )
                # 逐笔记落盘：中断可续，且**绝不在跑下一版前丢掉这一版的数据**
                snap_path.write_text(
                    json.dumps(
                        {"version": version, "runs": [r for r in grand["runs"] if r.get("version") == version]},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

        (out_dir / "all-runs.json").write_text(
            json.dumps(grand, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _log(f"全部完成，原始数据：{out_dir / 'all-runs.json'}")
        return 0
    finally:
        if stack is not None:
            _log("停隔离栈…")
            stack.terminate()
            try:
                stack.wait(timeout=20)
            except subprocess.TimeoutExpired:
                stack.kill()


if __name__ == "__main__":
    sys.exit(main())
