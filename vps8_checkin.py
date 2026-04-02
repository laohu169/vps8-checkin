#!/usr/bin/env python3
"""
VPS8 每日签到（日志直发 TG）
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
from loguru import logger

BASE_URL    = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL   = f"{BASE_URL}/login"
SIGNIN_URL  = f"{BASE_URL}/points/signin"

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

# ── 日志同时写文件 ───────────────────────────────────────────
LOG_FILE = OUT / "run.log"
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")
logger.add(LOG_FILE, level="INFO", encoding="utf-8")

# ── TG 大消息发送（支持分片） ────────────────────────────────
def tg(text):
    if not TG_TOKEN or not MY_CHAT_ID:
        return
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": MY_CHAT_ID, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15)
            time.sleep(0.5)
        except:
            pass

def tg_log():
    """运行结束时把日志文件发到 TG"""
    try:
        if LOG_FILE.exists():
            text = LOG_FILE.read_text(encoding="utf-8")
            tg("📜 完整日志:\n" + text[-4000:])
    except:
        pass

def tg_image(path):
    """发送截图到 TG"""
    if not TG_TOKEN or not MY_CHAT_ID or not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                data={"chat_id": MY_CHAT_ID, "caption": os.path.basename(path)},
                files={"photo": f},
                timeout=15)
    except Exception as e:
        logger.warning(f"TG 图: {e}")


# ── AI 识别 ──────────────────────────────────────────────────
def solve_grid(img_b64, question, rows, cols):
    if not AI_API_KEY:
        return []
    max_tile = rows * cols
    num_parts = []
    for r in range(rows):
        nums = ", ".join(str(r * cols + c + 1) for c in range(cols))
        num_parts.append(f"Row{r+1}: [{nums}]")
    num_text = "\n".join(num_parts)

    prompt = (
        f"Grid of {rows}x{cols} pictures.\n"
        f"Find ALL pictures containing: \"{question}\"\n\n"
        f"Cell numbering:\n{num_text}\n\n"
        f"Reply with ONLY matching cell numbers comma separated.\n"
        f"Example: 1, 4, 7\n"
        f"If NO matches, reply: -1\n"
        f"Nothing else."
    )

    try:
        img = Image.open(BytesIO(base64.b64decode(img_b64)))
        mx = 1024
        if max(img.size) > mx:
            ratio = mx / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        sb64 = base64.b64encode(buf.getvalue()).decode()

        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {"role": "system",
                     "content": "Image recognition expert. Reply with only cell numbers matching the description."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{sb64}", "detail": "high"}}]}],
                "max_tokens": 50, "temperature": 0.1,
            },
            timeout=60,
        )
        ans = r.json()["choices"][0]["message"]["content"].strip()
        logger.info(f"AI: {ans}")
        if "-1" in ans:
            return []
        nums = [int(n) for n in re.findall(r'\d+', ans)]
        return [n for n in nums if 1 <= n <= max_tile]
    except Exception as e:
        logger.error(f"AI: {e}")
        return []


# ── 提取问题文本 ─────────────────────────────────────────────
def extract_question(d):
    """从 .rc-imageselect-instructions 提取完整问题"""
    try:
        el = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-instructions")
        # 直接用 .text 拿所有子元素文本
        full_text = el.text.strip()
        if full_text:
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            return " ".join(lines)[:100]
    except:
        pass
    return ""


# ── 图片验证 ─────────────────────────────────────────────────
def break_image_challenge(sb):
    d = sb.driver
    for rnd in range(1, 25):
        tiles = d.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        total = len(tiles)
        if total == 16:
            rows, cols = 4, 4
        elif total == 12:
            rows, cols = 4, 3
        else:
            rows, cols = 3, 3

        question = extract_question(d)
        if not question:
            question = "identify object"

        logger.info(f" Round {rnd} | {rows}x{cols} ({total}) | Q: {question}")

        grid_b64 = ""
        try:
            grid_b64 = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
        except:
            try:
                grid_b64 = sb.get_screenshot_as_base64()
            except:
                break

        save_path = str(OUT / f"r{rnd}.png")
        try:
            with open(save_path, "wb") as f:
                f.write(base64.b64decode(grid_b64))
        except:
            pass

        nums = solve_grid(grid_b64, question, rows, cols)
        if nums:
            logger.info(f"  Click: {nums}")
            for n in nums:
                i = n - 1
                if 0 <= i < len(tiles):
                    tiles[i].click()
                    sb.sleep(0.2)
        else:
            logger.info("  AI returned no numbers, clicking Verify anyway")

        sb.sleep(1)
        try:
            d.find_element(By.ID, "recaptcha-verify-button").click()
            logger.info("  Verified clicked")
        except Exception as e:
            logger.warning(f"  Verify fail: {e}")
            return False

        sb.sleep(4)

        token = d.execute_script(
            "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(token) > 50:
            logger.info(f"  PASSED! token {token[:30]}")
            return True

        try:
            d.find_element(By.ID, "rc-imageselect")
        except:
            token2 = d.execute_script(
                "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
            if len(token2) > 50:
                logger.info("  PASSED (popup gone)")
                return True
            logger.info("  Popup gone, no token, breaking")
            break

    return False


def solve_full(sb):
    d = sb.driver
    d.switch_to.default_content()

    logger.info("Looking for checkbox iframe...")
    try:
        sb.wait_for_element_present("iframe[title*='recaptcha']", timeout=15)
    except:
        logger.warning("No checkbox iframe found")
        return False

    time.sleep(1)
    frames = d.find_elements(By.TAG_NAME, "iframe")
    cb = None
    for f in frames:
        t = (f.get_attribute("title") or "").lower()
        if "recaptcha" in t:
            cb = f
            break
    if not cb:
        logger.warning("No checkbox frame found")
        return False

    try:
        d.switch_to.frame(cb)
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        logger.info("Checkbox clicked")
    except Exception as e:
        d.switch_to.default_content()
        logger.warning(f"Checkbox click error: {e}")
        return False

    sb.sleep(3)
    t = d.execute_script(
        "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
    if len(t) > 50:
        logger.info("Auto-passed!")
        return True

    logger.info("Waiting for challenge iframe...")
    try:
        sb.wait_for_element_present("iframe[src*='bframe']", timeout=20)
    except:
        t2 = d.execute_script(
            "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        return len(t2) > 50

    cf = None
    for f in d.find_elements(By.TAG_NAME, "iframe"):
        src = f.get_attribute("src") or ""
        if "bframe" in src:
            try:
                d.switch_to.frame(f)
                d.find_element(By.ID, "rc-imageselect")
                cf = f
                logger.info("Entered challenge iframe")
                break
            except:
                d.switch_to.default_content()

    if not cf:
        logger.warning("No challenge frame found")
        return False

    ok = break_image_challenge(sb)
    d.switch_to.default_content()
    return ok


# ── 登录 ─────────────────────────────────────────────────────
def do_login(sb):
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("No email/password configured")
        return False

    for att in range(1, 4):
        logger.info(f"=== Login attempt {att}/3 ===")
        sb.open(LOGIN_URL)
        sb.sleep(4)

        cur = sb.get_current_url()
        logger.info(f"Current URL: {cur}")
        if "/login" not in cur:
            p = str(OUT / "logged_in.png")
            sb.save_screenshot(p)
            logger.info(f"Already logged in! Screenshot: {p}")
            return True

        sb.type("#email", VPS8_EMAIL)
        sb.type("#password", VPS8_PASSWORD)
        logger.info("Email/password typed")

        if not solve_full(sb):
            logger.warning(f"Verification FAILED ({att}/3)")
            sb.sleep(3)
            continue

        sb.click('button[type="submit"]')
        sb.sleep(10)

        cur = sb.get_current_url()
        logger.info(f"After submit URL: {cur}")

        p = str(OUT / f"submit{att}.png")
        sb.save_screenshot(p)
        tg_image(p)
        logger.info(f"Screenshot saved: {p}")

        if "/login" not in cur:
            logger.info("LOGIN SUCCESS!")
            return True
        logger.warning(f"Still on login page ({att}/3)")

    logger.error("All 3 login attempts failed")
    return False


# ── 签到 ─────────────────────────────────────────────────────
def do_signin(sb):
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()
    if "Login to your account" in src:
        return "Cookie invalid"
    if "已签到" in src:
        return "Already signed in"

    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "No CSRF token"
    csrf = m.group(1)
    ck = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())

    try:
        r = requests.post(
            f"{BASE_URL}/api/client/points/signin",
            params={"CSRFToken": csrf},
            headers={"Cookie": ck, "Referer": SIGNIN_URL,
                     "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=15)
        logger.info(f"API {r.status_code}: {r.text[:300]}")
        j = r.json()
        if j.get("error"):
            msg = j["error"]["message"]
            if "已签到" in msg or "already" in msg.lower():
                return "Already signed in"
            return f"API error: {msg}"
        if "result" in j and j["result"]:
            return "OK: " + json.dumps(j["result"], ensure_ascii=False)[:200]
        return f"Unknown: {r.text[:200]}"
    except Exception as e:
        logger.warning(f"API error: {e}")
        return f"API exception: {e}"


# ── Main ─────────────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info("VPS8 DAILY SIGNIN")
    logger.info(f"AI: {AI_MODEL_NAME}")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 30)

    result = ""
    ok = False

    try:
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
            src = sb.get_page_source()
            if "Login to your account" in src:
                logger.info("Not logged in, logging in first...")
                if do_login(sb):
                    result = do_signin(sb)
                    ok = "OK" in result or "Already" in result
                else:
                    result = "Login failed"
            else:
                logger.info("Cookie valid, signing in directly")
                result = do_signin(sb)
                ok = "OK" in result or "Already" in result
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        result = f"Fatal: {e}"
        import traceback
        tb = traceback.format_exc()
        logger.error(tb)

    # ── 结束 ──────────────────────────────────────────────────
    icon = "✅" if ok else "❌"
    summary = f"{icon} VPS8 Signin\nResult: {result}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M')}\nLog: see TG messages"
    tg(summary)

    # 发完整日志
    time.sleep(1)
    tg_log()

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"success={'true' if ok else 'false'}\n")
            f.write(f"result={result}\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
