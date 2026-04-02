#!/usr/bin/env python3
"""VPS8 auto-signin with AI captcha"""
import os, sys, time, json, re
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
import base64
from PIL import Image
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

OUT = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "output" / "vps8"
OUT.mkdir(parents=True, exist_ok=True)

logs = []
def L(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = ts + " | " + str(msg)
    logs.append(line)
    print(line, flush=True, file=sys.stderr)

def tg(text=""):
    if not TG_TOKEN or not MY_CHAT_ID: return
    for i in range(0, max(1, len(text)), 4000):
        try:
            requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                json={"chat_id": MY_CHAT_ID, "text": text[i:i+4000]}, timeout=30)
            time.sleep(0.5)
        except: pass

def tg_img(path):
    if not TG_TOKEN or not MY_CHAT_ID or not os.path.isfile(path): return
    try:
        with open(path, "rb") as f:
            requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendPhoto",
                data={"chat_id": MY_CHAT_ID, "caption": Path(path).name},
                files={"photo": f}, timeout=30)
        time.sleep(0.5)
    except: pass

def save_b64(b64, name):
    try: (OUT / (name + ".png")).write_bytes(base64.b64decode(b64))
    except: pass

# ═══════════════════════════════════════════════════════════
# AI solve reCAPTCHA image grid
# ═══════════════════════════════════════════════════════════
def ai_solve(b64, question, rows, cols):
    if not AI_API_KEY: return []
    mx = rows * cols
    nl = []
    for r in range(rows):
        nl.append("Row" + str(r+1) + ": [" + ", ".join(str(r*cols+c+1) for c in range(cols)) + "]")
    prompt = (
        "Grid " + str(rows) + "x" + str(cols) + " pictures.\n"
        "Find: \"" + question + "\"\n\n"
        "Numbering:\n" + "\n".join(nl) + "\n\n"
        "Reply ONLY cell numbers comma separated.\n"
        "Example: 1, 4, 7. If none: -1.")
    try:
        img = Image.open(BytesIO(base64.b64decode(b64)))
        if max(img.size) > 1024:
            ratio = 1024.0 / max(img.size)
            img = img.resize((int(img.width*ratio), int(img.height*ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        sb64 = base64.b64encode(buf.getvalue()).decode()
        save_b64(sb64, "ai")
        r = requests.post(AI_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + AI_API_KEY},
            json={"model": AI_MODEL_NAME, "messages": [
                {"role": "system", "content": "Return only cell numbers."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + sb64, "detail": "high"}}]}],
                "max_tokens": 50, "temperature": 0.1},
            timeout=60)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        L("AI: " + ans)
        if "-1" in ans: return []
        return [int(n) for n in re.findall(r'\d+', ans) if 1 <= int(n) <= mx]
    except Exception as e:
        L("AI err: " + str(e))
        return []

# ═══════════════════════════════════════════════════════════
# Captcha solving flow (called when logged into challenge iframe)
# ═══════════════════════════════════════════════════════════
def do_captcha_rounds(sb):
    d = sb.driver
    for rnd in range(1, 30):
        tiles = d.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        total = len(tiles)
        if total == 16: rows, cols = 4, 4
        elif total == 12: rows, cols = 4, 3
        else: rows, cols = 3, 3
        
        q = d.execute_script(
            "var el=document.querySelector('.rc-imageselect-instructions');"
            "return el?(el.innerText||el.textContent||'').trim().substring(0,120):'';") or "the object"
        L("R" + str(rnd) + " " + str(rows) + "x" + str(cols) + " Q:" + q)
        
        gb = ""
        try: gb = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table").screenshot_as_base64
        except:
            try: gb = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
            except: pass
        if gb:
            save_b64(gb, "r" + str(rnd))
            nums = ai_solve(gb, q, rows, cols)
            if nums:
                L("Click: " + str(nums))
                for n in nums:
                    i = n - 1
                    if 0 <= i < len(tiles): tiles[i].click(); time.sleep(0.2)
        time.sleep(1)
        try: d.find_element(By.ID, "recaptcha-verify-button").click()
        except: return False
        time.sleep(5)
        
        # Check token (from parent context)
        parent_token = d.execute_script(
            "return document.getElementById('g-recaptcha-response') ? "
            "document.getElementById('g-recaptcha-response').value : '';")
        if len(parent_token) > 50:
            L("PASSED!")
            d.switch_to.default_content()
            return True
        
        # Also check from within iframe context
        token = d.execute_script(
            "return document.getElementById('g-recaptcha-response') ? "
            "document.getElementById('g-recaptcha-response').value : '';")
        if len(token) > 50:
            L("PASSED from inside!")
            d.switch_to.default_content()
            return True

        # Check if popup disappeared
        try: d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table")
        except:
            d.switch_to.default_content()
            t = d.execute_script(
                "return document.getElementById('g-recaptcha-response') ? "
                "document.getElementById('g-recaptcha-response').value : '';")
            return len(t) > 50
    d.switch_to.default_content()
    return False

# ═══════════════════════════════════════════════════════════
# Full captcha flow
# ═══════════════════════════════════════════════════════════
def do_captcha(sb):
    d = sb.driver
    d.switch_to.default_content()
    L("Finding checkbox...")
    try:
        WebDriverWait(d, 15).until(
            EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[title*='recaptcha']")))
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        L("Checkbox clicked")
    except Exception as e:
        L("Checkbox err: " + str(e))
        return False
    sb.sleep(3)
    t = d.execute_script("return document.getElementById('g-recaptcha-response')?document.getElementById('g-recaptcha-response').value:'';")
    if len(t) > 50:
        L("Passed immediately!")
        return True

    L("Waiting challenge...")
    try:
        WebDriverWait(d, 20).until(
            EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[src*='bframe']")))
        d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table")
        L("In challenge")
    except:
        return len(d.execute_script("return document.getElementById('g-recaptcha-response')?document.getElementById('g-recaptcha-response').value:'';")) > 50
    
    ok = do_captcha_rounds(sb)
    d.switch_to.default_content()
    return ok

# ═══════════════════════════════════════════════════════════
# Login
# ═══════════════════════════════════════════════════════════
def do_login(sb):
    L("Navigating to login URL: " + LOGIN_URL)
    sb.open(LOGIN_URL)
    sb.sleep(5)

    # Take screenshot before anything
    p = str(OUT / "login_start.png")
    try:
        sb.save_screenshot(p)
        tg_img(p)
    except: pass

    # Type credentials
    try:
        sb.type("#email", VPS8_EMAIL)
        sb.type("#password", VPS8_PASSWORD)
        L("Credentials typed")
    except Exception as e:
        L("Type creds err: " + str(e))
        return False

    # Solve captcha
    if not do_captcha(sb):
        L("Captcha failed")
        p2 = str(OUT / "captcha_fail.png")
        try:
            sb.save_screenshot(p2)
            tg_img(p2)
        except: pass
        return False

    # Submit login
    L("Submitting login...")
    
    # Try multiple approaches
    submitted = False
    
    # Method 1: JS click the submit button
    try:
        sb.driver.execute_script(
            "var b=document.querySelector('form button[type=\"submit\"]');"
            "if(b){b.click();}")
        submitted = True
        L("JS click submit button")
    except Exception as e:
        L("JS click err: " + str(e))
    
    # Method 2: If JS didn't work, try SeleniumBase click
    if not submitted:
        try:
            sb.click('button[type="submit"]')
            submitted = True
            L("SeleniumBase click submit")
        except Exception as e:
            L("SB click err: " + str(e))
    
    # Wait for navigation
    L("Waiting for page to load...")
    sb.sleep(12)

    # Take result screenshot
    cur = sb.get_current_url()
    L("After submit URL: " + cur)
    p3 = str(OUT / "login_end.png")
    try:
        sb.save_screenshot(p3)
        tg_img(p3)
    except: pass
    
    # Check page state
    src = sb.get_page_source()
    if "Login to your account" in src:
        L("STILL on login page!")
        # Check for error messages on page
        try:
            body_text = sb.driver.find_element(By.TAG_NAME, "body").text
            for keyword in ["Invalid", "incorrect", "error", "wrong", "failed"]:
                if keyword.lower() in body_text.lower():
                    L("Found '" + keyword + "' in page body")
                    # Extract nearby text
                    idx = body_text.lower().find(keyword.lower())
                    L("Context: ..." + body_text[max(0,idx-50):idx+100] + "...")
                    break
        except:
            pass
        return False
    else:
        L("LOGIN SUCCESS! Navigated to: " + cur)
        return True


def main():
    L("=50")
    L("VPS8 SIGNIN")
    L("AI: " + AI_MODEL_NAME)
    L("Start: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L("=30")

    result = ""
    ok = False

    try:
        with SB(headed=False, locale="en",
                chromium_arg=["--disable-blink-features=AutomationControlled",
                    "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                    "--window-size=1280,900"]) as sb:
            
            sb.open(SIGNIN_URL)
            sb.sleep(3)
            src = sb.get_page_source()
            
            # Check if logged in
            if "Login to your account" in src:
                L("Not logged in, logging in...")
                if do_login(sb):
                    L("Login ok, doing signin...")
                    sb.open(SIGNIN_URL)
                    sb.sleep(3)
                    src2 = sb.get_page_source()
                    result = check_and_signin(sb, src2)
                    ok = "success" in result or "already" in result or "error" not in result
                else:
                    result = "login_failed"
            else:
                L("Cookie valid, signin directly...")
                result = check_and_signin(sb, src)
                ok = "success" in result or "already" in result or "error" not in result
            
            L("RESULT: " + str(result))
            L("OK: " + str(ok))

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        L("CRASH:")
        L(tb)
        result = "crash:" + str(e)
    
    L("=30")
    L("FINAL: ok=" + str(ok) + " result=" + str(result))
    icon = "[OK]" if ok else "[FAIL]"
    tg(icon + " Signin\n\nResult: " + str(result) + "\n\nTime: " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    for ss in sorted(OUT.glob("*.png")):
        tg_img(str(ss))
    time.sleep(1)
    tg("LOG:\n" + "\n".join(logs[-100:]))
    
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("success=" + ("true" if ok else "false") + "\n")
            f.write("result=" + str(result) + "\n")
    sys.exit(0 if ok else 1)

def check_and_signin(sb, src):
    """Check signin page and sign in if possible"""
    
    # Check already signed in
    if "今日已签" in src or "已经签到" in src:
        return "already_signed_in"
    
    # Check if cookie expired
    if "Login to your account" in src:
        return "cookie_expired"
    
    # Get CSRF
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "no_csrf"
    
    csrf = m.group(1)
    ck = "; ".join(c["name"] + "=" + c["value"] for c in sb.driver.get_cookies())
    
    L("CSRF=" + csrf[:20] + " cookies=" + ck)
    L("Calling signin API...")
    
    try:
        r = requests.post(
            BASE_URL + "/api/client/points/signin",
            data={"CSRFToken": csrf},
            headers={"Cookie": ck, "Referer": SIGNIN_URL,
                     "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=15)
        L("API: " + str(r.status_code) + " " + r.text[:500])
        try:
            j = r.json()
            if j.get("error"):
                msg = j["error"]["message"]
                if "already" in msg.lower() or "已签" in msg:
                    return "already_signed_in"
                return "api_error:" + msg
            if "result" in j and j["result"] is not None:
                return "success:" + json.dumps(j["result"], ensure_ascii=False)[:200]
        except: pass
        if r.status_code in (200, 302):
            sb.open(SIGNIN_URL)
            sb.sleep(3)
            if check_and_signin(sb, sb.get_page_source()) == "already_signed_in":
                return "already_signed_in"
            return "success"
        return "status_" + str(r.status_code)
    except Exception as e:
        L("API crash: " + str(e))
        return "api_err:" + str(e)

if __name__ == "__main__":
    main()
