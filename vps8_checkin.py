#!/usr/bin/env python3
"""VPS8 auto-signin — AI image grid solver"""
import os, sys, time, json, re, io, traceback
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
import base64
from PIL import Image
from seleniumbase import SB
from selenium.webdriver.common.by import By

BASE_URL    = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL   = BASE_URL + "/login"
SIGNIN_URL  = BASE_URL + "/points/signin"
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

log_buf = []

def L(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = ts + " | " + str(msg)
    log_buf.append(line)
    print(line, flush=True, file=sys.stderr)


def get_text():
    return "\n".join(log_buf)


def tg(text):
    if not TG_TOKEN or not MY_CHAT_ID:
        L("TG skip (no config)")
        return
    text = str(text)
    for i in range(0, len(text), 4000):
        try:
            requests.post(
                "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                json={"chat_id": MY_CHAT_ID, "text": text[i:i+4000], "parse_mode": "HTML"},
                timeout=30)
            time.sleep(1)
        except Exception as e:
            L("TG err: " + str(e))


def tg_img(path):
    if not TG_TOKEN or not MY_CHAT_ID or not os.path.isfile(path):
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                "https://api.telegram.org/bot" + TG_TOKEN + "/sendPhoto",
                data={"chat_id": MY_CHAT_ID, "caption": Path(path).name},
                files={"photo": f}, timeout=30)
        time.sleep(1)
        L("TG img ok: " + path)
    except Exception as e:
        L("TG img err: " + str(e))


def save_b64(b64, name):
    try:
        (OUT / (name + ".png")).write_bytes(base64.b64decode(b64))
    except:
        pass


def get_question(d):
    """用 JS 从 reCAPTCHA 弹窗里提取完整问题文本"""
    js = """
    (function() {
        var inst = document.querySelector('.rc-imageselect-instructions');
        if (!inst) return '';
        // 取所有子元素文本
        var texts = [];
        var strongs = inst.querySelectorAll('strong, span');
        for (var i = 0; i < strongs.length; i++) {
            var t = strongs[i].textContent.trim();
            if (t && t.length > 1) texts.push(t);
        }
        // 如果没找到子元素，取整个元素文本
        if (texts.length === 0) {
            texts.push(inst.textContent.trim());
        }
        return texts.join(' ').substring(0, 150);
    })()
    """
    try:
        result = d.execute_script(js)
        if result and len(result) > 3:
            return result
    except:
        pass
    # fallback: direct text
    try:
        el = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-instructions")
        t = el.text.strip()
        if t:
            return t
    except:
        pass
    return ""


def ai_solve(img_b64, question, rows, cols):
    if not AI_API_KEY:
        L("NO AI_API_KEY")
        return []
    mx = rows * cols
    parts = []
    for r in range(rows):
        parts.append(", ".join(str(r * cols + c + 1) for c in range(cols)))
    numbering = "\n".join("Row" + str(r+1) + ": [" + n + "]" for r, n in enumerate(parts))
    prompt = (
        "Grid of " + str(rows) + "x" + str(cols) + " small pictures.\n"
        "The question asks you to find ALL images containing: \"" + question + "\"\n\n"
        "Numbering of cells (top-left is 1):\n" + numbering + "\n\n"
        "Reply ONLY with the cell numbers that match, separated by commas.\n"
        "Example: 1, 4, 7\n"
        "If no image matches, reply exactly: -1\n"
        "No other text."
    )
    try:
        img = Image.open(BytesIO(base64.b64decode(img_b64)))
        if max(img.size) > 1024:
            ratio = 1024.0 / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        sb64 = base64.b64encode(buf.getvalue()).decode()
        r = requests.post(
            AI_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + AI_API_KEY},
            json={"model": AI_MODEL_NAME, "messages": [
                {"role": "system", "content": "You help identify which cells in a picture grid contain a specific type of image. Return only cell numbers."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + sb64, "detail": "high"}}]}],
                "max_tokens": 50, "temperature": 0.1},
            timeout=60)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        L("AI -> " + ans)
        if "-1" in ans:
            return []
        nums = [int(n) for n in re.findall(r'\d+', ans)]
        return [n for n in nums if 1 <= n <= mx]
    except Exception as e:
        L("AI err: " + str(e))
        return []


def do_captcha(sb):
    d = sb.driver
    d.switch_to.default_content()
    L("Looking for checkbox iframe...")
    try:
        sb.wait_for_element_present("iframe[title*='recaptcha']", timeout=15)
    except:
        L("No checkbox iframe found after 15s")
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
        L("No checkbox frame in " + str(len(frames)) + " iframes")
        return False
    try:
        d.switch_to.frame(cb)
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        L("Checkbox clicked OK")
    except Exception as e:
        d.switch_to.default_content()
        L("Checkbox click err: " + str(e))
        return False
    sb.sleep(3)
    t = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
    if len(t) > 50:
        L("Passed immediately!")
        return True

    L("Waiting for challenge iframe...")
    try:
        sb.wait_for_element_present("iframe[src*='bframe']", timeout=20)
    except:
        t2 = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(t2) > 50:
            L("Passed while waiting!")
            return True
        L("No challenge iframe and no token")
        return False

    # Switch into challenge iframe
    cf = None
    for f in d.find_elements(By.TAG_NAME, "iframe"):
        src = f.get_attribute("src") or ""
        if "bframe" in src:
            try:
                d.switch_to.frame(f)
                d.find_element(By.ID, "rc-imageselect")
                cf = f
                L("Switched into challenge iframe")
                break
            except:
                d.switch_to.default_content()
    if not cf:
        L("Could not find challenge frame")
        return False

    # Main solving loop
    for rnd in range(1, 30):
        tiles = d.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        total = len(tiles)
        if total == 16:
            rows, cols = 4, 4
        elif total == 12:
            rows, cols = 4, 3
        else:
            rows, cols = 3, 3

        question = get_question(d)
        if not question:
            question = "the described object"
        L("Round " + str(rnd) + " | " + str(rows) + "x" + str(cols) + " | " + str(total) + " tiles")
        L("Question: " + question)

        # Screenshot
        gb = ""
        try:
            gb = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
        except:
            try:
                gb = d.get_screenshot_as_base64()
            except:
                break

        if gb:
            save_b64(gb, "r" + str(rnd))
            nums = ai_solve(gb, question, rows, cols)
            if nums:
                L("Clicking cells: " + str(nums))
                for n in nums:
                    i = n - 1
                    if 0 <= i < len(tiles):
                        tiles[i].click()
                        sb.sleep(0.2)
            else:
                L("AI returned no cells")

        sb.sleep(1)

        # Click verify
        try:
            d.find_element(By.ID, "recaptcha-verify-button").click()
            L("Verify clicked")
        except Exception as e:
            L("Could not click verify: " + str(e))
            return False

        sb.sleep(5)

        # Check if passed
        token = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(token) > 50:
            L("PASSED! token=" + token[:30])
            return True

        # Check if popup still exists
        try:
            d.find_element(By.ID, "rc-imageselect")
        except:
            L("Popup disappeared")
            t2 = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
            if len(t2) > 50:
                L("PASSED (popup gone)")
                return True
            L("Popup gone but no token, breaking")
            break

    return False


def do_login(sb):
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        L("NO VPS8_EMAIL or VPS8_PASSWORD configured")
        return False
    for att in range(1, 4):
        L("========= Login attempt " + str(att) + "/3 =========")
        sb.open(LOGIN_URL)
        sb.sleep(4)
        cur = sb.get_current_url()
        L("Current URL: " + cur)
        if "/login" not in cur:
            p = str(OUT / "logged_in.png")
            sb.save_screenshot(p)
            tg_img(p)
            L("ALREADY LOGGED IN! Screenshot: " + p)
            return True

        # Type credentials
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            L("Credentials typed OK")
        except Exception as e:
            L("Failed to type credentials: " + str(e))
            return False

        # Solve captcha
        if not do_captcha(sb):
            L("CAPTCHA SOLVE FAILED (" + str(att) + "/3)")
            sb.sleep(3)
            continue

        # Click submit
        try:
            sb.click('button[type="submit"]')
            sb.sleep(10)
        except Exception as e:
            L("Failed to click submit: " + str(e))
            return False

        cur = sb.get_current_url()
        L("After submit URL: " + cur)

        p = str(OUT / "submit" + str(att) + ".png")
        sb.save_screenshot(p)
        tg_img(p)
        L("Screenshot saved: " + p)

        if "/login" not in cur:
            L("*** LOGIN SUCCESS ***")
            return True
        L("Still on login page (" + str(att) + "/3)")

    L("*** ALL 3 LOGIN ATTEMPTS FAILED ***")
    return False


def do_signin(sb):
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()
    if "Login to your account" in src:
        return "Cookie expired, redirected to login"
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "Could not find CSRF token"
    csrf = m.group(1)
    ck = "; ".join(c["name"] + "=" + c["value"] for c in sb.get_cookies())
    try:
        r = requests.post(
            BASE_URL + "/api/client/points/signin",
            params={"CSRFToken": csrf},
            headers={"Cookie": ck, "Referer": SIGNIN_URL,
                     "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=15)
        L("API response: " + str(r.status_code) + " " + r.text[:300])
        j = r.json()
        if j.get("error"):
            msg = j["error"]["message"]
            if "already" in msg.lower():
                return "Already signed in today"
            return "API error: " + msg
        if "result" in j and j["result"] is not None:
            return "OK: " + json.dumps(j["result"], ensure_ascii=False)[:200]
        return "Unexpected API response: " + r.text[:200]
    except Exception as e:
        L("API request error: " + str(e))
        return "API exception: " + str(e)


def main():
    L("=" * 50)
    L("VPS8 AUTO SIGN-IN")
    L("AI Model: " + AI_MODEL_NAME)
    L("AI Base: " + AI_BASE_URL)
    L("Start: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L("Email: " + VPS8_EMAIL[:5] + "***")
    L("=" * 30)

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
                L("Not logged in, starting login flow...")
                if do_login(sb):
                    L("Login success, now signing in...")
                    result = do_signin(sb)
                    ok = True
                else:
                    result = "All login attempts failed"
                    L(result)
            else:
                L("Already have valid cookie, signing in directly...")
                result = do_signin(sb)
                if "Cookie expired" in result:
                    L("Cookie actually expired mid-flow")
                else:
                    ok = "OK" in result or "Already" in result or "already" in result.lower()

    except Exception as e:
        tb = traceback.format_exc()
        L("FATAL ERROR:")
        L(tb)
        result = "CRASH: " + str(e)

    # ========== Send results via Telegram ==========
    L("=" * 30)
    final_status = "SUCCESS" if ok else "FAILURE"
    L("FINAL: " + final_status + " | " + result)

    icon = "OK" if ok else "FAIL"
    summary = icon + " VPS8 Sign-in\n\n" + result + "\n\nTime: " + datetime.now().strftime("%Y-%m-%d %H:%M")
    tg(summary)

    # Send screenshots
    ss_files = sorted(OUT.glob("*.png"))
    if ss_files:
        for ss in ss_files:
            tg_img(str(ss))
            time.sleep(0.5)
    else:
        L("No screenshots found in " + str(OUT))

    # Send full log
    full_log = get_text()
    time.sleep(1)
    tg("LOG:\n" + full_log)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("success=" + ("true" if ok else "false") + "\n")
            f.write("result=" + result + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
