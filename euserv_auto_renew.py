#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import base64
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL, SMTPDataError
import ddddocr

# 配置区域
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")

USE_PROXY = False
PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"} if USE_PROXY else {}

# 常量
LOGIN_MAX_RETRY_COUNT = 5
WAITING_TIME_OF_PIN = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
)

# 日志收集
logs = []
def log(msg):
    print(msg)
    logs.append(msg)

# OCR 识别

def ocr_space_recognize(image_bytes):
    if not OCRSPACE_API_KEY:
        return ""
    url = "https://api.ocr.space/parse/image"
    payload = {
        'apikey': OCRSPACE_API_KEY,
        'language': 'eng',
        'isOverlayRequired': False
    }
    files = {'file': ('captcha.png', image_bytes)}
    r = requests.post(url, data=payload, files=files, proxies=PROXIES)
    try:
        result = r.json()
        return result.get('ParsedResults', [{}])[0].get('ParsedText', '').strip()
    except:
        return ""


def ddddocr_recognize(image_bytes):
    ocr = ddddocr.DdddOcr()
    try:
        return ocr.classification(image_bytes)
    except:
        return ""


def validate_captcha(text):
    t = text.replace(" ", "")
    return bool(re.match(r'^[0-9+\-*/xX]+$', t)) and len(t) <= 7


def calculate_captcha(text):
    expr = text.lower().replace('x', '*')
    try:
        return str(int(eval(expr)))
    except:
        return text


def solve_captcha(session):
    img_url = "https://support.euserv.com/securimage_show.php"
    img_bytes = session.get(img_url, headers={"User-Agent": USER_AGENT}, proxies=PROXIES).content
    # ddddocr
    res = ddddocr_recognize(img_bytes)
    log(f"[ddddocr] 识别: {res}")
    if validate_captcha(res):
        val = calculate_captcha(res)
        log(f"[ddddocr] 计算: {val}")
        return val
    # ocr.space
    res2 = ocr_space_recognize(img_bytes)
    log(f"[OCR.space] 识别: {res2}")
    if validate_captcha(res2):
        val2 = calculate_captcha(res2)
        log(f"[OCR.space] 计算: {val2}")
        return val2
    return ""

# Mailparser PIN 获取

def get_pin_from_mailparser(url_id):
    r = requests.get(f"https://files.mailparser.io/d/{url_id}", proxies=PROXIES)
    r.raise_for_status()
    data = r.json()
    log(f"[Mailparser] 原始数据: {data}")
    pin = data[0].get('pin', '') if data else ''
    return pin

# 登录函数

def login(username, password):
    base_url = "https://support.euserv.com/index.iphp"
    session = requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": base_url,
    }

    for attempt in range(1, LOGIN_MAX_RETRY_COUNT + 1):
        log(f"[Login] 第 {attempt} 次尝试登录")

        # 1) 获取初始页面并提取 sess_id
        r0 = session.get(base_url, headers=headers, proxies=PROXIES)
        sid_match = re.search(r"PHPSESSID=(\\w+);", str(r0.headers))
        if not sid_match:
            log("[Login] 无法获取 sess_id")
            continue
        sess_id = sid_match.group(1)

        # 2) 构造带 sess_id 的登录 URL
        login_url = f"{base_url}?sess_id={sess_id}"

        # 3) 提交第一次登录请求
        data = {
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
        }
        resp = session.post(login_url, headers=headers, data=data, proxies=PROXIES)

        snippet = resp.text[:500].replace("\n", " ")
        log(f"[Login] 响应: {snippet}")

        # 4) 判断登录成功
        if "Hello" in resp.text or "Confirm or change your customer data here" in resp.text:
            log("[Login] 登录成功")
            return sess_id, session

        # 5) 如果检测到需要验证码
        if "solve the following captcha" in resp.text:
            log("[Login] 需要验证码识别")
            # 识别验证码并重试（略）
            # 注意：验证码提交也要用 login_url 而不是 base_url
            # data["captcha_code"] = solve_captcha(...)
            # resp2 = session.post(login_url, headers=headers, data=data, proxies=PROXIES)
            # 如果成功则 return

        log("[Login] 登录失败，重试")

    log("[Login] 登录失败超限")
    return "-1", session
    
def renew(sess_id, session, password, order_id, mailparser_id):
    url = "https://support.euserv.com/index.iphp"
    headers = {"User-Agent": USER_AGENT, "Referer": f"https://support.euserv.com/index.iphp?sess_id={sess_id}"}
    # 1) 选择订单
    session.post(url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details"
    }, proxies=PROXIES)
    # 2) 弹窗 & 重新发送 PIN
    prefix = f"kc2_customer_contract_details_extend_contract_{order_id}"
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": prefix,
        "type": 1
    }, proxies=PROXIES)
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "kc2_security_password_send_pin",
        "ident": prefix
    }, proxies=PROXIES)
    # 3) 获取 PIN
    pin = ''
    for i in range(5):
        time.sleep(WAITING_TIME_OF_PIN)
        pin = get_pin_from_mailparser(mailparser_id)
        log(f"[Mailparser] 第 {i+1} 次 PIN: {pin}")
        if re.match(r'^\d{6}$', pin):
            break
    if not re.match(r'^\d{6}$', pin):
        log("[Renew] PIN 无效")
        return False
    # 4) 换 token
    rtok = session.post(url, headers=headers, data={
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": prefix,
        "type": 1,
        "ident": prefix
    }, proxies=PROXIES)
    rtok.raise_for_status()
    tokj = rtok.json()
    token = tokj.get('token', {}).get('value', '')
    if not token:
        log(f"[Renew] token 失败: {rtok.text[:200]}")
        return False
    # 5) 提交续费
    final = session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token
    }, proxies=PROXIES)
    final.raise_for_status()
    t = final.text.lower()
    if "contract has been extended" in t or "successfully extended" in t:
        log(f"[Renew] {order_id} 成功")
        return True
    snippet = final.text[:300].replace("\n", " ")
    log(f"[Renew] 未检测到成功: {snippet}")
    return False

def main():
    if not USERNAME or not PASSWORD or not MAILPARSER_DOWNLOAD_URL_ID:
        log("[Main] 请确保环境变量 USERNAME, PASSWORD, MAILPARSER_DOWNLOAD_URL_ID 均已设置")
        return

    user_list = USERNAME.strip().split()
    passwd_list = PASSWORD.strip().split()
    mailparser_list = MAILPARSER_DOWNLOAD_URL_ID.strip().split()

    if len(user_list) != len(passwd_list) or len(user_list) != len(mailparser_list):
        log("[Main] 用户名、密码、mailparser链接数量不匹配")
        return

    for idx, user in enumerate(user_list):
        print("=" * 20)
        log(f"[Main] 开始续费第 {idx + 1} 个账号：{user}")

        sess_id, session = login(user, passwd_list[idx])
        if sess_id == "-1":
            log(f"[Main] 第 {idx + 1} 个账号登录失败")
            continue

        servers = get_servers(sess_id, session)
        log(f"[Main] 共检测到 {len(servers)} 台 VPS")

        if len(servers) == 0:
            continue

        for srv_id, need_renew in servers.items():
            if need_renew:
                if not renew(sess_id, session, passwd_list[idx], srv_id, mailparser_list[idx]):
                    log(f"[Main] ServerID: {srv_id} 续费失败")
                else:
                    log(f"[Main] ServerID: {srv_id} 续费成功")
            else:
                log(f"[Main] ServerID: {srv_id} 无需续费")

        time.sleep(10)

    print("=" * 20)
    log("[Main] 续费任务结束")

if __name__ == "__main__":
    main()
