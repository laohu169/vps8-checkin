#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到 — AI 视觉模型过 reCAPTCHA 免费版
技术：SeleniumBase + AI 视觉 3x3 / 3x4 图片网格识别
多轮识别（通常 5-6 轮）
需要 Secret: VPS8_EMAIL, VPS8_PASSWORD, AI_API_KEY, AI_BASE_URL, AI_MODEL_NAME
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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from loguru import logger

# ─── 配置 ────────────────────────────────────────────────────
BASE_URL       = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL      = f"{BASE_URL}/login"
SIGNIN_URL     = f"{BASE_URL}/points/signin"
API_SIGNIN     = f"{BASE_URL}/api/client/points/signin"

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

logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")


# ─── 通知 ──────────────────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not MY_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": MY_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram: {e}")


# ─── 保存截图（调试用）────────────────────────────────────────
def save_b64(b64: str, name: str = "cap"):
    try:
        p = OUTPUT_DIR / f"{name}.png"
        p.write_bytes(base64.b64decode(b64))
    except Exception:
        pass


# ─── AI 识别验证码图片 ──────────────────────────────────────────
def ai_solve(image_b64: str, question: str, grid_type: str = "grid") -> list:
    if not AI_API_KEY:
        logger.error("AI_API_KEY 未配置")
        return []

    if grid_type == "grid_3x4":
        rows, cols, max_tile = 4, 3, 12
    else:
        rows, cols, max_tile = 3, 3, 9

    numbering_parts = []
    for r in range(rows):
        nums = ", ".join(str(r * cols + c + 1) for c in range(cols))
        numbering_parts.append(f"第{r+1}行: {nums}")
    numbering_text = "，".join(numbering_parts)

    prompt = (
        f"这是一个 reCAPTCHA 人机验证的图片选择题，网格为 {rows} 行 × {cols} 列。\n"
        f"验证问题是：「{question}」\n\n"
        f"格子编号规则：{numbering_text}\n\n"
        f"请仔细观察每个格子，判断哪些格子包含问题中描述的物体。\n"
        f"**只返回应点击的格子编号**，逗号分隔，例如：1, 4, 7\n"
        f"如果没有符合的，返回空。\n"
        f"不要输出任何其他内容。"
    )

    logger.info(f"AI 识别: {question[:80]}")

    try:
        img_img = Image.open(BytesIO(base64.b64decode(image_b64)))
        max_dim = 1024
        if max(img_img.size) > max_dim:
            ratio = max_dim / max(img_img.size)
            img_img = img_img.resize(
                (int(img_img.width * ratio), int(img_img.height * ratio)),
                Image.LANCZOS)
        buf = BytesIO()
        img_img.save(buf, format="PNG")
        small_b64 = base64.b64encode(buf.getvalue()).decode()
        save_b64(small_b64, "ai_input")

        resp = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {"role": "system",
                     "content": "你是一个验证码识别专家。请准确识别 reCAPTCHA 图片网格中哪些格子包含指定物体，只返回编号列表。"},
                    {"role": "user",
                     "content": [
                         {"type": "text", "text": prompt},
                         {"type": "image_url", "image_url": {
                             "url": f"data:image/png;base64,{small_b64}",
                             "detail": "high",
                         }}]}],
                "max_tokens": 50,
                "temperature": 0.1,
            },
            timeout=60,
        )

        data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        logger.info(f"AI 返回: {answer}")

        nums = [int(n) for n in re.findall(r'\d+', answer)]
        nums = [n for n in nums if 1 <= n <= max_tile]
        logger.info(f"解析结果: {nums}")
        return nums

    except Exception as e:
        logger.error(f"AI 识别失败: {e}")
        return []


# ─── 获取验证码 iframe 列表（简化版）──────────────────────────────
def find_all_iframes(sb):
    """用 JavaScript 获取所有 iframe，返回列表"""
    return sb.driver.find_elements(By.TAG_NAME, "iframe")


def get_recaptcha_token(sb):
    try:
        t = sb.driver.execute_script(
            "var el=document.getElementById('g-recaptcha-response');"
            "return el?el.value:'';")
        return t if (t and len(t) > 50) else ""
    except Exception:
        return ""


def find_challenge_iframe(sb):
    """
    找到验证码挑战弹窗的 iframe。
    Selenium 原生 API, 不依赖 SeleniumBase 封装。
    """
    try:
        sb.switch_to.default_content()
        frames = find_all_iframes(sb)
        for frame in frames:
            sb.switch_to.frame(frame)
            # 检查是否有 rc-imageselect (验证码网格)
            try:
                sb.driver.find_element(By.ID, "rc-imageselect")
                sb.switch_to.default_content()
                return frame
            except Exception:
                pass
            # 或者看 title 属性
            title = (frame.get_attribute("title") or "").lower()
            if "recaptcha challenge" in title:
                sb.switch_to.default_content()
                return frame
            sb.switch_to.default_content()
        return None
    except Exception:
        sb.switch_to.default_content()
        return None


def get_question_text(sb):
    """获取验证码问题文本"""
    selectors = [
        (By.CSS_SELECTOR, ".rc-imageselect-instructions strong"),
        (By.CSS_SELECTOR, ".rc-imageselect-instructions"),
        (By.CSS_SELECTOR, ".rc-doscaptcha-header"),
    ]
    for by, sel in selectors:
        try:
            el = sb.driver.find_element(by, sel)
            txt = el.text.strip()
            if txt and len(txt) > 3:
                txt = re.split(r'如果没有|如果没|请点击', txt)[0].strip()
                if txt:
                    return txt
        except Exception:
            continue
    return "请识别图片"


def detect_grid_type(sb):
    try:
        tiles = sb.driver.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        if len(tiles) == 12:
            return "grid_3x4"
        return "grid"
    except Exception:
        return "grid"


def capture_grid(sb):
    try:
        el = sb.driver.find_element(By.ID, "rc-imageselect")
        return el.screenshot_as_base64
    except Exception:
        try:
            return sb.get_screenshot_as_base64()
        except Exception:
            return ""


def click_tiles(sb, nums):
    tiles = sb.driver.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
    logger.info(f"共 {len(tiles)} 个格，将点击 {nums}")
    for n in nums:
        i = n - 1
        if 0 <= i < len(tiles):
            try:
                tiles[i].click()
                logger.info(f"  ✅ 点了第 {n} 个")
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"  点 {n} 失败: {e}")
        else:
            logger.warning(f"  {n} 格超出范围")


# ─── reCAPTCHA 求解主流程 ──────────────────────────────────────
def solve_recaptcha(sb) -> bool:
    sb.sleep(2)

    # Step 1: 找 checkbox iframe → 点击
    logger.info("寻找 checkbox...")
    try:
        sb.switch_to.default_content()
        frames = find_all_iframes(sb)
        checkbox_found = False
        for frame in frames:
            try:
                sb.switch_to.frame(frame)
                cb = sb.driver.find_element(By.ID, "recaptcha-anchor")
                cb.click()
                logger.info("已点 checkbox")
                checkbox_found = True
                sb.switch_to.default_content()
                break
            except Exception:
                sb.switch_to.default_content()
                continue
        
        if not checkbox_found:
            logger.warning("未找到 checkbox")
            return False
    except Exception as e:
        logger.warning(f"点 checkbox 出错: {e}")
        return False

    sb.sleep(4)

    # Step 2: 检查直接通过
    token = get_recaptcha_token(sb)
    if token:
        logger.info("直接通过！")
        return True

    # Step 3: 循环图片验证（5-6 轮）
    for rnd in range(1, 12):
        logger.info(f"--- 第 {rnd} 轮 ---")

        # 找挑战 iframe
        cap_frame = find_challenge_iframe(sb)
        if not cap_frame:
            logger.info("弹窗未出现")
            break

        # 获取问题
        question = get_question_text(sb)
        logger.info(f"问题: {question[:80]}")

        # 截图网格
        grid_b64 = capture_grid(sb)
        if not grid_b64:
            logger.warning("截图失败")
            break

        # 类型判断 & AI
        gtype = detect_grid_type(sb)
        logger.info(f"网格: {gtype}")
        nums = ai_solve(grid_b64, question, gtype)
        if not nums:
            logger.warning("AI 未返回")
            break

        # 点击格子
        click_tiles(sb, nums)
        sb.sleep(1)

        # 点 Verify
        try:
            vb = sb.driver.find_element(By.ID, "recaptcha-verify-button")
            vb.click()
            logger.info("已点 Verify")
        except Exception as e:
            logger.warning(f"Verify 出错: {e}")

        sb.sleep(5)

        # 检查 token
        token = get_recaptcha_token(sb)
        if token:
            logger.info(f"✅ {token[:30]}...")
            return True

        # 检查弹窗是否还在
        new_frame = find_challenge_iframe(sb)
        if not new_frame:
            logger.info("弹窗消失")
            break

    token = get_recaptcha_token(sb)
    if token:
        logger.info(f"✅ {token[:30]}...")
        return True
    logger.warning("验证失败")
    return False


# ─── 登录 ─────────────────────────────────────────────────────────
def do_login(sb) -> bool:
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("未配置邮箱/密码")
        return False

    for attempt in range(1, 4):
        logger.info(f"登录 [{attempt}/3]...")
        sb.open(LOGIN_URL)
        sb.sleep(4)

        # 检查已登录
        if "/login" not in sb.get_current_url():
            return True

        # 填表单
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            logger.info("已填表")
        except Exception as e:
            logger.error(f"填表: {e}")
            return False

        # 解验证码
        if not solve_recaptcha(sb):
            logger.warning(f"验证码失败 {attempt}/3")
            continue
        sb.sleep(1)

        # 点登录
        try:
            sb.click('button[type="submit"]')
            sb.sleep(10)
        except Exception as e:
            logger.error(f"点登录: {e}")
            return False

        cur = sb.get_current_url()
        logger.info(f"URL: {cur}")
        if "/login" not in cur:
            with open(OUTPUT_DIR / "cookies.json", "w") as f:
                json.dump({"cookies": sb.get_cookies(),
                           "time": datetime.now().isoformat()}, f, indent=2)
            logger.info("✅ 登录成功！")
            return True
        logger.warning(f"失败 ({attempt}/3)")
        if attempt < 3:
            time.sleep(3)

    logger.error("3 次均失败")
    return False


# ─── 签到 ──────────────────────────────────────────────────────
def do_signin(sb) -> str:
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
        return "无法提取 CSRF Token"

    csrf = m.group(1)
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())

    try:
        resp = requests.post(
            API_SIGNIN,
            params={"CSRFToken": csrf},
            headers={
                "Cookie": cookie_str,
                "Referer": SIGNIN_URL,
                "Origin": BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        )
        logger.info(f"API {resp.status_code}: {resp.text[:300]}")
        if resp.status_code in (200, 302):
            try:
                j = resp.json()
                if j.get("error"):
                    msg = j["error"]["message"]
                    if "已签到" in msg or "already" in msg.lower():
                        return "已签到"
                    return f"API: {msg}"
                if "result" in j and j["result"] is not None:
                    return f"签到成功! {json.dumps(j['result'], ensure_ascii=False)[:200]}"
            except Exception:
                if "签到成功" in resp.text or "已签到" in resp.text:
                    return "签到成功!"
    except Exception as e:
        logger.warning(f"API: {e}")

    return "签到结果不确定"


# ─── 主函数 ─────────────────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info("VPS8 每日签到")
    logger.info(f"目标: {BASE_URL}")
    logger.info(f"AI: {AI_MODEL_NAME}")
    logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 30)

    result = ""
    ok = False

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
                result = do_signin(sb)
                ok = "成功" in result or "已签" in result
            else:
                result = "登录失败"
        else:
            logger.info("Cookie 有效")
            result = do_signin(sb)
            ok = "成功" in result or "已签" in result

    icon = "✅" if ok else "❌"
    msg = (
        f"VPS8 签到\n"
        f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"AI: {AI_MODEL_NAME}\n"
        f"{icon} {result}\n"
        f"https://vps8.zz.cd/points/signin"
    )
    send_telegram(msg)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"success={'true' if ok else 'false'}\n")
            f.write(f"result={result}\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
