"""直接测试理解管道（跳过转换和清洗，使用已有 Markdown 文件）"""
import requests
import time
import sys
import sqlite3
import uuid
import shutil
import os

BASE = "http://localhost:8001/api"

# 1. 登录
login = requests.post(f"{BASE}/auth/login", json={"email": "test3@test.com", "password": "test123456"})
print(f"Login: {login.status_code}")
if login.status_code != 200:
    print(f"Login failed: {login.text[:200]}")
    sys.exit(1)
token = login.json().get("access_token")
headers = {"Authorization": f"Bearer {token}"}

# 获取 user_id
conn = sqlite3.connect("data/db/engramnote.db")
c = conn.cursor()
c.execute("SELECT id FROM users WHERE email = 'test3@test.com'")
row = c.fetchone()
user_id = row[0]
print(f"User ID: {user_id}")

note_id = str(uuid.uuid4())

# Vault 结构：{user_id}/default 项目下，source 与 output/markdown 同主干（命名关联法则）
# base 与 source 文件名主干一致：source/{base}.pdf ↔ output/markdown/{base}.md
base = note_id
clean_md_path = f"{user_id}/default/output/markdown/{base}.clean.md"
original_md_path = f"{user_id}/default/output/markdown/{base}.md"

c.execute("""
    INSERT INTO notes (id, user_id, title, source_type, original_file_path, original_md_path, clean_md_path, status, file_size, page_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    note_id,
    user_id,
    "劳动合同书-田润鑫",
    "pdf",
    f"{user_id}/default/source/{base}.pdf",
    original_md_path,
    clean_md_path,
    "cleaned",
    3938752,
    11,
))
conn.commit()
conn.close()
print(f"Created note: {note_id}, status=cleaned")

# 复制已有的 Markdown 文件到 Vault 新路径
src_dir = "data/storage/markdown/15a92671-7123-491b-9733-54e59063e6fd/93b47cff-6f38-4b83-a6f3-ab6d34fcc140"
dst_dir = f"data/vault/{user_id}/default/output/markdown"
os.makedirs(dst_dir, exist_ok=True)

file_map = {
    "original.md": f"{base}.md",
    "clean.md": f"{base}.clean.md",
}
for src_fname, dst_fname in file_map.items():
    src = os.path.join(src_dir, src_fname)
    dst = os.path.join(dst_dir, dst_fname)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"Copied {src_fname} -> {dst_fname}: {os.path.getsize(dst)} bytes")

# 2. 触发理解管道
start = requests.post(f"{BASE}/understanding/{note_id}/start", headers=headers)
print(f"Start understanding: {start.status_code} {start.json()}")

# 3. 等待理解完成
print("\n--- 等待理解管道 ---")
status = "learning"
for i in range(120):
    time.sleep(10)
    r = requests.get(f"{BASE}/notes/{note_id}", headers=headers)
    status = r.json().get("status")
    print(f"  [{i*10}s] Status: {status}")
    if status in ["archived", "failed", "learning_failed"]:
        break

print(f"Final status: {status}")

# 4. 查看知识卡片
cards_resp = requests.get(f"{BASE}/notes/{note_id}/cards", headers=headers)
if cards_resp.status_code == 200:
    cards = cards_resp.json()
    print(f"Knowledge cards: {len(cards)}")
    if cards:
        print(f"  First card: title={cards[0].get('title')}, type={cards[0].get('card_type')}")
else:
    print(f"Cards request failed: {cards_resp.status_code} {cards_resp.text[:200]}")

# 5. 查看题目
questions_resp = requests.get(f"{BASE}/understanding/{note_id}/questions", headers=headers)
if questions_resp.status_code == 200:
    questions = questions_resp.json()
    if isinstance(questions, list):
        print(f"Quiz items: {len(questions)}")
        if questions:
            q = questions[0]
            print(f"  First question: {q.get('question', '')[:80]}...")
    elif isinstance(questions, dict):
        items = questions.get("items", questions.get("questions", []))
        print(f"Quiz items: {len(items)}")
else:
    print(f"Questions request failed: {questions_resp.status_code}")

# 6. RAG 问答测试
print("\n--- RAG 问答测试 ---")
for question in ["劳动合同期限是多久？", "田润鑫的工作地点在哪里？", "试用期长度是多少？"]:
    ask_resp = requests.post(
        f"{BASE}/understanding/ask",
        headers=headers,
        json={"question": question, "note_id": note_id},
    )
    if ask_resp.status_code == 200:
        answer_data = ask_resp.json()
        print(f"  Q: {question}")
        print(f"  A: {answer_data.get('answer', '')[:100]}...")
    else:
        print(f"  Ask failed: {ask_resp.status_code} {ask_resp.text[:100]}")

print("\n--- 测试完成 ---")
