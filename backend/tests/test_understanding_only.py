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

# clean_md_path 应该只存相对路径（不含 bucket 名）
# bucket = "markdown"，所以路径应该是 user_id/note_id/clean.md
clean_md_path = f"{user_id}/{note_id}/clean.md"
original_md_path = f"{user_id}/{note_id}/original.md"

c.execute("""
    INSERT INTO notes (id, user_id, title, source_type, original_file_path, original_md_path, clean_md_path, status, file_size, page_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    note_id,
    user_id,
    "劳动合同书-田润鑫",
    "pdf",
    f"{user_id}/{note_id}/劳动合同书-田润鑫.pdf",
    original_md_path,
    clean_md_path,
    "cleaned",
    3938752,
    11,
))
conn.commit()
conn.close()
print(f"Created note: {note_id}, status=cleaned")

# 复制已有的 Markdown 文件到新路径
src_dir = "data/storage/markdown/15a92671-7123-491b-9733-54e59063e6fd/93b47cff-6f38-4b83-a6f3-ab6d34fcc140"
dst_dir = f"data/storage/markdown/{user_id}/{note_id}"
os.makedirs(dst_dir, exist_ok=True)

for fname in ["original.md", "clean.md"]:
    src = os.path.join(src_dir, fname)
    dst = os.path.join(dst_dir, fname)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"Copied {fname}: {os.path.getsize(dst)} bytes")

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
