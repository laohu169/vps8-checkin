#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到
───────────────────
技术路线: SeleniumBase UC Mode 浏览器自动过 reCAPTCHA
───────────────────
需要 GitHub Secrets:
  VPS8_EMAIL        — vps8 登录邮箱
  VPS8_PASSWORD     — vps8 登录密码
  AI_API_KEY        — AI 识图 API Key (备用，如果 UC 模式失败)
  AI_BASE_URL       — AI API Base URL
  AI_MODEL_NAME     — AI 模型名
  TELEGRAM_BOT_TOKEN — Telegram Bot Token (通知，可选)
  MY_CHAT_ID         — Telegram Chat ID (通知，可选)
"""

import os
import sys
import time
import json
import re
from datetime import datetime
from pathlib import Path

import requests
from seleniumbase import SB
from loguru import logger

# ─── 配置 ────────────────────────────────────────────────────
BASE_URL = "https://vps8.zz.cd"
LOGIN_URL = f"{BASE_URL}/login"
SIGNIN_URL = f"{BASE_URL}/points/signin"
API_SIGNIN = f"{BASE_URL}/api/client/points/signin"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL_NAME = os.environ.get("AI_MODEL_NAME", "gpt-4o")
VPS8_EMAIL = os.environ.get("VPS8_EMAIL", "")
VPS8_PASSWORD = os.environ.get("VPS8_PASSWORD", "")

WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
OUTPUT_DIR = WORKSPACE / "output" / "vps8"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── 日志 ─────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")


# ─── 通知 ─────────────────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not MY_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": MY_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram 通知失败: {e}")


def ai_solve_captcha(image_base64: str) -> str:
    """用 AI 识别验证码"""
    if not AI_API_KEY:
        return ""
    try:
        resp = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "这是 reCAPTCHA 验证码截图，请识别并只返回验证码值。"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                        ]
                    }
                ],
                "max_tokens": 50,
                "temperature": 0.1,
            },
            timeout=30,
        )
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        return ""


# ─── 登录 ─────────────────────────────────────────────────────
def do_login(sb: "SB") -> bool:
    """
    登录登录。FOSSBilling 使用 JS 表单提交，所以需要用
    SeleniumBase UC Mode 自动过 reCAPTCHA。
    
    策略：
    1. 打开登录页 2. 填表单 3. UC 处理验证码 4. JS 提交表单
    """
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("未配置邮箱/密码，跳过自动登录")
        return False

    for attempt in range(1, 4):
        logger.info(f"登录尝试 [{attempt}/3]...")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=3)
        sb.sleep(4)

        try:
            # 检查是否已经登录
            if "/login" not in sb.get_current_url():
                logger.info("已处于登录状态，跳过登录")
                return True
        except:
            pass

        # 填表单
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
        except Exception as e:
            logger.error(f"填表单失败: {e}")
            return False

        # 过验证码 - UC Mode（必须在填完表单后处理，保证 token 新鲜）
        try:
            if "recaptcha" in sb.get_page_source().lower():
                logger.info("检测到 reCAPTCHA，UC Mode 过验证码...")
                sb.uc_gui_click_captcha()
                sb.sleep(3)
        except Exception as e:
            logger.warning(f"UC 验证码处理：{e}")

        # 提交表单
        try:
            sb.click('button[type="submit"]')
            sb.sleep(8)
            logger.info("已点击登录按钮，等待跳转...")
        except Exception as e:
            logger.error(f"点击登录失败: {e}")
            return False

        # 检查结果
        current_url = sb.get_current_url()
        logger.info(f"当前 URL: {current_url}")

        if "/login" not in current_url:
            logger.info("✅ 登录成功")
            # 保存 cookies
            cookies = sb.get_cookies()
            cookie_file = OUTPUT_DIR / "cookies.json"
            with open(cookie_file, "w") as f:
                json.dump({"url": BASE_URL, "cookies": cookies, "time": datetime.now().isoformat()}, f, indent=2)
            return True

        logger.warning(f"登录失败，仍在登录页 ({attempt}/3)")
        # 检测错误信息
        try:
            err = sb.find_element(".alert-danger, .alert-error, .invalid-feedback", timeout=2)
            logger.warning(f"错误：{err.text}")
        except:
            logger.warning("未检测到错误信息，可能是验证码未通过或表单提交失败")
        # 多等一会儿再重试
        if attempt < 3:
            time.sleep(3)

    logger.error("3 次登录尝试均告失败")
    return False


# ─── 登录完成 ──────────────────────────────────────────────────
def do_signin(sb: "SB") -> str:
    """
    1. 检查是否已签到 2. 提取 CSRF Token 3. POST 签到 API
    4. 检查签到结果
    """
    # 1. 打开签到页
    logger.info("打开签到页...")
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    source = sb.get_page_source()

    # 检查登录
    if "Login to your account" in source:
        return "❌ Cookie 失效，登录被踢到登录页面"

    # 检查是否已签到
    if "已签到" in source:
        logger.info("今天已签到过了 ✅")
        return "✅ 今天已签到过了"

    # 2. 提取 CSRF Token
    csrf_match = re.search(r'name="CSRFToken"\s+value="(\w+)"', source)
    if not csrf_match:
        csrf_match = re.search(r'name="csrf-token"\s+content="(\w+)"', source)
    if not csrf_match:
        logger.error("无法提取 CSRF Token")
        return "❌ 无法提取 CSRF Token"
    csrf_token = csrf_match.group(1)
    logger.info(f"CSRF Token: {csrf_token[:10]}...")

    # 3. POST 签到 API
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())
    logger.info("尝试直接提交签到 API...")
    try:
        resp = requests.post(
            API_SIGNIN,
            params={"CSRFToken": csrf_token},
            headers={
                "Cookie": cookie_str,
                "Referer": SIGNIN_URL,
                "Origin": BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": sb.get_user_agent(),
            },
            timeout=10,
        )
        logger.info(f"API [{resp.status_code}]: {resp.text[:300]}")

        if resp.status_code in (200, 302):
            try:
                j = resp.json()
                if j.get("error"):
                    msg = j["error"].get("message", "")
                    if "已签到" in msg or "already" in msg.lower():
                        return "✅ 已签到"
                    return f"⚠️ API 错误: {msg}"
                if "result" in j and j["result"] is not None:
                    return f"✅ 签到成功! 结果: {json.dumps(j['result'], ensure_ascii=False)[:200]}"
            except:
                if "签到成功" in resp.text or "已签到" in resp.text:
                    return "✅ 签到成功!"
                return f"⚠️ 响应不确定: {resp.text[:200]}
    except Exception as e:
        logger.warning(f"API 签到尝试失败: {e}

    # 4. 页面上点击签到按钮（如果有验证码尝试过）
    try:
        logger.info("尝试页面签到按钮...")
        # 如果有验证码，用 UC Mode 过
        if "recaptcha" in sb.get_page_source().lower():
            try:
                sb.uc_gui_click_captcha()
                sb.sleep(3)
            except:
                pass
            pass

        sb.click("#points-signin-submit")
        sb.sleep(6)
        new_source = sb.get_page_source()
        if "已签到" in new_source:
            return "✅ 签到成功! (页面提交)"
    except Exception as e:
        logger.warning(f"页面签到失败: {e}

    return "⚠️ 签到状态不确定，请手动检查"


# ─── 主函数 ───────────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info("🦞 VPS8 每日签到开始")
    logger.info(f"🌐 目标: {BASE_URL}")
    logger.info(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    result_msg = ""
    success = False

    # 判断系统
    is_linux = sys.platform.startswith("linux")

    with SB(
        uc=True,
        locale="en",
        undetectable=True,
        log_cdp=False,
        chromium_arg=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
        ],
    ) as sb:
        # 1. 尝试直接访问签到页
        logger.info("打开签到页...")
        sb.open(SIGNIN_URL)
        sb.sleep(3)
        source = sb.get_page_source()

        if "Login to your account" in source:
            logger.info("Cookie 无效，执行登录...")
            if not do_login(sb):
                result_msg = "❌ 登录失败 (检查邮箱/密码或验证码)"
                success = False
            else:
                result_msg = do_sign(sb)
                success = "✅" in result_msg and "❌" not in result_msg
        else:
            logger.info("Cookie 有效，直接签到")
            result_msg = do_sign(sb)
            success = "✅" in result_msg

    # 发送通知
    msg = (
        f"🦞 <b>VPS8 签到结果</b>\n"
        f"📅 日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'✅' if success else '❌'} {result_msg}\n"
        f"🌐 https://vps8.zz.cd/points/signin"
    )
    send_telegram(msg)

    # GitHub Actions 输出
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"success={'true' if success else 'false'}\n")
            f.write(f"result={result_msg}\n")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
