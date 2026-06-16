#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import ddddocr
import requests
from DrissionPage import ChromiumPage, ChromiumOptions

MAX_RETRY = 3              # 登录重试次数（含验证码重试）
CAPTCHA_RETRY = 3          # 单次登录中验证码重试次数


def get_swu_token(username: str, password: str) -> str:
    """
    使用 DrissionPage 登录，自动适配 GitHub Actions 无头环境。
    返回 access_token，若所有重试失败则抛出异常。
    """
    last_exception = None
    file_path = None

    for attempt in range(1, MAX_RETRY + 1):
        dp = None
        try:
            # ---------- 浏览器配置 ----------
            co = ChromiumOptions()
            co.set_argument('--window-size=1920,1080')

            if os.environ.get("GITHUB_ACTIONS") == "true":
                co.headless(True)
                co.set_argument('--no-sandbox')
                co.set_argument('--disable-dev-shm-usage')
                co.set_argument('--disable-gpu')
                co.set_argument('--disable-blink-features=AutomationControlled')
                # 显式指定浏览器路径（GitHub Actions 中 chromium-browser 的默认路径）
                co.set_paths(browser_path='/usr/bin/chromium-browser')
            else:
                co.headless(False)   # 本地调试可见

            dp = ChromiumPage(co)

            # ---------- 访问登录页 ----------
            login_url = (
                'https://of.swu.edu.cn/cas/oauth/login/SWU_CAS2_FEDERAL'
                '?service=https%3A%2F%2Fof.swu.edu.cn%2Fgateway%2Ffighter-middle'
                '%2Fapi%2Fintegrate%2Fuaap%2Fcas%2Fresolve-cas-return'
                '%3Fnext%3Dhttps%253A%252F%252Fof.swu.edu.cn%252F%2523%252FcasLogin'
                '%253Ffrom%253D%25252FappCenter'
            )
            dp.get(login_url)
            dp.wait.load_start()   # 等待页面加载完成

            # ---------- 点击“统一身份认证”按钮 ----------
            unified_btn = dp.ele('@src=img/unified_button.png', timeout=5)
            if not unified_btn:
                unified_btn = dp.ele('div[onclick="_goLogin()"]', timeout=5)
            if unified_btn:
                unified_btn.click()
                dp.wait.load_start()
            else:
                print("⚠️ 未找到统一身份认证按钮，可能已跳转，继续...")

            # ---------- 输入账号密码 ----------
            username_input = dp.ele('@class=hd', index=1, timeout=5)
            password_input = dp.ele('@class=hd', index=2, timeout=5)
            if not username_input or not password_input:
                username_input = dp.ele('#loginName', timeout=5)
                password_input = dp.ele('#password', timeout=5)
            if not username_input or not password_input:
                raise Exception("未找到用户名或密码输入框")

            username_input.clear()
            username_input.input(username)
            password_input.clear()
            password_input.input(password)

            # ---------- 处理验证码（带重试） ----------
            os.makedirs('images', exist_ok=True)
            captcha_success = False
            for captcha_attempt in range(1, CAPTCHA_RETRY + 1):
                # 获取验证码图片元素（如果之前刷新过，需要重新获取）
                img = dp.ele('#kaptchaImage', timeout=3)
                if not img:
                    img = dp.ele('@src=validate', timeout=3)
                if not img:
                    raise Exception("未找到验证码图片元素")

                file_path = 'images/captcha.png'
                if os.path.exists(file_path):
                    os.remove(file_path)
                img.save(path='images', name='captcha.png')

                with open(file_path, 'rb') as f:
                    image_bytes = f.read()
                ocr = ddddocr.DdddOcr(show_ad=False)
                result = ocr.classification(image_bytes)
                print(f"[登录尝试 {attempt}/{MAX_RETRY}] [验证码尝试 {captcha_attempt}/{CAPTCHA_RETRY}] 识别结果: {result}")

                # 输入验证码
                captcha_input = dp.ele('@class=dfinput', timeout=3)
                if not captcha_input:
                    captcha_input = dp.eles('tag=input@@type=text')[-1]
                captcha_input.clear()
                dp.actions.click(captcha_input).type(result)
                # 触发输入事件（确保前端校验）
                dp.run_js('''
                    var el = arguments[0];
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                ''', captcha_input)
                time.sleep(0.5)

                # 点击登录按钮
                login_btn = dp.ele('@style=vertical-align: top;', timeout=3)
                if not login_btn:
                    login_btn = dp.ele('.btn.btn-default.blue', timeout=3)
                if not login_btn:
                    login_btn = dp.ele('tag=input@@type=submit', timeout=3)
                if not login_btn:
                    raise Exception("未找到登录按钮")
                login_btn.click()

                # 等待登录处理（最多8秒）
                time.sleep(3)

                # 检查是否出现验证码错误提示
                err = dp.ele('#err', timeout=1)
                if err and ('验证码错误' in err.text or '验证码不正确' in err.text):
                    print(f"❌ 验证码错误，准备刷新验证码重试...")
                    # 点击验证码图片刷新（部分网站点击验证码图可刷新）
                    refresh_btn = dp.ele('#kaptchaImage', timeout=2)
                    if refresh_btn:
                        refresh_btn.click()
                        time.sleep(1)
                    continue
                else:
                    # 没有错误提示，认为登录可能成功
                    captcha_success = True
                    break

            if not captcha_success:
                raise Exception("验证码识别重试次数用尽，登录失败")

            # ---------- 等待跳转并获取 Token ----------
            print("登录提交成功，等待跳转...")
            # 等待页面跳转到目标地址（最多15秒）
            dp.wait.url_change(login_url, timeout=15)
            time.sleep(2)   # 额外等待页面脚本执行

            print("尝试从 localStorage 获取 access_token...")
            for _ in range(20):
                time.sleep(1)
                try:
                    token = dp.run_js(
                        'return localStorage.getItem("access_token") || sessionStorage.getItem("access_token")'
                    )
                    if token:
                        print(f"✅ 成功获取 Token: {token[:10]}...")
                        return token
                except Exception as e:
                    print(f"读取 token 时异常: {e}")

                # 如果页面出现登录失败信息，提前退出
                err = dp.ele('#err', timeout=0.5)
                if err and ('验证码错误' in err.text or '登录失败' in err.text):
                    print("登录失败，准备重试...")
                    break

            # 如果循环结束仍未获取到 token
            raise Exception("获取 token 超时，可能登录失败")

        except Exception as e:
            last_exception = e
            print(f"第 {attempt} 次登录尝试失败: {e}")
            if attempt < MAX_RETRY:
                time.sleep(3)
        finally:
            if dp:
                dp.quit()
            # 清理临时图片文件
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                try:
                    os.rmdir('images')
                except OSError:
                    pass

    raise Exception(f"所有登录重试均失败，最后错误: {last_exception}")


def get_transition_today(token: str):
    """获取今日打卡任务"""
    url = "https://of.swu.edu.cn/gateway/fighter-baida/api/cqtj/getTransitionByToday"
    headers = {"fighter-auth-token": token}
    resp = requests.post(url, headers=headers, data={"pageNum": 1, "pageSize": 1}).json()
    records = resp.get("data", {}).get("records", [])
    return records[0] if records else None


def get_student_id(token: str):
    """获取学号"""
    url = "https://of.swu.edu.cn/gateway/fighter-middle/api/auth/user?appType=fighter-portal"
    headers = {"fighter-auth-token": token}
    resp = requests.get(url, headers=headers).json()
    return resp["data"]["subject"]["username"]


def checkin(token: str):
    """执行打卡"""
    task = get_transition_today(token)
    if not task:
        print("❌ 今日无打卡任务")
        return False
    if task.get("qdzt") == "已签到":
        print("✅ 今日已打卡，无需重复操作")
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
        print("🚀 开始自动登录获取 token...")
        token = get_swu_token(username, password)
        print("正在执行打卡...")
        success = checkin(token)
        if not success:
            raise SystemExit(1)
        print("🎉 所有任务执行完毕")
    except Exception as e:
        print(f"❌ 运行失败: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
