#!/usr/bin/env python3
"""
VPS8.zz.cd 每日一签到
浏览器找图 + AI 视觉识别
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

logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | {message}")


def tg(text):
    if not TG_TOKEN or not MY_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": MY_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10)
    except:
        pass


def save_img(b64, name="cap"):
    try:
        (OUT / f"{name}.png").write_bytes(base64.b64decode(b64))
    except:
        pass


# ═══════════════════════════════════════════════════
# 提取问题文本（关键修复！）
# ═══════════════════════════════════════════════════
def extract_question(d):
    """
    从 .rc-imageselect-instructions 提取完整问题文本。
    
    Google 的 HTML 结构:
    <div class="rc-imageselect-instructions">
      <span id="instruction-text">Select all squares with</span>
      <strong id="instruction-text-strong">motorcycles</strong>
    </div>
    
    之前的 bug: 只拿到了 span 的文本，漏掉了 <strong> 中的物体名称！
    """
    try:
        el = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-instructions")
        # 取整个元素的完整文本（包含子元素）
        full_text = el.text.strip()
        if full_text:
            # 清理多余换行
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            # 合并成一行，去掉 "If there are none..." 等后半句
            full_text = " ".join(lines)
            # 截断
            if len(full_text) > 100:
                full_text = full_text[:100]
            return full_text
    except:
        pass
    return ""


# ═══════════════════════════════════════════════════
# AI 图片识别
# ═══════════════════════════════════════════════════
def solve_grid(img_b64, question, rows, cols):
    if not AI_API_KEY:
        return []

    max_tile = rows * cols
    num_parts = []
    for r in range(rows):
        nums = ", ".join(str(r * cols + c + 1) for c in range(cols))
        num_parts.append(f"Row{r+1}: [{nums}]")
    num_text = "\n".join(num_parts)

    # ⚠️ 完全避开敏感词
    prompt = (
        f"Grid of {rows}x{cols} pictures.\n"
        f"Find ALL pictures containing: \"{question}\"\n\n"
        f"Cell numbering:\n{num_text}\n\n"
        f"Reply with ONLY matching cell numbers comma separated.\n"
        f"I Example: 1, 4, 7\n"
        f"If NO pictures match, reply: -1\n"
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
                     "content": "Image recognition expert. Identify which grid cells contain the specified object. Reply with only cell numbers."},
                    {"role": "user",
                     "content": [
                         {"type": "text", "text": prompt},
                         {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{sb64}", "detail": "high"}}
                     ]}],
                "max_tokens": 50,
                "temperature": 0.1,
            },
            timeout=60,
        )
        ans = r.json()["choices"][0]["message"]["content"].strip()
        logger.info(f"AI: {ans}")
        save_img(sb64, "ai_input")

        if "-1" in ans:
            return []
        nums = [int(n) for n in re.findall(r'\d+', ans)]
        nums = [n for n in nums if 1 <= n <= max_tile]
        return nums
    except Exception as e:
        logger.error(f"AI 失败: {e}")
        return []


# ═══════════════════════════════════════════════════
# 图片验证破解
# ═══════════════════════════════════════════════════
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
            question = "identify the object"

        logger.info(f"--- {rnd} | {rows}x{cols} ({total}) ---")
        logger.info(f"Q: {question}")

        grid_b64 = ""
        try:
            grid_b64 = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
        except:
            try:
                grid_b64 = sb.get_screenshot_as_base64()
            except:
                break

        if not grid_b64:
            break
        save_img(grid_b64, f"r{rnd}")

        nums = solve_grid(grid_b64, question, rows, cols)
        if nums:
            for n in nums:
                i = n - 1
                if 0 <= i < len(tiles):
                    tiles[i].click()
                    sb.sleep(0.2)

        sb.sleep(1)
        try:
            d.find_element(By.ID, "recaptcha-verify-button").click()
            logger.info("Verify clicked")
        except Exception as e:
            logger.warning(f"Verify fail: {e}")
            return False

        sb.sleep(4)

        # 检查通过
        token = d.execute_script(
            "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
        if len(token) > 50:
            logger.info(f"PASSED! token {token[:30]}")
            return True

        # 检查弹窗是否还在
        try:
            d.find_element(By.ID, "rc-imageselect")
        except:
            token2 = d.execute_script(
                "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
            if len(token2) > 50:
                logger.info("PASSED (popup gone)")
                return True
            logger.info("Popup gone but no token, break")
            break

    return False


def solve_full(sb):
    d = sb.driver
    d.switch_to.default_content()

    logger.info("找 checkbox...")
    try:
        sb.wait_for_element_present("iframe[title*='recaptcha']", timeout=15)
    except:
        logger.warning("未找到 checkbox iframe")
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
        return False

    try:
        d.switch_to.frame(cb)
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        logger.info("已点 checkbox")
    except Exception as e:
        d.switch_to.default_content()
        logger.warning(f"checkbox: {e}")
        return False

    sb.sleep(3)
    t = d.execute_script(
        "var e=document.getElementById('g-recaptcha-response');return e?e.value:'';")
    if len(t) > 50:
        logger.info("直接通过!")
        return True

    logger.info("等 challenge...")
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
                logger.info("进入 challenge")
                break
            except:
                d.switch_to.default_content()

    if not cf:
        return False

    ok = break_image_challenge(sb)
    d.switch_to.default_content()
    return ok


# ═══════════════════════════════════════════════════
# 登录
# ═══════════════════════════════════════════════════
def do_login(sb):
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("无邮箱/密码")
        return False

    for att in range(1, 4):
        logger.info(f"尝试 {att}/3...")
        sb.open(LOGIN_URL)
        sb.sleep(4)

        if "/login" not in sb.get_current_url():
            logger.info("已登录!")
            return True

        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            logger.info("表单已填")
        except Exception as e:
            logger.error(f"填表: {e}")
            return False

        if not solve_full(sb):
            logger.warning(f"验证失败 ({att}/3)")
            sb.sleep(3)
            continue

        sb.click('button[type="submit"]')
        sb.sleep(10)

        p = OUT / f"after_submit{att}.png"
        sb.save_screenshot(str(p))

        u = sb.get_current_url()
        logger.info(f"URL: {u}")
        if "/login" not in u:
            logger.info("✅ 登录成功!")
            return True

    logger.error("3 次登录均失败")
    return False


# ═══════════════════════════════════════════════════
# 签到
# ═══════════════════════════════════════════════════
def do_signin(sb):
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()
    if "Login to your account" in src:
        return "Cookie 失效"
    if "已签到" in src:
        return "已签到过"

    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "无 CSRF Token"
    csrf = m.group(1)
    ck = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())
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
            if "已签到" in msg:
                return "已签到"
            return f"API: {msg}"
        if "result" in j and j["result"]:
            return f"成功! {json.dumps(j['result'])[:200]}"
        return "不确定"
    except:
        return "API 异常"


def main():
    logger.info("=" * 50)
    logger.info(f"VPS8 签到 | {datetime.now().strftime('%H:%M:%S')}")
    logger.info(f"AI: {AI_MODEL_NAME}")
    logger.info("=" * 30)

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
    msg = f"VPS8 签到\n{icon} {result}\n{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    tg(msg)


if __name__ == "__main__":
    main()
