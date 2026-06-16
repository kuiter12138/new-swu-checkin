#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import ddddocr
import requests
from playwright.sync_api import sync_playwright

MAX_RETRY = 3

def get_swu_token(username: str, password: str) -> str:
    """使用 Playwright 登录并获取 token，支持验证码重试"""
    last_exception = None
    file_path = None

    for attempt in range(1, MAX_RETRY + 1):
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                    ]
                )
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()

                login_url = 'https://of.swu.edu.cn/cas/oauth/login/SWU_CAS2_FEDERAL?service=https%3A%2F%2Fof.swu.edu.cn%2Fgateway%2Ffighter-middle%2Fapi%2Fintegrate%2Fuaap%2Fcas%2Fresolve-cas-return%3Fnext%3Dhttps%253A%252F%252Fof.swu.edu.cn%252F%2523%252FcasLogin%253Ffrom%253D%25252FappCenter'
                page.goto(login_url, wait_until='networkidle')

                # 点击统一身份认证按钮
                unified_btn = page.locator('img[src="img/unified_button.png"]')
                if unified_btn.count() > 0:
                    unified_btn.click()
                    time.sleep(1)

                # 输入账号密码
                page.locator('.hd').nth(1).fill(username)
                page.locator('.hd').nth(2).fill(password)

                # 处理验证码
                os.makedirs('images', exist_ok=True)
                img = page.locator('#kaptchaImage')
                if not img.count():
                    raise Exception("未找到验证码图片")
                file_path = 'images/captcha.png'
                img.screenshot(path=file_path)

                with open(file_path, 'rb') as f:
                    image_bytes = f.read()
                ocr = ddddocr.DdddOcr(show_ad=False)
                captcha_text = ocr.classification(image_bytes)
                print(f"[尝试 {attempt}/{MAX_RETRY}] 识别验证码: {captcha_text}")

                # 输入验证码
                captcha_input = page.locator('.dfinput')
                captcha_input.fill(captcha_text)
                time.sleep(0.3)

                # 点击登录按钮
                login_btn = page.locator('[style="vertical-align: top;"]')
                if not login_btn.count():
                    login_btn = page.locator('.btn.btn-default.blue')
                login_btn.click()

                print("等待页面跳转加载...")
                page.wait_for_load_state('networkidle', timeout=15000)
                time.sleep(2)

                # 获取 token
                print("等待登录完成并获取 token...")
                for _ in range(30):
                    time.sleep(1)
                    try:
                        token = page.evaluate(
                            "() => localStorage.getItem('access_token') || sessionStorage.getItem('access_token')"
                        )
                        if token:
                            return token
                    except Exception as e:
                        print(f"读取 token 时异常: {e}，继续等待...")

                    # 检查验证码错误提示
                    try:
                        err = page.locator('#err')
                        if err.count() > 0:
                            err_text = err.inner_text()
                            if '验证码错误' in err_text or '验证码不正确' in err_text:
                                print("验证码错误，准备重试...")
                                break
                    except:
                        pass
                else:
                    raise Exception("获取 token 超时，可能登录失败")

            except Exception as e:
                last_exception = e
                print(f"第 {attempt} 次尝试失败: {e}")
                if attempt < MAX_RETRY:
                    time.sleep(2)
            finally:
                if browser:
                    browser.close()
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    try:
                        os.rmdir('images')
                    except OSError:
                        pass

    raise Exception(f"所有重试均失败，最后错误: {last_exception}")


def get_transition_today(token: str):
    url = "https://of.swu.edu.cn/gateway/fighter-baida/api/cqtj/getTransitionByToday"
    headers = {"fighter-auth-token": token}
    resp = requests.post(url, headers=headers, data={"pageNum": 1, "pageSize": 1}).json()
    records = resp.get("data", {}).get("records", [])
    return records[0] if records else None


def get_student_id(token: str):
    url = "https://of.swu.edu.cn/gateway/fighter-middle/api/auth/user?appType=fighter-portal"
    headers = {"fighter-auth-token": token}
    resp = requests.get(url, headers=headers).json()
    return resp["data"]["subject"]["username"]


def checkin(token: str):
    task = get_transition_today(token)
    if not task:
        print("❌ 今日无打卡任务")
        return False
    if task.get("qdzt") == "已签到":
        print("✅ 今日已打卡")
        return True

    student_id = get_student_id(token)
    formid = task["formId"]
    record_id = task["id"]
    url = "https://of.swu.edu.cn/gateway/fighter-baida/api/form-instance/save"
    params = {"formId": formid, "isSubmitProcess": False}
    headers = {"fighter-auth-token": token, "Content-Type": "application/json"}
    payload = {
        "id": record_id,
        "formId": formid,
        "tsrq": time.strftime("%Y-%m-%d"),
        "xh": student_id,
        "qdsj": ["21:00", "23:30"],
    }
    resp = requests.post(url, headers=headers, params=params, data=json.dumps(payload)).json()
    if resp.get("code") == 200 and resp.get("data"):
        print("✅ 打卡成功！")
        return True
    else:
        print(f"❌ 打卡失败: {resp.get('msg', '未知错误')}")
        return False


def main():
    username = os.environ.get("SWU_USERNAME")
    password = os.environ.get("SWU_PASSWORD")
    if not username or not password:
        print("❌ 缺少环境变量 SWU_USERNAME 或 SWU_PASSWORD")
        raise SystemExit(1)

    try:
        print("开始自动登录获取 token...")
        token = get_swu_token(username, password)
        print(f"获取 token 成功: {token[:10]}...")
        print("正在执行打卡...")
        success = checkin(token)
        if not success:
            raise SystemExit(1)
    except Exception as e:
        print(f"❌ 运行失败: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
