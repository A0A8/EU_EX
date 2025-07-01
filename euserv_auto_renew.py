#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import base64
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL, SMTPDataError

try:
    import ddddocr
except ImportError:
    ddddocr = None

USERNAME = os.environ.get("USERNAME", "").strip()
PASSWORD = os.environ.get("PASSWORD", "").strip()
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "").strip()
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "").strip()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_USER_ID = os.environ.get("TG_USER_ID", "").strip()
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org").strip()

RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "").strip()
YD_EMAIL = os.environ.get("YD_EMAIL", "").strip()
YD_APP_PWD = os.environ.get("YD_APP_PWD", "").strip()

PROXIES = None  # 如需要代理可设置格式 {"http": "http://ip:port", "https": "http://ip:port"}

LOGIN_MAX_RETRY_COUNT = 5
WAITING_TIME_OF_PIN = 15

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36")

_logs = []
def log(msg):
    print(msg)
    _logs.append(msg)

def ocrspace_solver(img_bytes: bytes) -> str:
    if not OCRSPACE_API_KEY:
        raise RuntimeError("OCR.space API key 未设置")
    payload = {
        'apikey': OCRSPACE_API_KEY,
        'language': 'eng',
        'isOverlayRequired': False,
    }
    files = {'file': ('captcha.png', img_bytes)}
    resp = requests.post("https://api.ocr.space/parse/image", data=payload, files=files, proxies=PROXIES)
    resp.raise_for_status()
    data = resp.json()
    if data.get("IsErroredOnProcessing"):
        raise RuntimeError("OCR.space 错误: " + str(data.get("ErrorMessage")))
    text = data["ParsedResults"][0]["ParsedText"].strip()
    log(f"[OCR.space] 识别结果：{text}")
    return text

def ddddocr_solver(img_bytes: bytes) -> str:
    if ddddocr is None:
        raise RuntimeError("ddddocr 未安装")
    ocr = ddddocr.DdddOcr()
    res = ocr.classification(img_bytes)
    log(f"[ddddocr] 识别结果：{res}")
    return res

def solve_captcha(captcha_url: str, sess: requests.Session) -> str:
    r = sess.get(captcha_url, headers={"User-Agent": USER_AGENT}, proxies=PROXIES)
    r.raise_for_status()
    img_bytes = r.content
    if OCRSPACE_API_KEY:
        try:
            return ocrspace_solver(img_bytes)
        except Exception as e:
            log(f"[OCR.space] 识别失败，切换到 ddddocr: {e}")
    if ddddocr:
        try:
            return ddddocr_solver(img_bytes)
        except Exception as e:
            log(f"[ddddocr] 识别失败: {e}")
            raise
    raise RuntimeError("没有可用的验证码识别方法")

def login(username: str, password: str, retries: int = LOGIN_MAX_RETRY_COUNT):
    login_url = "https://support.euserv.com/index.iphp"
    captcha_url = "https://support.euserv.com/securimage_show.php"
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})

    for attempt in range(1, retries + 1):
        try:
            r0 = sess.get(login_url, proxies=PROXIES)
            r0.raise_for_status()
            sid = sess.cookies.get("PHPSESSID", "")
            if not sid:
                raise RuntimeError("未获取到 PHPSESSID")
            data = {
                "email": username,
                "password": password,
                "form_selected_language": "en",
                "Submit": "Login",
                "subaction": "login",
                "sess_id": sid
            }
            r1 = sess.post(login_url, data=data, proxies=PROXIES)
            r1.raise_for_status()
            if "solve the following captcha" in r1.text:
                log("[Login] 检测到验证码，开始识别")
                code = solve_captcha(captcha_url, sess)
                data.update({"captcha_code": code})
                r2 = sess.post(login_url, data=data, proxies=PROXIES)
                r2.raise_for_status()
                if "solve the following captcha" in r2.text:
                    raise RuntimeError("验证码识别失败")
            log("[Login] 登录成功")
            return sid, sess
        except Exception as e:
            log(f"[Login] 第 {attempt} 次尝试失败：{e}")
            time.sleep(2)
    log("[Login] 重试结束，登录失败")
    return None, None

def get_servers(sess_id: str, session: requests.Session) -> dict:
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    headers = {"user-agent": USER_AGENT, "origin": "https://www.euserv.com"}
    resp = session.get(url, headers=headers, proxies=PROXIES)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    servers = {}
    rows = soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr")
    for tr in rows:
        server_id_elems = tr.select(".td-z1-sp1-kc")
        if len(server_id_elems) != 1:
            continue
        server_id = server_id_elems[0].get_text()
        action_text = tr.select(".td-z1-sp2-kc .kc2_order_action_container")[0].get_text()
        contract_possible = action_text.find("Contract extension possible from") == -1
        servers[server_id] = contract_possible
    return servers

def get_pin_from_mailparser(url_id: str) -> str:
    url = f"https://files.mailparser.io/d/{url_id}"
    resp = requests.get(url, proxies=PROXIES)
    resp.raise_for_status()
    data = resp.json()
    pin = data[0]["pin"]
    return pin

def renew(sess_id: str, session: requests.Session, password: str, order_id: str, mailparser_dl_url_id: str) -> bool:
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "user-agent": USER_AGENT,
        "Host": "support.euserv.com",
        "origin": "https://support.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }
    # 选择续费订单详情
    session.post(url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details"
    }, proxies=PROXIES)

    # 触发安全密码输入窗口（发送PIN）
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": "1",
    }, proxies=PROXIES)

    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(mailparser_dl_url_id)
    log(f"[MailParser] PIN: {pin}")

    # 使用PIN获取令牌
    data = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    resp = session.post(url, headers=headers, data=data, proxies=PROXIES)
    resp.raise_for_status()
    resp_json = resp.json()
    if resp_json.get("rs") != "success":
        return False
    token = resp_json["token"]["value"]

    # 提交续费请求
    data = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    session.post(url, headers=headers, data=data, proxies=PROXIES)
    time.sleep(5)
    return True

def check(sess_id: str, session: requests.Session):
    log("Checking servers status ...")
    servers = get_servers(sess_id, session)
    all_ok = True
    for sid, can_renew in servers.items():
        if can_renew:
            log(f"[EUserv] ServerID: {sid} 续费失败！")
            all_ok = False
    if all_ok:
        log("[EUserv] 所有服务器续费完成！")

def telegram_push():
    if not (TG_BOT_TOKEN and TG_USER_ID):
        return
    data = {"chat_id": TG_USER_ID, "text": "EUserv续费日志\n\n" + "\n".join(_logs)}
    resp = requests.post(f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage", data=data)
    if resp.status_code != 200:
        log("Telegram 推送失败")
    else:
        log("Telegram 推送成功")

def send_mail_by_yandex(to_email, from_email, subject, text, files, sender_email, sender_password):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(text, _charset="utf-8"))
    if files:
        for file_name, file_content in files:
            part = MIMEApplication(file_content)
            part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", file_name))
            msg.attach(part)
    with SMTP_SSL("smtp.yandex.ru", 465) as server:
        server.login(sender_email, sender_password)
        server.sendmail(from_email, to_email, msg.as_string())

def email_push():
    if not (RECEIVER_EMAIL and YD_EMAIL and YD_APP_PWD):
        return
    try:
        send_mail_by_yandex(RECEIVER_EMAIL, YD_EMAIL, "EUserv 续费日志", "\n".join(_logs), None, YD_EMAIL, YD_APP_PWD)
        log("邮件推送成功")
    except Exception as e:
        log(f"邮件推送失败: {e}")

if __name__ == "__main__":
    if not USERNAME or not PASSWORD or not MAILPARSER_DOWNLOAD_URL_ID:
        log("[EUserv] 必须设置 USERNAME, PASSWORD 和 MAILPARSER_DOWNLOAD_URL_ID 环境变量")
        exit(1)

    users = USERNAME.split()
    passwords = PASSWORD.split()
    mailparser_ids = MAILPARSER_DOWNLOAD_URL_ID.split()

    if len(users) != len(passwords):
        log("[EUserv] 用户名和密码数量不匹配")
        exit(1)
    if len(users) != len(mailparser_ids):
        log("[EUserv] 用户和 Mailparser 下载 ID 数量不匹配")
        exit(1)

    for i in range(len(users)):
        log(f"============ 正在处理第 {i+1} 个账号 ============")
        sid, sess = login(users[i], passwords[i])
        if not sid:
            log(f"[EUserv] 第 {i+1} 个账号登录失败，请检查账号信息")
            continue

        servers = get_servers(sid, sess)
        log(f"[EUserv] 第 {i+1} 个账号检测到 {len(servers)} 台服务器")

        for server_id, can_renew in servers.items():
            if can_renew:
                if renew(sid, sess, passwords[i], server_id, mailparser_ids[i]):
                    log(f"[EUserv] ServerID: {server_id} 续费成功！")
                else:
                    log(f"[EUserv] ServerID: {server_id} 续费失败！")
            else:
                log(f"[EUserv] ServerID: {server_id} 不需要续费")

        time.sleep(15)
        check(sid, sess)
        time.sleep(5)

    telegram_push()
    email_push()

    log("所有账号续费完成")
    print("=============================================")
