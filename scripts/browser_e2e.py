"""Real Edge browser smoke test using Playwright and an isolated database."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def _wait_for_server(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"Streamlit did not become ready: {url}")


def _assert_no_app_error(page: Page, step: str) -> None:
    text = page.locator("body").inner_text()
    markers = ("This app has encountered an error", "Uncaught app execution", "Traceback")
    if any(marker in text for marker in markers):
        raise AssertionError(f"{step}: page contains an application error")


def _wait_text(page: Page, text: str, timeout: int = 30_000) -> None:
    page.get_by_text(text, exact=False).first.wait_for(timeout=timeout)
    time.sleep(0.6)
    _assert_no_app_error(page, text)


def _run_browser(base_url: str, screenshot: Path) -> None:
    edge = next((path for path in EDGE_PATHS if path.exists()), None)
    if edge is None:
        raise RuntimeError("Microsoft Edge executable not found")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(edge),
            args=["--disable-gpu", "--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(30_000)
        try:
            page.goto(base_url, wait_until="networkidle")
            _wait_text(page, "首次初始化")
            page.get_by_label("企业名称").fill("浏览器测试企业")
            page.get_by_label("管理员姓名").fill("测试管理员")
            page.get_by_label("登录用户名").fill("browser-admin")
            page.get_by_label("登录密码", exact=True).fill("browser-password-123")
            page.get_by_label("确认密码").fill("browser-password-123")
            page.get_by_role("button", name="创建企业空间").click()
            _wait_text(page, "账号安全")
            _assert_no_app_error(page, "管理员初始化")

            session_cookies = [
                cookie
                for cookie in page.context.cookies(base_url)
                if cookie["name"] == "finguard_session" and cookie["value"]
            ]
            if not session_cookies:
                raise AssertionError("Persistent browser session cookie was not created")
            page.reload(wait_until="networkidle")
            _wait_text(page, "账号安全")
            if page.get_by_label("用户名").count():
                raise AssertionError("Login page reappeared after browser refresh")

            page.get_by_role("button", name="账号安全").click()
            _wait_text(page, "登录邮箱")
            page.get_by_label("验证邮箱").fill("browser@example.com")
            page.get_by_role("button", name="发送验证码").click()
            _wait_text(page, "开发测试验证码")
            alerts = "\n".join(page.locator('[data-testid="stAlert"]').all_inner_texts())
            match = re.search(r"开发测试验证码：(\d{6})", alerts)
            if not match:
                raise AssertionError("Development verification code was not rendered")
            page.get_by_label("邮箱验证码").fill(match.group(1))
            page.get_by_role("button", name="验证并绑定邮箱").click()
            _wait_text(page, "邮箱已绑定")

            page.get_by_role("button", name="← 返回主页").click()
            _wait_text(page, "7. 审计项目与证据链")
            page.get_by_role("button", name="7. 审计项目与证据链").click()
            _wait_text(page, "项目概览")
            page.get_by_label("项目名称").fill("浏览器自动化审计项目")
            page.get_by_label("审计期间").fill("2026-11")
            page.get_by_role("button", name="创建审计项目").click()
            _wait_text(page, "当前项目")

            page.get_by_role("button", name="← 返回主页").click()
            page.get_by_role("button", name="1. 财务审计").click()
            page.get_by_role("button", name="载入合成演示数据").click()
            _wait_text(page, "凭证总笔数")
            page.screenshot(path=str(screenshot), full_page=True)
            print("browser_e2e PASS")
        finally:
            browser.close()


def main() -> int:
    port = 8765
    base_url = f"http://localhost:{port}"
    work_root = PROJECT_ROOT / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    test_root = work_root / f"browser-e2e-{os.getpid()}"
    test_root.mkdir(parents=True, exist_ok=True)
    database = test_root / "browser.db"
    backups = test_root / "backups"
    server_log = (test_root / "streamlit.log").open("w", encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(test_root / "pycache"),
            "FINGUARD_DB_PATH": str(database),
            "FINGUARD_BACKUP_DIR": str(backups),
            "FINGUARD_AUTH_REQUIRED": "1",
            "FINGUARD_USE_POSTGRES": "0",
            "FINGUARD_DATABASE_URL": "",
            "FINGUARD_EMAIL_DEV_MODE": "1",
            "FINGUARD_DISABLE_AI": "1",
            "SMTP_HOST": "",
            "SMTP_USER": "",
            "SMTP_AUTH_CODE": "",
            "SMTP_FROM": "",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.headless=true",
            f"--server.port={port}",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_server(f"{base_url}/_stcore/health")
        _run_browser(base_url, test_root / "browser-e2e.png")
        marker = PROJECT_ROOT / "data" / "browser_e2e_last_success.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "status": "success",
                    "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "url": base_url,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        shutil.rmtree(test_root, ignore_errors=True)
        return 0
    except Exception:
        server_log.flush()
        log_text = (test_root / "streamlit.log").read_text(encoding="utf-8", errors="replace")
        print(log_text[-4000:])
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        server_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
