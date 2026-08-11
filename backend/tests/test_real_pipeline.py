"""全流程真实测试：上传 PDF → 转换 → 清洗 → 理解 → 题目生成"""
import requests
import time
import sys

BASE = "http://localhost:8001/api"

# 1. 注册/登录
reg = requests.post(f"{BASE}/auth/register", json={"username": "testuser3", "email": "test3@test.com", "password": "test123456"})
print(f"Register: {reg.status_code}")
if reg.status_code != 200:
    login = requests.post(f"{BASE}/auth/login", json={"email": "test3@test.com", "password": "test123456"})
    print(f"Login: {login.status_code}, {login.text[:200]}")
    token = login.json().get("access_token") or login.json().get("token")
else:
    token = reg.json().get("access_token") or reg.json().get("token")

headers = {"Authorization": f"Bearer {token}"}
print(f"Token: {token[:20]}...")

# 2. 上传 PDF
with open(os.environ.get("TEST_PDF_PATH", r"D:\engramnote\resource\tests\劳动合同书-田润鑫.pdf"), "rb") as f:
    upload = requests.post(
        f"{BASE}/upload/",
        headers=headers,
        files={"file": ("劳动合同书-田润鑫.pdf", f, "application/pdf")},
    )
print(f"Upload: {upload.status_code}")
note_data = upload.json()
note_id = note_data["id"]
print(f"Note ID: {note_id}, Status: {note_data.get('status')}")

# 3. 等待转换+清洗完成
print("\n--- 等待转换+清洗 ---")
status = note_data.get("status")
for i in range(60):
    time.sleep(5)
    r = requests.get(f"{BASE}/notes/{note_id}", headers=headers)
    status = r.json().get("status")
    print(f"  [{i*5}s] Status: {status}")
    if status in ["cleaned", "learning", "archived", "failed",
                   "converting_failed", "cleaning_failed", "learning_failed"]:
        break

print(f"Status after convert+clean: {status}")

# 4. 触发理解管道
if status == "cleaned":
    start = requests.post(f"{BASE}/understanding/{note_id}/start", headers=headers)
    print(f"Start understanding: {start.status_code} {start.json()}")
elif status == "archived":
    print("Already archived, skipping understanding")
else:
    print(f"Unexpected status: {status}")
    sys.exit(1)

# 5. 等待理解完成
print("\n--- 等待理解管道 ---")
for i in range(120):
    time.sleep(10)
    r = requests.get(f"{BASE}/notes/{note_id}", headers=headers)
    status = r.json().get("status")
    print(f"  [{i*10}s] Status: {status}")
    if status in ["archived", "failed", "learning_failed"]:
        break

print(f"Final status: {status}")

# 6. 查看知识卡片
cards_resp = requests.get(f"{BASE}/notes/{note_id}/cards", headers=headers)
if cards_resp.status_code == 200:
    cards = cards_resp.json()
    print(f"Knowledge cards: {len(cards)}")
    if cards:
        print(f"  First card: title={cards[0].get('title')}, type={cards[0].get('card_type')}")
else:
    print(f"Cards request failed: {cards_resp.status_code}")

# 7. 查看题目
questions_resp = requests.get(f"{BASE}/understanding/{note_id}/questions", headers=headers)
if questions_resp.status_code == 200:
    questions = questions_resp.json()
    print(f"Quiz items: {len(questions)}")
    if questions:
        q = questions[0]
        print(f"  First question: {q.get('question', '')[:60]}...")
else:
    print(f"Questions request failed: {questions_resp.status_code}")

# 8. RAG 问答测试
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
