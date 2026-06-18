"""最终验证：题目API + RAG问答API"""
import httpx
import json
import sys

sys.stdout.reconfigure(line_buffering=True)

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxNWE5MjY3MS03MTIzLTQ5MWItOTczMy01NGU1OTA2M2U2ZmQiLCJleHAiOjE3ODEyODY0MDh9.swiQ-uYwA492u2i2oXKe8BtPR5mJDTeykrFwliznKrw"
NOTE_ID = "93b47cff-6f38-4b83-a6f3-ab6d34fcc140"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://localhost:8000/api"

# ===== 1. 验证题目API =====
print("=" * 60)
print("  1. 验证题目API")
print("=" * 60)
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/questions?page_size=5", headers=HEADERS)
data = r.json()
total = data["total"]
print(f"状态码: {r.status_code}, 总题目数: {total}")
for q in data["items"][:5]:
    qtype = q["question_type"]
    question = q["question"][:60]
    answer = q.get("answer", "")[:30]
    print(f"  [{qtype}] {question}... -> {answer}")

# ===== 2. 验证RAG问答API =====
print("\n" + "=" * 60)
print("  2. 验证RAG问答API")
print("=" * 60)
test_questions = [
    "劳动合同的期限是多久？",
    "田润鑫的工作地点在哪里？",
    "试用期有多长？",
]

for q in test_questions:
    print(f"\n  问题: {q}")
    try:
        r = httpx.post(
            f"{BASE}/understanding/ask",
            json={"question": q},
            headers=HEADERS,
            timeout=120.0,
        )
        if r.status_code == 200:
            data = r.json()
            answer = data.get("answer", "")
            sources = data.get("sources", [])
            provider = data.get("provider", "")
            print(f"  回答 ({provider}): {answer[:200]}")
            print(f"  引用来源: {len(sources)} 个")
        else:
            print(f"  [FAIL] 状态码: {r.status_code}")
            print(f"  响应: {r.text[:300]}")
    except Exception as e:
        print(f"  [FAIL] 请求异常: {e}")

# ===== 汇总 =====
print("\n" + "=" * 60)
print("  测试汇总")
print("=" * 60)
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/status", headers=HEADERS)
print(f"笔记状态: {r.json().get('status')}")

r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/cards?page_size=1", headers=HEADERS)
print(f"知识卡片: {r.json()['total']} 张")

r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/questions?page_size=1", headers=HEADERS)
print(f"题目: {r.json()['total']} 道")

print("\n全流程测试完成!")
