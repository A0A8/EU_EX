# SPDX-License-Identifier: GPL-3.0-or-later

"""
euserv 自动续期脚本
支持 OCR 验证（TrueCaptcha / ocr.space / ddddocr）
"""

import os
import re
import json
import time
import base64
import requests
import subprocess
from bs4 import BeautifulSoup

# 自动安装 ddddocr
try:
    import ddddocr
except ImportError:
    subprocess.run(["pip", "install", "ddddocr"])
    import ddddocr

USERNAME = os.getenv("EUSERV_USERNAME")
PASSWORD = os.getenv("EUSERV_PASSWORD")
TRUECAPTCHA_USERID = os.getenv("TRUECAPTCHA_USERID")
TRUECAPTCHA_APIKEY = os.getenv("TRUECAPTCHA_APIKEY")
OCR_SPACE_APIKEY = os.getenv("OCR_SPACE_APIKEY")
MAILPARSER_DOWNLOAD_URL_ID = os.getenv("MAILPARSER_DOWNLOAD_URL_ID")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_USER_ID = os.getenv("TG_USER_ID")
TG_API_HOST = "https://api.telegram.org"
PROXIES = {"http": "http://127.0.0.1:10808", "https": "http://127.0.0.1:10808"}
LOGIN_MAX_RETRY_COUNT = 5
WAITING_TIME_OF_PIN = 15
CHECK_CAPTCHA_SOLVER_USAGE = True
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "truecaptcha")

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/95.0.4638.69 Safari/537.36"
)
desp = ""

def log(info: str):
    global desp
    print(info)
    desp += info + "\n"

def captcha_solver(captcha_image_url: str, session: requests.Session) -> dict:
    response = session.get(captcha_image_url)
    image_bytes = response.content

    if OCR_PROVIDER == "truecaptcha":
        encoded_string = base64.b64encode(image_bytes).decode()
        data = {
            "userid": TRUECAPTCHA_USERID,
            "apikey": TRUECAPTCHA_APIKEY,
            "case": "mixed",
            "mode": "human",
            "data": encoded_string,
        }
        r = requests.post("https://api.apitruecaptcha.org/one/gettext", json=data)
        return json.loads(r.text)

    elif OCR_PROVIDER == "ocrspace":
        files = {"file": ("captcha.jpg", image_bytes)}
        headers = {"apikey": OCR_SPACE_APIKEY}
        r = requests.post("https://api.ocr.space/parse/image", files=files, data={"OCREngine": 2}, headers=headers)
        parsed = r.json().get("ParsedResults", [{}])[0].get("ParsedText", "").strip()
        return {"result": parsed}

    elif OCR_PROVIDER == "ddddocr":
        ocr = ddddocr.DdddOcr()
        result = ocr.classification(image_bytes)
        return {"result": result}

    else:
        raise ValueError("Unsupported OCR_PROVIDER")

def handle_captcha_solved_result(solved: dict) -> str:
    if "result" not in solved:
        raise KeyError("No result in captcha solving response")
    text = solved["result"]
    operators = ["+", "-", "x", "X", "*"]
    for op in operators:
        if op in text:
            parts = re.split(rf"\s*[{re.escape(op)}]\s*", text)
            if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
                left, right = map(int, parts)
                return str(eval(f"{left} {op.replace('x','*').replace('X','*')} {right}"))
    return text

def get_captcha_solver_usage():
    url = "https://api.apitruecaptcha.org/one/getusage"
    params = {"username": TRUECAPTCHA_USERID, "apikey": TRUECAPTCHA_APIKEY}
    return requests.get(url, params=params).json()

def get_pin_from_mailparser(url_id: str) -> str:
    r = requests.get(f"https://files.mailparser.io/d/{url_id}")
    return r.json()[0]["pin"]

def login_retry(*args, **kwargs):
    def wrapper(func):
        def inner(username, password):
            ret, sess = func(username, password)
            max_retry = kwargs.get("max_retry", 3)
            for i in range(1, max_retry + 1):
                if ret != "-1":
                    return ret, sess
                log(f"[Retry] 登录尝试第 {i} 次")
                ret, sess = func(username, password)
            return ret, sess
        return inner
    return wrapper

@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username, password):
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()

    sess = session.get(url, headers=headers)
    sess_id = re.findall("PHPSESSID=(\\w{10,100});", str(sess.headers))[0]

    login_data = {
        "email": username, "password": password,
        "form_selected_language": "en", "Submit": "Login",
        "subaction": "login", "sess_id": sess_id,
    }
    resp = session.post(url, headers=headers, data=login_data)

    if "captcha" in resp.text:
        log("[Captcha] 检测到验证码，开始识别...")
        solved = captcha_solver(captcha_image_url, session)
        code = handle_captcha_solved_result(solved)
        log(f"[Captcha] 验证码是: {code}")
        if OCR_PROVIDER == "truecaptcha" and CHECK_CAPTCHA_SOLVER_USAGE:
            usage = get_captcha_solver_usage()
            log(f"[TrueCaptcha] API 使用次数: {usage[0]['count']}")
        resp2 = session.post(url, headers=headers, data={
            "subaction": "login", "sess_id": sess_id, "captcha_code": code
        })
        if "captcha" in resp2.text:
            log("[Captcha] 验证失败")
            return "-1", session
        else:
            return sess_id, session
    elif "Hello" in resp.text:
        return sess_id, session
    else:
        return "-1", session

def get_servers(sess_id, session):
    d = {}
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    soup = BeautifulSoup(session.get(url, headers={"user-agent": user_agent}).text, "html.parser")
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table tr"):
        sid_elem = tr.select_one(".td-z1-sp1-kc")
        if not sid_elem:
            continue
        sid = sid_elem.get_text(strip=True)
        can_renew = "Contract extension possible from" not in tr.get_text()
        d[sid] = can_renew
    return d

def renew(sess_id, session, password, order_id, url_id):
    url = "https://support.euserv.com/index.iphp"
    session.post(url, headers={"user-agent": user_agent}, data={
        "Submit": "Extend contract", "sess_id": sess_id, "ord_no": order_id,
        "subaction": "choose_order", "choose_order_subaction": "show_contract_details"
    })
    session.post(url, headers={"user-agent": user_agent}, data={
        "sess_id": sess_id, "subaction": "show_kc2_security_password_dialog",
        "prefix": "kc2_customer_contract_details_extend_contract_", "type": "1"
    })
    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(url_id)
    log(f"[PIN] 获取到 PIN: {pin}")
    token_resp = session.post(url, headers={"user-agent": user_agent}, data={
        "auth": pin, "sess_id": sess_id, "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_", "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}"
    })
    token = token_resp.json().get("token", {}).get("value")
    if not token:
        return False
    session.post(url, headers={"user-agent": user_agent}, data={
        "sess_id": sess_id, "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term", "token": token
    })
    return True

def check(sess_id, session):
    servers = get_servers(sess_id, session)
    all_ok = True
    for sid, ok in servers.items():
        if ok:
            log(f"[Check] ServerID {sid} 续期成功")
        else:
            log(f"[Check] ServerID {sid} 续期失败")
            all_ok = False
    if all_ok:
        log("[Check] 所有续期成功")

def telegram():
    if not TG_BOT_TOKEN or not TG_USER_ID:
        return
    requests.post(f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage", data={
        "chat_id": TG_USER_ID,
        "text": "<b>AutoEUServerless 日志</b>\n\n" + desp,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def main_handler(event=None, context=None):
    if not USERNAME or not PASSWORD:
        log("[Init] 未设置账户信息")
        return
    users = USERNAME.strip().split()
    pwds = PASSWORD.strip().split()
    url_ids = MAILPARSER_DOWNLOAD_URL_ID.strip().split()
    for i, (usr, pwd, uid) in enumerate(zip(users, pwds, url_ids)):
        log(f"[Account] 处理账户 {i+1}: {usr}")
        sess_id, sess = login(usr, pwd)
        if sess_id == "-1":
            log(f"[Account] 登录失败: {usr}")
            continue
        servers = get_servers(sess_id, sess)
        log(f"[Account] 检测到 {len(servers)} 台 VPS")
        for sid, can in servers.items():
            if can:
                if renew(sess_id, sess, pwd, sid, uid):
                    log(f"[Renew] ServerID {sid} 续期成功")
                else:
                    log(f"[Renew] ServerID {sid} 续期失败")
            else:
                log(f"[Skip] ServerID {sid} 无需续期")
        check(sess_id, sess)
    telegram()

if __name__ == "__main__":
    main_handler()
