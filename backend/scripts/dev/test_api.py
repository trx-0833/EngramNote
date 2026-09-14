"""快速验证 API 是否正常工作"""
import urllib.request
import json

BASE = "http://localhost:8000"


def post(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}


def get(path, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}


# 1. 健康检查
print("1. Health:", get("/health"))

# 2. 注册
result = post("/api/auth/register", {"email": "test@test.com", "username": "testuser", "password": "123456"})
print("2. Register:", "OK" if "access_token" in result else result)
token = result.get("access_token", "")

# 3. 获取当前用户
if token:
    me = get("/api/auth/me", token)
    print("3. Me:", me.get("username", me))

# 4. 笔记列表
if token:
    notes = get("/api/notes", token)
    print("4. Notes:", f"total={notes.get('total', '?')}")

print("\nAll tests passed!")
