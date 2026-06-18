"""验证理解管道最终结果"""
import requests

BASE = "http://localhost:8001/api"

login = requests.post(f"{BASE}/auth/login", json={"email": "test3@test.com", "password": "test123456"})
token = login.json().get("access_token")
headers = {"Authorization": f"Bearer {token}"}
note_id = "3dd66854-fced-4abe-8222-e41576e642c3"

# 知识卡片
r1 = requests.get(f"{BASE}/understanding/{note_id}/cards", headers=headers)
print(f"Cards: {r1.status_code}")
if r1.status_code == 200:
    data = r1.json()
    if isinstance(data, dict):
        items = data.get("cards", data.get("items", []))
    else:
        items = data
    print(f"  Total: {len(items)}")
    types = {}
    for c in items:
        t = c.get("card_type", "unknown")
        types[t] = types.get(t, 0) + 1
    print(f"  Types: {types}")
    for c in items[:3]:
        ct = c.get("card_type", "")
        title = c.get("title", "")[:50]
        print(f"  [{ct}] {title}")

# 题目
r2 = requests.get(f"{BASE}/understanding/{note_id}/questions", headers=headers)
print(f"\nQuestions: {r2.status_code}")
if r2.status_code == 200:
    data = r2.json()
    if isinstance(data, dict):
        items = data.get("questions", data.get("items", []))
    else:
        items = data
    print(f"  Total: {len(items)}")
    types = {}
    for q in items:
        t = q.get("question_type", "unknown")
        types[t] = types.get(t, 0) + 1
    print(f"  Types: {types}")
    for q in items[:2]:
        qt = q.get("question_type", "")
        question_text = q.get("question", "")[:60]
        print(f"  [{qt}] {question_text}...")

# RAG 问答
print("\n--- RAG 问答 ---")
for question in ["劳动合同期限是多久？", "田润鑫的工作地点在哪里？", "试用期长度是多少？"]:
    ask_resp = requests.post(
        f"{BASE}/understanding/ask",
        headers=headers,
        json={"question": question, "note_id": note_id},
        timeout=60,
    )
    if ask_resp.status_code == 200:
        answer = ask_resp.json().get("answer", "")
        print(f"Q: {question}")
        print(f"A: {answer[:120]}...")
    else:
        print(f"Ask failed ({question}): {ask_resp.status_code}")

print("\n--- 验证完成 ---")
