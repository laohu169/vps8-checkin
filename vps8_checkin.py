#!/usr/bin/env python3
"""VPS8 auto-signin - AI image grid solver"""
import os, sys, time, json, re
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

log_lines = []

def L(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = ts + " | " + str(msg)
    log_lines.append(line)
    print(line, flush=True, file=sys.stderr)


def tg(text=""):
    if not TG_TOKEN or not MY_CHAT_ID:
        return
    text = str(text)
    for i in range(0, max(1, len(text)), 4000):
        try:
            requests.post(
                "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                json={"chat_id": MY_CHAT_ID, "text": text[i:i+4000], "parse_mode": "HTML"}, timeout=30)
            time.sleep(1)
        except:
            pass


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
    except:
        pass


def save_b64(b64, name):
    try:
        (OUT / (name + ".png")).write_bytes(base64.b64decode(b64))
    except:
        pass


def get_question(d):
    js = """(function() {
        var el = document.querySelector('.rc-imageselect-instructions');
        if (!el) return '';
        return (el.innerText || el.textContent || '').trim().replace(/\\n/g, ' ').substring(0, 150);
    })()"""
    try:
        result = d.execute_script(js)
        if result and len(result) > 5:
            return result
    except:
        pass
    return ""


def ai_solve(img_b64, question, rows, cols):
    if not AI_API_KEY:
        return []
    mx = rows * cols
    nl = []
    for r in range(rows):
        vals = ", ".join(str(r * cols + c + 1) for c in range(cols))
        nl.append("Row" + str(r+1) + ": [" + vals + "]")
    prompt = (
        "You see a " + str(rows) + " by " + str(cols) + " grid of small pictures.\n"
        "Question: Which pictures contain \"" + question + "\"\n\n"
        "Cell numbering:\n" + "\n".join(nl) + "\n\n"
        "Reply with ONLY the matching cell numbers, separated by commas.\n"
        "Example: 1, 4, 7\n"
        "If nothing matches, reply: -1\n"
        "No explanations."
    )
    try:
        img = Image.open(BytesIO(base64.b64decode(img_b64)))
        if max(img.size) > 1024:
            ratio = 1024.0 / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        sb64 = base64.b64encode(buf.getvalue()).decode()
        save_b64(sb64, "ai_in")
        r = requests.post(
            AI_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + AI_API_KEY},
            json={"model": AI_MODEL_NAME, "messages": [
                {"role": "system", "content": "Return only cell numbers matching."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + sb64, "detail": "high"}}]}],
                "max_tokens": 50, "temperature": 0.1},
            timeout=60)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        L("AI: " + ans)
        if "-1" in ans:
            return []
        return [int(n) for n in re.findall(r'\d+', ans) if 1 <= int(n) <= mx]
    except Exception as e:
        L("AI err: " + str(e))
        return []


def do_captcha(sb):
    d = sb.driver
    d.switch_to.default_content()
    L("Finding checkbox...")
    try:
        sb.wait_for_element_present("iframe[title*='recaptcha']", timeout=15)
    except:
        L("No checkbox iframe")
        return False
    time.sleep(1)
    frames = d.find_elements(By.TAG_NAME, "iframe")
    cb = None
    for f in frames:
        if "recaptcha" in (f.get_attribute("title") or "").lower():
            cb = f
            break
    if not cb:
        L("No checkbox frame in " + str(len(frames)))
        return False
    try:
        d.switch_to.frame(cb)
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        L("Checkbox clicked")
    except Exception as e:
        d.switch_to.default_content()
        L("Checkbox err: " + str(e))
        return False
    sb.sleep(3)
    t = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
    if len(t) > 50:
        L("Passed immediately!")
        return True
    L("Waiting for challenge...")
    try:
        sb.wait_for_element_present("iframe[src*='bframe']", timeout=20)
    except:
        t2 = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(t2) > 50:
            return True
        return False
    cf = None
    for f in d.find_elements(By.TAG_NAME, "iframe"):
        if "bframe" in (f.get_attribute("src") or ""):
            try:
                d.switch_to.frame(f)
                d.find_element(By.ID, "rc-imageselect")
                cf = f
                L("Entered challenge iframe")
                break
            except:
                d.switch_to.default_content()
    if not cf:
        L("No challenge frame")
        return False
    for rnd in range(1, 25):
        tiles = d.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        total = len(tiles)
        if total == 16:
            rows, cols = 4, 4
        elif total == 12:
            rows, cols = 4, 3
        else:
            rows, cols = 3, 3
        question = get_question(d) or "the described object"
        L("R" + str(rnd) + " " + str(rows) + "x" + str(cols) + " Q:" + question)
        gb = ""
        try:
            gb = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
        except:
            gb = d.get_screenshot_as_base64()
        if gb:
            save_b64(gb, "r" + str(rnd))
            nums = ai_solve(gb, question, rows, cols)
            if nums:
                L("Click: " + str(nums))
                for n in nums:
                    i = n - 1
                    if 0 <= i < len(tiles):
                        tiles[i].click()
                        sb.sleep(0.2)
        sb.sleep(1)
        try:
            d.find_element(By.ID, "recaptcha-verify-button").click()
        except:
            d.switch_to.default_content()
            return False
        sb.sleep(5)
        token = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(token) > 50:
            L("PASSED!")
            d.switch_to.default_content()
            return True
        try:
            d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table")
        except:
            d.switch_to.default_content()
            token2 = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
            if len(token2) > 50:
                return True
            return False
    d.switch_to.default_content()
    return False


def do_login(sb):
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        L("NO credentials")
        return False
    for att in range(1, 4):
        L("Login " + str(att) + "/3")
        sb.open(LOGIN_URL)
        sb.sleep(4)
        cur = sb.get_current_url()
        L("URL: " + cur)
        if "/login" not in cur:
            p = str(OUT / "logged_in.png")
            sb.save_screenshot(p)
            tg_img(p)
            L("Already logged in! " + p)
            return True
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            L("Typed creds")
        except Exception as e:
            L("Type err: " + str(e))
            return False
        if not do_captcha(sb):
            L("Captcha failed (" + str(att) + "/3)")
            sb.sleep(3)
            continue
        try:
            sb.click('button[type="submit"]')
            sb.sleep(10)
        except Exception as e:
            L("Submit err: " + str(e))
            return False
        cur = sb.get_current_url()
        L("After submit: " + cur)
        p = str(OUT / ("submit" + str(att) + ".png"))
        sb.save_screenshot(p)
        tg_img(p)
        if "/login" not in cur:
            L("LOGIN SUCCESS!")
            return True
        L("Still login page (" + str(att) + "/3)")
    L("ALL 3 FAILED")
    return False


def is_already_signed_in(d):
    """Check the signin page to see if already signed in today"""
    try:
        body_text = d.find_element(By.TAG_NAME, "body").text
        L("Page body text (first 500 chars): " + body_text[:500])
        if "已签到" in body_text or "今日已签" in body_text:
            L("Found signed-in text in page body!")
            return True
    except Exception as e:
        L("body text err: " + str(e))
    try:
        src = d.page_source
        if "已签到" in src or "今日已签" in src:
            L("Found signed-in text in page source")
            return True
    except:
        pass
    return False


def do_signin(sb):
    sb.open(SIGNIN_URL)
    sb.sleep(4)

    # Screenshot
    p = str(OUT / "signin_page.png")
    sb.save_screenshot(p)
    tg_img(p)
    L("Signin page screenshot taken")

    src = sb.get_page_source()
    if "Login to your account" in src:
        L("Redirected to login - cookie expired")
        return "cookie_expired"

    # Check already signed in BEFORE trying API
    if is_already_signed_in(sb.driver):
        return "already_signed_in"

    # Extract CSRF
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        L("No CSRF token")
        return "no_csrf"
    csrf = m.group(1)
    ck = "; ".join(c["name"] + "=" + c["value"] for c in sb.get_cookies())

    try:
        r = requests.post(
            BASE_URL + "/api/client/points/signin",
            params={"CSRFToken": csrf},
            headers={"Cookie": ck, "Referer": SIGNIN_URL,
                     "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=15)
        L("API status: " + str(r.status_code) + " body: " + r.text[:800])
        try:
            j = r.json()
            if j.get("error"):
                msg = j["error"]["message"]
                if "already" in msg.lower() or "已签" in msg:
                    return "already_signed_in"
                return "api_error:" + msg
            if "result" in j and j["result"] is not None:
                return "success:" + json.dumps(j["result"], ensure_ascii=False)[:200]
        except:
            pass
        # Reload and check
        sb.sleep(2)
        if is_already_signed_in(sb.driver):
            return "already_signed_in"
        if r.status_code in (200, 302):
            return "success"
        return "status_" + str(r.status_code)
    except Exception as e:
        L("API crash: " + str(e))
        return "api_crash:" + str(e)


def main():
    L("=" * 50)
    L("VPS8 AUTO SIGNIN")
    L("AI: " + AI_MODEL_NAME)
    L("Start: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L("=" * 30)
    result = ""
    ok = False
    try:
        with SB(
            headed=False, locale="en",
            chromium_arg=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-gpu",
                "--disable-dev-shm-usage", "--window-size=1280,900",
            ],
        ) as sb:
            sb.open(SIGNIN_URL)
            sb.sleep(3)
            src = sb.get_page_source()
            if "Login to your account" in src:
                L("Not logged in, doing login...")
                if do_login(sb):
                    result = do_signin(sb)
                else:
                    result = "login_failed"
            else:
                L("Cookie valid, goto signin...")
                result = do_signin(sb)
                # Handle cookie expired mid-flight
                if result == "cookie_expired":
                    L("Cookie expired, logging in again...")
                    if do_login(sb):
                        result = do_signin(sb)

            L("Result: " + str(result))
            if result == "already_signed_in":
                ok = True
                L("Already signed in today - considered SUCCESS")
            elif result == "success" or (isinstance(result, str) and result.startswith("success:")):
                ok = True
                L("Signed in OK!")
            else:
                L("NOT successful: " + str(result))
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        L("FATAL:\n" + tb)
        result = "crash:" + str(e)

    L("=" * 30)
    L("FINAL: " + ("OK" if ok else "FAIL") + " | " + str(result))
    icon = "[OK]" if ok else "[FAIL]"
    msg = icon + " VPS8 Signin\n\n"
    if result == "already_signed_in":
        msg += "Already signed in today\n"
    elif isinstance(result, str) and result.startswith("success:"):
        msg += "Signed in!\n" + result[8:]
    else:
        msg += "Result: " + str(result)
    msg += "\n\nTime: " + datetime.now().strftime("%Y-%m-%d %H:%M")
    tg(msg)
    ss_files = sorted(OUT.glob("*.png"))
    for ss in ss_files:
        tg_img(str(ss))
    time.sleep(1)
    tg("LOG:\n" + "\n".join(log_lines[-100:]))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("success=" + ("true" if ok else "false") + "\n")
            f.write("result=" + str(result) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
