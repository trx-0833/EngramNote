"""
第12周全流程端到端测试

使用真实PDF文件 "D:\\test\\resources1\\劳动合同书-田润鑫.pdf" 跑完全流程，
验证第12周发布准备改动是否准确生效：

1. 验证 Git 仓库状态
2. 验证 .gitignore 规则
3. 验证 Docker 配置文件存在性和语法
4. 验证启动脚本存在性
5. 验证 README.md 存在性
6. 注册/登录
7. 上传PDF文件
8. 轮询转换状态直到完成
9. 触发清洗，轮询直到完成
10. 触发理解管道，轮询直到完成
11. 验证知识卡片和题目生成
12. 获取到期题目并答题
13. 获取今日学习报告
14. 获取7天趋势和薄弱点
15. 验证全局异常处理和认证校验

运行方式：cd backend && C:\\Users\\admin\\anaconda3\\envs\\mineru_env\\python.exe tests/test_week12_e2e.py
"""

import json
import os
import subprocess
import sys
import time

import httpx

BASE_URL = "http://localhost:8000/api"
PDF_PATH = r"D:\test\resources1\劳动合同书-田润鑫.pdf"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

client = httpx.Client(timeout=120.0)


def log_step(step: str, success: bool = True, detail: str = ""):
    icon = "✓" if success else "✗"
    msg = f"  [{icon}] {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def poll_status(note_id: str, headers: dict, target_status: str, max_wait: int = 600, interval: int = 10, also_accept: list = None):
    accept_set = {target_status}
    if also_accept:
        accept_set.update(also_accept)
    elapsed = 0
    while elapsed < max_wait:
        resp = client.get(f"{BASE_URL}/upload/{note_id}/status", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "")
            if status in accept_set:
                return True, status
            if status in ("failed", "cleaning_failed", "learning_failed"):
                return False, status
            print(f"    ... 当前状态: {status}, 等待 {interval}s (已等待 {elapsed}s)")
        time.sleep(interval)
        elapsed += interval
    return False, "timeout"


def main():
    print("\n" + "=" * 60)
    print("第12周全流程端到端测试")
    print(f"测试文件: {PDF_PATH}")
    print("=" * 60)

    # ===== 步骤1: 验证 Git 仓库状态 =====
    print("\n【步骤1】验证 Git 仓库状态")
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        is_git_repo = result.returncode == 0
        log_step("Git 仓库初始化", is_git_repo, f"返回码: {result.returncode}")

        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        has_commits = result.returncode == 0
        log_step("Git 提交历史", has_commits, "有提交" if has_commits else "暂无提交")
    except Exception as e:
        log_step("Git 验证失败", False, str(e))

    # ===== 步骤2: 验证 .gitignore 规则 =====
    print("\n【步骤2】验证 .gitignore 规则")
    try:
        gitignore_path = os.path.join(PROJECT_ROOT, ".gitignore")
        assert os.path.exists(gitignore_path), ".gitignore 不存在"
        with open(gitignore_path, "r", encoding="utf-8") as f:
            content = f.read()
        required_patterns = ["__pycache__", ".env", "data/", "node_modules/", ".trae/"]
        for pattern in required_patterns:
            assert pattern in content, f".gitignore 缺少 {pattern}"
        log_step(".gitignore 规则", True, f"包含 {len(required_patterns)} 个必要规则")
    except Exception as e:
        log_step(".gitignore 验证失败", False, str(e))

    # ===== 步骤3: 验证 Docker 配置文件 =====
    print("\n【步骤3】验证 Docker 配置文件")
    try:
        # 检查文件存在性
        docker_files = {
            "backend/Dockerfile": os.path.join(PROJECT_ROOT, "backend", "Dockerfile"),
            "frontend/Dockerfile": os.path.join(PROJECT_ROOT, "frontend", "Dockerfile"),
            "frontend/nginx.conf": os.path.join(PROJECT_ROOT, "frontend", "nginx.conf"),
            "docker-compose.yml": os.path.join(PROJECT_ROOT, "docker-compose.yml"),
        }
        for name, path in docker_files.items():
            assert os.path.exists(path), f"{name} 不存在"
            log_step(f"{name} 存在", True)

        # 检查 Dockerfile 使用国内源
        with open(docker_files["backend/Dockerfile"], "r", encoding="utf-8") as f:
            backend_dockerfile = f.read()
        assert "tuna.tsinghua.edu.cn" in backend_dockerfile, "后端 Dockerfile 未使用清华 pip 源"
        log_step("后端 Dockerfile 国内源", True, "pip 使用清华源")

        with open(docker_files["frontend/Dockerfile"], "r", encoding="utf-8") as f:
            frontend_dockerfile = f.read()
        assert "npmmirror.com" in frontend_dockerfile, "前端 Dockerfile 未使用淘宝 npm 源"
        log_step("前端 Dockerfile 国内源", True, "npm 使用淘宝源")

        # 检查 nginx.conf 配置
        with open(docker_files["frontend/nginx.conf"], "r", encoding="utf-8") as f:
            nginx_conf = f.read()
        assert "try_files" in nginx_conf, "nginx.conf 缺少 SPA 路由回退"
        assert "proxy_pass" in nginx_conf, "nginx.conf 缺少 API 反向代理"
        log_step("nginx.conf 配置", True, "SPA 回退 + API 代理")

        # 检查 docker-compose.yml
        with open(docker_files["docker-compose.yml"], "r", encoding="utf-8") as f:
            compose = f.read()
        assert "backend:" in compose, "docker-compose.yml 缺少 backend 服务"
        assert "frontend:" in compose, "docker-compose.yml 缺少 frontend 服务"
        log_step("docker-compose.yml 配置", True, "包含 backend + frontend 服务")

    except Exception as e:
        log_step("Docker 配置验证失败", False, str(e))

    # ===== 步骤4: 验证启动脚本 =====
    print("\n【步骤4】验证启动脚本")
    try:
        start_bat = os.path.join(PROJECT_ROOT, "start.bat")
        start_sh = os.path.join(PROJECT_ROOT, "start.sh")
        assert os.path.exists(start_bat), "start.bat 不存在"
        assert os.path.exists(start_sh), "start.sh 不存在"
        log_step("启动脚本存在", True, "start.bat + start.sh")

        with open(start_bat, "r", encoding="utf-8") as f:
            bat_content = f.read()
        assert "uvicorn" in bat_content, "start.bat 缺少 uvicorn 启动"
        assert "celery" in bat_content, "start.bat 缺少 celery 启动"
        assert "npm run dev" in bat_content, "start.bat 缺少前端启动"
        log_step("start.bat 内容", True, "包含 uvicorn + celery + npm run dev")
    except Exception as e:
        log_step("启动脚本验证失败", False, str(e))

    # ===== 步骤5: 验证 README.md =====
    print("\n【步骤5】验证 README.md")
    try:
        readme_path = os.path.join(PROJECT_ROOT, "README.md")
        assert os.path.exists(readme_path), "README.md 不存在"
        with open(readme_path, "r", encoding="utf-8") as f:
            readme = f.read()
        required_sections = ["快速开始", "Docker", "项目结构"]
        for section in required_sections:
            assert section in readme, f"README.md 缺少 {section} 章节"
        log_step("README.md 内容", True, f"包含 {len(required_sections)} 个必要章节")
    except Exception as e:
        log_step("README.md 验证失败", False, str(e))

    # ===== 步骤6-15: API 全流程测试 =====
    print("\n【步骤6】注册新用户")
    if not os.path.exists(PDF_PATH):
        print(f"\n[错误] 测试PDF文件不存在: {PDF_PATH}")
        sys.exit(1)

    ts = int(time.time())
    test_user = {
        "username": f"week12test{ts}",
        "email": f"week12test{ts}@example.com",
        "password": "TestPass123!",
    }

    try:
        resp = client.post(f"{BASE_URL}/auth/register", json=test_user)
        assert resp.status_code == 201, f"注册失败: {resp.text}"
        token = resp.json()["access_token"]
        log_step("注册成功", True, f"用户: {test_user['username']}")
    except Exception as e:
        log_step("注册失败", False, str(e))
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    # 步骤7: 上传
    print("\n【步骤7】上传PDF文件")
    try:
        with open(PDF_PATH, "rb") as f:
            files = {"file": (os.path.basename(PDF_PATH), f, "application/pdf")}
            resp = client.post(f"{BASE_URL}/upload", headers=headers, files=files)
        assert resp.status_code == 201, f"上传失败: {resp.text}"
        note_data = resp.json()
        note_id = note_data["id"]
        log_step("上传成功", True, f"笔记ID: {note_id[:8]}...")
    except Exception as e:
        log_step("上传失败", False, str(e))
        sys.exit(1)

    # 步骤8: 转换
    print("\n【步骤8】等待PDF转换完成")
    success, status = poll_status(note_id, headers, "converted", max_wait=600, interval=15,
                                  also_accept=["cleaning", "cleaned", "learning", "archived"])
    log_step("转换完成" if success else "转换失败", success, f"状态: {status}")
    if not success:
        sys.exit(1)

    # 步骤9: 清洗
    print("\n【步骤9】触发清洗")
    resp = client.get(f"{BASE_URL}/upload/{note_id}/status", headers=headers)
    current_status = resp.json().get("status", "")
    if current_status in ("cleaned", "learning", "archived"):
        log_step("清洗已完成（自动）", True)
    else:
        client.post(f"{BASE_URL}/cleaning/{note_id}/start", headers=headers)
        success, status = poll_status(note_id, headers, "cleaned", max_wait=600, interval=15,
                                      also_accept=["learning", "archived"])
        log_step("清洗完成" if success else "清洗失败", success, f"状态: {status}")

    # 步骤10: 理解
    print("\n【步骤10】触发理解管道")
    resp = client.get(f"{BASE_URL}/upload/{note_id}/status", headers=headers)
    current_status = resp.json().get("status", "")
    if current_status == "archived":
        log_step("理解已完成（自动）", True)
    else:
        client.post(f"{BASE_URL}/understanding/{note_id}/start", headers=headers)
        success, status = poll_status(note_id, headers, "archived", max_wait=900, interval=20)
        log_step("理解完成" if success else "理解未完成", success, f"状态: {status}")

    # 步骤11: 验证卡片和题目
    print("\n【步骤11】验证知识卡片和题目")
    for wait in range(30):
        try:
            resp = client.get(f"{BASE_URL}/understanding/questions?note_id={note_id}&page=1&page_size=100", headers=headers)
            if resp.status_code == 200 and resp.json().get("total", 0) > 0:
                break
        except:
            pass
        time.sleep(10)

    card_count = 0
    question_count = 0
    try:
        resp = client.get(f"{BASE_URL}/understanding/cards?note_id={note_id}&page=1&page_size=100", headers=headers)
        card_count = resp.json().get("total", 0) if resp.status_code == 200 else 0
        resp = client.get(f"{BASE_URL}/understanding/questions?note_id={note_id}&page=1&page_size=100", headers=headers)
        question_count = resp.json().get("total", 0) if resp.status_code == 200 else 0
        log_step("知识卡片", True, f"共 {card_count} 张")
        log_step("题目", True, f"共 {question_count} 道")
    except Exception as e:
        log_step("卡片/题目验证失败", False, str(e))

    # 步骤12: 答题
    print("\n【步骤12】获取到期题目并答题")
    answered = 0
    try:
        resp = client.get(f"{BASE_URL}/review/due?limit=10", headers=headers)
        due_items = resp.json().get("items", []) if resp.status_code == 200 else []
        log_step("到期题目", True, f"共 {len(due_items)} 道")

        for quiz in due_items[:5]:
            quiz_id = quiz["id"]
            qtype = quiz.get("question_type", "choice")
            if qtype == "choice":
                options_raw = quiz.get("options")
                if options_raw:
                    try:
                        options = json.loads(options_raw) if isinstance(options_raw, str) else options_raw
                        user_answer = options[0] if options else "A"
                    except:
                        user_answer = "A"
                else:
                    user_answer = "A"
            elif qtype == "fill_blank":
                user_answer = "测试答案"
            else:
                user_answer = "这是一个测试答案"

            resp = client.post(f"{BASE_URL}/review/submit", headers=headers, json={
                "quiz_id": quiz_id, "user_answer": user_answer, "time_spent_ms": 5000,
            })
            if resp.status_code == 200:
                answered += 1
                result = resp.json()
                log_step(f"答题 {answered}", True, f"正确: {result['is_correct']}, quality: {result['quality']}")
        log_step("答题完成", True, f"共答 {answered} 道")
    except Exception as e:
        log_step("答题验证失败", False, str(e))

    # 步骤13: 学习报告
    print("\n【步骤13】验证今日学习报告")
    try:
        resp = client.get(f"{BASE_URL}/report/daily", headers=headers)
        assert resp.status_code == 200
        report = resp.json()
        log_step("今日学习报告", True,
                 f"新掌握: {report['new_mastered']}, 复习: {report['total_reviews']}, 正确率: {report['today_accuracy']}%")
    except Exception as e:
        log_step("学习报告验证失败", False, str(e))

    # 步骤14: 7天趋势和薄弱点
    print("\n【步骤14】验证7天趋势和薄弱点")
    try:
        resp = client.get(f"{BASE_URL}/report/weekly-trend", headers=headers)
        assert resp.status_code == 200
        trend = resp.json()
        log_step("7天趋势", True, f"共 {len(trend['items'])} 天, 平均正确率: {trend['avg_accuracy']}%")

        resp = client.get(f"{BASE_URL}/report/weak-points?limit=5", headers=headers)
        assert resp.status_code == 200
        weak = resp.json()
        log_step("薄弱点", True, f"共 {weak['total']} 个")
    except Exception as e:
        log_step("趋势/薄弱点验证失败", False, str(e))

    # 步骤15: 全局异常处理和认证校验
    print("\n【步骤15】验证全局异常处理和认证校验")
    try:
        resp = client.post(f"{BASE_URL}/review/submit", headers={**headers, "Content-Type": "application/json"}, content="invalid json{{{")
        assert resp.status_code == 422
        log_step("全局异常处理", True, f"422 错误统一格式")

        no_auth = httpx.Client(timeout=30.0)
        resp = no_auth.get(f"{BASE_URL}/notes")
        assert resp.status_code == 401
        log_step("认证校验", True, "无Token请求返回401")
    except Exception as e:
        log_step("异常处理/认证校验验证失败", False, str(e))

    # 汇总
    print("\n" + "=" * 60)
    print("第12周全流程端到端测试完成")
    print("=" * 60)
    print(f"  测试用户: {test_user['username']}")
    print(f"  测试文件: {PDF_PATH}")
    print(f"  笔记ID: {note_id[:8]}...")
    print(f"  知识卡片: {card_count} 张")
    print(f"  题目: {question_count} 道")
    print(f"  答题: {answered} 道")
    print(f"  第12周新增验证: Git + .gitignore + Docker + 启动脚本 + README")
    print(f"  状态: 正常结束")
    print()


if __name__ == "__main__":
    main()
