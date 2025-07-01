#!/usr/bin/env python3
# -*- coding: utf-8 -*-
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/95.0.4638.69 Safari/537.36"
)
import os
import re
import time
import json
import base64
import requests
from bs4 import BeautifulSoup
import ddddocr

USE_PROXY = False  # 改成 True 就启用代理
PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
} if USE_PROXY else {}

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

def login(username: str, password: str) -> (str, requests.Session):
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "User-Agent": user_agent,
        "Origin": "https://www.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }
    session = requests.Session()

    for attempt in range(1, LOGIN_MAX_RETRY_COUNT + 1):
        log(f"[Login] 第 {attempt} 次尝试登录")
        sess = session.get(url, headers=headers)
        try:
            sess_id = re.findall("PHPSESSID=(\\w{10,100});", str(sess.headers))[0]
        except IndexError:
            log("[Login] 无法从响应头获取 sess_id")
            return "-1", session

        session.get("https://support.euserv.com/pic/logo_small.png", headers=headers)

        login_data = {
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
        }
        f = session.post(url, headers=headers, data=login_data, proxies=PROXIES)

        # 打印登录响应内容前1000字符，方便调试
        snippet = f.text[:1000].replace('\n', ' ').replace('\r', ' ')
        log(f"[Login] 登录响应内容前1000字符:\n{snippet}")

        if "Hello" in f.text or "Confirm or change your customer data here" in f.text:
            log("[Login] 登录成功")
            return sess_id, session

        # 检查是否需要验证码处理（可在此基础上集成你的验证码识别）
        if "To finish the login process please solve the following captcha." in f.text:
            log("[Login] 需要验证码，登录流程暂未处理验证码，请在此处集成验证码识别代码")
            # 这里可调用你的验证码识别流程
            # 例如：captcha_code = your_captcha_solver(...)
            # 发送验证码进行登录重试等逻辑
            # 为了示范，此处跳过，继续下一次尝试
            pass

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

de
def renew(sess_id: str, session: requests.Session, password: str, order_id: str, mailparser_dl_url_id: str) -> bool:
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "User-Agent": user_agent,
        "Origin": "https://support.euserv.com",
        "Referer": f"https://support.euserv.com/index.iphp?sess_id={sess_id}",
    }

    # 1) 选择订单详情
    session.post(url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }, proxies=PROXIES)

    # 2) 触发 PIN 验证弹窗（发送 PIN 邮件）
    prefix = f"kc2_customer_contract_details_extend_contract_{order_id}"
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": prefix,
        "type": 1,
    }, proxies=PROXIES)

    # 3) 等待 PIN 到达
    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(mailparser_dl_url_id)
    log(f"[Mailparser] 获取到 PIN: {pin}")
    if not pin:
        log("[Renew] PIN 获取失败")
        return False

    # 4) 用 PIN 换取 token —— 注意使用同一个 prefix 和 ident
    token_resp = session.post(url, headers=headers, data={
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": prefix,
        "type": 1,
        "ident": prefix,
    }, proxies=PROXIES)
    token_resp.raise_for_status()
    js = token_resp.json()
    token = js.get("token", {}).get("value", "")
    if not token:
        log(f"[Renew] 获取 token 失败，响应片段：{token_resp.text}")
        return False

    # 5) 提交续期请求
    final_resp = session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }, proxies=PROXIES)
    final_resp.raise_for_status()

    # 6) 验证续期是否真正成功
    txt = final_resp.text.lower()
    if "contract has been extended" in txt or "successfully extended" in txt:
        log(f"[Renew] ServerID: {order_id} 续费确认成功")
        return True
    else:
        snippet = final_resp.text[:300].replace("\n"," ")
        log(f"[Renew] 续费后响应未检测到成功提示，响应片段：{snippet}")
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
