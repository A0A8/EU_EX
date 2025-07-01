#!/usr/bin/env python3
# SPDX-License-IdentifierText: (c) 2020-2021 CokeMine & Its repository contributors
# SPDX-License-IdentifierText: (c) 2021 A beam of light
# SPDX-License-Identifier: GPL-3.0-or-later

"""
EUserv auto-renew script with captcha solving (ddddocr + OCR.space fallback)
and scheduling logic to run every 20 days.
"""

import os
import re
import time
import base64
import requests
import ddddocr
from bs4 import BeautifulSoup
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP_SSL, SMTPDataError
from datetime import datetime, timedelta

# 配置项（来自环境变量）
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

desp = ""  # 日志汇总
ocr_local = ddddocr.DdddOcr()

def log(info: str):
    print(info)
    global desp
    desp += info + "\n"

def should_run():
    """Controller: only run once every 20 days."""
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

def solve_captcha(image_url: str, session: requests.Session) -> str:
    resp = session.get(image_url, headers={"User-Agent": user_agent})
    img_bytes = resp.content
    try:
        text = ocr_local.classification(img_bytes)
        if text.strip():
            return text.strip()
        raise ValueError("ddddocr 返回空")
    except Exception as e:
        log(f"[Captcha] ddddocr 错误: {e}")
    try:
        b64 = base64.b64encode(img_bytes).decode()
        payload = {
            "apikey": OCR_SPACE_API_KEY,
            "language": "eng",
            "isOverlayRequired": False,
            "base64Image": "data:image/png;base64," + b64
        }
        r = requests.post("https://api.ocr.space/parse/image", data=payload)
        r.raise_for_status()
        parsed = r.json().get("ParsedResults", [{}])[0].get("ParsedText", "")
        return parsed.strip() or "无法识别"
    except Exception as e:
        log(f"[Captcha] OCR.space 错误: {e}")
        return "无法识别"

def login_retry(max_retry=3):
    def deco(func):
        def wrapper(username, password):
            for i in range(max_retry + 1):
                sid, sess = func(username, password)
                if sid != "-1":
                    return sid, sess
                log(f"[Login] 第{i+1}次失败")
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

    data = {
        "email": username, "password": password,
        "form_selected_language": "en", "Submit": "Login",
        "subaction": "login", "sess_id": sid
    }
    f = session.post(url, headers=headers, data=data); f.raise_for_status()
    if "Please solve the following captcha" in f.text:
        log("[Captcha] 开始识别")
        code = solve_captcha(captcha_url, session)
        log(f"[Captcha] 识别结果: {code}")
        f2 = session.post(url, headers=headers, data={
            "subaction": "login", "sess_id": sid, "captcha_code": code
        })
        if "Please solve the following captcha" not in f2.text:
            log("[Login] 验证通过"); return sid, session
        log("[Login] 验证失败"); return "-1", session
    log("[Login] 登录成功，无需验证码"); return sid, session

def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    r = session.get(url, headers={"User-Agent": user_agent})
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"):
        sid_ = tr.select(".td-z1-sp1-kc")
        if len(sid_) != 1: continue
        flag = "Contract extension possible from" not in tr.get_text()
        servers[sid_[0].get_text()] = flag
    return servers

def get_pin_from_mailparser(url_id):
    r = requests.get(f"{MAILPARSER_DOWNLOAD_BASE_URL}{url_id}")
    return r.json()[0]["pin"]

def renew(sess_id, session, pwd, order_id, mail_id):
    base = "https://support.euserv.com/index.iphp"
    session.post(base, headers={"User-Agent": user_agent}, data={
        "Submit":"Extend contract","sess_id":sess_id,
        "ord_no":order_id,"subaction":"choose_order",
        "choose_order_subaction":"show_contract_details"
    })
    session.post(base, headers={"User-Agent": user_agent}, data={
        "sess_id":sess_id,"subaction":"show_kc2_security_password_dialog",
        "prefix":"kc2_customer_contract_details_extend_contract_","type":"1"
    })
    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(mail_id)
    log(f"[MailParser] PIN: {pin}")
    resp = session.post(base, headers={"User-Agent": user_agent}, data={
        "auth":pin,"sess_id":sess_id,
        "subaction":"kc2_security_password_get_token",
        "prefix":"kc2_customer_contract_details_extend_contract_","type":1,
        "ident":f"kc2_customer_contract_details_extend_contract_{order_id}"
    })
    token = resp.json().get("token",{}).get("value","")
    if not token: return False
    session.post(base, headers={"User-Agent": user_agent}, data={
        "sess_id":sess_id,"ord_id":order_id,
        "subaction":"kc2_customer_contract_details_extend_contract_term",
        "token":token
    })
    time.sleep(5); return True

def check(sess_id, session):
    all_ok = True
    for sid_, need in get_servers(sess_id, session).items():
        if need:
            log(f"[EUserv] {sid_} 续费失败！"); all_ok = False
    if all_ok: log("[EUserv] 全部续费完成！")

def telegram():
    data = (("chat_id", TG_USER_ID), ("text", "EUserv 日志\n\n" + desp))
    response = requests.post(
        f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage",
        data=data
    )
    if response.status_code != 200:
        print("Telegram 推送失败")
    else:
        print("Telegram 推送成功")

def send_mail_by_yandex(to_email, from_email, subject, text, files, sender_email, sender_password):
    msg = MIMEMultipart(); msg["Subject"]=subject; msg["From"]=from_email; msg["To"]=to_email
    msg.attach(MIMEText(text, _charset="utf-8"))
    s = SMTP_SSL("smtp.yandex.ru",465); s.login(sender_email, sender_password)
    s.sendmail(from_email,to_email,msg.as_string()); s.quit()

def email():
    try:
        send_mail_by_yandex(RECEIVER_EMAIL,YD_EMAIL,"EUserv 日志",desp,None,YD_EMAIL,YD_APP_PWD)
        print("Email 推送成功")
    except Exception as e:
        print(f"Email 推送失败: {e}")

if __name__ == "__main__":
    if not should_run():
        exit(0)
    if not USERNAME or not PASSWORD or not MAILPARSER_DOWNLOAD_URL_ID:
        log("[EUserv] 配置缺失，退出")
        exit(1)
    users = USERNAME.split()
    pwds = PASSWORD.split()
    ids = MAILPARSER_DOWNLOAD_URL_ID.split()
    if not (len(users)==len(pwds)==len(ids)):
        log("[EUserv] 配置数量不匹配，退出"); exit(1)
    for u,p,i in zip(users,pwds,ids):
        log(f"[Main] 处理账户: {u}")
        sid, sess = login(u, p)
        if sid == "-1": continue
        for sid2, flag in get_servers(sid, sess).items():
            if flag:
                if renew(sid, sess, p, sid2, i):
                    log(f"[EUserv] {sid2} 续费成功")
                else:
                    log(f"[EUserv] {sid2} 续费失败")
            else:
                log(f"[EUserv] {sid2} 不需续费")
        check(sid, sess)
        time.sleep(5)
    if TG_BOT_TOKEN and TG_USER_ID: telegram()
    if RECEIVER_EMAIL and YD_EMAIL and YD_APP_PWD: email()
