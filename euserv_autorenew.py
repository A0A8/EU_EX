# SPDX-License-Identifier: GPL-3.0-or-later

"""
euserv 自动续期脚本（模块化、注释详细）
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

# ==== 配置模块 ====

def load_env_config():
    """
    读取环境变量配置
    """
    config = {
        "USERNAME": os.getenv("EUSERV_USERNAME"),
        "PASSWORD": os.getenv("EUSERV_PASSWORD"),
        "TRUECAPTCHA_USERID": os.getenv("TRUECAPTCHA_USERID"),
        "TRUECAPTCHA_APIKEY": os.getenv("TRUECAPTCHA_APIKEY"),
        "OCR_SPACE_APIKEY": os.getenv("OCR_SPACE_APIKEY"),
        "MAILPARSER_DOWNLOAD_URL_ID": os.getenv("MAILPARSER_DOWNLOAD_URL_ID"),
        "TG_BOT_TOKEN": os.getenv("TG_BOT_TOKEN"),
        "TG_USER_ID": os.getenv("TG_USER_ID"),
        "TG_API_HOST": "https://api.telegram.org",
        "PROXIES": {"http": "http://127.0.0.1:10808", "https": "http://127.0.0.1:10808"},
        "LOGIN_MAX_RETRY_COUNT": 5,
        "WAITING_TIME_OF_PIN": 15,
        "CHECK_CAPTCHA_SOLVER_USAGE": True,
        "OCR_PROVIDER": os.getenv("OCR_PROVIDER", "truecaptcha"),  # 可选值：truecaptcha, ocrspace, ddddocr
        "USER_AGENT": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/95.0.4638.69 Safari/537.36"),
    }
    return config

config = load_env_config()


# ==== 日志模块 ====

desp = ""  # 全局日志存储

def log(info: str):
    """
    统一日志打印及收集
    """
    global desp
    emoji_map = {
        "续期失败": "⚠️",
        "续订成功": "🎉",
        "登录失败": "❗",
        "验证码": "🧩",
        "登录尝试": "🔑",
        "PIN": "🔢",
        "API 使用次数": "📊",
        "无需续订": "✅",
        "所有续期成功": "🏁",
    }
    for key, emoji in emoji_map.items():
        if key in info:
            info = f"{emoji} {info}"
            break
    print(info)
    desp += info + "\n"


# ==== 验证码模块 ====

# 自动安装 ddddocr
try:
    import ddddocr
except ImportError:
    subprocess.run(["pip", "install", "ddddocr"])
    import ddddocr

def solve_captcha_truecaptcha(image_bytes):
    """
    使用 TrueCaptcha API 识别验证码
    """
    encoded_string = base64.b64encode(image_bytes).decode()
    data = {
        "userid": config["TRUECAPTCHA_USERID"],
        "apikey": config["TRUECAPTCHA_APIKEY"],
        "case": "mixed",
        "mode": "human",
        "data": encoded_string,
    }
    r = requests.post("https://api.apitruecaptcha.org/one/gettext", json=data)
    return r.json()

def solve_captcha_ocrspace(image_bytes):
    """
    使用 OCR.Space API 识别验证码
    """
    files = {"file": ("captcha.jpg", image_bytes)}
    headers = {"apikey": config["OCR_SPACE_APIKEY"]}
    r = requests.post("https://api.ocr.space/parse/image", files=files, data={"OCREngine": 2}, headers=headers)
    res = r.json()
    parsed_text = res.get("ParsedResults", [{}])[0].get("ParsedText", "").strip()
    return {"result": parsed_text}

def solve_captcha_ddddocr(image_bytes):
    """
    使用 ddddocr 识别验证码
    """
    ocr = ddddocr.DdddOcr()
    result = ocr.classification(image_bytes)
    return {"result": result}

def captcha_solver(captcha_image_url, session):
    """
    根据配置的 OCR_PROVIDER 选择对应验证码识别方案
    """
    resp = session.get(captcha_image_url)
    image_bytes = resp.content
    provider = config["OCR_PROVIDER"].lower()
    if provider == "truecaptcha":
        return solve_captcha_truecaptcha(image_bytes)
    elif provider == "ocrspace":
        return solve_captcha_ocrspace(image_bytes)
    elif provider == "ddddocr":
        return solve_captcha_ddddocr(image_bytes)
    else:
        raise ValueError("不支持的 OCR_PROVIDER")

def parse_captcha_result(solved):
    """
    解析验证码结果，自动计算数学题
    """
    if "result" not in solved:
        raise KeyError("验证码结果中没有 'result' 字段")
    text = solved["result"]
    # 处理算术表达式
    ops = ["+", "-", "x", "X", "*"]
    for op in ops:
        if op in text:
            parts = re.split(rf"\s*[{re.escape(op)}]\s*", text)
            if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
                left, right = map(int, parts)
                # 将 x,X 替换为 *
                operator = op.replace("x", "*").replace("X", "*")
                return str(eval(f"{left}{operator}{right}"))
    return text

def get_truecaptcha_usage():
    """
    查询 TrueCaptcha API 使用次数
    """
    url = "https://api.apitruecaptcha.org/one/getusage"
    params = {"username": config["TRUECAPTCHA_USERID"], "apikey": config["TRUECAPTCHA_APIKEY"]}
    r = requests.get(url, params=params)
    return r.json()


# ==== 登录模块 ====

def login_retry(max_retry=3):
    """
    登录重试装饰器
    """
    def decorator(func):
        def wrapper(username, password):
            for attempt in range(1, max_retry+1):
                sess_id, session = func(username, password)
                if sess_id != "-1":
                    return sess_id, session
                log(f"🔑 登录尝试第 {attempt} 次失败")
            return "-1", None
        return wrapper
    return decorator

@login_retry(max_retry=config["LOGIN_MAX_RETRY_COUNT"])
def login(username, password):
    """
    登录 euserv，处理验证码识别及重试逻辑
    """
    headers = {
        "user-agent": config["USER_AGENT"],
        "origin": "https://www.euserv.com"
    }
    login_url = "https://support.euserv.com/index.iphp"
    captcha_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()

    # 首次请求获取 sess_id
    resp = session.get(login_url, headers=headers)
    m = re.search(r"PHPSESSID=(\w{10,100});", str(resp.headers))
    sess_id = m.group(1) if m else None

    if not sess_id:
        log("❗ 获取 sess_id 失败")
        return "-1", session

    login_data = {
        "email": username,
        "password": password,
        "form_selected_language": "en",
        "Submit": "Login",
        "subaction": "login",
        "sess_id": sess_id,
    }
    resp_post = session.post(login_url, headers=headers, data=login_data)

    # 检测是否需要验证码
    if "captcha" in resp_post.text:
        log("🧩 需要验证码，开始识别...")
        solved = captcha_solver(captcha_url, session)
        code = parse_captcha_result(solved)
        log(f"🧩 识别验证码结果: {code}")
        # 如果是 truecaptcha，显示使用情况
        if config["OCR_PROVIDER"].lower() == "truecaptcha" and config["CHECK_CAPTCHA_SOLVER_USAGE"]:
            usage = get_truecaptcha_usage()
            count = usage[0]["count"] if usage else "未知"
            log(f"📊 TrueCaptcha 今日 API 使用次数: {count}")
        # 提交验证码
        resp_retry = session.post(login_url, headers=headers, data={
            "subaction": "login",
            "sess_id": sess_id,
            "captcha_code": code
        })
        if "captcha" in resp_retry.text:
            log("❌ 验证码验证失败")
            return "-1", session
        else:
            return sess_id, session
    elif "Hello" in resp_post.text or "Confirm or change your customer data here" in resp_post.text:
        log("✔️ 登录成功")
        return sess_id, session
    else:
        log("❗ 登录失败")
        return "-1", session


# ==== 服务器管理模块 ====

def get_servers(sess_id, session):
    """
    获取所有服务器和是否可以续期的状态
    """
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    headers = {"user-agent": config["USER_AGENT"]}
    resp = session.get(url, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    servers = {}
    # 选择对应的表格行
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table tr"):
        server_id_cell = tr.select(".td-z1-sp1-kc")
        if not server_id_cell:
            continue
        server_id = server_id_cell[0].get_text(strip=True)
        can_renew = "Contract extension possible from" not in tr.get_text()
        servers[server_id] = can_renew
    return servers

def renew(sess_id, session, password, order_id, mailparser_url_id):
    """
    进行续期操作
    """
    url = "https://support.euserv.com/index.iphp"
    headers = {"user-agent": config["USER_AGENT"]}

    # 第一步，选择续期订单
    data_step1 = {
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }
    session.post(url, headers=headers, data=data_step1)

    # 第二步，弹出 PIN 输入框，自动触发邮件发送PIN
    data_step2 = {
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": "1",
    }
    session.post(url, headers=headers, data=data_step2)

    # 等待 PIN 邮件解析
    time.sleep(config["WAITING_TIME_OF_PIN"])
    pin = get_pin_from_mailparser(mailparser_url_id)
    log(f"🔢 获取 PIN: {pin}")

    # 第三步，使用 PIN 获取 token
    data_step3 = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    token_resp = session.post(url, headers=headers, data=data_step3)
    token_json = token_resp.json()
    if token_json.get("rs") != "success":
        log("❗ 获取续期 token 失败")
        return False
    token = token_json.get("token", {}).get("value")

    # 第四步，提交续期请求
    data_step4 = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    session.post(url, headers=headers, data=data_step4)
    log(f"🎉 ServerID {order_id} 续期请求已提交")
    return True


def check_renew_status(sess_id, session):
    """
    检查所有服务器续期状态
    """
    servers = get_servers(sess_id, session)
    all_success = True
    for sid, can_renew in servers.items():
        if can_renew:
            all_success = False
            log(f"⚠️ ServerID {sid} 续期失败")
    if all_success:
        log("🏁 所有续期成功")


# ==== 邮件解析模块 ====

def get_pin_from_mailparser(url_id):
    """
    从 Mailparser.io 获取续期 PIN
    """
    resp = requests.get(f"https://files.mailparser.io/d/{url_id}")
    resp.raise_for_status()
    return resp.json()[0]["pin"]


# ==== Telegram 推送模块 ====

def send_telegram_message():
    """
    发送 Telegram 消息推送
    """
    if not config["TG_BOT_TOKEN"] or not config["TG_USER_ID"]:
        log("⚠️ Telegram 配置不完整，跳过通知发送")
        return

    data = {
        "chat_id": config["TG_USER_ID"],
        "text": f"<b>AutoEUServerless 日志</b>\n\n{desp}\n\n"
                "<b>版权声明：</b>\n"
                "本脚本基于 GPL-3.0 许可协议，版权所有。\n\n"
                "<b>致谢：</b>\n"
                "感谢所有贡献者。\n",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(f"{config['TG_API_HOST']}/bot{config['TG_BOT_TOKEN']}/sendMessage", data=data)
    if resp.status_code == 200:
        log("✅ Telegram 推送成功")
    else:
        log("❗ Telegram 推送失败")


# ==== 主流程 ====

def main_handler():
    """
    主函数，处理账户登录、续期和通知
    """
    if not config["USERNAME"] or not config["PASSWORD"]:
        log("❗ 未设置账户信息，请配置环境变量")
        return

    users = config["USERNAME"].strip().split()
    passwords = config["PASSWORD"].strip().split()
    mailparser_ids = config["MAILPARSER_DOWNLOAD_URL_ID"].strip().split()

    if len(users) != len(passwords) or len(users) != len(mailparser_ids):
        log("❗ 用户名、密码或 Mailparser URL 数量不匹配")
        return

    for i, (user, pwd, url_id) in enumerate(zip(users, passwords, mailparser_ids)):
        log(f"🌐 续期第 {i+1} 个账号: {user}")

        sess_id, session = login(user, pwd)
        if sess_id == "-1":
            log(f"❗ 账号登录失败: {user}")
            continue

        servers = get_servers(sess_id, session)
        log(f"🔍 检测到 {len(servers)} 台 VPS，开始续期")

        for sid, can_renew in servers.items():
            if can_renew:
                if renew(sess_id, session, pwd, sid, url_id):
                    log(f"🎉 ServerID {sid} 续订成功")
                else:
                    log(f"⚠️ ServerID {sid} 续订失败")
            else:
                log(f"✅ ServerID {sid} 无需续订")

        check_renew_status(sess_id, session)

    send_telegram_message()

if __name__ == "__main__":
    main_handler()
