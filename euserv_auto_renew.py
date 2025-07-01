#!/usr/bin/env python3
# SPDX-FileCopyrightText: (c) 2020-2021 CokeMine & Its repository contributors
# SPDX-FileCopyrightText: (c) 2021 A beam of light
# SPDX-License-Identifier: GPL-3.0-or-later

"""
EUserv 自动续费脚本（功能完整改写版）
- OCR.space 验证码识别
- TrueCaptcha 删除
- Mailparser PIN 自动获取
- 登录重试
- 动态代理支持
- Telegram & 邮件通知
"""

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

# —— 配置读取 —— #
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

# 代理设置（可选），形如 http://hostname:port
proxy_env = os.environ.get("PROXIES", "").strip()
if proxy_env:
    PROXIES = {"http": proxy_env, "https": proxy_env}
    print(f"[Config] 使用代理：{proxy_env}")
else:
    PROXIES = None
    print("[Config] 不使用代理")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36")

# 日志缓冲
_logs = []

def log(msg: str):
    print(msg)
    _logs.append(msg)

# —— HTTP 请求封装 —— #
def session_get(sess: requests.Session, url: str, **kwargs):
    if PROXIES:
        kwargs.setdefault('proxies', PROXIES)
    return sess.get(url, **kwargs)

def session_post(sess: requests.Session, url: str, **kwargs):
    if PROXIES:
        kwargs.setdefault('proxies', PROXIES)
    return sess.post(url, **kwargs)

# —— OCR.space 验证码破解 —— #
def ocrspace_solver(img_url: str, sess: requests.Session) -> str:
    r = session_get(sess, img_url, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    payload = {
        'apikey': OCRSPACE_API_KEY,
        'language': 'eng',
        'isOverlayRequired': False,
    }
    files = {'file': ('captcha.png', r.content)}
    resp = requests.post("https://api.ocr.space/parse/image", data=payload, files=files)
    resp.raise_for_status()
    data = resp.json()
    if data.get("IsErroredOnProcessing"):
        raise RuntimeError("OCR.space 错误: " + str(data.get("ErrorMessage")))
    text = data["ParsedResults"][0]["ParsedText"].strip()
    log(f"[OCR] 识别到：{text}")
    # 如果是简单算术表达式则计算
    if re.match(r"^\d+\s*[\+\-\*xX/]\s*\d+$", text):
        expr = text.replace('x','*').replace('X','*')
        try:
            res = str(eval(expr))
            log(f"[OCR] 计算结果：{res}")
            return res
        except:
            pass
    return text

# —— Mailparser PIN 获取 —— #
def get_pin_from_mailparser(url_id: str) -> str:
    url = f"https://files.mailparser.io/d/{url_id}"
    resp = requests.get(url, proxies=PROXIES) if PROXIES else requests.get(url)
    resp.raise_for_status()
    arr = resp.json()
    if not arr or "pin" not in arr[0]:
        raise RuntimeError("Mailparser 返回格式异常")
    return arr[0]["pin"]

# —— 登录（带重试） —— #
def login(username: str, password: str, retries: int = 3):
    login_url = "https://support.euserv.com/index.iphp"
    captcha_url = "https://support.euserv.com/securimage_show.php"
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})

    for attempt in range(1, retries+1):
        try:
            r0 = session_get(sess, login_url)
            r0.raise_for_status()
            # 从 cookie 中取 PHPSESSID
            sid = sess.cookies.get("PHPSESSID", "")
            if not sid:
                raise RuntimeError("未获取到 PHPSESSID")
            # 第一次登录提交
            data = {
                "email": username, "password": password,
                "form_selected_language": "en", "Submit": "Login",
                "subaction": "login", "sess_id": sid
            }
            r1 = session_post(sess, login_url, data=data)
            r1.raise_for_status()
            if "solve the following captcha" in r1.text:
                log("[Login] 检测到 CAPTCHA，开始 OCR.space 识别")
                code = ocrspace_solver(captcha_url, sess)
                data.update({"captcha_code": code})
                r2 = session_post(sess, login_url, data=data)
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

# —— 获取可续期服务器列表 —— #
def get_servers(sess_id: str, sess: requests.Session) -> dict:
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    r = session_get(sess, url)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # 尝试精确选择器
    rows = soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr")
    if not rows:
        rows = soup.select("table.kc2_content_table tr")

    servers = {}
    for tr in rows:
        tds = tr.find_all("td")
        if not tds:
            continue
        order_id = tds[0].get_text(strip=True)
        # 操作列一般为倒数第二个 td
        action_cell = tds[-2] if len(tds) >= 2 else None
        text = action_cell.get_text() if action_cell else ""
        need = ("Contract extension possible from" not in text)
        servers[order_id] = need

    return servers

# —— 执行续期 —— #
def renew(sess_id: str, sess: requests.Session, password: str, order_id: str, mailparser_id: str) -> bool:
    url = "https://support.euserv.com/index.iphp"
    headers = {"User-Agent": USER_AGENT, "Origin": "https://support.euserv.com"}
    # 选择订单
    session_post(sess, url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    })
    # 弹出 PIN 对话框
    session_post(sess, url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": f"kc2_customer_contract_details_extend_contract_{order_id}",
        "type": "1",
    })
    time.sleep(15)
    pin = get_pin_from_mailparser(mailparser_id)
    log(f"[Renew] 获取到 PIN：{pin}")

    # 获取 token
    resp = session_post(sess, url, headers=headers, data={
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": f"kc2_customer_contract_details_extend_contract_{order_id}",
        "type": "1",
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}"
    })
    resp.raise_for_status()
    js = resp.json()
    if js.get("rs") != "success":
        log("[Renew] token 获取失败")
        return False
    token = js["token"]["value"]

    # 提交续期
    final = session_post(sess, url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token
    })
    time.sleep(5)
    success = final.status_code == 200
    log(f"[Renew] 续期订单 {order_id} {'成功' if success else '失败'}")
    return success

# —— Telegram 推送 —— #
def telegram_notify():
    if not (TG_BOT_TOKEN and TG_USER_ID):
        return
    txt = "\n".join(_logs)
    try:
        requests.post(f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage",
                      data={"chat_id": TG_USER_ID, "text": "续费日志\n\n" + txt},
                      proxies=PROXIES or {})
        log("[Telegram] 推送完成")
    except Exception as e:
        log(f"[Telegram] 推送异常：{e}")

# —— 邮件推送 —— #
def email_notify():
    if not (RECEIVER_EMAIL and YD_EMAIL and YD_APP_PWD):
        return
    msg = MIMEMultipart()
    msg["From"] = YD_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = "EUserv 续费日志"
    msg.attach(MIMEText("\n".join(_logs), "plain", "utf-8"))
    try:
        with SMTP_SSL("smtp.yandex.ru", 465) as smtp:
            smtp.login(YD_EMAIL, YD_APP_PWD)
            smtp.sendmail(YD_EMAIL, RECEIVER_EMAIL, msg.as_string())
        log("[Email] 推送完成")
    except Exception as e:
        log(f"[Email] 推送失败：{e}")

# —— 主流程 —— #
def main():
    if not all([USERNAME, PASSWORD, OCRSPACE_API_KEY, MAILPARSER_DOWNLOAD_URL_ID]):
        log("[Error] 请设置 USERNAME, PASSWORD, OCRSPACE_API_KEY, MAILPARSER_DOWNLOAD_URL_ID")
        return

    users = USERNAME.split()
    pwds = PASSWORD.split()
    mail_ids = MAILPARSER_DOWNLOAD_URL_ID.split()
    if not (len(users) == len(pwds) == len(mail_ids)):
        log("[Error] 用户名、密码、Mailparser ID 数量不匹配")
        return

    for i, user in enumerate(users):
        log(f"=== 续费第 {i+1} 个账号：{user} ===")
        sid, sess = login(user, pwds[i])
        if not sid:
            log("[Error] 登录失败，跳过")
            continue
        servers = get_servers(sid, sess)
        log(f"[Info] 找到 {len(servers)} 台 VPS")
        for oid, need in servers.items():
            if need:
                renew(sid, sess, pwds[i], oid, mail_ids[i])
            else:
                log(f"[Info] 订单 {oid} 不需续期")
            time.sleep(5)

    telegram_notify()
    email_notify()
    log("所有账号处理完毕。")

if __name__ == "__main__":
    main()

