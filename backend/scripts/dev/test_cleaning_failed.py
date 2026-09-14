"""
测试清洗失败状态和停止清洗功能的脚本

测试场景：
1. 手动触发清洗 → 正常完成
2. 模拟清洗卡住 → 停止清洗 → 状态变为 cleaning_failed
3. 从 cleaning_failed 状态重新触发清洗
"""
import requests
import time

BASE_URL = "http://localhost:8000/api"

def test_cleaning_flow():
    # 1. 注册/登录
    print("=== 步骤 1: 登录 ===")
    login_resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "test@example.com",
        "password": "test123456"
    })
    if login_resp.status_code != 200:
        # 尝试不同密码
        for pwd in ["test123456", "Test123456", "123456", "password"]:
            login_resp = requests.post(f"{BASE_URL}/auth/login", json={
                "email": "test@example.com",
                "password": pwd
            })
            if login_resp.status_code == 200:
                break
        
        if login_resp.status_code != 200:
            print("登录失败，尝试注册新用户...")
            import uuid
            test_email = f"test{uuid.uuid4().hex[:8]}@example.com"
            test_user = f"test{uuid.uuid4().hex[:8]}"
            reg_resp = requests.post(f"{BASE_URL}/auth/register", json={
                "email": test_email,
                "username": test_user,
                "password": "test123456"
            })
            if reg_resp.status_code != 201:
                print(f"注册失败: {reg_resp.text}")
                return
            token = reg_resp.json()["access_token"]
        else:
            token = login_resp.json()["access_token"]
    else:
        token = login_resp.json()["access_token"]
    
    headers = {"Authorization": f"Bearer {token}"}
    print("登录成功")

    # 2. 查找已转换的笔记
    print("\n=== 步骤 2: 查找已转换的笔记 ===")
    notes_resp = requests.get(f"{BASE_URL}/notes?page=1&page_size=20", headers=headers)
    notes = notes_resp.json()["items"]
    
    # 找一个 converted 或 cleaned 状态的笔记
    target_note = None
    for note in notes:
        if note["status"] in ("converted", "cleaned"):
            target_note = note
            break
    
    if not target_note:
        print("没有找到 converted 或 cleaned 状态的笔记，请先上传一个文件")
        return
    
    note_id = target_note["id"]
    print(f"找到笔记: {target_note['title']} (状态: {target_note['status']}, ID: {note_id})")

    # 3. 测试手动触发清洗
    print("\n=== 步骤 3: 手动触发清洗 ===")
    start_resp = requests.post(f"{BASE_URL}/cleaning/{note_id}/start", headers=headers)
    print(f"触发清洗响应: {start_resp.status_code} - {start_resp.json()}")
    
    if start_resp.status_code != 200:
        print("触发清洗失败，跳过后续测试")
        return

    # 4. 等待清洗完成
    print("\n=== 步骤 4: 等待清洗完成 ===")
    for i in range(60):
        time.sleep(5)
        status_resp = requests.get(f"{BASE_URL}/cleaning/{note_id}/status", headers=headers)
        status_data = status_resp.json()
        current_status = status_data["status"]
        print(f"  [{i*5}s] 状态: {current_status}")
        
        if current_status == "cleaned":
            print("清洗成功完成!")
            break
        elif current_status == "cleaning_failed":
            print(f"清洗失败: {status_data.get('error_message', '未知错误')}")
            break
        elif current_status == "failed":
            print(f"处理失败: {status_data.get('error_message', '未知错误')}")
            break
    else:
        print("清洗超时（5分钟）")

    # 5. 测试从 cleaned 状态重新触发清洗
    print("\n=== 步骤 5: 从 cleaned 状态重新触发清洗 ===")
    status_resp = requests.get(f"{BASE_URL}/cleaning/{note_id}/status", headers=headers)
    current_status = status_resp.json()["status"]
    
    if current_status == "cleaned":
        start_resp = requests.post(f"{BASE_URL}/cleaning/{note_id}/start", headers=headers)
        print(f"重新触发清洗响应: {start_resp.status_code} - {start_resp.json()}")
    else:
        print(f"当前状态不是 cleaned（{current_status}），跳过重新触发测试")

    # 6. 测试停止清洗
    print("\n=== 步骤 6: 测试停止清洗 ===")
    # 先检查当前状态
    status_resp = requests.get(f"{BASE_URL}/cleaning/{note_id}/status", headers=headers)
    current_status = status_resp.json()["status"]
    
    if current_status == "cleaning":
        stop_resp = requests.post(f"{BASE_URL}/cleaning/{note_id}/stop", headers=headers)
        print(f"停止清洗响应: {stop_resp.status_code} - {stop_resp.json()}")
    else:
        print(f"当前状态不是 cleaning（{current_status}），模拟停止场景...")
        # 先触发清洗，然后立即停止
        start_resp = requests.post(f"{BASE_URL}/cleaning/{note_id}/start", headers=headers)
        if start_resp.status_code == 200:
            print("已触发清洗，立即尝试停止...")
            time.sleep(1)
            stop_resp = requests.post(f"{BASE_URL}/cleaning/{note_id}/stop", headers=headers)
            print(f"停止清洗响应: {stop_resp.status_code} - {stop_resp.json()}")

    # 7. 验证 cleaning_failed 状态
    print("\n=== 步骤 7: 验证 cleaning_failed 状态 ===")
    status_resp = requests.get(f"{BASE_URL}/cleaning/{note_id}/status", headers=headers)
    status_data = status_resp.json()
    print(f"当前状态: {status_data['status']}")
    if status_data.get("error_message"):
        print(f"错误信息: {status_data['error_message']}")

    # 8. 测试从 cleaning_failed 状态重新触发清洗
    print("\n=== 步骤 8: 从 cleaning_failed 状态重新触发清洗 ===")
    if status_data["status"] == "cleaning_failed":
        start_resp = requests.post(f"{BASE_URL}/cleaning/{note_id}/start", headers=headers)
        print(f"重新触发清洗响应: {start_resp.status_code} - {start_resp.json()}")
        if start_resp.status_code == 200:
            print("从 cleaning_failed 状态重新触发清洗成功!")
    else:
        print(f"当前状态不是 cleaning_failed（{status_data['status']}），跳过此测试")

    # 9. 测试无效状态触发清洗
    print("\n=== 步骤 9: 测试无效状态触发清洗 ===")
    # 找一个 uploading 或 converting 状态的笔记
    for note in notes:
        if note["status"] in ("uploading", "converting"):
            invalid_note_id = note["id"]
            invalid_resp = requests.post(f"{BASE_URL}/cleaning/{invalid_note_id}/start", headers=headers)
            print(f"无效状态触发清洗响应: {invalid_resp.status_code} - {invalid_resp.json()}")
            break
    else:
        print("没有找到 uploading/converting 状态的笔记，跳过无效状态测试")

    print("\n=== 测试完成 ===")


if __name__ == "__main__":
    test_cleaning_flow()
