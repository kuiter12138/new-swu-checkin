#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import traceback
import ddddocr
import requests
from DrissionPage import ChromiumPage, ChromiumOptions

# ---------- 配置参数 ----------
MAX_LOGIN_RETRY = 3          # 登录总重试次数
CAPTCHA_RETRY = 3            # 单次登录中验证码重试次数
TOKEN_WAIT_TIMEOUT = 30      # 获取Token的最大等待时间（秒）
PAGE_LOAD_TIMEOUT = 20       # 页面加载超时

# ---------- 强制刷新输出（确保日志实时显示） ----------
sys.stdout.reconfigure(line_buffering=True)


def log(msg, level="INFO"):
    """统一日志输出"""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}")
    sys.stdout.flush()


def get_swu_token(username: str, password: str) -> str:
    """
    使用 DrissionPage 登录，返回 access_token。
    包含详细的异常捕获和日志输出，适配 GitHub Actions 无头环境。
    """
    last_exception = None
    file_path = None

    for attempt in range(1, MAX_LOGIN_RETRY + 1):
        dp = None
        try:
            log(f"===== 登录尝试 {attempt}/{MAX_LOGIN_RETRY} =====")

            # ---------- 浏览器配置 ----------
            co = ChromiumOptions()
            co.set_argument('--window-size=1920,1080')

            if os.environ.get("GITHUB_ACTIONS") == "true":
                log("检测到 GitHub Actions 环境，启用无头模式")
                co.headless(True)
                co.set_argument('--no-sandbox')
                co.set_argument('--disable-dev-shm-usage')
                co.set_argument('--disable-gpu')
                co.set_argument('--disable-blink-features=AutomationControlled')
                # 指定浏览器路径（GitHub Actions 默认安装位置）
                browser_path = '/usr/bin/chromium-browser'
                if os.path.exists(browser_path):
                    co.set_paths(browser_path=browser_path)
                    log(f"浏览器路径已指定: {browser_path}")
                else:
                    log(f"警告: 未找到 {browser_path}，将尝试自动发现", "WARN")
            else:
                log("本地环境，使用有头模式")
                co.headless(False)

            dp = ChromiumPage(co)
            dp.set.timeouts(base=PAGE_LOAD_TIMEOUT)   # 设置全局超时
            log("浏览器启动成功")

            # ---------- 访问登录页 ----------
            login_url = (
                'https://of.swu.edu.cn/cas/oauth/login/SWU_CAS2_FEDERAL'
                '?service=https%3A%2F%2Fof.swu.edu.cn%2Fgateway%2Ffighter-middle'
                '%2Fapi%2Fintegrate%2Fuaap%2Fcas%2Fresolve-cas-return'
                '%3Fnext%3Dhttps%253A%252F%252Fof.swu.edu.cn%252F%2523%252FcasLogin'
                '%253Ffrom%253D%25252FappCenter'
            )
            log(f"正在访问登录页: {login_url[:60]}...")
            dp.get(login_url)
            dp.wait.load_start()
            log("登录页加载完成")

            # ---------- 点击“统一身份认证”按钮 ----------
            unified_btn = dp.ele('@src=img/unified_button.png', timeout=5)
            if not unified_btn:
                unified_btn = dp.ele('div[onclick="_goLogin()"]', timeout=5)
            if unified_btn:
                log("找到统一身份认证按钮，点击")
                unified_btn.click()
                dp.wait.load_start()
            else:
                log("未找到统一身份认证按钮（可能已自动跳转）", "WARN")

            # ---------- 输入账号密码 ----------
            username_input = dp.ele('@class=hd', index=1, timeout=5)
            password_input = dp.ele('@class=hd', index=2, timeout=5)
            if not username_input or not password_input:
                username_input = dp.ele('#loginName', timeout=5)
                password_input = dp.ele('#password', timeout=5)
            if not username_input or not password_input:
                raise Exception("未找到用户名或密码输入框")
            log("找到输入框，正在填写账号密码...")
            username_input.clear()
            username_input.input(username)
            password_input.clear()
            password_input.input(password)

            # ---------- 处理验证码（带重试） ----------
            os.makedirs('images', exist_ok=True)
            captcha_success = False
            for captcha_attempt in range(1, CAPTCHA_RETRY + 1):
                log(f"--- 验证码尝试 {captcha_attempt}/{CAPTCHA_RETRY} ---")
                # 获取验证码图片元素
                img = dp.ele('#kaptchaImage', timeout=3)
                if not img:
                    img = dp.ele('@src=validate', timeout=3)
                if not img:
                    raise Exception("未找到验证码图片元素")

                file_path = 'images/captcha.png'
                if os.path.exists(file_path):
                    os.remove(file_path)
                img.save(path='images', name='captcha.png')
                log("验证码图片已保存")

                with open(file_path, 'rb') as f:
                    image_bytes = f.read()
                ocr = ddddocr.DdddOcr(show_ad=False)
                result = ocr.classification(image_bytes)
                log(f"OCR识别结果: {result}")

                # 输入验证码
                captcha_input = dp.ele('@class=dfinput', timeout=3)
                if not captcha_input:
                    captcha_input = dp.eles('tag=input@@type=text')[-1]
                captcha_input.clear()
                dp.actions.click(captcha_input).type(result)
                # 触发输入事件
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
                log("点击登录按钮")
                login_btn.click()

                # 等待登录处理
                time.sleep(3)

                # 检查是否出现验证码错误提示
                err = dp.ele('#err', timeout=1)
                if err and ('验证码错误' in err.text or '验证码不正确' in err.text):
                    log("验证码错误，准备刷新并重试", "WARN")
                    refresh_btn = dp.ele('#kaptchaImage', timeout=2)
                    if refresh_btn:
                        refresh_btn.click()
                        time.sleep(1)
                    continue
                else:
                    captcha_success = True
                    break

            if not captcha_success:
                raise Exception("验证码识别重试次数用尽")

            # ---------- 等待跳转并获取 Token ----------
            log("登录提交成功，等待跳转...")
            dp.wait.url_change(login_url, timeout=15)
            time.sleep(2)   # 等待脚本执行

            log("尝试从 localStorage 获取 access_token...")
            start_time = time.time()
            token = None
            while time.time() - start_time < TOKEN_WAIT_TIMEOUT:
                try:
                    token = dp.run_js(
                        'return localStorage.getItem("access_token") || sessionStorage.getItem("access_token")'
                    )
                    if token:
                        log(f"成功获取 Token: {token[:10]}...")
                        return token
                except Exception as e:
                    log(f"读取 token 异常: {e}", "WARN")
                time.sleep(1)

                # 检查是否有登录失败信息
                err = dp.ele('#err', timeout=0.5)
                if err and ('验证码错误' in err.text or '登录失败' in err.text):
                    log("页面出现登录失败信息", "WARN")
                    break

            raise Exception("获取 token 超时或登录失败")

        except Exception as e:
            last_exception = e
            log(f"第 {attempt} 次登录尝试失败: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")
            if attempt < MAX_LOGIN_RETRY:
                log(f"等待 3 秒后重试...")
                time.sleep(3)
        finally:
            if dp:
                try:
                    dp.quit()
                    log("浏览器已关闭")
                except:
                    pass
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
    log("正在获取今日打卡任务...")
    url = "https://of.swu.edu.cn/gateway/fighter-baida/api/cqtj/getTransitionByToday"
    headers = {"fighter-auth-token": token}
    try:
        resp = requests.post(url, headers=headers, data={"pageNum": 1, "pageSize": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", {}).get("records", [])
        return records[0] if records else None
    except Exception as e:
        log(f"获取任务失败: {e}", "ERROR")
        return None


def get_student_id(token: str):
    """获取学号"""
    log("正在获取学号...")
    url = "https://of.swu.edu.cn/gateway/fighter-middle/api/auth/user?appType=fighter-portal"
    headers = {"fighter-auth-token": token}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["data"]["subject"]["username"]
    except Exception as e:
        log(f"获取学号失败: {e}", "ERROR")
        raise


def checkin(token: str):
    """执行打卡"""
    task = get_transition_today(token)
    if not task:
        log("❌ 今日无打卡任务", "ERROR")
        return False
    if task.get("qdzt") == "已签到":
        log("✅ 今日已打卡，无需重复操作")
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
    log(f"正在提交打卡数据: {payload}")
    try:
        resp = requests.post(url, headers=headers, params=params, data=json.dumps(payload), timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 200 and result.get("data"):
            log("✅ 打卡成功！")
            return True
        else:
            log(f"❌ 打卡失败: {result.get('msg', '未知错误')}", "ERROR")
            return False
    except Exception as e:
        log(f"打卡请求异常: {e}", "ERROR")
        return False


def main():
    log("🚀 脚本启动")
    log(f"Python 版本: {sys.version}")
    log(f"当前工作目录: {os.getcwd()}")

    username = os.environ.get("SWU_USERNAME")
    password = os.environ.get("SWU_PASSWORD")
    if not username or not password:
        log("❌ 缺少环境变量 SWU_USERNAME 或 SWU_PASSWORD", "ERROR")
        sys.exit(1)
    log(f"用户名长度: {len(username)} (不显示明文)")

    # 检查必要依赖
    try:
        import ddddocr
        log("ddddocr 导入成功")
    except Exception as e:
        log(f"ddddocr 导入失败: {e}", "ERROR")
        sys.exit(1)

    try:
        log("开始自动登录获取 token...")
        token = get_swu_token(username, password)
        log("正在执行打卡...")
        success = checkin(token)
        if not success:
            log("❌ 打卡流程未完全成功", "ERROR")
            sys.exit(1)
        log("🎉 所有任务执行完毕")
    except Exception as e:
        log(f"❌ 运行失败: {e}", "ERROR")
        log(traceback.format_exc(), "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
