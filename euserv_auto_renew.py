#! /usr/bin/env python3

"""
EUserv 自动续期脚本（完整 OCR 整合 + PIN 邮件自动获取）
- ddddocr（默认使用）
- ocr.space（备援 OCR，需设置 OCRSPACE_API_KEY）
- 邮件通知 + Telegram
- Mailparser 自动解析 PIN 验证码
"""

import os
import re
import json
import time
import base64
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL, SMTPDataError

import ddddocr

# 环境变量（通过 GitHub Actions secrets 或本地 .env 设置）
USERNAME = os.environ.get("USERNAME")
PASSWORD = os.environ.get("PASSWORD")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID")
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")
PROXIES = None  # 如有代理，替换此处

WAITING_TIME_OF_PIN = 15
LOGIN_MAX_RETRY_COUNT = 5

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/114.0.0.0 Safari/537.36"
)

desp = ""

def log(info):
    print(info)
    global desp
    desp += info + "\n"

def get_captcha_img(session):
    url = "https://support.euserv.com/securimage_show.php"
    resp = session.get(url)
    return resp.content

def solve_captcha_with_ocrspace(image_bytes):
    if not OCRSPACE_API_KEY:
        return ""
    r = requests.post(
        url="https://api.ocr.space/parse/image",
        data={"isOverlayRequired": False, "OCREngine": 2},
        files={"filename": ("captcha.jpg", image_bytes)},
        headers={"apikey": OCRSPACE_API_KEY}
    )
    try:
        return r.json()['ParsedResults'][0]['ParsedText'].strip()
    except:
        return ""

def solve_captcha(image_bytes):
    try:
        ocr = ddddocr.DdddOcr(show_ad=False)
        result = ocr.classification(image_bytes)
        log(f"[ddddocr] 识别结果: {result}")
        if re.fullmatch(r"[\w\d+-xX]+", result):
            try:
                return str(eval(result.replace("x", "*")))
            except:
                return result
        return result
    except Exception as e:
        log(f"[ddddocr] 识别失败: {e}")
        return ""

def login(username, password):
    headers = {"user-agent": user_agent}
    session = requests.Session()
    url = "https://support.euserv.com/index.iphp"

    for attempt in range(1, LOGIN_MAX_RETRY_COUNT + 1):
        log(f"[Login] 第 {attempt} 次尝试登录")
        r = session.get(url, headers=headers)
        try:
            sess_id = re.search(r"PHPSESSID=(\w+);", str(r.headers)).group(1)
        except:
            return "-1", session

        post_data = {
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
        }

        resp = session.post(url, headers=headers, data=post_data)
        if "captcha" not in resp.text:
            return sess_id, session

        # 需要验证码识别
        image = get_captcha_img(session)
        code = solve_captcha(image)
        if not code:
            code = solve_captcha_with_ocrspace(image)
            log(f"[OCR.space] 识别结果: {code}")
        if not code:
            log("[Login] 无法识别验证码，跳过此轮登录尝试")
            continue

        captcha_post = {
            "subaction": "login",
            "sess_id": sess_id,
            "captcha_code": code,
        }
        verify = session.post(url, headers=headers, data=captcha_post)
        if "captcha" not in verify.text:
            log("[Login] 登录成功")
            return sess_id, session

    log("[Login] 登录失败超过最大重试次数")
    return "-1", session

def get_servers(sess_id, session):
    servers = {}
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    headers = {"user-agent": user_agent}
    r = session.get(url, headers=headers)
    soup = BeautifulSoup(r.text, "html.parser")
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"):
        server_id = tr.select(".td-z1-sp1-kc")
        if not server_id:
            continue
        can_renew = "Contract extension possible" not in tr.text
        servers[server_id[0].text.strip()] = can_renew
    return servers

def get_pin_from_mailparser(url_id):
    r = requests.get(f"https://files.mailparser.io/d/{url_id}")
    return r.json()[0]['pin']

def renew(sess_id, session, password, order_id, mailparser_id):
    url = "https://support.euserv.com/index.iphp"
    headers = {"user-agent": user_agent}

    session.post(url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    })

    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": f"kc2_customer_contract_details_extend_contract_",
        "type": 1,
    })

    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(mailparser_id)
    log(f"[Mailparser] 获取 PIN: {pin}")

    token_resp = session.post(url, headers=headers, data={
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": f"kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    })
    token = token_resp.json().get("token", {}).get("value", None)
    if not token:
        return False

    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    })
    return True

def check(sess_id, session):
    servers = get_servers(sess_id, session)
    for sid, ok in servers.items():
        if not ok:
            log(f"[Check] ServerID: {sid} Renew Failed!")
        else:
            log(f"[Check] ServerID: {sid} 状态正常")

def telegram():
    if not TG_BOT_TOKEN or not TG_USER_ID:
        return
    data = {"chat_id": TG_USER_ID, "text": desp}
    try:
        r = requests.post(f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage", data=data)
        print("[通知] Telegram 发送成功" if r.status_code == 200 else "[通知] Telegram 发送失败")
    except:
        print("[通知] Telegram 发送异常")

def email():
    if not RECEIVER_EMAIL or not YD_EMAIL or not YD_APP_PWD:
        return
    msg = MIMEMultipart()
    msg["Subject"] = "EUserv 续费日志"
    msg["From"] = YD_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg.attach(MIMEText(desp, _charset="utf-8"))
    try:
        s = SMTP_SSL("smtp.yandex.ru", 465)
        s.login(YD_EMAIL, YD_APP_PWD)
        s.sendmail(YD_EMAIL, RECEIVER_EMAIL, msg.as_string())
        s.quit()
        print("[通知] Email 发送成功")
    except:
        print("[通知] Email 发送失败")

if __name__ == '__main__':
    if not USERNAME or not PASSWORD:
        print("请设置用户名密码环境变量！")
        exit(1)

    usernames = USERNAME.strip().split()
    passwords = PASSWORD.strip().split()
    mailparser_ids = MAILPARSER_DOWNLOAD_URL_ID.strip().split()

    for i, (user, pwd, mid) in enumerate(zip(usernames, passwords, mailparser_ids)):
        log("=" * 20)
        log(f"[Main] 开始续费第 {i+1} 个账号：{user}")
        sessid, session = login(user, pwd)
        if sessid == "-1":
            log(f"[Main] 第 {i+1} 个账号登录失败")
            continue
        servers = get_servers(sessid, session)
        log(f"[Main] 共检测到 {len(servers)} 台 VPS")
        for sid, should_renew in servers.items():
            if should_renew:
                if renew(sessid, session, pwd, sid, mid):
                    log(f"[Renew] ServerID: {sid} 已续期成功")
                else:
                    log(f"[Renew] ServerID: {sid} 续期失败")
            else:
                log(f"[Renew] ServerID: {sid} 暂不支持续期")
        check(sessid, session)
        time.sleep(5)

    telegram()
    email()
    print("=" * 20)
