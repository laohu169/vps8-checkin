#!/usr/bin/env python3
"""
VPS8.zz.cd 每日签到 — AI 视觉模型过验证码免费版
──────────────────────────────────────────────────
技术路线: SeleniumBase 浏览器 + AI视觉识别reCAPTCHA图片网格 
多轮验证(通常5-6轮)
──────────────────────────────────────────────────
需要 GitHub Secrets:
  VPS8_EMAIL / VPS8_PASSWORD    — 登录凭证
  AI_API_KEY                    — AI API Key（需支持 vision）
  AI_BASE_URL                   — AI API 地址（OpenAI 兼容）
  AI_MODEL_NAME                 — AI 模型（推荐 gpt-4o / qwen-vl-max / claude-3.5-sonnet）
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

# ─────────────────────────────────────────────
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

# ──────────────────────────────────────────────
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


# ─── 保存截图（调试用）─────────────────────────────────────────
def save_debug_image(base64_str: str, name: str = "captcha") -> str:
    try:
        img_data = base64.b64decode(base64_str)
        path = OUTPUT_DIR / f"{name}.png"
        path.write_bytes(img_data)
        return str(path)
    except Exception:
        return ""


# ─── AI 识别验证码 ─────────────────────────────────────────────
def ai_solve_image_captcha(image_base64: str, question: str, grid_type: str = "grid") -> list:
    """
    AI 视觉模型识别 reCAPTCHA 图片网格。
    返回应点击的图片编号列表（1-based，从左到右、从上到下）。

    流程：
    截图 → base64 → AI API → 解析返回数字 → [1, 4, 7]
    """
    if not AI_API_KEY:
        logger.error(" AI_API_KEY 未配置")
        return []

    # 网格维度
    if grid_type == "grid_3x4":
        rows, cols, max_tile = 4, 3, 12
    else:
        rows, cols, max_tile = 3, 3, 9

    # 编号说明
    numbering = []
    for r in range(rows):
        numbering.append(", ".join(str(r * cols + c + 1) for c in range(cols)))
    numbering_text = "，".join(f"第{r+1}行: {n}" for r, n in enumerate(numbering))

    prompt = (
        f"这是一个 reCAPTCHA 人机验证的图片选择题，网格尺寸为 {rows} 行 × {cols} 列。\n\n"
        f"🔍 验证问题是：「{question}」\n\n"
        f"格子编号规则：\n"
        f"{numbering_text}\n\n"
        f"请仔细观察图片中的每个格子，判断哪些格子包含问题中描述的物体。\n"
        f"**只返回应点击的格子编号**，用逗号分隔，例如：1, 4, 7\n"
        f"不要输出任何其他内容。"
    )

    logger.info(f"🤖 AI 识别: {question[:80]}...")

    # 保存调试截图
    save_debug_image(image_base64, f"captcha_{int(time.time())}")

    try:
        # 压缩图片到合理大小
        img_bytes = base64.b64decode(image_base64)
        img = Image.open(BytesIO(img_bytes))
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        small_b64 = base64.b64encode(buf.getvalue()).decode()

        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL_NAME,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个验证码识别助手。你擅长准确识别 reCAPTCHA 图片网格中哪些格子包含指定的物体。你只返回编号，不返回其他内容。"
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{small_b64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                "max_tokens": 50,
                "temperature": 0.1,
            },
            timeout=60,
        )

        data = r.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        logger.info(f"🤖 返回: {answer}")

        # 解析编号
        nums = [int(n) for n in re.findall(r'\d+', answer)]
        nums = [n for n in nums if 1 <= n <= max_tile]
        logger.info(f"✅ 结果: 点击 {nums}")
        save_debug_image(small_b64, f"captcha_small_{int(time.time())}")
        return nums

    except Exception as e:
        logger.error(f"AI 识别失败: {e}")
        return []


# ─── reCAPTCHA v2 求解主逻辑 ────────────────────────────────────
def solve_recaptcha(sb: "SB") -> bool:
    """
    完整解决 reCAPTCHA v2 图片验证。

    流程:
    1. 切换 reCAPTCHA iframe → 点 checkbox
    2. 等待验证弹窗出现
    3. 切换到验证码挑战 iframe
    4. 截图网格 → AI 识别 → 点格子
    5. 点 Verify
    6. 检查是否有新一轮 → 重复 3-5
    7. 拿到 g-recaptcha-response token

    返回 True 表示拿到 token。
    """
    try:
        sb.sleep(2)

        # Step 1: 切换到 reCAPTCHA checkbox iframe 并点击
        logger.info("寻找 reCAPTCHA checkbox...")
        try:
            sb.switch_to_frame("iframe[title*='recaptcha']")
        except Exception:
            # 备用：通过 src 属性找
            try:
                frames = sb.find_elements("css=iframe[src*='recaptcha']")
                if frames:
                    sb.driver.switch_to.frame(frames[0])
                else:
                    logger.warning("未找到 reCAPTCHA iframe")
                    return False
            except Exception as e:
                logger.warning(f"找 iframe 出错: {e}")
                return False

        try:
            checkbox = sb.find_element("#recaptcha-anchor")
            checkbox.click()
            logger.info("已点击 checkbox")
        except Exception as e:
            logger.warning(f"点击 checkbox 失败: {e}")
            sb.switch_to_default_content()
            return False

        sb.switch_to_default_content()
        sb.sleep(4)  # 等待验证弹窗

        # Step 2: 检查是否直接通过（无需图片）
        token = get_recaptcha_token(sb)
        if token:
            logger.info("验证码直接通过！")
            return True

        # Step 3: 循环处理图片验证（通常 5-6 轮）
        for rnd in range(1, 10):  # 最多 9 轮
            logger.info(f"--- 第 {rnd} 轮验证 ---")

            # 3a. 找到验证码挑战 iframe
            cap_frame = find_captcha_frame(sb)
            if not cap_frame:
                logger.info("验证码弹窗未出现")
                break

            # 3b. 获取验证问题
            question = get_question_text(sb)
            if not question:
                logger.warning("未获取到验证问题")
                break
            logger.info(f"问题: {question}")

            # 3c. 截图验证码网格
            grid_b64 = capture_grid(sb)
            if not grid_b64:
                logger.warning("截图网格失败")
                break

            # 3d. 判断网格类型 & AI 识别
            gtype = detect_grid_type(sb)
            logger.info(f"网格类型: {gtype}")
            nums = ai_solve_image_captcha(grid_b64, question, gtype)
            if not nums:
                logger.warning("AI 未返回有效答案")
                break

            # 3e. 点击对应的格子
            click_tiles(sb, nums)
            sb.sleep(1)

            # 3f. 点击 Verify 按钮
            try:
                sb.find_element("#recaptcha-verify-button").click()
                logger.info("已点击 Verify")
            except Exception as e:
                logger.warning(f"点击 Verify 失败: {e}")

            sb.sleep(4)  # 等待反馈

            # 3g. 检查是否拿到 token
            token = get_recaptcha_token(sb)
            if token:
                logger.info(f"验证通过！token: {token[:30]}...")
                return True

            # 3h. 检查是否出现新一轮验证
            new_frame = find_captcha_frame(sb)
            if not new_frame:
                logger.info("弹窗消失，验证可能通过")
                break

        # 最终检查
        token = get_recaptcha_token(sb)
        if token:
            logger.info(f"最终拿到 token: {token[:30]}...")
            return True
        
        logger.warning("验证未通过（AI 识别可能不够准确）")
        return False

    except Exception as e:
        logger.error(f"验证过程出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ─── 辅助函数 ──────────────────────────────────────────────────
def get_recaptcha_token(sb: "SB") -> str:
    """获取页面中的 g-recaptcha-response 值"""
    try:
        t = sb.execute_script(
            "var el=document.getElementById('g-recaptcha-response');"
            "return el?el.value:'';")
        return t if (t and len(t) > 50) else ""
    except Exception:
        return ""


def find_captcha_frame(sb: "SB"):
    """
    找到 reCAPTCHA 验证码挑战弹窗的 iframe。
    返回 iframe element 或 None。
    """
    try:
        frames = sb.find_elements("css=iframe")
        for f in frames:
            sb.switch_to_default_content()
            try:
                sb.driver.switch_to.frame(f)
                title = (f.get_attribute("title") or "").lower()
                if "recaptcha challenge" in title:
                    logger.info("找到验证码弹窗 iframe")
                    return f
            except Exception:
                continue
        
        # 备用：通过 src 找
        sb.switch_to_default_content()
        frames = sb.find_elements("css=iframe[src*='recaptcha']")
        for f in frames:
            sb.switch_to_default_content()
            try:
                sb.driver.switch_to.frame(f)
                # 检查页面是否有验证码网格
                try:
                    sb.find_element("#rc-imageselect")
                    logger.info("通过 src 属性找到验证码弹窗")
                    return f
                except Exception:
                    pass
            except Exception:
                continue
        
        sb.switch_to_default_content()
        return None
    except Exception as e:
        logger.debug(f"找 iframe 出错: {e}")
        sb.switch_to_default_content()
        return None


def get_question_text(sb: "SB") -> str:
    """获取验证码问题文本"""
    try:
        # 尝试多个可能的选择器
        selectors = [
            ".rc-imageselect-instructions strong",
            ".rc-imageselect-instructions",
            ".rc-doscaptcha-header",
        ]
        for sel in selectors:
            try:
                el = sb.find_element(sel)
                txt = el.text.strip()
                if txt and len(txt) > 3:
                    # 清理文本
                    txt = re.split(r'如果没有|如果没|请点击|Please try again', txt)[0].strip()
                    if txt:
                        return txt
            except Exception:
                continue
    except Exception:
        pass
    return "请识别图片"


def detect_grid_type(sb: "SB") -> str:
    """检测网格类型：3x3 还是 3x4"""
    try:
        tiles = sb.find_elements(".rc-imageselect-tile")
        if len(tiles) == 12:
            return "grid_3x4"
        return "grid"  # 默认 3x3
    except Exception:
        return "grid"


def capture_grid(sb: "SB") -> str:
    """截取验证码网格区域的 base64 图片"""
    try:
        grid_el = sb.find_element("#rc-imageselect")
        return grid_el.screenshot_as_base64
    except Exception:
        try:
            # 兜底：截取整个页面
            return sb.get_screenshot_as_base64()
        except Exception:
            return ""


def click_tiles(sb: "SB", nums: list) -> None:
    """点击验证码网格中指定编号的格子（1-based）"""
    try:
        tiles = sb.find_elements(".rc-imageselect-tile")
        logger.info(f"共 {len(tiles)} 个格子，将点击 {nums}")
        for n in nums:
            i = n - 1  # 转 0-based
            if 0 <= i < len(tiles):
                try:
                    tiles[i].click()
                    logger.info(f"  点击了第 {n} 个")
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning(f"  点击第 {n} 个失败: {e}")
            else:
                logger.warning(f"  第 {n} 个超出范围（共 {len(tiles)} 个）")
    except Exception as e:
        logger.warning(f"点格子时出错: {e}")


# ─── 登录 ─────────────────────────────────────────────────────────
def do_login(sb: "SB") -> bool:
    """FOSSBilling 登录流程"""
    if not VPS8_EMAIL or not VPS8_PASSWORD:
        logger.warning("邮箱/密码未配置")
        return False

    for attempt in range(1, 4):
        logger.info(f"登录尝试 [{attempt}/3]...")
        sb.open(LOGIN_URL)
        sb.sleep(4)

        # 检查是否已登录
        if "/login" not in sb.get_current_url():
            logger.info("已处于登录状态")
            return True

        # 填表单
        try:
            sb.type("#email", VPS8_EMAIL)
            sb.type("#password", VPS8_PASSWORD)
            logger.info("已填表单")
        except Exception as e:
            logger.error(f"填表单失败: {e}")
            return False

        # 解验证码
        if not solve_recaptcha(sb):
            logger.warning(f"验证码失败 ({attempt}/3)")
            continue
        sb.sleep(1)

        # 提交登录
        try:
            sb.click('button[type="submit"]')
            sb.sleep(10)
            logger.info("已点击登录")
        except Exception as e:
            logger.error(f"点击登录失败: {e}")
            return False

        # 检查结果
        cur = sb.get_current_url()
        logger.info(f"当前 URL: {cur}")
        if "/login" not in cur:
            logger.info("登录成功！")
            cookies = sb.get_cookies()
            with open(OUTPUT_DIR / "cookies.json", "w") as f:
                json.dump({"cookies": cookies, "time": datetime.now().isoformat()}, f, indent=2)
            return True

        logger.warning(f"登录失败 ({attempt}/3)，仍在登录页")
        if attempt < 3:
            time.sleep(3)

    logger.error("3 次登录尝试全部失败")
    return False


# ─── 签到 ─────────────────────────────────────────────────────────
def do_signin(sb: "SB") -> str:
    """在已登录状态下完成签到"""
    sb.open(SIGNIN_URL)
    sb.sleep(4)
    src = sb.get_page_source()

    # 检查登录状态
    if "Login to your account" in src:
        return "Cookie 失效"
    if "已签到" in src:
        return "今天已签到过了"

    # 提取 CSRF Token
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "无法提取 CSRF Token"
    
    csrf = m.group(1)
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in sb.get_cookies())

    # 尝试 POST API 签到
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

    # 尝试页面按钮签到
    try:
        logger.info("尝试页面按钮签到...")
        if "recaptcha" in sb.get_page_source().lower():
            solve_recaptcha(sb)
        sb.click("#points-signin-submit")
        sb.sleep(6)
        if "已签到" in sb.get_page_source():
            return "签到成功! (页面按钮)"
    except Exception as e:
        logger.warning(f"按钮签到: {e}")

    return "签到结果不确定"


# ─── 主函数 ─────────────────────────────────────────────────────
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
        # 检查登录状态
        sb.open(SIGNIN_URL)
        sb.sleep(3)
        src = sb.get_page_source()

        if "Login to your account" in src:
            logger.info("未登录，先登录...")
            if do_login(sb):
                result = do_signin(sb)
                ok = result.startswith("签到成功") or result.startswith("已签到")
            else:
                result = "登录失败"
        else:
            logger.info("Cookie 有效，直接签到")
            result = do_signin(sb)
            ok = result.startswith("签到成功") or result.startswith("已签到")

    # 发送通知
    icon = "✅" if ok else "❌"
    msg = (
        f"🦞 <b>VPS8 签到结果</b>\n"
        f"📅 日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"🤖 AI: {AI_MODEL_NAME}\n"
        f"{icon} {result}\n"
        f"🌐 {SIGNIN_URL}"
    )
    send_telegram(msg)

    # GitHub Actions output
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"success={'true' if ok else 'false'}\n")
            f.write(f"result={result}\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
