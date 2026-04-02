#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到
───────────────────
技术路线: SeleniumBase + 手动注入 reCAPTCHA token
───────────────────
需要 GitHub Secrets:
  VPS8_EMAIL / VPS8_PASSWORD    — 登录凭证
  RECAPTCHA_KEY                 — reCAPTCHA API Key (2Captcha 或 Capsolver)
  CAPTCHA_SERVICE               — 2captcha 或 capsolver (默认 2captcha)
  AI_API_KEY / AI_BASE_URL / AI_MODEL_NAME — AI 识图 (备用，可选)
  TELEGRAM_BOT_TOKEN / MY_CHAT_ID  — 通知 (可选)
"""

import os, sys, time, json, re, base64
from datetime import datetime
from pathlib import Path
import requests
from seleniumbase import SB
from loguru import logger

# ─── 配置 ─────────────────────────────────────────────────────
BASE_URL       = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL      = f"{BASE_URL}/login"
SIGNIN_URL     = f"{BASE_URL}/points/signin"
API_SIGNIN     = f"{BASE_URL}/api/client/points/signin"
RECAPTCHA_SITEKEY = "6LemX2YsAAAAAHtenbdCpRE_3qj83yzhTM4-Jvit"  # 从网站提取的

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MY_CHAT_ID         = os.environ.get("MY_CHAT_ID", "")
AI_API_KEY         = os.environ.get("AI_API_KEY", "")
AI_BASE_URL        = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL_NAME      = os.environ.get("AI_MODEL_NAME", "gpt-4o")
VPS8_EMAIL         = os.environ.get("VPS8_EMAIL", "")
VPS8_PASSWORD      = os.environ.get("VPS8_PASSWORD", "")
RECAPTCHA_KEY      = os.environ.get("RECAPTCHA_KEY", "")       # 2Captcha API Key
CAPTCHA_SERVICE    = os.environ.get("CAPTCHA_SERVICE", "2captcha")  # 2captcha or capsolver

WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
OUTPUT_DIR = WORKSPACE / "output" / "vps8"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── 日志 ─────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")


# ─── 通知 ─────────────────────────────────────────────────────
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


# ─── reCAPTCHA 解决 ───────────────────────────────────────────

def solve_recaptcha_2captcha(sitekey: str, pageurl: str, api_key: str) -> str:
    """用 2Captcha API 解 reCAPTCHA v2"""
    logger.info(f"2Captcha 提交验证码请求: {sitekey[:20]}...")

    # 创建任务
    result_url = f"https://2captcha.com/in.php?key={api_key}&method=userrecaptcha&googlekey={sitekey}&pageurl={pageurl}&json=1"
    r = requests.get(result_url, timeout=30)
    d = r.json()
    if d.get("status") != 1:
        logger.error(f"2Captcha 提交失败: {d}")
        return ""

    captcha_id = d["request"]
    logger.info(f"2Captcha 任务 ID: {captcha_id}, 等待结果...")

    # 轮询结果
    for _ in range(60):
        time.sleep(5)
        r2 = requests.get(
            f"https://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}&json=1",
            timeout=30,
        )
        d2 = r2.json()
        if d2.get("status") == 1:
            token = d2["request"]
            logger.info(f"✅ 2Captcha 返回 token (长度 {len(token)})")
            return token
        if d2.get("request") == "CAPCHA_NOT_READY":
            continue
        logger.error(f"2Captcha 错误: {d2}")
        break

    return ""


def solve_recaptcha_capsolver(sitekey: str, pageurl: str, api_key: str) -> str:
    """用 Capsolver API 解 reCAPTCHA v2"""
    import urllib.parse
    logger.info(f"Capsolver 提交验证码请求...")

    r = requests.post(
        "https://api.capsolver.com/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": pageurl,
                "websiteKey": sitekey,
            }
        },
        timeout=30,
    )
    d = r.json()
    if d.get("errorId") != 0:
        logger.error(f"Capsolver 提交失败: {d}")
        return ""

    task_id = d["taskId"]
    logger.info(f"Capsolver 任务 ID: {task_id}, 等待结果...")

    for _ in range(60):
        time.sleep(5)
        r2 = requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=30,
        )
        d2 = r2.json()
        if d2.get("status") == "ready":
            token = d2["solution"]["gRecaptchaResponse"]
            logger.info(f"✅ Capsolver 返回 token (长度 {len(token)})")
            return token
        if d2.get("status") == "processing":
            continue
        logger.error(f"Capsolver 错误: {d2}")
        break

    return ""


def solve_recaptcha(sitekey: str, pageurl: str) -> str:
    """选择验证码服务获取 reCAPTCHA token"""
    if CAPTCHA_SERVICE == "capsolver":
        return solve_recaptcha_capsolver(sitekey, pageurl, RECAPTCHA_KEY)
    else:
        return solve_recaptcha_2captcha(sitekey, pageurl, RECAPTCHA_KEY)


def inject_recaptcha_token(sb: "SB", token: str, callback: str | None = None) -> bool:
    """
    把 reCAPTCHA token 注入页面，让页面认为验证码已通过。
    
    原理: Google reCAPTCHA 提交后会把 token 放进
    textarea#g-recaptcha-response，然后回调 callback。
    """
    # 1. 找到 textarea 并填入值
    sb.execute_script(
        f"""
        var el = document.getElementById('g-recaptcha-response');
        if (el) {{
            el.value = '{token}';
            el.textContent = '{token}';
        }}
        """
    )
    logger.info("已注入 g-recaptcha-response token")

    # 2. 调用 Google 回调（让页面知道验证通过了）
    if callback:
        sb.execute_script(callback)
        logger.info(f"已调用回调: {callback[:100]}...")

    # 3. 手动设置 ___grecaptcha 状态 (如果有)
    sb.execute_script(
        """
        if (window.___grecaptcha) {
            var widgetIds = document.querySelectorAll('.g-recaptcha');
            for (var i = 0; i < widgetIds.length; i++) {
                if (widgetIds[i].getAttribute('data-callback')) {
                    widgetIds[i].setAttribute('data-grecaptcha', 'done');
                }
            }
        }
        """
    )

    return True


# ─── AI 识图 (备用) ───────────────────────────────────────────────
def ai_solve_captcha(image_base64: str) -> str:
    """AI 截图识图 (备用方案)"""
    if not AI_API_KEY:
        return ""
    try:
        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [{"role": "user", "content": [
                    {"type": "text",
                     "text": "这是 reCAPTCHA 截图，请识别验证码值并只返回该值。"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                ]}],
                "max_tokens": 50, "temperature": 0.1
            },
            timeout=30,
        )
        return r.json().get("choices", [{}])[0] \
                       .get("message", {}).get("content", "").strip()
    except Exception:
        return ""


# ─── 尝试绕过验证码：直接注入 token ──────────────────────────────
def try_bypass_captcha(sb: "SB") -> bool:
    """
    尝试绕过当前页面的 reCAPTCHA。
    优先用第三方 API（2Captcha/Capsolver），备用用 AI 识别。
    """
    page_url = sb.get_current_url()
    src = sb.get_page_source().lower()

    if "recaptcha" not in src:
        logger.info("没有检测到 reCAPTCHA，跳过")
        return True

    logger.info("发现 reCAPTCHA，尝试解验证码...")

    # 第1方案: 第三方 API
    if RECAPTCHA_KEY:
        token = solve_recaptcha(RECAPTCHA_SITEKEY, page_url)
        if token:
            # 找 Google callback
            callback = sb.execute_script(
                """
                var el = document.querySelector('.g-recaptcha');
                return el ? el.getAttribute('data-callback') : '';
                """
            )
            inject_recaptcha_token(sb, token, callback or None)
            sb.sleep(2)
            logger.info("✅ 验证码已注入！")
            return True
        logger.warning("第三方验证码服务返回空，尝试 AI 备用方案...")

    # 第2方案: AI 识图
    if AI_API_KEY:
        logger.info("AI 识图备用方案...")
        try:
            screenshot = sb.get_screenshot_as_base64()
            answer = ai_solve_captcha(screenshot)
            if answer:
                inject_recaptcha_token(sb, answer)
                sb.sleep(2)
                logger.info(f"✅ AI 识图填入: {answer[:50]}...")
                return True
        except Exception as e:
            logger.warning(f"AI 识图失败: {e}")
    else:
        logger.warning("AI_API_KEY 未配置")

    logger.error("所有验证码方案均失败")
    return False


# ─── 登录 ───────────────────────────────────────────────────────
def do_login(sb: "SB") -> bool:
    """FOSSBilling 登录"""
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("未配置邮箱/密码")
        return False

    for attempt in range(1, 4):
        logger.info(f"登录尝试 [{attempt}/3]...")
        sb.open(LOGIN_URL)
        sb.sleep(4)

        # 检查已登录
        if "/login" not in sb.get_current_url():
            logger.info("已处于登录状态")
            return True

        # 填表单
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            logger.info("已填邮箱密码")
        except Exception as e:
            logger.error(f"填表单失败: {e}")
            return False

        # 找 Google callback (在注入前记录)
        callback_name = ""
        try:
            callback_name = sb.execute_script(
                """
                var el = document.querySelector('.g-recaptcha');
                return el ? (el.getAttribute('data-callback') || '') : '';
                """
            )
            logger.info(f"reCAPTCHA callback: {callback_name or '未找到'}")
        except Exception as e:
            logger.warning(f"找 callback 失败: {e}，使用 'captcha-callback'")
            callback_name = ""

        # 解验证码
        success = try_bypass_captcha(sb)
        if not success:
            logger.error("验证码解失败")
            return False

        # 如果有 Google callback，调用它
        if callback_name:
            try:
                sb.execute_script(
                    f"""
                    var cb = window.{callback_name};
                    if (typeof cb === 'function') {{
                        var el = document.getElementById('g-recaptcha-response');
                        cb(el.value);
                    }}
                    """
                )
                logger.info(f"已调用 Google callback: {callback_name}")
            except Exception as e:
                logger.warning(f"调用 callback 失败: {e}")

        sb.sleep(2)

        # 点击登录
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

        logger.warning(f"登录失败 ({attempt}/3)，仍在登录页")
        # 检查错误
        try:
            err = sb.find_element(".alert-danger, .alert-error, .invalid-feedback, .text-danger", timeout=2)
            logger.warning(f"错误: {err.text}")
        except:
            logger.warning("未找到错误信息")
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

    if "Login to your account" in src:
        return "❌ Cookie 失效，被踢到登录页"
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
        return "❌ 无法提取 CSRF Token"
    csrf_token = csrf_match.group(1)
    logger.info(f"CSRF Token: {csrf_token[:10]}...")

    # 直接 POST API
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())
    logger.info("尝试 POST API 签到...")
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
                if "签到成功" in resp.text:
                    return "✅ 签到成功!"
                return f"⚠️ 响应不确定: {resp.text[:200]}"
    except Exception as e:
        logger.warning(f"API 签到尝试: {e}")

    # 页面按钮
    try:
        logger.info("尝试页面按钮签到...")
        if "recaptcha" in sb.get_page_source().lower():
            try_bypass_captcha(sb)
        sb.click("#points-signin-submit")
        sb.sleep(6)
        if "已签到" in sb.get_page_source():
            return "✅ 签到成功! (页面提交)"
    except Exception as e:
        logger.warning(f"页面签到: {e}")

    return "⚠️ 签到状态不确定"


# ─── 主函数 ─────────────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info("VPS8 每日签到")
    logger.info(f"目标: {BASE_URL}")
    logger.info(f"验证码服务: {CAPTCHA_SERVICE} (key:{'已配置' if RECAPTCHA_KEY else '未配置'})")
    logger.info(f"AI 识图: {'已配置' if AI_API_KEY else '未配置'}")
    logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    result_msg = ""
    success    = False

    with SB(
        uc=True,
        locale="en",
        log_cdp=False,
        chromium_arg=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
        ],
    ) as sb:
        sb.open(SIGNIN_URL)
        sb.sleep(3)
        src = sb.get_page_source()

        if "Login to your account" in src:
            logger.info("未登录，先登录...")
            if do_login(sb):
                result_msg = do_signin(sb)
                success = "✅" in result_msg
            else:
                result_msg = "❌ 登录失败"
        else:
            logger.info("Cookie 有效，直接签到")
            result_msg = do_signin(sb)
            success = "✅" in result_msg

    # 通知
    icon = "✅" if success else "❌"
    msg = (
        f"🦞 <b>VPS8 签到结果</b>\n"
        f"📅 日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"验证码服务: {CAPTCHA_SERVICE} ({'已配置' if RECAPTCHA_KEY else '未配置'})\n"
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
