#! /usr/bin/env python3
# SPDX-FileCopyrightText: (c) 2020-2021 CokeMine & Its repository contributors
# SPDX-FileCopyrightText: (c) 2021 A beam of light
# SPDX-License-Identifier: GPL-3.0-or-later

"""euserv auto-renew script with OCR.space captcha recognition"""

import os
import re
import json
import time
import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP_SSL, SMTPDataError
import requests
from bs4 import BeautifulSoup

# Configuration from environment
USERNAME = os.environ.get("USERNAME")  # 用户名或邮箱，多账号用空格分隔
PASSWORD = os.environ.get("PASSWORD")  # 密码，多账号用空格分隔
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY")  # OCR.space API Key
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID")  # Mailparser 下载 URL ID，多账号空格分隔
MAILPARSER_DOWNLOAD_BASE_URL = "https://files.mailparser.io/d/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")
PROXIES = {"http": "http://127.0.0.1:10808", "https": "http://127.0.0.1:10808"}
LOGIN_MAX_RETRY_COUNT = int(os.environ.get("LOGIN_MAX_RETRY_COUNT", 5))
WAITING_TIME_OF_PIN = int(os.environ.get("WAITING_TIME_OF_PIN", 15))
CHECK_CAPTCHA_SOLVER_USAGE = os.environ.get("CHECK_CAPTCHA_SOLVER_USAGE", "False").lower() in ("true", "1", "yes")
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
)

desp = ""

def log(info: str):
    global desp
    desp += info + "\n\n"
    print(info)


def captcha_solver(captcha_image_url: str, session: requests.Session) -> str:
    """
    使用 OCR.space 识别验证码，返回纯文本或计算结果。
    文档: https://ocr.space/ocrapi
    """
    # 下载验证码图片
    resp = session.get(captcha_image_url, headers={"User-Agent": user_agent}, proxies=PROXIES)
    resp.raise_for_status()
    # 调用 OCR.space API
    data = {
        'apikey': OCRSPACE_API_KEY,
        'language': 'eng',
        'isOverlayRequired': False,
    }
    files = {'file': ('captcha.png', resp.content)}
    r = requests.post('https://api.ocr.space/parse/image', data=data, files=files)
    r.raise_for_status()
    result = r.json()
    if result.get('IsErroredOnProcessing'):
        raise RuntimeError(f"OCR.space 错误: {result.get('ErrorMessage')}")
    parsed = result['ParsedResults'][0]['ParsedText'].strip()
    # 简单算术表达式计算
    if re.match(r'^\d+\s*[+\-*/xX]\s*\d+$', parsed):
        expr = parsed.replace('X', '*').replace('x', '*')
        try:
            return str(eval(expr))
        except Exception:
            pass
    return parsed


def get_pin_from_mailparser(url_id: str) -> str:
    response = requests.get(f"{MAILPARSER_DOWNLOAD_BASE_URL}{url_id}")
    response.raise_for_status()
    pin = response.json()[0].get("pin")
    if not pin:
        raise RuntimeError("从 Mailparser 获取 PIN 失败")
    return pin


def login_retry(max_retry=3):
    def decorator(func):
        def wrapper(username, password):
            sess_id, session = func(username, password)
            count = 0
            while sess_id == "-1" and count < max_retry:
                count += 1
                log(f"[EUserv] 登录重试 {count}/{max_retry}")
                sess_id, session = func(username, password)
            return sess_id, session
        return wrapper
    return decorator


@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username: str, password: str) -> (str, requests.Session):
    headers = {"User-Agent": user_agent, "Origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()
    # 初始化会话
    r0 = session.get(url, headers=headers, proxies=PROXIES)
    r0.raise_for_status()
    sess_id = re.search(r"PHPSESSID=(\w+);", r0.headers.get("set-cookie", ""))[1]
    # 加载 logo 触发 cookie
    session.get("https://support.euserv.com/pic/logo_small.png", headers=headers, proxies=PROXIES)
    # 初次登录请求
    data = {
        "email": username,
        "password": password,
        "form_selected_language": "en",
        "Submit": "Login",
        "subaction": "login",
        "sess_id": sess_id,
    }
    r1 = session.post(url, headers=headers, data=data, proxies=PROXIES)
    r1.raise_for_status()
    # 检查是否需要验证码
    if "solve the following captcha" in r1.text:
        log("[Captcha Solver] 识别验证码...")
        code = captcha_solver(captcha_image_url, session)
        log(f"[Captcha Solver] 识别结果: {code}")
        # 提交验证码
        r2 = session.post(url, headers=headers, data={
            "subaction": "login",
            "sess_id": sess_id,
            "captcha_code": code,
        }, proxies=PROXIES)
        r2.raise_for_status()
        if "solve the following captcha" in r2.text:
            log("[Captcha Solver] 验证失败")
            return "-1", session
        log("[Captcha Solver] 验证通过")
    return sess_id, session


def get_servers(sess_id: str, session: requests.Session) -> dict:
    """
    获取可续期的服务器列表, 返回 {order_id: True/False}
    True 表示可续期
    """
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    headers = {"User-Agent": user_agent, "Origin": "https://support.euserv.com"}
    r = session.get(url, headers=headers, proxies=PROXIES)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    rows = soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr")
    for tr in rows:
        cols = tr.select("td.td-z1-sp1-kc")
        if not cols:
            continue
        order_id = cols[0].get_text().strip()
        action_col = tr.select_one("td.td-z1-sp2-kc .kc2_order_action_container")
        need_ext = action_col and "Contract extension possible from" not in action_col.get_text()
        servers[order_id] = need_ext
    return servers


def renew(sess_id: str, session: requests.Session, password: str, order_id: str, mailparser_id: str) -> bool:
    """
    执行续期: 触发 PIN, 获取 PIN, 获取 token, 提交续期
    """
    base_url = "https://support.euserv.com/index.iphp"
    headers = {"User-Agent": user_agent, "Host": "support.euserv.com", "Origin": "https://support.euserv.com", "Referer": base_url}
    # 选择订单
    session.post(base_url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }, proxies=PROXIES)
    # 触发 PIN 窗口
    session.post(base_url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": f"kc2_customer_contract_details_extend_contract_{order_id}",
        "type": "1",
    }, proxies=PROXIES)
    # 等待 PIN 邮件
    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(mailparser_id)
    log(f"[MailParser] PIN: {pin}")
    # 获取 token
    data_token = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": f"kc2_customer_contract_details_extend_contract_{order_id}",
        "type": "1",
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    r_token = session.post(base_url, headers=headers, data=data_token, proxies=PROXIES)
    r_token.raise_for_status()
    token_json = r_token.json()
    if token_json.get("rs") != "success":
        return False
    token = token_json["token"]["value"]
    # 提交续期
    session.post(base_url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }, proxies=PROXIES)
    time.sleep(5)
    return True


def check(sess_id: str, session: requests.Session):
    log("[EUserv] 检查续期结果...")
    servers = get_servers(sess_id, session)
    all_done = True
    for oid, ok in servers.items():
        if ok:
            log(f"[EUserv] ServerID: {oid} 续期失败！")
            all_done = False
    if all_done:
        log("[EUserv] 全部续期完成！")


def telegram():
    payload = {"chat_id": TG_USER_ID, "text": "EUserv续费日志\n\n" + desp}
    r = requests.post(f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage", data=payload)
    if r.status_code != 200:
        print("Telegram 推送失败")
    else:
        print("Telegram 推送成功")


def send_mail_by_yandex(to_email, from_email, subject, text, files, sender_email, sender_password):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(text, _charset="utf-8"))
    if files:
        for fname, content in files:
            part = MIMEApplication(content)
            part.add_header("Content-Disposition", "attachment", filename=("gb18030", "", fname))
            msg.attach(part)
    with SMTP_SSL("smtp.yandex.ru", 465) as smtp:
        smtp.login(sender_email, sender_password)
        smtp.sendmail(from_email, to_email, msg.as_string())


def email():
    msg = "EUserv 续费日志\n\n" + desp
    try:
        send_mail_by_yandex(RECEIVER_EMAIL, YD_EMAIL, "EUserv 续费日志", msg, None, YD_EMAIL, YD_APP_PWD)
        print("Email 推送成功")
    except Exception as e:
        print(f"Email 推送失败: {e}")


if __name__ == "__main__":
    # 校验必要参数
    if not all([USERNAME, PASSWORD, OCRSPACE_API_KEY, MAILPARSER_DOWNLOAD_URL_ID]):
        log("缺少环境变量: USERNAME, PASSWORD, OCRSPACE_API_KEY, MAILPARSER_DOWNLOAD_URL_ID")
        exit(1)
    users = USERNAME.split()
    pwds = PASSWORD.split()
    mail_ids = MAILPARSER_DOWNLOAD_URL_ID.split()
    if len(users) != len(pwds) or len(users) != len(mail_ids):
        log("用户名、密码和 Mailparser ID 数量不匹配！")
        exit(1)

    for idx, user in enumerate(users, 1):
        print(f"{'*'*30}\n续费第 {idx} 个账号: {user}")
        sess_id, sess = login(user, pwds[idx-1])
        if sess_id == "-1":
            log(f"[EUserv] 第 {idx} 个账号登录失败")
            continue
        servers = get_servers(sess_id, sess)
        log(f"[EUserv] 检测到 {len(servers)} 台 VPS，开始续期...")
        for oid, need in servers.items():
            if need:
                success = renew(sess_id, sess, pwds[idx-1], oid, mail_ids[idx-1])
                if success:
                    log(f"[EUserv] ServerID {oid} 续期成功！")
                else:
                    log(f"[EUserv] ServerID {oid} 续期失败！")
            else:
                log(f"[EUserv] ServerID {oid} 无需续期")
            time.sleep(15)
        check(sess_id, sess)
        time.sleep(5)
        if TG_BOT_TOKEN and TG_USER_ID:
            telegram()
        if RECEIVER_EMAIL and YD_EMAIL and YD_APP_PWD:
            email()
        print("*"*30)
