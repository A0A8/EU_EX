#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from bs4 import BeautifulSoup
import ddddocr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from smtplib import SMTP_SSL

# —— 配置区 —— #
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")

USE_PROXY = False
PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"} if USE_PROXY else {}

LOGIN_RETRIES = 5
PIN_WAIT = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/95.0.4638.69 Safari/537.36"

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

# —— HTTP 封装 —— #
def get(url, **kwargs):
    kwargs.setdefault("headers", {"User-Agent": USER_AGENT})
    kwargs.setdefault("proxies", PROXIES)
    return requests.get(url, **kwargs)

def post(url, **kwargs):
    kwargs.setdefault("headers", {"User-Agent": USER_AGENT})
    kwargs.setdefault("proxies", PROXIES)
    return requests.post(url, **kwargs)

# —— CAPTCHA 识别 —— #
def solve_captcha(session):
    img = get("https://support.euserv.com/securimage_show.php", session=session).content
    # 1) ddddocr
    ocr = ddddocr.DdddOcr()
    res = ocr.classification(img)
    log(f"[ddddocr] {res}")
    if re.fullmatch(r"[0-9+\-*/xX]{1,7}", res.replace(" ", "")):
        return str(eval(res.lower().replace("x", "*")))
    # 2) ocr.space
    if OCRSPACE_API_KEY:
        r = post("https://api.ocr.space/parse/image",
                 data={"apikey": OCRSPACE_API_KEY, "language": "eng"},
                 files={"file": ("captcha.png", img)})
        j = r.json()
        text = j.get("ParsedResults", [{}])[0].get("ParsedText", "").strip()
        log(f"[OCR.space] {text}")
        if re.fullmatch(r"[0-9+\-*/xX]{1,7}", text.replace(" ", "")):
            return str(eval(text.lower().replace("x", "*")))
    return ""

# —— 登录 —— #
def login(user, pwd):
    base = "https://support.euserv.com/index.iphp"
    session = requests.Session()
    for i in range(1, LOGIN_RETRIES+1):
        log(f"[Login] 尝试 {i}")
        r0 = session.get(base)
        soup = BeautifulSoup(r0.text, "html.parser")
        inp = soup.find("input", {"name": "sess_id"})
        if not inp or not inp.get("value"):
            log("[Login] 获取 sess_id 失败")
            continue
        sid = inp["value"]
        login_url = f"{base}?sess_id={sid}"

        data = {
            "email": user, "password": pwd,
            "form_selected_language": "en", "Submit": "Login",
            "subaction": "login", "sess_id": sid
        }
        resp = session.post(login_url, data=data)
        snippet = resp.text[:200].replace("\n"," ")
        log(f"[Login] {snippet}")

        if "Hello" in resp.text:
            log("[Login] 成功")
            return sid, session

        if "solve the following captcha" in resp.text:
            code = solve_captcha(session)
            log(f"[Login] CAPTCHA: {code}")
            if not code:
                continue
            data["captcha_code"] = code
            resp2 = session.post(login_url, data=data)
            if "Hello" in resp2.text:
                log("[Login] CAPTCHA 验证通过")
                return sid, session

    log("[Login] 失败")
    return None, None

# —— 获取 VPS 列表 —— #
def get_servers(sid, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sid}"
    r = session.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    for tr in soup.select(".kc2_content_table tr"):
        cols = tr.find_all("td")
        if len(cols) >= 2:
            vid = cols[0].text.strip()
            ok = "Contract extension possible" not in cols[-1].text
            servers[vid] = ok
    return servers

# —— 获取 PIN —— #
def get_pin(mail_id):
    r = get(f"https://files.mailparser.io/d/{mail_id}")
    data = r.json()
    pin = data[0].get("pin","") if data else ""
    log(f"[PIN] {pin}")
    return pin

# —— 续费 —— #
def renew(sid, session, vid, mail_id):
    base = "https://support.euserv.com/index.iphp"
    headers = {"Referer": f"{base}?sess_id={sid}"}

    # 选择
    post(base, headers=headers, data={
        "Submit":"Extend contract","sess_id":sid,
        "ord_no":vid,"subaction":"choose_order",
        "choose_order_subaction":"show_contract_details"
    })
    prefix = f"kc2_customer_contract_details_extend_contract_{vid}"
    # PIN Dialog + resend
    post(base, headers=headers, data={"sess_id":sid,"subaction":"show_kc2_security_password_dialog","prefix":prefix,"type":1})
    post(base, headers=headers, data={"sess_id":sid,"subaction":"kc2_security_password_send_pin","ident":prefix})

    # 等待取 PIN
    pin=""
    for _ in range(5):
        time.sleep(PIN_WAIT)
        pin = get_pin(mail_id)
        if re.fullmatch(r"\d{6}", pin): break
    if not re.fullmatch(r"\d{6}", pin):
        log("[Renew] PIN 无效"); return False

    # Token
    rtok = post(base, headers=headers, data={
        "auth":pin,"sess_id":sid,"subaction":"kc2_security_password_get_token",
        "prefix":prefix,"type":1,"ident":prefix
    })
    j=rtok.json(); token=j.get("token",{}).get("value","")
    if not token: log(f"[Renew] token 失败 {rtok.text[:200]}"); return False

    # 最终续费
    final=post(base, headers=headers, data={"sess_id":sid,"ord_id":vid,"subaction":"kc2_customer_contract_details_extend_contract_term","token":token})
    txt=final.text.lower()
    ok = "extended" in txt
    log(f"[Renew] {vid} {'成功' if ok else '失败'}")
    return ok

# —— 主流程 —— #
def main():
    if not (USERNAME and PASSWORD and MAILPARSER_DOWNLOAD_URL_ID):
        log("请设置 USERNAME/PASSWORD/MAILPARSER_DOWNLOAD_URL_ID")
        return

    users, pwds = USERNAME.split(), PASSWORD.split()
    mail_ids = MAILPARSER_DOWNLOAD_URL_ID.split()
    for u,p,mid in zip(users,pwds,mail_ids):
        log("="*20)
        sid, sess = login(u,p)
        if not sid: continue
        svs = get_servers(sid, sess)
        log(f"[Main] 找到 {len(svs)} 台 VPS")
        for vid,need in svs.items():
            if need: renew(sid, sess, vid, mid)
            else: log(f"[Skip] {vid}")
    # 通知可加在这里
    log("完成")

if __name__=="__main__":
    main()
