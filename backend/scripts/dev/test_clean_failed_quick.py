"""快速测试清洗失败和停止清洗功能"""
import requests
import time
import uuid
import os
from app.test_support.corpus import require_pdf_path

BASE = 'http://localhost:8000/api'

# 注册新用户
email = f'test{uuid.uuid4().hex[:8]}@example.com'
user = f'test{uuid.uuid4().hex[:8]}'
r = requests.post(f'{BASE}/auth/register', json={'email': email, 'username': user, 'password': 'test123456'})
token = r.json()['access_token']
headers = {'Authorization': f'Bearer {token}'}
print('1. 注册登录成功')

# 上传一个 PDF
pdf_path = require_pdf_path()
if not os.path.exists(pdf_path):
    print(f'PDF 文件不存在: {pdf_path}')
    exit(1)

with open(pdf_path, 'rb') as f:
    r = requests.post(f'{BASE}/upload', headers=headers, files={'file': ('test.pdf', f, 'application/pdf')})
note_id = r.json()['id']
print(f'2. 上传成功, note_id={note_id}')

# 等待转换完成
for i in range(60):
    time.sleep(5)
    r = requests.get(f'{BASE}/upload/{note_id}/status', headers=headers)
    status = r.json()['status']
    print(f'  [{i*5}s] 状态: {status}')
    if status in ('converted', 'cleaned', 'cleaning_failed', 'failed'):
        break

current_status = r.json()['status']
print(f'3. 当前状态: {current_status}')

# 触发清洗
if current_status in ('converted', 'cleaned'):
    r = requests.post(f'{BASE}/cleaning/{note_id}/start', headers=headers)
    print(f'4. 触发清洗: {r.status_code} - {r.json()}')
    
    # 立即尝试停止清洗
    time.sleep(2)
    r = requests.post(f'{BASE}/cleaning/{note_id}/stop', headers=headers)
    print(f'5. 停止清洗: {r.status_code} - {r.json()}')
    
    # 检查状态
    r = requests.get(f'{BASE}/cleaning/{note_id}/status', headers=headers)
    status_data = r.json()
    print(f'6. 当前状态: {status_data["status"]}, 错误: {status_data.get("error_message", "")}')
    
    # 从 cleaning_failed 重新触发清洗
    if status_data['status'] == 'cleaning_failed':
        r = requests.post(f'{BASE}/cleaning/{note_id}/start', headers=headers)
        print(f'7. 从 cleaning_failed 重新触发: {r.status_code} - {r.json()}')
        print('测试通过! cleaning_failed -> 重新清洗 成功')
    else:
        print(f'7. 状态不是 cleaning_failed: {status_data["status"]}')
else:
    print(f'4. 状态不是 converted/cleaned: {current_status}')

# 测试无效状态触发清洗（对 uploading 状态的笔记）
print('\n--- 测试无效状态 ---')
r = requests.post(f'{BASE}/cleaning/nonexistent/start', headers=headers)
print(f'不存在的笔记: {r.status_code}')

# 测试对非 cleaning 状态停止清洗
r = requests.post(f'{BASE}/cleaning/{note_id}/stop', headers=headers)
print(f'非 cleaning 状态停止: {r.status_code} - {r.json()}')

print('\n=== 测试完成 ===')
