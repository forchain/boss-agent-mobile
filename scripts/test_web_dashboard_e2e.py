"""
scripts/test_web_dashboard_e2e.py
=================================
Automated End-to-End verification script for Web Dashboard and Backend REST APIs.
"""

import sys
import requests
from rich.console import Console

console = Console()
BASE_URL = "http://127.0.0.1:8088"

def run_dashboard_e2e_tests():
    console.print(f"[bold cyan]🚀 Starting Web Dashboard E2E Automation Tests on {BASE_URL}...[/bold cyan]\n")

    # 1. Test Dashboard HTML Rendering
    console.print("1️⃣ [bold]Testing Web Dashboard HTML Rendering...[/bold]")
    resp = requests.get(f"{BASE_URL}/dashboard")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert "Boss Agent Mobile" in resp.text
    assert "求职者画像与结构化记忆" in resp.text
    assert "大模型配置 (LLM Settings)" in resp.text
    assert "岗位匹配与 AI 破冰招呼语沙盒" in resp.text
    assert "自动化任务控制台" in resp.text
    console.print("   ✅ Web Dashboard HTML and Panels rendered successfully!\n")

    # 2. Test Candidate Profile CRUD
    console.print("2️⃣ [bold]Testing Candidate Profile CRUD API...[/bold]")
    profile_payload = {
        "name": "周黄金",
        "years_of_experience": 19,
        "core_skills": ["Python", "FastAPI", "LLM Agent", "Android", "Unity"],
        "target_positions": ["AI Agent 架构师", "全栈研发专家"],
        "raw_summary": "19年全栈与AI Agent开发架构经验",
    }
    resp = requests.put(f"{BASE_URL}/api/candidate/profile", json=profile_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"]["name"] == "周黄金"
    assert data["profile"]["years_of_experience"] == 19
    console.print(f"   ✅ Saved profile for [cyan]{data['profile']['name']}[/cyan] ({data['profile']['years_of_experience']}年经验)")

    resp = requests.get(f"{BASE_URL}/api/candidate/profile")
    assert resp.status_code == 200
    assert resp.json()["profile"]["name"] == "周黄金"
    console.print("   ✅ Verified candidate profile retrieval from broker/database!\n")

    # 3. Test LLM Settings API
    console.print("3️⃣ [bold]Testing LLM Settings API...[/bold]")
    llm_payload = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "temperature": 0.3,
    }
    resp = requests.put(f"{BASE_URL}/api/settings/llm", json=llm_payload)
    assert resp.status_code == 200
    assert resp.json()["provider"] == "openai"
    console.print("   ✅ LLM Settings updated and verified!\n")

    # 4. Test Live JD Match Sandbox Evaluation
    console.print("4️⃣ [bold]Testing Live Match & Greeting Sandbox API...[/bold]")
    jd_payload = {
        "job_title": "资深 AI Agent 研发专家",
        "company_name": "未来智能未来科技",
        "salary_range": "40-60K·16薪",
        "job_description": "负责移动端 Agent 系统架构设计，要求精通 Python、多端通信与大模型工程化落地，主导过大型项目。",
    }
    resp = requests.post(f"{BASE_URL}/api/match/evaluate", json=jd_payload)
    assert resp.status_code == 200
    match_data = resp.json()
    console.print(f"   🎯 [bold green]Match Score:[/bold green] {match_data['match_score']}/100")
    console.print(f"   🔍 [bold magenta]Extracted JD Key Requirements:[/bold magenta] {match_data.get('jd_key_requirements')}")
    console.print(f"   💬 [bold cyan]Tailored Greeting Draft:[/bold cyan] {match_data['greeting_message']}")
    console.print("   ✅ Live match scoring & greeting generation sandbox passed!\n")

    # 5. Test Automation Task Queue & Control
    console.print("5️⃣ [bold]Testing Automation Task Submission & Cancellation...[/bold]")
    task_payload = {
        "task_type": "AUTO_APPLY",
        "payload": {
            "keyword": "agent",
            "min_score": 75,
            "preview_only": True,
            "preview_timeout_sec": 3.0,
        }
    }
    resp = requests.post(f"{BASE_URL}/api/tasks", json=task_payload)
    assert resp.status_code == 202
    task_info = resp.json()
    task_id = task_info["task_id"]
    console.print(f"   📋 Created task [yellow]{task_id}[/yellow] (Status: {task_info['status']})")

    # Query status
    resp = requests.get(f"{BASE_URL}/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id
    console.print(f"   🔍 Task {task_id} status verified: {resp.json()['status']}")

    # Cancel task
    resp = requests.post(f"{BASE_URL}/api/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    console.print(f"   🛑 Task {task_id} successfully cancelled!")

    console.print("\n🎉 [bold green]All Web Dashboard and Backend REST API E2E Tests PASSED![/bold green]")

if __name__ == "__main__":
    run_dashboard_e2e_tests()
