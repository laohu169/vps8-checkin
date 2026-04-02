#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到
───────────────────
技术路线: SeleniumBase UC Mode 浏览器自动过 reCAPTCHA
───────────────────
需要 GitHub Secrets:
  VPS8_EMAIL / VPS8_PASSWORD — vps8 登录凭证
  AI_API_KEY / AI_BASE_URL / AI_MODEL_NAME — AI 识图 (备用)
  TELEGRAM_BOT_TOKEN / MY_CHAT_ID — 通知 (可选)
"""

import os, sys, time, json, re
from datetime import datetime
from pathlib import Path

import requests
from seleniumbase import SB
from loguru import logger

# ─── 配置 ─────────────────────────────────────────────────────
BASE_URL   = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL  = f"{BASE_URL}/login"
SIGNIN_URL = f"{BASE_URL}/points/signin"
API_SIGNIN = f"{BASE_URL}/api/client/points/signin"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MY_CHAT_ID         = os.environ.get("MY_CHAT_ID", "")
AI_API_KEY         = os.environ.get("AI_API_KEY", "")
AI_BASE_URL        = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL_NAME      = os.environ.get("AI_MODEL_NAME", "gpt-4o")
VPS8_EMAIL         = os.environ.get("VPS8_EMAIL", "")
VPS8_PASSWORD      = os.environ.get("VPS8_PASSWORD", "")

WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
OUTPUT_DIR = WORKSPACE / "output" / "vps8"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── 日志 ──────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")


# ─── 通知 ───────────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not MY_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": MY_CHAT_ID, "text": text,
                  "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram 通知失败: {e}")


def ai_solve_captcha(image_base64: str) -> str:
    if not AI_API_KEY:
        return ""
    try:
        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {"role": "user", "content": [
                        {"type": "text",
                         "text": "这是 reCAPTCHA 截图，请识别验证码并只返回值。"},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ]}],
                "max_tokens": 50, "temperature": 0.1
            },
            timeout=30,
        )
        return r.json().get("choices", [{}])[0] \
                       .get("message", {}).get("content", "").strip()
    except Exception as e:
        logger.warning(f"AI 识图失败: {e}")
        return ""


# ─── 登录 ───────────────────────────────────────────────────────
def do_login(sb: "SB") -> bool:
    """FOSSBilling 登录，UC Mode 自动过 reCAPTCHA"""
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("未配置邮箱/密码")
        return False

    for attempt in range(1, 4):
        logger.info(f"登录尝试 [{attempt}/3]...")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=3)
        sb.sleep(4)

        # 检查已登录
        if "/login" not in sb.get_current_url():
            logger.info("已处于登录状态")
            return True

        # 填表单
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
        except Exception as e:
            logger.error(f"填表单失败: {e}")
            return False

        # 过 reCAPTCHA (UC Mode)
        try:
            src = sb.get_page_source().lower()
            if "recaptcha" in src:
                logger.info("检测到 reCAPTCHA，UC 过验证码...")
                sb.uc_gui_click_captcha()
                sb.sleep(5)  # 等 token 生成
                # 验证 response 有值
                rc_resp = sb.execute_script(
                    "var e=document.getElementById('g-recaptcha-response');"
                    "return e?e.value:''")
                if rc_resp and len(rc_resp) > 100:
                    logger.info("✅ reCAPTCHA response 就绪")
                else:
                    logger.warning(f"⚠️ reCAPTCHA 可能无效: {len(rc_resp)} chars")
        except Exception as e:
            logger.warning(f"UC 验证码处理: {e}")

        # 提交
        try:
            sb.click('button[type="submit"]')
            sb.sleep(8)
            logger.info("已点击登录按钮")
        except Exception as e:
            logger.error(f"点击登录失败: {e}")
            return False

        # 检查结果
        cur = sb.get_current_url()
        logger.info(f"当前 URL: {cur}")
        if "/login" not in cur:
            logger.info("✅ 登录成功")
            cookies = sb.get_cookies()
            with open(OUTPUT_DIR / "cookies.json", "w") as f:
                json.dump({"cookies": cookies, "time": datetime.now().isoformat()}, f, indent=2)
            return True

        logger.warning(f"登录失败 ({attempt}/3)")
        if attempt < 3:
            time.sleep(3)

    logger.error("3 次登录均失败")
    return False


# ─── 签到 ───────────────────────────────────────────────────────
def do_signin(sb: "SB") -> str:
    """在已登录状态下完成签到"""
    logger.info("打开签到页...")
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()

    # 检查登录状态
    if "Login to your account" in src:
        return "❌ Cookie 失效，被踢到登录页"

    # 检查已签到
    if "已签到" in src:
        logger.info("今天已签到过了 ✅")
        return "✅ 今天已签到过了"

    # 提取 CSRF Token
    csrf_match = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not csrf_match:
        meta_match = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
        if meta_match:
            csrf_match = meta_match
    if not csrf_match:
        logger.error("无法提取 CSRF Token")
        return "❌ 无法提取 CSRF Token"
    csrf_token = csrf_match.group(1)
    logger.info(f"CSRF Token: {csrf_token[:10]}...")

    # 尝试 POST API 签到
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())
    logger.info("尝试直接 POST 签到 API...")
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
            timeout=15,
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
            except Exception:
                if "签到成功" in resp.text or "已签到" in resp.text:
                    return "✅ 签到成功!"
                return f"⚠️ 响应不确定: {resp.text[:200]}"
    except Exception as e:
        logger.warning(f"API 签到尝试: {e}")

    # 页面签到按钮
    try:
        logger.info("尝试页面按钮签到...")
        if "recaptcha" in sb.get_page_source().lower():
            try:
                sb.uc_gui_click_captcha()
                sb.sleep(3)
            except Exception:
                pass
        sb.click("#points-signin-submit")
        sb.sleep(6)
        if "已签到" in sb.get_page_source():
            return "✅ 签到成功! (页面提交)"
    except Exception as e:
        logger.warning(f"页面签到: {e}")

    return "⚠️ 签到状态不确定，请手动检查"


# ─── 主函数 ─────────────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info("🦞 VPS8 每日签到开始")
    logger.info(f"🌐 目标: {BASE_URL}")
    logger.info(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    result_msg = ""
    success    = False

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

        # 检查登录状态
        sb.open(SIGNIN_URL)
        sb.sleep(3)
        src = sb.get_page_source()

        if "Login to your account" in src:
            logger.info("未登录，执行登录...")
            if do_login(sb):
                result_msg = do_signin(sb)
                success = "✅" in result_msg
            else:
                result_msg = "❌ 登录失败"

        else:
            logger.info("Cookie 有效，直接签到")
            result_msg = do_signin(sb)
            success = "✅" in result_msg

    # ─── 通知 & 输出 ─────────────────────────────────────────
    icon = "✅" if success else "❌"
    msg = (
        f"🦞 <b>VPS8 签到结果</b>\n"
        f"📅 日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{icon} {result_msg}\n"
        f"🌐 {SIGNIN_URL}"
    )
    send_telegram(msg)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"success={'true' if success else 'false'}\n")
            f.write(f"result={result_msg}\n")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
