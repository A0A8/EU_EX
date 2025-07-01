#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from bs4 import BeautifulSoup
import ddddocr

# ===== 配置读取 =====
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")

PROXIES = {
    "http": os.environ.get("HTTP_PROXY", ""),
    "https": os.environ.get("HTTPS_PROXY", ""),
} if os.environ.get("USE_PROXY", "false").lower() == "true" else {}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT
}

# ===== 日志函数 =====
logs = []
def log(msg):
    print(msg)
    logs.append(msg)

# ===== OCR 验证码处理 =====
def solve_captcha(session):
    ocr = ddddocr.DdddOcr()
    url = "https://support.euserv.com/securimage_show.php"
    resp = session.get(url, headers=HEADERS, proxies=PROXIES)
    code = ocr.classification(resp.content)
    try:
        result = str(eval(code.lower().replace('x', '*')))
        return result
    except:
        return code

# ===== 登录处理 =====
def login(username, password):
    base_url = "https://support.euserv.com/index.iphp"
    session = requests.Session()

    for attempt in range(1, 6):
        log(f"[Login] 尝试第 {attempt} 次")
        r = session.get(base_url, headers=HEADERS, proxies=PROXIES)
        if "sess_id" not in r.url:
            log("[Login] sess_id 未找到")
            continue

        sess_id = re.findall(r"sess_id=([a-zA-Z0-9]+)", r.url)[0]
        login_url = f"{base_url}?sess_id={sess_id}"

        data = {
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id
        }

        resp = session.post(login_url, headers=HEADERS, data=data, proxies=PROXIES)

        if "Hello" in resp.text or "Confirm or change your customer data here" in resp.text:
            log("[Login] 登录成功")
            return sess_id, session

        if "captcha" in resp.text:
            log("[Login] 检测到验证码，尝试识别")
            code = solve_captcha(session)
            data["captcha_code"] = code
            resp = session.post(login_url, headers=HEADERS, data=data, proxies=PROXIES)
            if "Hello" in resp.text:
                log("[Login] 登录成功")
                return sess_id, session

        log("[Login] 登录失败")

    log("[Login] 登录失败超限")
    return None, session

# ===== 获取续期服务列表 =====
def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}&subaction=show_kc2_contract_details"
    resp = session.get(url, headers=HEADERS, proxies=PROXIES)
    soup = BeautifulSoup(resp.text, "html.parser")
    ids = {}
    for option in soup.select("select[name=ord_no] option"):
        val = option.get("value")
        if val and "contract will be extended" not in option.text:
            ids[val] = True
        else:
            ids[val] = False
    return ids

# ===== 获取 PIN =====
def get_pin(mailparser_id):
    try:
        r = requests.get(f"https://files.mailparser.io/d/{mailparser_id}", proxies=PROXIES)
        r.raise_for_status()
        j = r.json()
        return j[0].get("pin", "")
    except:
        return ""

# ===== 执行续期 =====
def renew(sess_id, session, order_id, mailparser_id):
    url = "https://support.euserv.com/index.iphp"
    prefix = f"kc2_customer_contract_details_extend_contract_{order_id}"

    def post(data):
        return session.post(url, headers=HEADERS, data=data, proxies=PROXIES)

    post({
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details"
    })

    post({
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": prefix,
        "type": 1
    })

    post({
        "sess_id": sess_id,
        "subaction": "kc2_security_password_send_pin",
        "ident": prefix
    })

    pin = ""
    for i in range(5):
        time.sleep(15)
        pin = get_pin(mailparser_id)
        log(f"[PIN] 第 {i+1} 次尝试获取 PIN: {pin}")
        if re.fullmatch(r"\d{6}", pin):
            break

    if not re.fullmatch(r"\d{6}", pin):
        log("[PIN] 获取失败")
        return

    token_resp = post({
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": prefix,
        "type": 1,
        "ident": prefix
    })

    try:
        token = token_resp.json().get("token", {}).get("value", "")
    except:
        log("[Renew] 获取 token 失败")
        return

    final = post({
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token
    })

    if "contract has been extended" in final.text.lower():
        log(f"[Renew] {order_id} 续期成功")
    else:
        log(f"[Renew] {order_id} 续期失败")

# ===== 主函数入口 =====
def main():
    users = USERNAME.split()
    pwds = PASSWORD.split()
    mids = MAILPARSER_DOWNLOAD_URL_ID.split()

    for idx, user in enumerate(users):
        print("=" * 20)
        log(f"[Main] 开始续费第 {idx+1} 个账号：{user}")

        sess_id, session = login(user, pwds[idx])
        if not sess_id:
            log("[Main] 登录失败")
            continue

        servers = get_servers(sess_id, session)
        log(f"[Main] 找到 {len(servers)} 台 VPS")

        for vid, need in servers.items():
            if need:
                renew(sess_id, session, vid, mids[idx])
            else:
                log(f"[Main] Server {vid} 无需续期")

        time.sleep(5)

    print("=" * 20)
    log("[Main] 续费任务结束")

if __name__ == "__main__":
    main()
