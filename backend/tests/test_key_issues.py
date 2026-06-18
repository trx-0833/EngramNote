"""验证知识卡片、章节摘要、题目生成、RAG问答"""
import httpx
import json
import sys
import time

sys.stdout.reconfigure(line_buffering=True)

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxNWE5MjY3MS03MTIzLTQ5MWItOTczMy01NGU1OTA2M2U2ZmQiLCJleHAiOjE3ODEyODY0MDh9.swiQ-uYwA492u2i2oXKe8BtPR5mJDTeykrFwliznKrw"
NOTE_ID = "93b47cff-6f38-4b83-a6f3-ab6d34fcc140"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://localhost:8000/api"

def sep(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ===== 1. 验证知识卡片 =====
sep("1. 验证知识卡片")
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/cards?page_size=5", headers=HEADERS)
data = r.json()
print(f"状态码: {r.status_code}, 总卡片数: {data['total']}")
for card in data["items"][:5]:
    print(f"  [{card['card_type']}] {card['title'][:50]}")

# ===== 2. 验证章节摘要 =====
sep("2. 验证章节摘要")
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/chapters", headers=HEADERS)
data = r.json()
print(f"状态码: {r.status_code}, 章节数: {len(data['chapters'])}")
for ch in data["chapters"][:5]:
    summary = ch.get("summary", "")[:80]
    print(f"  {ch['chapter_title']}: {summary}...")

# ===== 3. 验证知识卡片详情 =====
sep("3. 验证知识卡片详情")
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/cards?page_size=1", headers=HEADERS)
if r.json()["items"]:
    card_id = r.json()["items"][0]["id"]
    r2 = httpx.get(f"{BASE}/understanding/cards/{card_id}", headers=HEADERS)
    card = r2.json()
    print(f"状态码: {r2.status_code}")
    print(f"  标题: {card['title']}")
    print(f"  类型: {card['card_type']}")
    print(f"  内容: {card['content'][:100]}...")
    print(f"  章节: {card['chapter_title']}")

# ===== 4. 重点测试：RAG问答（上次429限流失败） =====
sep("4. 重点测试：RAG问答（上次429限流失败）")
test_questions = [
    "劳动合同的期限是多久？",
    "田润鑫的工资是多少？",
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
            for s in sources[:2]:
                print(f"    - {s.get('note_title', '')} / {s.get('chapter_title', '')}")
        else:
            print(f"  [FAIL] 状态码: {r.status_code}")
            print(f"  响应: {r.text[:300]}")
    except Exception as e:
        print(f"  [FAIL] 请求异常: {e}")

# ===== 5. 重点测试：题目生成（上次0道题） =====
sep("5. 重点测试：题目生成（上次0道题）")
print("触发题目生成...")
r = httpx.post(f"{BASE}/understanding/{NOTE_ID}/generate-questions", headers=HEADERS)
print(f"状态码: {r.status_code}, 响应: {r.json()}")

# 等待题目生成
print("等待题目生成（最多5分钟）...")
start = time.time()
last_count = 0
while time.time() - start < 300:
    r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/questions?page_size=1", headers=HEADERS)
    if r.status_code == 200:
        count = r.json()["total"]
        elapsed = int(time.time() - start)
        if count != last_count:
            print(f"  [{elapsed}s] 题目数: {count}")
            last_count = count
        if count > 5:
            print(f"  题目生成进行中，当前 {count} 道，继续等待...")
            if count > 20 or elapsed > 120:
                break
    time.sleep(10)

# 验证题目
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/questions?page_size=5", headers=HEADERS)
data = r.json()
print(f"\n总题目数: {data['total']}")
for q in data["items"][:5]:
    print(f"  [{q['question_type']}] {q['question'][:60]}...")

# ===== 6. 重点测试：嵌入模型段错误问题 =====
sep("6. 重点测试：嵌入模型段错误问题")
print("当前 RAG 服务已禁用向量检索（避免段错误），使用知识卡片检索替代")
print("测试知识卡片检索是否正常工作...")
r = httpx.post(
    f"{BASE}/understanding/ask",
    json={"question": "华电金沙江上游水电开发有限公司的地址在哪里？"},
    headers=HEADERS,
    timeout=120.0,
)
if r.status_code == 200:
    data = r.json()
    print(f"  回答: {data.get('answer', '')[:200]}")
    print(f"  来源数: {len(data.get('sources', []))}")
else:
    print(f"  [FAIL] 状态码: {r.status_code}")

# ===== 汇总 =====
sep("测试汇总")
r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/status", headers=HEADERS)
print(f"笔记状态: {r.json().get('status')}")

r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/cards?page_size=1", headers=HEADERS)
print(f"知识卡片: {r.json()['total']} 张")

r = httpx.get(f"{BASE}/understanding/{NOTE_ID}/questions?page_size=1", headers=HEADERS)
print(f"题目: {r.json()['total']} 道")

print("\n全流程测试完成!")
