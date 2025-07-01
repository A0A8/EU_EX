#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import base64
import requests
from bs4 import BeautifulSoup
import ddddocr

# 账号密码，多个账号用空格隔开
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")

# OCR.space API key
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")

# mailparser配置（用于获取PIN码）
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")
MAILPARSER_DOWNLOAD_BASE_URL = "https://files.mailparser.io/d/"

# 其它配置
LOGIN_MAX_RETRY_COUNT = 5
WAITING_TIME_OF_PIN = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
)

desp = ""  # 日志内容收集

def log(msg):
    global desp
    print(msg)
    desp += msg + "\n"

def ocr_space_recognize(image_bytes, api_key):
    url = "https://api.ocr.space/parse/image"
    payload = {
        "apikey": api_key,
        "language": "eng",
        "isOverlayRequired": False,
    }
    files = {"file": ("captcha.jpg", image_bytes)}
    try:
        response = requests.post(url, data=payload, files=files, timeout=30)
        result = response.json()
        if result.get("IsErroredOnProcessing") or not result.get("ParsedResults"):
            return ""
        text = result["ParsedResults"][0]["ParsedText"].strip()
        return text
    except Exception as e:
        log(f"[OCR.space] 识别异常: {e}")
        return ""

def ddddocr_recognize(image_bytes):
    ocr = ddddocr.DdddOcr()
    try:
        res = ocr.classification(image_bytes)
        return res
    except Exception as e:
        log(f"[ddddocr] 识别异常: {e}")
        return ""

def validate_captcha_text(text):
    if not text or len(text) > 7:
        return False
    text = text.replace(" ", "")
    if re.match(r'^[0-9+\-*/xX]+$', text):
        return True
    return False

def calculate_captcha(text):
    expr = text.lower().replace("x", "*")
    try:
        return str(int(eval(expr)))
    except Exception:
        return text

def solve_captcha(image_bytes, ocrspace_api_key):
    # 先用 ddddocr
    res = ddddocr_recognize(image_bytes)
    log(f"[ddddocr] 识别结果: {res}")
    if validate_captcha_text(res):
        val = calculate_captcha(res)
        log(f"[ddddocr] 计算结果: {val}")
        return val

    # ddddocr失败，再用 OCR.space
    if ocrspace_api_key:
        res2 = ocr_space_recognize(image_bytes, ocrspace_api_key)
        log(f"[OCR.space] 识别结果: {res2}")
        if validate_captcha_text(res2):
            val2 = calculate_captcha(res2)
            log(f"[OCR.space] 计算结果: {val2}")
            return val2

    return ""

def get_pin_from_mailparser(url_id):
    try:
        resp = requests.get(f"{MAILPARSER_DOWNLOAD_BASE_URL}{url_id}", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if len(data) == 0:
            log("[Mailparser] 没有找到 PIN")
            return ""
        pin = data[0].get("pin", "")
        log(f"[Mailparser] 获取到 PIN: {pin}")
        return pin
    except Exception as e:
        log(f"[Mailparser] 获取 PIN 失败: {e}")
        return ""

def login(username, password):
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    headers = {"User-Agent": USER_AGENT, "Origin": "https://www.euserv.com"}

    session = requests.Session()

    for attempt in range(1, LOGIN_MAX_RETRY_COUNT + 1):
        log(f"[Login] 第 {attempt} 次尝试登录")
        resp = session.get(url, headers=headers)
        if resp.status_code != 200:
            log("[Login] 获取登录页面失败")
            continue

        # 获取PHPSESSID
        sess_id_match = re.search(r"PHPSESSID=(\w+);", str(resp.headers))
        sess_id = sess_id_match.group(1) if sess_id_match else ""

        if not sess_id:
            log("[Login] 未找到 PHPSESSID")
            continue

        # 访问logo图，模拟浏览器行为
        session.get("https://support.euserv.com/pic/logo_small.png", headers=headers)

        # 先发起登录请求
        login_data = {
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
        }
        login_resp = session.post(url, headers=headers, data=login_data)

        # 判断是否登录成功
        if "Hello" in login_resp.text or "Confirm or change your customer data here" in login_resp.text:
            log("[Login] 登录成功")
            return sess_id, session

        # 需要验证码
        if "To finish the login process please solve the following captcha." in login_resp.text:
            log("[Login] 需要验证码识别")

            # 获取验证码图片
            captcha_img_bytes = session.get(captcha_image_url, headers=headers).content
            captcha_code = solve_captcha(captcha_img_bytes, OCRSPACE_API_KEY)
            if not captcha_code:
                log("[Login] 无法识别验证码，跳过此轮登录尝试")
                continue

            # 带验证码重新登录
            login_data.update({
                "captcha_code": captcha_code
            })
            login_resp2 = session.post(url, headers=headers, data=login_data)
            if "To finish the login process please solve the following captcha." not in login_resp2.text:
                log("[Login] 验证码验证通过，登录成功")
                return sess_id, session
            else:
                log("[Login] 验证码错误或登录失败，尝试下一次")
        else:
            log("[Login] 登录失败，未知原因，尝试下一次")

    log("[Login] 登录失败超过最大重试次数")
    return "-1", session

def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    headers = {"User-Agent": USER_AGENT, "Origin": "https://www.euserv.com"}
    resp = session.get(url, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    servers = {}
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"):
        server_id_tag = tr.select(".td-z1-sp1-kc")
        if len(server_id_tag) != 1:
            continue
        server_id = server_id_tag[0].text.strip()
        action_container = tr.select_one(".td-z1-sp2-kc .kc2_order_action_container")
        if not action_container:
            continue
        renew_flag = action_container.get_text().find("Contract extension possible from") == -1
        servers[server_id] = renew_flag
    return servers

def renew(sess_id, session, password, order_id, mailparser_dl_url_id):
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "User-Agent": USER_AGENT,
        "Host": "support.euserv.com",
        "Origin": "https://support.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }

    # 选择订单
    data = {
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }
    session.post(url, headers=headers, data=data)

    # 触发安全密码弹窗及发送PIN码邮件
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": "1",
    })

    time.sleep(WAITING_TIME_OF_PIN)

    pin = get_pin_from_mailparser(mailparser_dl_url_id)
    if not pin:
        log("[Renew] 获取PIN失败，续费失败")
        return False

    # 使用PIN码获取token
    data = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    resp = session.post(url, headers=headers, data=data)
    if not resp.ok:
        log(f"[Renew] 获取token请求失败，状态码: {resp.status_code}")
        return False

    resp_json = resp.json()
    if resp_json.get("rs") != "success":
        log(f"[Renew] 获取token失败，响应: {resp.text}")
        return False

    token = resp_json.get("token", {}).get("value", "")
    if not token:
        log("[Renew] token为空，续费失败")
        return False

    # 提交续费请求
    data = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    session.post(url, headers=headers, data=data)
    time.sleep(5)
    log(f"[Renew] ServerID: {order_id} 续费成功")
    return True

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
