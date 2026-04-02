#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到 — AI 视觉识别 reCAPTCHA 免费版
标准浏览器 + AI 识别图片网格，纯免费方案
"""
import os, sys, time, json, re
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
import base64
from PIL import Image
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from loguru import logger

BASE_URL   = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL  = f"{BASE_URL}/login"
SIGNIN_URL = f"{BASE_URL}/points/signin"

AI_API_KEY    = os.environ.get("AI_API_KEY", "")
AI_BASE_URL   = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL_NAME = os.environ.get("AI_MODEL_NAME", "gpt-4o")
VPS8_EMAIL    = os.environ.get("VPS8_EMAIL", "")
VPS8_PASSWORD = os.environ.get("VPS8_PASSWORD", "")
MY_CHAT_ID    = os.environ.get("MY_CHAT_ID", "")
TG_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")

WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
OUT = WORKSPACE / "output" / "vps8"
OUT.mkdir(parents=True, exist_ok=True)

logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | {message}")


# ─── 通知 ─────────────────────────────────────────────────────
def tg(text: str):
    if not TG_TOKEN or not MY_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": MY_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        logger.warning(f"TG: {e}")


# ─── AI 识别 ──────────────────────────────────────────────────
def ai_solve(img_b64: str, question: str, cols: int) -> list:
    if not AI_API_KEY:
        return []
    max_tile = 9 if cols == 3 else 12
    rows = max_tile // cols
    num_text = ", ".join(
        f"第{r+1}行: {', '.join(str(r*cols+c+1) for c in range(cols))}"
        for r in range(rows)
    )
    prompt = (
        f"ReCAPTCHA grid puzzle. {rows}x{cols}.\n"
        f"Question: {question}\n"
        f"Cell numbers: {num_text}\n"
        f"Return ONLY matching cell numbers, comma separated. Nothing else."
    )
    try:
        img = Image.open(BytesIO(base64.b64decode(img_b64)))
        mx = 1024
        if max(img.size) > mx:
            ratio = mx / max(img.size)
            img = img.resize((int(img.width*ratio), int(img.height*ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        b = base64.b64encode(buf.getvalue()).decode()
        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={"model": AI_MODEL_NAME, "messages": [
                {"role": "system", "content": "CAPTCHA solver, return cell numbers only."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}]}],
                "max_tokens": 50, "temperature": 0.1},
            timeout=60)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        logger.info(f"AI: {ans}")
        nums = [int(n) for n in re.findall(r'\d+', ans)]
        return [n for n in nums if 1 <= n <= max_tile]
    except Exception as e:
        logger.error(f"AI: {e}")
        return []


# ─── reCAPTCHA 求解 ────────────────────────────────────────────
def captcha(sb):
    """
    1. 等 checkbox iframe, 点它
    2. 等 challenge iframe
    3. 截图 → AI → 点格 → Verify → 检查 token → 循环
    """
    d = sb.driver
    sb.sleep(2)

    # point 1: checkbox iframe
    logger.info("找 checkbox...")
    try:
        sb.wait_for_element_present("iframe[title*='recaptcha']", timeout=20)
    except Exception:
        logger.warning("未找到 checkbox iframe")
        return False

    frames = d.find_elements(By.TAG_NAME, "iframe")
    cb = None
    for f in frames:
        if "recaptcha" in (f.get_attribute("title") or "").lower():
            cb = f
            break
    if not cb:
        logger.warning("未找到 checkbox frame")
        return False

    try:
        d.switch_to.frame(cb)
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        logger.info("已点 checkbox")
    except Exception as e:
        d.switch_to.default_content()
        logger.warning(f"checkbox 失败: {e}")
        return False

    sb.sleep(4)

    # check token already
    t = d.execute_script(
        "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
    if len(t) > 50:
        logger.info("直接通过!")
        return True

    # point 2: wait for challenge iframe
    logger.info("等 challenge...")
    try:
        sb.wait_for_element_present("iframe[src*='bframe']", timeout=15)
    except Exception:
        t2 = d.execute_script(
            "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        return len(t2) > 50

    # point 3: picture rounds
    for rnd in range(1, 12):
        logger.info(f"--- {rnd} ---")

        # find bframe (challenge)
        bf = None
        for f in d.find_elements(By.TAG_NAME, "iframe"):
            s = f.get_attribute("src") or ""
            if "bframe" in s:
                try:
                    d.switch_to.frame(f)
                    d.find_element(By.ID, "rc-imageselect")
                    bf = f
                    break
                except:
                    d.switch_to.default_content()
        if not bf:
            logger.info("弹窗消失")
            break

        # question
        q = "识别图片"
        try:
            q = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-instructions").text.strip()
        except:
            pass
        logger.info(f"Q: {q[:60]}")

        # screenshot grid
        gb = ""
        try:
            gb = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
        except:
            try:
                gb = sb.get_screenshot_as_base64()
            except:
                pass
        if not gb:
            d.switch_to.default_content()
            break

        cols = 3
        tiles = d.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        if len(tiles) == 12:
            cols = 4
        logger.info(f"tiles: {len(tiles)}, cols: {cols}")

        nums = ai_solve(gb, q, cols)
        if nums:
            for n in nums:
                i = n - 1
                if 0 <= i < len(tiles):
                    tiles[i].click()
                    sb.sleep(0.3)

        sb.sleep(1)
        try:
            d.find_element(By.ID, "recaptcha-verify-button").click()
            logger.info("Verify clicked")
        except:
            pass
        d.switch_to.default_content()
        sb.sleep(5)

        t3 = d.execute_script(
            "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(t3) > 50:
            logger.info(f"过! {t3[:25]}...")
            return True

    d.switch_to.default_content()
    t4 = d.execute_script(
        "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
    return len(t4) > 50


# ─── 登录 ─────────────────────────────────────────────────────
def do_login(sb) -> bool:
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("无邮箱/密码")
        return False

    for att in range(1, 4):
        logger.info(f"Login {att}/3...")
        sb.open(LOGIN_URL)
        sb.sleep(4)
        u = sb.get_current_url()
        if "/login" not in u:
            return True

        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            logger.info("表单已填")
        except Exception as e:
            logger.error(f"填表单: {e}")
            return False

        if not captcha(sb):
            logger.warning(f"验证码失败 ({att}/3)")
            sb.sleep(3)
            continue

        sb.click('button[type="submit"]')
        sb.sleep(10)
        logger.info(f"URL: {sb.get_current_url()}")
        if "/login" not in sb.get_current_url():
            logger.info("✅ 登录成功!")
            return True

    logger.error("3 次登录均失败")
    return False


# ─── 签到 ─────────────────────────────────────────────────────
def do_signin(sb) -> str:
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()
    if "Login to your account" in src:
        return "Cookie 失效"
    if "已签到" in src:
        return "已签到过"

    ck = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "无 CSRF Token"
    csrf = m.group(1)
    try:
        r = requests.post(
            f"{BASE_URL}/api/client/points/signin",
            params={"CSRFToken": csrf},
            headers={"Cookie": ck, "Referer": SIGNIN_URL,
                     "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=15)
        j = r.json()
        if j.get("error"):
            msg = j["error"]["message"]
            if "已签到" in msg or "already" in msg.lower():
                return "已签到"
            return f"API: {msg}"
        if "result" in j and j["result"]:
            return f"签到成功! {json.dumps(j['result'])[:200]}"
        return f"API: {r.text[:200]}"
    except Exception as e:
        logger.warning(f"API: {e}")
    return "不确定"


# ─── Main ─────────────────────────────────────────────────────
def main():
    logger.info("="*50)
    logger.info(f"VPS8 签到 | {datetime.now().strftime('%H:%M:%S')}")
    logger.info(f"AI: {AI_MODEL_NAME}")
    logger.info("="*30)

    result = ""
    ok = False

    with SB(
        headed=False,
        locale="en",
        chromium_arg=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
        ],
    ) as sb:
        sb.open(SIGNIN_URL)
        sb.sleep(3)
        if "Login to your account" in sb.get_page_source():
            logger.info("未登录，先登录...")
            if do_login(sb):
                result = do_signin(sb)
                ok = "成功" in result or "已签" in result
            else:
                result = "登录失败"
        else:
            result = do_signin(sb)
            ok = "成功" in result or "已签" in result

    icon = "✅" if ok else "❌"
    tg(f"VPS8 签到\n{icon} {result}\n{datetime.now().strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()
