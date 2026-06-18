"""轮询笔记状态直到转换+清洗完成"""
import httpx, time, sys, os

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxNWE5MjY3MS03MTIzLTQ5MWItOTczMy01NGU1OTA2M2U2ZmQiLCJleHAiOjE3ODEyODY0MDh9.swiQ-uYwA492u2i2oXKe8BtPR5mJDTeykrFwliznKrw"
NOTE_ID = "93b47cff-6f38-4b83-a6f3-ab6d34fcc140"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://localhost:8000/api"

sys.stdout.reconfigure(line_buffering=True)

print(f"轮询笔记 {NOTE_ID} 状态...", flush=True)
start = time.time()
while time.time() - start < 600:
    try:
        r = httpx.get(f"{BASE}/notes/{NOTE_ID}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            status = data.get("status")
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] status={status}", flush=True)
            if status in ("cleaned", "learning", "archived"):
                print(f"转换+清洗完成! 最终状态: {status}", flush=True)
                break
            if status == "failed":
                print(f"失败! error: {data.get('error_message')}", flush=True)
                sys.exit(1)
            if status == "clean_failed":
                print(f"清洗失败! error: {data.get('error_message')}", flush=True)
                sys.exit(1)
    except Exception as e:
        print(f"  查询异常: {e}", flush=True)
    time.sleep(15)
else:
    print("超时!", flush=True)
    sys.exit(1)

print(f"\n总耗时: {int(time.time()-start)}s", flush=True)
