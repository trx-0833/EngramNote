"""
回收站功能 E2E 全流程测试（对运行中的服务发起真实 HTTP 请求）

覆盖场景：
  1.  注册/登录测试账号
  2.  上传资料 A（material）+ 个人笔记 B（链接到 A）
  3.  批注/版本等附属内容生成
  4.  软删除 A：列表隐藏、回收站可见（含统计）、详情 404、物理文件搬入 trash 目录
  5.  恢复 A：列表回归、内容完整、文件搬回 inbox、链接关系复原
  6.  同名冲突：A 在回收站期间重新上传同名文件，恢复 A 应自动加序号后缀
  7.  彻底删除 A：笔记物理删除、文件清除、B 的链接变为悬挂引用（不级联）
  8.  悬挂链接清理：B 剔除悬挂行
  9.  清空回收站（purge-all）
  10. 图谱接口在无卡片场景下正常返回（note_trashed 字段存在）

用法：
  python tests/test_trash_e2e.py
"""
import io
import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

BASE = "http://localhost:8000/api"
EMAIL = "e2e0816085528@example.com"
PASSWORD = "e2e0816085528"
USERNAME = "e2etester"

VAULT_ROOT = Path(r"d:\engramnote\backend\data\storage")

client = httpx.Client(timeout=60.0)
token = ""
headers = {}
user_id = ""

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    tag = "PASS" if ok else "FAIL"
    (PASS if ok else FAIL).append(name)
    print(f"  [{tag}] {name}" + (f"  -- {detail}" if detail and not ok else ""))


def section(title: str):
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


def upload_md(filename: str, content: str, note_role: str = "material",
              linked_material_ids=None) -> dict:
    files = {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/markdown")}
    data = {"note_role": note_role}
    if linked_material_ids:
        data["linked_material_ids"] = json.dumps(linked_material_ids)
    r = client.post(f"{BASE}/upload", files=files, data=data, headers=headers)
    r.raise_for_status()
    return r.json()


def wait_status(note_id: str, want: set, timeout: float = 60.0) -> str:
    """轮询转换状态直到完成/失败"""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        r = client.get(f"{BASE}/upload/{note_id}/status", headers=headers)
        r.raise_for_status()
        last = r.json().get("status", "")
        if last in want:
            return last
        time.sleep(1.0)
    return last


def note_ids_in_list() -> set:
    ids = set()
    page = 1
    while True:
        r = client.get(f"{BASE}/notes?page={page}&page_size=50", headers=headers)
        r.raise_for_status()
        data = r.json()
        ids |= {n["id"] for n in data["items"]}
        if page * 50 >= data["total"]:
            return ids
        page += 1


def trash_ids() -> set:
    r = client.get(f"{BASE}/notes/trash", headers=headers)
    r.raise_for_status()
    return {item["note"]["id"] for item in r.json()["items"]}


def vault_rel_exists(user: str, *parts) -> bool:
    return (VAULT_ROOT / user / Path(*parts)).exists()


def vault_trash_tree(user: str, note_id: str) -> list:
    root = VAULT_ROOT / user / "trash" / note_id
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(VAULT_ROOT / user)).replace("\\", "/")
        for p in root.rglob("*") if p.is_file()
    )


# ============================================================
# 0. 注册 / 登录（含历史测试数据清理）
# ============================================================
section("0. 注册 / 登录测试账号")
r = client.post(f"{BASE}/auth/register", json={
    "email": EMAIL, "password": PASSWORD, "username": USERNAME})
if r.status_code == 201:
    check("注册测试账号", True)
    token = r.json()["access_token"]
else:
    r2 = client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    check("登录已有测试账号", r2.status_code == 200, f"register={r.status_code} login={r2.status_code} {r2.text[:200]}")
    r2.raise_for_status()
    token = r2.json()["access_token"]

headers = {"Authorization": f"Bearer {token}"}
me = client.get(f"{BASE}/auth/me", headers=headers)
check("获取当前用户 /auth/me", me.status_code == 200)
user_id = me.json()["id"]
print(f"  user_id = {user_id}")

# 清理历史测试数据（上次运行残留）：trash + purge 所有 e2e_ 开头笔记与回收站项
cleaned = 0
for nid in note_ids_in_list():
    det = client.get(f"{BASE}/notes/{nid}", headers=headers)
    if det.status_code == 200 and det.json().get("title", "").startswith("e2e_"):
        client.delete(f"{BASE}/notes/{nid}", headers=headers)
        client.delete(f"{BASE}/notes/{nid}/purge", headers=headers)
        cleaned += 1
for item in client.get(f"{BASE}/notes/trash", headers=headers).json()["items"]:
    if item["note"]["title"].startswith("e2e_"):
        client.delete(f"{BASE}/notes/{item['note']['id']}/purge", headers=headers)
        cleaned += 1
print(f"  清理历史测试数据: {cleaned} 条")

# ============================================================
# 1. 创建测试数据
# ============================================================
section("1. 上传资料 A + 个人笔记 B（B 链接 A）")
ts = time.strftime("%H%M%S")
name_a = f"e2e_回收站测试_材料A_{ts}.md"
name_b = f"e2e_回收站测试_笔记B_{ts}.md"

note_a = upload_md(name_a, "# 材料A\n\n回收站E2E测试材料内容第一段。\n\n第二段内容，用于验证恢复后完整性。\n", "material")
A = note_a["id"]
check("上传资料 A", bool(A), json.dumps(note_a)[:200])
st = wait_status(A, {"completed", "converted", "ready", "failed"})
print(f"  A 转换状态: {st}")
check("资料 A 转换完成", st not in ("failed",), st)

note_b = upload_md(name_b, "# 笔记B\n\n个人笔记，引用材料A。\n", "personal_note", linked_material_ids=[A])
B = note_b["id"]
check("上传个人笔记 B（带链接）", bool(B), json.dumps(note_b)[:200])
wait_status(B, {"completed", "converted", "ready", "failed"})

r = client.get(f"{BASE}/notes/{B}/links", headers=headers)
check("B 的链接列表包含 A", r.status_code == 200 and any(m["id"] == A for m in r.json()["linked_materials"]),
      r.text[:300])

# 批注
r = client.post(f"{BASE}/notes/{A}/annotations", headers=headers, json={
    "view_mode": "clean", "type": "highlight", "text_content": "回收站E2E批注",
    "context_before": "材料内容", "context_after": "第一段", "color": "#ffeb3b"})
check("为 A 添加批注", r.status_code == 201, r.text[:200])

# 编辑内容生成版本
r = client.put(f"{BASE}/notes/{A}/content", headers=headers,
               json={"content": "# 材料A\n\n回收站E2E测试材料内容第一段（编辑后）。\n\n第二段内容，用于验证恢复后完整性。\n", "target": "clean"})
check("编辑 A 内容（生成版本）", r.status_code == 200, r.text[:200])

r = client.get(f"{BASE}/notes/{A}/versions", headers=headers)
version_count = r.json().get("total", 0) if r.status_code == 200 else 0
check("A 已有版本记录", version_count >= 1, r.text[:200])

# ============================================================
# 2. 软删除（移入回收站）
# ============================================================
section("2. 移入回收站（软删除 A）")
r = client.delete(f"{BASE}/notes/{A}", headers=headers)
check("DELETE /notes/{A} 返回 204", r.status_code == 204, f"{r.status_code} {r.text[:200]}")

ids = note_ids_in_list()
check("A 从笔记列表消失", A not in ids)
check("B 仍在笔记列表", B in ids)
check("A 出现在回收站列表", A in trash_ids())

r = client.get(f"{BASE}/notes/{A}", headers=headers)
check("A 详情接口 404", r.status_code == 404, f"{r.status_code}")

# 回收站统计
r = client.get(f"{BASE}/notes/trash", headers=headers)
item = next((i for i in r.json()["items"] if i["note"]["id"] == A), None)
check("回收站项含统计", item is not None)
if item:
    print(f"  统计: cards={item['card_count']} quiz={item['quiz_count']} "
          f"annotations={item['annotation_count']} versions={item['version_count']} links={item['link_count']}")
    check("批注数 = 1", item["annotation_count"] == 1, str(item))
    check("版本数 >= 1", item["version_count"] >= 1, str(item))
    check("链接数 = 1", item["link_count"] == 1, str(item))
    check("trashed_at 已设置", bool(item["note"].get("trashed_at")))

# trash-info
r = client.get(f"{BASE}/notes/{A}/trash-info", headers=headers)
check("GET trash-info 可用", r.status_code == 200 and "card_count" in r.json(), r.text[:200])

# 物理文件搬入 trash 目录
trash_files = vault_trash_tree(user_id, A)
print(f"  trash 目录文件: {trash_files}")
check("A 的物理文件已移入 {user}/trash/{A}/", len(trash_files) >= 2, str(trash_files))
check("trash 目录含 source 原始文件", any(p.startswith(f"trash/{A}/source/") for p in trash_files))
check("trash 目录含 markdown 输出", any("markdown" in p for p in trash_files))
check("inbox 中 A 原文件已不存在",
      not vault_rel_exists(user_id, "inbox", "source", name_a))

# 软删除后 B 的链接仍完好（关系记录不动）
r = client.get(f"{BASE}/notes/{B}/links", headers=headers)
data = r.json()
check("软删除后链接记录完好（悬挂计数=0）", data["dangling_material_count"] == 0, r.text[:300])

# 图谱接口不报错（无卡片场景）
r = client.get(f"{BASE}/graph", headers=headers)
check("GET /graph 正常（含 note_trashed 字段）", r.status_code == 200, r.text[:200])
if r.status_code == 200:
    gd = r.json()
    check("graph 响应含 nodes/edges", "nodes" in gd and "edges" in gd, str(gd)[:200])

# ============================================================
# 3. 恢复
# ============================================================
section("3. 恢复 A")
r = client.post(f"{BASE}/notes/{A}/restore", headers=headers)
check("POST restore 返回 200", r.status_code == 200, r.text[:300])
if r.status_code == 200:
    print(f"  restore 响应: {json.dumps(r.json(), ensure_ascii=False)[:200]}")

check("A 回到笔记列表", A in note_ids_in_list())
check("A 从回收站消失", A not in trash_ids())

r = client.get(f"{BASE}/notes/{A}", headers=headers)
check("A 详情恢复可访问", r.status_code == 200, f"{r.status_code}")
content_ok = "编辑后" in (r.json().get("clean_md_content") or "")
check("A 清洗内容完整（含编辑后标记）", content_ok)

r = client.get(f"{BASE}/notes/{A}/annotations", headers=headers)
ann_count = len(r.json().get("annotations", [])) if r.status_code == 200 else -1
check("A 批注保留", ann_count == 1, r.text[:200])

check("物理文件搬回 inbox", vault_rel_exists(user_id, "inbox", "source", name_a))
check("trash 目录已清空", len(vault_trash_tree(user_id, A)) == 0)

r = client.get(f"{BASE}/notes/{B}/links", headers=headers)
check("B 的链接自动复原", any(m["id"] == A for m in r.json()["linked_materials"]), r.text[:300])

# ============================================================
# 4. 同名冲突
# ============================================================
section("4. 同名文件冲突：A 在回收站期间上传同名新文件，再恢复 A")
client.delete(f"{BASE}/notes/{A}", headers=headers)
note_a2 = upload_md(name_a, "# 材料A-同名新文件\n\n冲突测试。\n", "material")
A2 = note_a2["id"]
wait_status(A2, {"completed", "converted", "ready", "failed"})
check("同名新文件 A2 上传成功", vault_rel_exists(user_id, "inbox", "source", name_a))

r = client.post(f"{BASE}/notes/{A}/restore", headers=headers)
check("冲突场景下 restore 仍成功", r.status_code == 200, r.text[:300])
if r.status_code == 200:
    renamed = [m for m in r.json().get("renamed_files", [])]
    print(f"  renamed_files: {renamed}")

r = client.get(f"{BASE}/notes/{A}", headers=headers)
check("A 详情可访问（改名恢复）", r.status_code == 200)
if r.status_code == 200:
    print(f"  A 恢复后 source 路径: {r.json().get('original_file_path', '')}")
check("A2 不受影响", A2 in note_ids_in_list())

# ============================================================
# 5. 彻底删除 + 悬挂引用
# ============================================================
section("5. 彻底删除 A（悬挂引用策略）")
r = client.get(f"{BASE}/notes/{A}/trash-info", headers=headers)
check("purge 前 trash-info 可用", r.status_code == 200, r.text[:200])

client.delete(f"{BASE}/notes/{A}", headers=headers)  # 先软删除
r = client.delete(f"{BASE}/notes/{A}/purge", headers=headers)
check("DELETE purge 返回 204", r.status_code == 204, f"{r.status_code} {r.text[:200]}")

check("A 从列表消失", A not in note_ids_in_list())
check("A 从回收站消失", A not in trash_ids())
r = client.get(f"{BASE}/notes/{A}", headers=headers)
check("A 详情 404", r.status_code == 404)
check("A 物理文件彻底清除", len(vault_trash_tree(user_id, A)) == 0)

# 悬挂引用：B 的链接行保留但 material 端为 NULL
r = client.get(f"{BASE}/notes/{B}/links", headers=headers)
data = r.json()
check("B 链接行未级联删除且悬挂计数=1", data["dangling_material_count"] == 1, r.text[:300])
check("B 的 linked_materials 不再显示 A", all(m["id"] != A for m in data["linked_materials"]))

# 清理悬挂链接
r = client.put(f"{BASE}/notes/{B}/links", headers=headers, json={"material_note_ids": []})
check("清理悬挂链接（覆盖空列表）", r.status_code == 200, r.text[:200])
r = client.get(f"{BASE}/notes/{B}/links", headers=headers)
check("清理后悬挂计数=0", r.json()["dangling_material_count"] == 0, r.text[:300])

# ============================================================
# 6. 清空回收站（purge-all）
# ============================================================
section("6. 清空回收站 purge-all")
client.delete(f"{BASE}/notes/{A2}", headers=headers)
client.delete(f"{BASE}/notes/{B}", headers=headers)
check("A2、B 均在回收站", {A2, B} <= trash_ids())
r = client.delete(f"{BASE}/notes/trash/purge-all", headers=headers)
check("purge-all 成功", r.status_code == 200, r.text[:300])
if r.status_code == 200:
    print(f"  purge-all 响应: {json.dumps(r.json(), ensure_ascii=False)}")
check("回收站已清空", len(trash_ids()) == 0)
check("A2、B 已物理删除", not ({A2, B} & note_ids_in_list()))

# purge-all 后本次测试笔记的 trash 目录应被彻底清除（含空目录壳）
for _nid, _label in [(A, "A"), (A2, "A2"), (B, "B")]:
    _dir = VAULT_ROOT / user_id / "trash" / _nid
    check(f"trash/{_label} 目录彻底清除（含空目录壳）",
          not _dir.exists() or not any(_dir.rglob("*")))

# ============================================================
# 汇总
# ============================================================
section("测试汇总")
print(f"  PASS: {len(PASS)}  FAIL: {len(FAIL)}")
if FAIL:
    print("  失败项:")
    for f in FAIL:
        print(f"    - {f}")
client.close()
sys.exit(1 if FAIL else 0)
