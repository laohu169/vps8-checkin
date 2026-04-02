#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到 — AI 识图过 reCAPTCHA 免费版
技术路线: SeleniumBase 浏览器 + AI 视觉模型识别验证码图片网格
需要 GitHub Secrets:
  VPS8_EMAIL / VPS8_PASSWORD    — 登录凭证
  AI_API_KEY                    — AI API Key（需支持 vision）
  AI_BASE_URL                   — AI API 地址
  AI_MODEL_NAME                 — AI 模型（推荐 gpt-4o / qwen-vl-max）
  TELEGRAM_BOT_TOKEN / MY_CHAT_ID  — 通知（可选）
"""

import os, sys, time, json, re
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
from PIL import Image
from seleniumbase import SB
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
            json={"chat_id": MY_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram: {e}")


# ─── 保存截图到文件（调试用）────────────────────────────────────
def save_debug_image(base64_str: str, name: str = "captcha") -> str:
    try:
        img_data = base64.b64decode(base64_str)
        path = OUTPUT_DIR / f"{name}.png"
        path.write_bytes(img_data)
        return str(path)
    except Exception:
        return ""


# ─── AI 识别验证码图片 ────────────────────────────────────────────
def ai_solve_image_captcha(image_base64: str, question: str, grid_type: str = "grid") -> list:
    """
    用 AI 视觉模型识别验证码图片，返回应点击的图片编号列表（1-based）。
    """
    if not AI_API_KEY:
        logger.error("AI_API_KEY 未配置")
        return []

    if grid_type == "grid":
        grid_rows = 3
        grid_cols = 3
        max_tile = 9
        layout_text = "第一行 1,2,3，第二行 4,5,6，第三行 7,8,9"
    elif grid_type == "grid_3x4":
        grid_rows = 4
        grid_cols = 3
        max_tile = 12
        layout_text = "第一行 1,2,3，第二行 4,5,6，第三行 7,8,9，第四行 10,11,12"
    else:
        max_tile = 16
        layout_text = "从左到右、从上到下编号"

    prompt = (
        f"这是一道 reCAPTCHA 人机验证的图片选择题。\n"
        f"验证问题是：**{question}**\n\n"
        f"图片是一个 {grid_rows}x{grid_cols} 的网格。\n"
        f"格子编号规则：{layout_text}\n\n"
        f"请：\n"
        f"1. 找出问题中描述的物体出现在哪些编号的格子里\n"
        f"2. **只返回编号列表**，逗号分隔，例如：1, 4, 7\n"
        f"如果没有符合的，返回空。\n"
        f"不要返回其他内容。"
    )

    logger.info(f"AI 识别验证码... 问题: {question}")

    try:
        # 压缩图片
        img_bytes = base64.b64decode(image_base64)
        img = Image.open(BytesIO(img_bytes))
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        small_b64 = base64.b64encode(buf.getvalue()).decode()

        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{small_b64}",
                            "detail": "high"}}]}],
                "max_tokens": 50,
                "temperature": 0.1,
            },
            timeout=60,
        )

        data = r.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        logger.info(f"AI 返回: {answer}")

        nums = [int(n) for n in re.findall(r'\d+', answer)]
        nums = [n for n in nums if 1 <= n <= max_tile]
        logger.info(f"解析结果: {nums}")
        return nums

    except Exception as e:
        logger.error(f"AI 识别失败: {e}")
        return []


# ─── reCAPTCHA v2 求解 ──────────────────────────────────────────
def solve_recaptcha(sb: "SB") -> bool:
    """
    完整解决 reCAPTCHA v2 图片验证。
    返回 True 表示拿到 token。
    """
    try:
        sb.sleep(2)

        # 1. 切换到 reCAPTCHA iframe 并点击 checkbox
        logger.info("查找 reCAPTCHA...")
        try:
            sb.switch_to_frame("iframe[title*='recaptcha']")
        except Exception:
            logger.warning("未找到 reCAPTCHA iframe")
            return False

        try:
            sb.find_element("#recaptcha-anchor").click()
            logger.info("已点击 checkbox")
        except Exception as e:
            logger.warning(f"点 checkbox 失败: {e}")
            return False
        sb.switch_to_default_content()
        sb.sleep(4)

        # 2. 检查是否直接通过
        token = get_recaptcha_token(sb)
        if token:
            logger.info("验证码直接通过！")
            return True

        # 3. 循环处理图片验证轮
        for rnd in range(1, 8):
            logger.info(f"--- 第 {rnd} 轮 ---")

            # 找验证码弹窗 iframe
            cap_frame = find_captcha_frame(sb)
            if not cap_frame:
                logger.info("弹窗未出现，可能已通过")
                break

            # 获取问题
            question = get_question_text(sb)
            logger.info(f"问题: {question}")

            # 截图网格
            grid_b64 = capture_grid(sb)
            if not grid_b64:
                logger.warning("截图失败")
                break

            # 判断类型 & 识别
            gtype = detect_grid_type(sb)
            nums = ai_solve_image_captcha(grid_b64, question, gtype)
            if not nums:
                logger.warning("AI 未返回答案")
                break

            # 点击格子
            click_tiles(sb, nums)
            sb.sleep(1)

            # 点 Verify
            try:
                sb.find_element("#recaptcha-verify-button").click()
                logger.info("已点 Verify")
            except Exception as e:
                logger.warning(f"点 Verify 失败: {e}")

            sb.sleep(4)

            token = get_recaptcha_token(sb)
            if token:
                logger.info(f"拿到 token: {token[:30]}...")
                return True

            new_frame = find_captcha_frame(sb)
            if not new_frame:
                logger.info("弹窗消失")
                break

        token = get_recaptcha_token(sb)
        if token:
            logger.info(f"最终 token: {token[:30]}...")
            return True
        logger.warning("验证码未通过")
        return False

    except Exception as e:
        logger.error(f"验证码过程出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ─── 辅助函数 ──────────────────────────────────────────────────
def get_recaptcha_token(sb: "SB") -> str:
    try:
        t = sb.execute_script(
            "var el=document.getElementById('g-recaptcha-response');"
            "return el?el.value:'';")
        return t if (t and len(t) > 50) else ""
    except Exception:
        return ""


def find_captcha_frame(sb: "SB"):
    try:
        frames = sb.find_elements("css=iframe")
        for f in frames:
            sb.switch_to_default_content()
            try:
                sb.driver.switch_to.frame(f)
                title = (f.get_attribute("title") or "").lower()
                if "recaptcha challenge" in title:
                    return f
            except Exception:
                continue
        sb.switch_to_default_content()
        return None
    except Exception:
        sb.switch_to_default_content()
        return None


def get_question_text(sb: "SB") -> str:
    try:
        el = sb.find_element(".rc-imageselect-instructions")
        txt = el.text.strip()
        txt = re.split(r'如果没有|如果没', txt)[0].strip()
        if txt:
            return txt
    except Exception:
        try:
            el = sb.find_element(".rc-doscaptcha-header")
            if el.text.strip():
                return el.text.strip()
        except Exception:
            pass
    return "请识别图片"


def detect_grid_type(sb: "SB") -> str:
    try:
        tiles = sb.find_elements(".rc-imageselect-tile")
        if len(tiles) == 12:
            return "grid_3x4"
        return "grid"
    except Exception:
        return "grid"


def capture_grid(sb: "SB") -> str:
    try:
        return sb.find_element("#rc-imageselect").screenshot_as_base64
    except Exception:
        try:
            return sb.get_screenshot_as_base64()
        except Exception:
            return ""


def click_tiles(sb: "SB", nums: list) -> None:
    tiles = sb.find_elements(".rc-imageselect-tile")
    logger.info(f"共 {len(tiles)} 个格，点击 {nums}")
    for n in nums:
        i = n - 1
        if 0 <= i < len(tiles):
            try:
                tiles[i].click()
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"点击 {n} 失败: {e}")


# ─── 登录 ─────────────────────────────────────────────────────────
def do_login(sb: "SB") -> bool:
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("邮箱/密码未配置")
        return False

    for attempt in range(1, 4):
        logger.info(f"登录尝试 [{attempt}/3]...")
        sb.open(LOGIN_URL)
        sb.sleep(4)

        if "/login" not in sb.get_current_url():
            return True

        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            logger.info("已填表单")
        except Exception as e:
            logger.error(f"填表单: {e}")
            return False

        if not solve_recaptcha(sb):
            logger.warning(f"验证码失败 {attempt}/3")
            continue
        sb.sleep(1)

        try:
            sb.click('button[type="submit"]')
            sb.sleep(10)
        except Exception as e:
            logger.error(f"点击登录: {e}")
            return False

        cur = sb.get_current_url()
        logger.info(f"URL: {cur}")
        if "/login" not in cur:
            with open(OUTPUT_DIR / "cookies.json", "w") as f:
                json.dump({"cookies": sb.get_cookies(), "time": datetime.now().isoformat()}, f, indent=2)
            logger.info("登录成功！")
            return True
        logger.warning(f"失败 ({attempt}/3)")
        if attempt < 3:
            time.sleep(3)

    logger.error("3 次登录均失败")
    return False


# ─── 签到 ─────────────────────────────────────────────────────────
def do_signin(sb: "SB") -> str:
    sb.open(SSIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()

    if "Login to your account" in src:
        return "Cookie 失效"
    if "已签到" in src:
        return "今天已签到过了"

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
                    return f"API 错误: {msg}"
                if "result" in j and j["result"] is not None:
                    return f"签到成功! {json.dumps(j['result'], ensure_ascii=False)[:200]}"
            except Exception:
                if "签到成功" in resp.text or "已签到" in resp.text:
                    return "签到成功!"
    except Exception as e:
        logger.warning(f"API 签到: {e}")

    try:
        logger.info("尝试页面按钮...")
        if "recaptcha" in sb.get_page_source().lower():
            solve_recaptcha(sb)
        sb.click("#points-signin-submit")
        sb.sleep(6)
        if "已签到" in sb.get_page_source():
            return "签到成功! (页面按钮)"
    except Exception as e:
        logger.warning(f"按钮: {e}")

    return "结果不确定"


# ─── 主函数 ─────────────────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info("VPS8 每日签到")
    logger.info(f"目标: {BASE_URL}")
    logger.info(f"AI: {AI_MODEL_NAME}")
    logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

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
                ok = True if result.startswith("签到成功") or result.startswith("已签到") else False
            else:
                result = "登录失败"
        else:
            logger.info("Cookie 有效")
            result = do_signin(sb)
            ok = True if result.startswith("签到成功") or result.startswith("已签到") else False

    # 通知
    icon = "✅" if ok else "❌"
    msg = (
        f"VPS8 签到结果\n"
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
