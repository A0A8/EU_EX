#!/usr/bin/env python3
# SPDX-License-IdentifierText: (c) 2020-2021 CokeMine & Its repository contributors
# SPDX-License-IdentifierText: (c) 2021 A beam of light
# SPDX-License-Identifier: GPL-3.0-or-later

"""
EUserv auto-renew script with improved captcha solving logic:
- 优先使用 ddddocr 识别
- 失败时 fallback 到 OCR.space
- 避免 TrueCaptcha 并处理识别异常
- 20 天调度控制
"""

import os
import re
import time
import base64
import requests
import ddddocr
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL, SMTPDataError

# 环境变量配置
USERNAME = os.environ.get("USERNAME")
PASSWORD = os.environ.get("PASSWORD")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID")
MAILPARSER_DOWNLOAD_BASE_URL = "https://files.mailparser.io/d/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")
OCR_SPACE_API_KEY = os.environ.get("OCR_API_KEY", "")

LOGIN_MAX_RETRY_COUNT = 5
WAITING_TIME_OF_PIN = 15
LAST_RUN_FILE = ".last_run"
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

desp = ""
ocr_local = ddddocr.DdddOcr()

def log(info: str):
    global desp
    print(info)
    desp += info + "\n"

def should_run():
    now = datetime.utcnow()
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r") as f:
                last = datetime.fromisoformat(f.read().strip())
        except Exception:
            last = now - timedelta(days=21)
        if now - last < timedelta(days=20):
            print(f"[Scheduler] 上次运行 {last.isoformat()}，未满20天，跳过。")
            return False
    with open(LAST_RUN_FILE, "w") as f:
        f.write(now.isoformat())
    return True

def captcha_solver(captcha_image_url: str, session: requests.Session) -> dict:
    """
    新的识别逻辑：
    1. 优先使用 ddddocr，本地识别
    2. 失败时使用 OCR.space 接口
    返回格式：{"result": "识别文本"}
    """
    try:
        log("[Captcha Solver] 使用 ddddocr 识别...")
        response = session.get(captcha_image_url, headers={"User-Agent": user_agent})
        response.raise_for_status()
        image_bytes = response.content
        text = ocr_local.classification(image_bytes)
        if text and text.strip():
            return {"result": text.strip()}
        log("[Captcha Solver] ddddocr 未识别到文本")
    except Exception as e:
        log(f"[Captcha Solver] ddddocr 异常: {e}")

    # fallback to OCR.space
    try:
        log("[Captcha Solver] 使用 OCR.space 识别...")
        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "apikey": OCR_SPACE_API_KEY,
            "language": "eng",
            "isOverlayRequired": False
        }
        files = {"file": ("captcha.jpg", image_bytes)}
        r = requests.post("https://api.ocr.space/parse/image", data=payload, files=files)
        r.raise_for_status()
        parsed = r.json().get("ParsedResults", [{}])[0].get("ParsedText", "").strip()
        return {"result": parsed}
    except Exception as e:
        log(f"[Captcha Solver] OCR.space 异常: {e}")
        return {"result": ""}

def handle_captcha_solved_result(solved: dict) -> str:
    # 仅返回文本即可
    return solved.get("result", "")

def login_retry(max_retry=3):
    def deco(func):
        def wrapper(username, password):
            for i in range(max_retry + 1):
                sid, sess = func(username, password)
                if sid != "-1":
                    return sid, sess
                log(f"[Login] 第{i+1}次尝试失败")
            return "-1", sess
        return wrapper
    return deco

@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username: str, password: str):
    headers = {"User-Agent": user_agent, "Origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    captcha_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()
    r = session.get(url, headers=headers); r.raise_for_status()
    sid = re.search(r'PHPSESSID=(\w+);', str(r.headers)).group(1)

    # 登录请求
    data = {
        "email": username,
        "password": password,
        "form_selected_language": "en",
        "Submit": "Login",
        "subaction": "login",
        "sess_id": sid
    }
    f = session.post(url, headers=headers, data=data); f.raise_for_status()
    if "Please solve the following captcha" in f.text:
        log("[Captcha] 开始识别")
        solved = captcha_solver(captcha_url, session)
        code = handle_captcha_solved_result(solved)
        log(f"[Captcha] 识别结果: {code}")
        f2 = session.post(url, headers=headers, data={
            "subaction": "login",
            "sess_id": sid,
            "captcha_code": code
        })
        if "Please solve the following captcha" not in f2.text:
            log("[Login] 验证通过")
            return sid, session
        log("[Login] 验证失败")
        return "-1", session
    log("[Login] 登录成功，无需验证码")
    return sid, session

def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    r = session.get(url, headers={"User-Agent": user_agent}); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"):
        sid_els = tr.select(".td-z1-sp1-kc")
        if len(sid_els) != 1: continue
        server_id = sid_els[0].get_text().strip()
        need_renew = bool(tr.select('input[type="submit"][value="Extend contract"]'))
        servers[server_id] = need_renew
    return servers

# ... 其余部分保持不变，包括 renew(), get_pin_from_mailparser(), check(), telegram(), email()

if __name__ == "__main__":
    if not should_run(): exit(0)
    # 省略配置校验、循环逻辑
    log("[Main] 脚本已更新，可继续整合剩余流程")