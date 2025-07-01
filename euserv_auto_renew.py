#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import base64
import requests
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL, SMTPDataError
import ddddocr

# === 环境变量读取 ===
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
PROXIES_STR = os.environ.get("PROXIES", "")  # 格式例：http://127.0.0.1:10808
PROXIES = {"http": PROXIES_STR, "https": PROXIES_STR} if PROXIES_STR else {}

# 用户代理
user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36")

LOGIN_MAX_RETRY = 5
WAIT_PIN_SECONDS = 15

log_text = ""  # 日志累积变量

def log(s: str):
    global log_text
    print(s)
    log_text += s + "\n"

# 使用 ddddocr 识别验证码图片 bytes
def ddddocr_solver(img_bytes: bytes) -> str:
    ocr = ddddocr.DdddOcr()
    res = ocr.classification(img_bytes)
    log(f"[ddddocr] 识别结果: {res}")
    return res

# 使用 OCR.space 识别验证码图片 bytes
def ocr_space_solver(img_bytes: bytes) -> str:
    log("[OCR.space] 发送验证码图片进行识别")
    payload = {
        'apikey': OCRSPACE_API_KEY,
        'language': 'eng',
        'isOverlayRequired': False
    }
    files = {'file': ('captcha.png', img_bytes)}
    r = requests.post("https://api.ocr.space/parse/image", data=payload, files=files, proxies=PROXIES)
    result = r.json()
    if result.get("IsErroredOnProcessing"):
        log(f"[OCR.space] 识别错误: {result.get('ErrorMessage')}")
        return ""
    parsed_text = result["ParsedResults"][0]["ParsedText"].strip()
    log(f"[OCR.space] 识别结果: {parsed_text}")
    return parsed_text

# 登录，失败时自动重试，支持验证码识别（优先用ddddocr，失败则ocr.space）
def login(username, password):
    session = requests.Session()
    headers = {"User-Agent": user_agent, "Origin": "https://www.euserv.com"}

    url_login = "https://support.euserv.com/index.iphp"
    captcha_url = "https://support.euserv.com/securimage_show.php"

    for attempt in range(1, LOGIN_MAX_RETRY + 1):
        log(f"[Login] 第 {attempt} 次尝试登录")
        resp_init = session.get(url_login, headers=headers, proxies=PROXIES)
        sess_id_match = re.search(r"PHPSESSID=(\w{10,100});", str(resp_init.headers))
        if not sess_id_match:
            log("[Login] 未能获取到 PHPSESSID")
            continue
        sess_id = sess_id_match.group(1)

        # 获取验证码图片（如果有）
        captcha_resp = session.get(captcha_url, headers=headers, proxies=PROXIES)
        captcha_img_bytes = captcha_resp.content

        # 先用 ddddocr 识别
        captcha_code = ddddocr_solver(captcha_img_bytes)
        if not captcha_code or len(captcha_code) < 4:
            # ddddocr 识别失败时用 OCR.space 识别
            captcha_code = ocr_space_solver(captcha_img_bytes)

        if not captcha_code:
            log("[Login] 无法识别验证码，跳过此轮登录尝试")
            continue

        login_data = {
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
            "captcha_code": captcha_code,
        }
        response = session.post(url_login, headers=headers, data=login_data, proxies=PROXIES)
        if "Hello" in response.text or "Confirm or change your customer data here" in response.text:
            log("[Login] 登录成功")
            return sess_id, session
        else:
            log("[Login] 登录失败或验证码错误，尝试下一次")

    log("[Login] 登录失败超过最大重试次数")
    return None, None

def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    headers = {"User-Agent": user_agent, "Origin": "https://www.euserv.com"}
    resp = session.get(url, headers=headers, proxies=PROXIES)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    servers = {}
    rows = soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr")
    for tr in rows:
        sid_td = tr.select(".td-z1-sp1-kc")
        if len(sid_td) != 1:
            continue
        server_id = sid_td[0].get_text(strip=True)
        action_text = tr.select_one(".td-z1-sp2-kc .kc2_order_action_container").get_text(strip=True)
        can_renew = "Contract extension possible from" not in action_text
        servers[server_id] = can_renew
    return servers

def get_pin_from_mailparser(url_id):
    base_url = "https://files.mailparser.io/d/"
    r = requests.get(base_url + url_id, proxies=PROXIES)
    r.raise_for_status()
    data = r.json()
    if len(data) == 0:
        log("[MailParser] 未获取到 PIN")
        return None
    pin = data[0].get("pin")
    log(f"[MailParser] 取到 PIN: {pin}")
    return pin

def renew(sess_id, session, order_id, mailparser_url_id):
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "User-Agent": user_agent,
        "Host": "support.euserv.com",
        "Origin": "https://support.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }

    # 选择续费订单
    data_choose = {
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }
    session.post(url, headers=headers, data=data_choose, proxies=PROXIES)

    # 触发发送PIN码的安全窗口
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": "1"
    }, proxies=PROXIES)

    # 等待 PIN 到达 mailparser
    log(f"[Renew] 等待 {WAIT_PIN_SECONDS} 秒获取 PIN")
    time.sleep(WAIT_PIN_SECONDS)
    pin = get_pin_from_mailparser(mailparser_url_id)
    if not pin:
        log("[Renew] 未能获取 PIN，续费失败")
        return False

    # 用 PIN 获取令牌 token
    data_token = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    r_token = session.post(url, headers=headers, data=data_token, proxies=PROXIES)
    r_token.raise_for_status()
    resp_json = r_token.json()
    if resp_json.get("rs") != "success":
        log("[Renew] 获取 token 失败")
        return False
    token = resp_json["token"]["value"]

    # 续费合同
    data_renew = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    session.post(url, headers=headers, data=data_renew, proxies=PROXIES)
    log(f"[Renew] Server {order_id} 续费完成")
    time.sleep(5)
    return True

def check_renew_results(sess_id, session):
    servers = get_servers(sess_id, session)
    all_ok = True
    for sid, needs_renew in servers.items():
        if needs_renew:
            log(f"[Check] Server {sid} 续费失败")
            all_ok = False
        else:
            log(f"[Check] Server {sid} 不需要续费")
    if all_ok:
        log("[Check] 所有服务器续费成功")

def telegram_push():
    if not (TG_BOT_TOKEN and TG_USER_ID):
        return
    data = {
        "chat_id": TG_USER_ID,
        "text": "EUserv续费日志\n\n" + log_text,
        "disable_web_page_preview": True,
    }
    url = f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage"
    r = requests.post(url, data=data, proxies=PROXIES)
    if r.status_code == 200:
        log("[Telegram] 推送成功")
    else:
        log(f"[Telegram] 推送失败: {r.status_code} {r.text}")

def send_mail():
    if not (RECEIVER_EMAIL and YD_EMAIL and YD_APP_PWD):
        return
    msg = MIMEMultipart()
    msg["From"] = YD_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = "EUserv续费日志"
    msg.attach(MIMEText(log_text, "plain", "utf-8"))
    try:
        smtp = SMTP_SSL("smtp.yandex.ru", 465)
        smtp.login(YD_EMAIL, YD_APP_PWD)
        smtp.sendmail(YD_EMAIL, RECEIVER_EMAIL, msg.as_string())
        smtp.quit()
        log("[Email] 推送成功")
    except Exception as e:
        log(f"[Email] 推送失败: {e}")

def main():
    if not USERNAME or not PASSWORD or not MAILPARSER_DOWNLOAD_URL_ID:
        log("[Main] 请确保 USERNAME, PASSWORD, MAILPARSER_DOWNLOAD_URL_ID 已设置")
        return
    users = USERNAME.strip().split()
    pwds = PASSWORD.strip().split()
    mail_ids = MAILPARSER_DOWNLOAD_URL_ID.strip().split()
    if not (len(users) == len(pwds) == len(mail_ids)):
        log("[Main] 用户名、密码、Mailparser URL 数量不匹配")
        return

    for idx, (user, pwd, mail_id) in enumerate(zip(users, pwds, mail_ids), start=1):
        log("="*20)
        log(f"[Main] 开始续费第 {idx} 个账号：{user}")
        sess_id, sess = login(user, pwd)
        if not sess_id:
            log(f"[Main] 第 {idx} 个账号登录失败")
            continue

        servers = get_servers(sess_id, sess)
        log(f"[Main] 账号 {idx} 检测到 {len(servers)} 台服务器")
        for sid, need_renew in servers.items():
            if need_renew:
                if renew(sess_id, sess, sid, mail_id):
                    log(f"[Main] Server {sid} 续费成功")
                else:
                    log(f"[Main] Server {sid} 续费失败")
            else:
                log(f"[Main] Server {sid} 不需要续费")

        check_renew_results(sess_id, sess)
        time.sleep(10)

    telegram_push()
    send_mail()
    log("="*20)
    log("续费任务结束")

if __name__ == "__main__":
    main()
