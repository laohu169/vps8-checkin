#!/usr/bin/env python3
"""
VPS8.zz.cd 自动签到
─────────────────
技术路线: SeleniumBase UC Mode (浏览器自动化绕过验证码)
─────────────────
需要 Secret 环境变量:
  AI_API_KEY        — AI 识图 API Key (OpenAI 兼容接口)
  AI_BASE_URL       — AI API Base URL (如 https://api.openai.com/v1)
  AI_MODEL_NAME     — 模型名 (如 gpt-4o)
  TELEGRAM_BOT_TOKEN — Telegram Bot Token (通知用)
  MY_CHAT_ID         — Telegram Chat ID (通知用)
  VPS8_EMAIL         — VPS8 登录邮箱 (可选, Cookie 失效时自动登录)
  VPS8_PASSWORD      — VPS8 登录密码 (可选)
"""

import os
import sys
import time
import json
import random
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
    """发送 Telegram 通知"""
    if not TELEGRAM_BOT_TOKEN or not MY_CHAT_ID:
        logger.warning("未配置 Telegram 通知, 跳过")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": MY_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        logger.info("Telegram 通知已发送")
    except Exception as e:
        logger.error(f"Telegram 通知失败: {e}")


def ai_solve_captcha(image_base64: str) -> str:
    """用 AI 识图解验证码

    Parameters
    ----------
    image_base64 : 截图的 base64 字符串

    Returns
    -------
    AI 返回的文本答案
    """
    if not AI_API_KEY:
        logger.warning("未配置 AI_API_KEY, 跳过 AI 识图")
        return ""

    try:
        resp = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": (
                                "这是一个 reCAPTCHA 验证码截图。请识别图中的验证码值并只返回该值，不要返回其他内容。"
                                "如果是图像九宫格验证，请返回 'need_click' 标记。"
                            )},
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
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        logger.info(f"AI 返回: {answer}")
        return answer
    except Exception as e:
        logger.error(f"AI 识图失败: {e}")
        return ""


# ─── 签到（带验证码）─────────────────────────────────────────

def checkin_with_captcha(sb: "SB") -> str:
    """在已登录状态下完成签到, 处理 reCAPTCHA

    Returns
    -------
    str: 签到结果消息
    """
    # 1. 打开签到页面
    logger.info("打开签到页面...")
    sb.open(SIGNIN_URL)
    sb.sleep(3)

    source = sb.get_page_source()

    # 检查是否未登录
    if "Login to your account" in source:
        return "❌ Cookie 失效, 未登录"

    # 2. 检查是否已签到
    if "已签到" in source:
        logger.info("今天已经签到过了 ✅")
        return "✅ 今天已经签到过了"

    # 3. 提取 CSRF Token
    csrf_match = re.search(r'name="CSRFToken"\s+value="(\w+)"', source)
    if not csrf_match:
        csrf_match = re.search(r'name="csrf-token"\s+content="(\w+)"', source)
    if not csrf_match:
        logger.error("无法提取 CSRF Token")
        return "❌ 无法提取 CSRF Token"

    csrf_token = csrf_match.group(1)
    logger.info(f"CSRF Token: {csrf_token[:10]}...")

    # 4. 尝试直接 POST 签到 API (绕过验证码)
    logger.info("尝试直接提交签到 API...")

    # 方式 1: query string 传 token
    try:
        resp = requests.post(
            API_SIGNIN,
            params={"CSRFToken": csrf_token},
            headers={
                "Cookie": "; ".join([f"{c['name']}={c['value']}" for c in sb.get_cookies()]),
                "Referer": SIGNIN_URL,
                "Origin": BASE_URL,
                "User-Agent": sb.get_user_agent(),
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=10,
        )
        logger.info(f"API 响应 [{resp.status_code}]: {resp.text[:300]}")

        if resp.status_code in (200, 302):
            try:
                j = resp.json()
                if j.get("error"):
                    msg = j["error"].get("message", "")
                    if "已签到" in msg or "already" in msg.lower():
                        return "✅ 已签到"
                    return f"⚠️ API 返回错误: {msg}"
                if "result" in j and j["result"] is not None:
                    return f"✅ 签到成功! 结果: {json.dumps(j['result'], ensure_ascii=False)[:200]}"
            except:
                if "签到成功" in resp.text or "已签到" in resp.text:
                    return "✅ 签到成功!"
                return f"⚠️ 响应不确定: {resp.text[:200]}"
    except Exception as e:
        logger.warning(f"API 签到尝试失败: {e}")

    # 方式 2: 检查是否有验证码，如果有，用 AI 识别后填入
    logger.info("尝试处理验证码...")
    try:
        # 截图 reCAPTCHA
        sb.sleep(2)
        elements = sb.find_elements(".g-recaptcha")
        if elements:
            logger.info("发现 reCAPTCHA, 截图识别...")
            # 截图整个页面作为上下文
            captcha_img = sb.get_screenshot_as_base64()
            # AI 识别验证码
            answer = ai_solve_captcha(captcha_img)
            if answer and answer != "need_click":
                # 如果是文本验证码, 填入 reCAPTCHA response
                sb.execute_script(
                    f"document.getElementById('g-recaptcha-response').innerHTML='{answer}';"
                )
                sb.execute_script(
                    f"document.getElementById('g-recaptcha-response').textContent='{answer}';"
                )
                logger.info(f"已填入验证码, 答案: {answer}")
                sb.sleep(1)
        else:
            logger.info("没有发现验证码元素")
    except Exception as e:
        logger.warning(f"验证码处理失败: {e}")

    # 方式 3: 点击提交按钮
    try:
        submit_btn = sb.find_element("#points-signin-submit", timeout=5)
        if submit_btn.is_displayed() and submit_btn.is_enabled():
            sb.click("#points-signin-submit")
            sb.sleep(5)
            logger.info("点击了签到按钮")

            # 检查签到结果
            new_source = sb.get_page_source()
            if "已签到" in new_source:
                return "✅ 签到成功!"
            elif "签到成功" in new_source:
                return "✅ 签到成功!"
    except Exception as e:
        logger.warning(f"提交按钮点击失败: {e}")

    # 方式 4: 终极方案 - UC Mode 自动过验证码
    try:
        logger.info("尝试 UC Mode 自动绕过...")
        sb.uc_open_with_reconnect(SIGNIN_URL, reconnect_time=3)
        sb.sleep(5)

        # 检查页面是否出现验证码
        source = sb.get_page_source().lower()
        if "recaptcha" in source or "turnstile" in source:
            logger.info("检测到验证码, 尝试 UC 模式自动点击...")
            try:
                sb.uc_gui_click_captcha()
                sb.sleep(5)
            except:
                pass

        # 点击签到
        try:
            sb.click("#points-signin-submit")
            sb.sleep(5)
        except:
            pass

        # 检查结果
        final_source = sb.get_page_source()
        if "已签到" in final_source:
            return "✅ 签到成功! (UC Mode)"
        elif "签到成功" in final_source:
            return "✅ 签到成功! (UC Mode)"
    except Exception as e:
        logger.warning(f"UC Mode 签到失败: {e}")

    return "⚠️ 签到状态不确定, 请手动检查"


# ─── 登录 ─────────────────────────────────────────────────────

def login_and_save_cookies(sb: "SB") -> bool:
    """使用 UC Mode 登录并返回是否成功"""
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("未配置 VPS8_EMAIL / VPS8_PASSWORD, 跳过自动登录")
        return False

    logger.info(f"UC Mode 登录中... 邮箱: {VPS8_EMAIL}")
    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=3)
    sb.sleep(3)

    # 输入邮箱密码
    try:
        sb.type("#email", VPS8_EMAIL)
        sb.type("#password", VPS8_PASSWORD)
        logger.info("已输入邮箱和密码")
    except Exception as e:
        logger.error(f"输入表单失败: {e}")
        return False

    # 处理 reCAPTCHA - UC Mode 自动过
    try:
        source = sb.get_page_source().lower()
        if "recaptcha" in source:
            logger.info("检测到 reCAPTCHA, 尝试 UC 模式自动过验证码...")
            sb.uc_gui_click_captcha()
            sb.sleep(8)
            logger.info("UC 验证码处理完成")
    except Exception as e:
        logger.warning(f"UC 验证码处理失败: {e}")

    # 点击登录
    try:
        sb.click('button[type="submit"]')
        sb.sleep(5)
        logger.info("已点击登录")
    except Exception as e:
        logger.error(f"点击登录按钮失败: {e}")
        return False

    # 检查登录结果
    current_url = sb.get_current_url()
    logger.info(f"登录后 URL: {current_url}")

    if "/login" in current_url:
        logger.error("登录后仍然在登录页面, 登录失败")
        # 尝试检查是否有错误消息
        try:
            alert = sb.find_element(".alert-danger, .alert-error, .text-danger", timeout=3)
            logger.error(f"登录错误: {alert.text}")
        except:
            pass
        return False

    logger.info("✅ 登录成功")

    # 保存 cookies
    cookies = sb.get_cookies()
    output_cookies = OUTPUT_DIR / "cookies.json"
    with open(output_cookies, "w", encoding="utf-8") as f:
        json.dump({"url": BASE_URL, "cookies": cookies, "time": datetime.now().isoformat()}, f, indent=2, ensure_ascii=False)
    logger.info(f"Cookie 已保存: {output_cookies}")

    return True


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
        # 设置 UA
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        sb.driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua})

        # 第 1 步: 尝试直接访问签到页 (判断 Cookie 是否有效)
        try:
            sb.open(SIGNIN_URL)
            sb.sleep(3)
            source = sb.get_page_source()

            if "Login to your account" in source:
                logger.info("Cookie 无效, 执行登录...")
                if not login_and_save_cookies(sb):
                    result_msg = "❌ 登录失败 (请检查邮箱密码或验证码)"
                    success = False
                else:
                    # 登录后再次访问签到页
                    result_msg = checkin_with_captcha(sb)
                    success = "✅" in result_msg and "❌" not in result_msg
            else:
                logger.info("Cookie 有效, 直接签到")
                result_msg = checkin_with_captcha(sb)
                success = "✅" in result_msg

        except Exception as e:
            logger.error(f"签到失败: {e}")
            result_msg = f"❌ 签到异常: {e}"

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

    # 退出码
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
