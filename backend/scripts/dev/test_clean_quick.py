"""快速测试清洗功能"""
import httpx
import time

API = 'http://127.0.0.1:8000'
r = httpx.post(API+'/api/auth/login', json={'email':'e2e_test@test.com','password':'test123456'})
t = r.json()['access_token']
h = {'Authorization': 'Bearer '+t}

# 查看现有笔记
r = httpx.get(API+'/api/notes', headers=h)
d = r.json()
print('notes total:', d['total'])
for i in d['items']:
    nid_short = i['id'][:8]
    print(f'  {nid_short} {i["title"]} {i["status"]}')

# 找到需要处理的笔记
for i in d['items']:
    nid = i['id']
    status = i['status']

    if status == 'cleaning':
        print(f'Waiting for cleaning: {nid}')
        for j in range(120):
            time.sleep(5)
            r = httpx.get(API+'/api/cleaning/'+nid+'/status', headers=h, timeout=15)
            st = r.json()
            s = st['status']
            print(f'  poll {j}: status={s}')
            if s in ('cleaned', 'failed'):
                print('Result:', st)
                break

    elif status == 'converted':
        print(f'Triggering cleaning for: {nid}')
        r = httpx.post(API+'/api/cleaning/'+nid+'/start', headers=h)
        print('Trigger result:', r.json())
        for j in range(120):
            time.sleep(5)
            r = httpx.get(API+'/api/cleaning/'+nid+'/status', headers=h, timeout=15)
            st = r.json()
            s = st['status']
            print(f'  poll {j}: status={s}')
            if s in ('cleaned', 'failed'):
                print('Result:', st)
                break

    elif status == 'cleaned':
        print(f'Already cleaned: {nid}')
        r = httpx.get(API+'/api/notes/'+nid, headers=h)
        detail = r.json()
        meta = detail.get('metadata_', {})
        print(f'  metadata: {meta}')
        clean_content = detail.get('clean_md_content', '')
        print(f'  clean content (前200字): {clean_content[:200] if clean_content else "EMPTY"}')
