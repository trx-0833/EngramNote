"""RAG 问答测试"""
import httpx

c = httpx.Client(timeout=120)
BASE = "http://localhost:8000/api"

r = c.post(BASE + "/auth/login", json={"email": "test_1781194945@example.com", "password": "TestPass123!"})
t = r.json()["access_token"]
h = {"Authorization": "Bearer " + t}

print("=== RAG 问答测试 ===")
test_qs = [
    "劳动合同的期限是多久？",
    "田润鑫的工资是多少？",
]
for q in test_qs:
    r = c.post(BASE + "/understanding/ask", headers=h, json={"question": q}, timeout=120)
    if r.status_code == 200:
        a = r.json()
        answer_text = a["answer"][:300]
        provider = a["provider"]
        print(f"Q: {q}")
        print(f"A: {answer_text}")
        print(f"Provider: {provider}")
        if a.get("sources"):
            for s in a["sources"][:2]:
                print(f"  Source: {s['note_title']}")
        print()
    else:
        print(f"Failed: {r.status_code} {r.text[:200]}")
