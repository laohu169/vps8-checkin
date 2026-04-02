#!/usr/bin/env python3
"""VPS8 auto-signin with AI image recognition"""
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

log_buf = io.StringIO()

def L(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = ts + " | " + msg
    log_buf.write(line + "\n")
    print(line, flush=True, file=sys.stderr)


def tg(text):
    if not TG_TOKEN or not MY_CHAT_ID:
        return
    for i in range(0, max(1, len(text)), 4000):
        chunk = text[i:min(i+4000, len(text))]
        try:
            requests.post(
                "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                json={"chat_id": MY_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=30)
            time.sleep(1)
        except Exception as e:
            L("TG send err: " + str(e))


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
        L("TG image sent: " + path)
    except Exception as e:
        L("TG img err: " + str(e))


def save_b64(b64, name):
    try:
        (OUT / (name + ".png")).write_bytes(base64.b64decode(b64))
    except:
        pass


def get_question(d):
    try:
        el = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-instructions")
        t = el.text.strip()
        if t:
            return " ".join([l.strip() for l in t.split("\n") if l.strip()])[:120]
    except:
        pass
    return ""


def ai_grid(img_b64, question, rows, cols):
    if not AI_API_KEY:
        return []
    mx_t = rows * cols
    np = []
    for r in range(rows):
        np.append(", ".join(str(r * cols + c + 1) for c in range(cols)))
    numbering = "\n".join("Row" + str(r+1) + ": [" + n + "]" for r, n in enumerate(np))
    prompt = (
        "Grid " + str(rows) + "x" + str(cols) + " pictures.\n"
        "Find ALL containing: \"" + question + "\"\n"
        "Cell numbering:\n" + numbering + "\n\n"
        "Reply ONLY matching cell numbers comma separated.\n"
        "Example: 1, 4, 7. If no match: -1. Nothing else."
    )
    try:
        img = Image.open(BytesIO(base64.b64decode(img_b64)))
        if max(img.size) > 1024:
            ratio = 1024.0 / max(img.size)
            img = img.resize((int(img.width*ratio), int(img.height*ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        sb = base64.b64encode(buf.getvalue()).decode()
        r = requests.post(
            AI_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + AI_API_KEY},
            json={"model": AI_MODEL_NAME, "messages": [
                {"role": "system", "content": "Return only cell numbers matching."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + sb, "detail": "high"}}]}],
                "max_tokens": 50, "temperature": 0.1},
            timeout=60)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        L("AI: " + ans)
        if "-1" in ans:
            return []
        nums = [int(n) for n in re.findall(r'\d+', ans)]
        return [n for n in nums if 1 <= n <= mx_t]
    except Exception as e:
        L("AI err: " + str(e))
        return []


def do_captcha(sb):
    d = sb.driver
    d.switch_to.default_content()
    L("find checkbox...")
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
        L("No checkbox frame")
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
        L("Auto-passed!")
        return True
    L("wait challenge...")
    try:
        sb.wait_for_element_present("iframe[src*='bframe']", timeout=20)
    except:
        t2 = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        return len(t2) > 50
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
        q = get_question(d) or "identify object"
        L("R" + str(rnd) + " " + str(rows) + "x" + str(cols) + " Q:" + q)
        gb = ""
        try:
            gb = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
        except:
            try:
                gb = sb.get_screenshot_as_base64()
            except:
                break
        if gb:
            save_b64(gb, "r" + str(rnd))
            nums = ai_grid(gb, q, rows, cols)
            if nums:
                L("  click: " + str(nums))
                for n in nums:
                    i = n - 1
                    if 0 <= i < len(tiles):
                        tiles[i].click()
                        sb.sleep(0.2)
        sb.sleep(1)
        try:
            d.find_element(By.ID, "recaptcha-verify-button").click()
            L("  verify clicked")
        except Exception as e:
            L("  verify fail: " + str(e))
            return False
        sb.sleep(4)
        t = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(t) > 50:
            L("  PASSED token:" + t[:30])
            return True
        try:
            d.find_element(By.ID, "rc-imageselect")
        except:
            t2 = d.execute_script("var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
            if len(t2) > 50:
                L("  PASSED popup gone")
                return True
            L("  Popup gone no token")
            break
    return False


def do_login(sb):
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        L("NO EMAIL/PASSWORD")
        return False
    for att in range(1, 4):
        L("=== Login " + str(att) + "/3 ===")
        sb.open(LOGIN_URL)
        sb.sleep(4)
        cur = sb.get_current_url()
        L("URL: " + cur)
        if "/login" not in cur:
            p = str(OUT / "success.png")
            sb.save_screenshot(p)
            tg_img(p)
            L("ALREADY LOGGED IN! " + p)
            return True
        sb.type("#email", VPS8_EMAIL)
        sb.type("#password", VPS8_PASSWORD)
        L("Typed email/password")
        if not do_captcha(sb):
            L("VERIFY FAILED (" + str(att) + "/3)")
            sb.sleep(3)
            continue
        sb.click('button[type="submit"]')
        sb.sleep(10)
        cur = sb.get_current_url()
        L("After submit URL: " + cur)
        p = str(OUT / ("submit" + str(att) + ".png"))
        sb.save_screenshot(p)
        tg_img(p)
        L("Screenshot: " + p)
        if "/login" not in cur:
            L("LOGIN SUCCESS!")
            return True
        L("Still login page (" + str(att) + "/3)")
    L("ALL 3 FAILED")
    return False


def do_signin(sb):
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()
    if "Login to your account" in src:
        return "Cookie invalid"
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "No CSRF token"
    ck = "; ".join(c["name"] + "=" + c["value"] for c in sb.get_cookies())
    try:
        r = requests.post(
            BASE_URL + "/api/client/points/signin",
            params={"CSRFToken": m.group(1)},
            headers={"Cookie": ck, "Referer": SIGNIN_URL,
                     "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"},
            timeout=15)
        L("API " + str(r.status_code))
        j = r.json()
        if j.get("error"):
            msg = j["error"]["message"]
            if "already" in msg.lower():
                return "Already signed in"
            return "API: " + msg
        if "result" in j and j["result"] is not None:
            return "OK " + json.dumps(j["result"])[:200]
        return "Response: " + r.text[:200]
    except Exception as e:
        L("API err: " + str(e))
        return "API exception: " + str(e)


def main():
    L("=" * 50)
    L("VPS8 SIGNIN")
    L("AI: " + AI_MODEL_NAME)
    L("Time: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L("Email: " + VPS8_EMAIL[:5] + "***")
    L("=" * 30)
    result = ""
    ok = False
    try:
        with SB(
            headed=False, locale="en",
            chromium_arg=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-gpu",
                "--disable-dev-shm-usage",
                "--window-size=1280,900",
            ],
        ) as sb:
            sb.open(SIGNIN_URL)
            sb.sleep(3)
            src = sb.get_page_source()
            if "Login to your account" in src:
                L("Not logged in, logging in...")
                if do_login(sb):
                    result = do_signin(sb)
                    ok = True
                else:
                    result = "Login failed"
            else:
                L("Cookie valid, signin directly")
                result = do_signin(sb)
                ok = True
    except Exception as e:
        tb = traceback.format_exc()
        L("FATAL ERROR:\n" + tb)
        result = "Crash: " + str(e)

    L("=" * 30)
    L("DONE: " + ("OK" if ok else "FAIL") + " | " + result)
    full_log = log_buf.getvalue()
    icon = "[OK]" if ok else "[FAIL]"
    summary = icon + " VPS8 Signin\n\n" + result + "\n\nAI: " + AI_MODEL_NAME

    ss_files = sorted(OUT.glob("*.png"))
    for ss in ss_files:
        tg_img(str(ss))
        time.sleep(1)

    tg(summary)
    time.sleep(1)
    if len(full_log) > 4000:
        tg("LOG:\n" + full_log[-3990:])
    else:
        tg("LOG:\n" + full_log)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("success=" + ("true" if ok else "false") + "\n")
            f.write("result=" + result + "\n")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
