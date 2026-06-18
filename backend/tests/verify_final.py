"""最终验证脚本 - 测试已完成笔记的知识卡片、题目和RAG问答"""
import httpx

BASE_URL = "http://localhost:8000/api"
client = httpx.Client(timeout=120.0)

# 使用已完成理解的用户
resp = client.post(f"{BASE_URL}/auth/login", json={
    "email": "test_1781194945@example.com",
    "password": "TestPass123!",
})
if resp.status_code not in (200, 201):
    print(f"Login failed: {resp.status_code} {resp.text}")
    exit(1)

token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 查看笔记
resp = client.get(f"{BASE_URL}/notes", headers=headers)
data = resp.json()
notes = data if isinstance(data, list) else data.get("items", [])
print("=== 笔记列表 ===")
for note in notes:
    print(f"  {note['title']} | 状态: {note['status']} | ID: {note['id']}")

# 找到 archived 的笔记
archived_note = None
for note in notes:
    if note["status"] == "archived":
        archived_note = note
        break

if not archived_note:
    # 找 cleaned 的笔记
    for note in notes:
        if note["status"] == "cleaned":
            archived_note = note
            break

if not archived_note:
    print("没有可用的笔记")
    exit(1)

note_id = archived_note["id"]
print(f"\n使用笔记: {archived_note['title']} (状态: {archived_note['status']})")

# 如果是 cleaned 状态，触发理解
if archived_note["status"] == "cleaned":
    print("触发理解管道...")
    resp = client.post(f"{BASE_URL}/understanding/{note_id}/start", headers=headers)
    print(f"结果: {resp.status_code} {resp.text[:200]}")
    print("理解管道已触发，请等待完成后再运行此脚本")
    exit(0)

# 查看知识卡片
print("\n=== 知识卡片 ===")
resp = client.get(f"{BASE_URL}/understanding/{note_id}/cards", headers=headers)
if resp.status_code == 200:
    cards = resp.json()
    print(f"共 {cards['total']} 张卡片")
    for card in cards["items"][:5]:
        print(f"  [{card['card_type']}] {card['title'][:60]}")
else:
    print(f"查询失败: {resp.status_code} {resp.text[:200]}")

# 查看章节摘要
print("\n=== 章节摘要 ===")
resp = client.get(f"{BASE_URL}/understanding/{note_id}/chapters", headers=headers)
if resp.status_code == 200:
    chapters = resp.json()
    for ch in chapters.get("chapters", [])[:5]:
        print(f"  {ch['chapter_title']} (卡片数: {ch['card_count']})")
        print(f"    摘要: {ch['summary'][:80]}...")
else:
    print(f"查询失败: {resp.status_code}")

# 查看题目
print("\n=== 题目 ===")
resp = client.get(f"{BASE_URL}/understanding/{note_id}/questions", headers=headers)
if resp.status_code == 200:
    questions = resp.json()
    print(f"共 {questions['total']} 道题")
    for q in questions["items"][:5]:
        print(f"  [{q['question_type']}/{q['difficulty']}] {q['question'][:60]}")
else:
    print(f"查询失败: {resp.status_code}")

# 查看所有知识卡片
print("\n=== 全部知识卡片 ===")
resp = client.get(f"{BASE_URL}/understanding/cards", headers=headers)
if resp.status_code == 200:
    all_cards = resp.json()
    print(f"共 {all_cards['total']} 张卡片")

# 查看所有题目
resp = client.get(f"{BASE_URL}/understanding/questions", headers=headers)
if resp.status_code == 200:
    all_q = resp.json()
    print(f"全部题目: {all_q['total']} 道")

# RAG 问答
print("\n=== RAG 问答测试 ===")
test_questions = [
    "劳动合同的期限是多久？",
    "田润鑫的工资是多少？",
]
for q in test_questions:
    try:
        resp = client.post(f"{BASE_URL}/understanding/ask", headers=headers, json={"question": q}, timeout=120.0)
        if resp.status_code == 200:
            answer = resp.json()
            print(f"Q: {q}")
            print(f"A: {answer['answer'][:200]}")
            print(f"提供商: {answer['provider']}")
            if answer.get("sources"):
                for s in answer["sources"][:2]:
                    print(f"引用: {s['note_title']}")
            print()
        else:
            print(f"问答失败: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"问答异常: {e}")

# 查看知识卡片详情
if cards["items"]:
    first_card = cards["items"][0]
    resp = client.get(f"{BASE_URL}/understanding/cards/{first_card['id']}", headers=headers)
    if resp.status_code == 200:
        detail = resp.json()
        print(f"\n=== 卡片详情 ===")
        print(f"标题: {detail['title']}")
        print(f"类型: {detail['card_type']}")
        print(f"内容: {detail['content'][:200]}")
        if detail.get("source_text"):
            print(f"出处: {detail['source_text'][:200]}")

print("\n=== 验证完成 ===")
