#!/usr/bin/env python3
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL

# --- 配置读取 ---
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")

# 代理配置，环境变量示例：http://yourproxy:1080
proxy_env = os.environ.get("PROXIES", "").strip()
if proxy_env:
    PROXIES = {"http": proxy_env, "https": proxy_env}
    print(f"[Config] 使用代理: {proxy_env}")
else:
    PROXIES = None
    print("[Config] 不使用代理")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"

log_msgs = []

def log(msg):
    print(msg)
    log_msgs.append(msg)

# --- 请求封装 ---
def session_get(session, url, **kwargs):
    if PROXIES:
        kwargs['proxies'] = PROXIES
    return session.get(url, **kwargs)

def session_post(session, url, **kwargs):
    if PROXIES:
        kwargs['proxies'] = PROXIES
    return session.post(url, **kwargs)

# --- OCR.space 识别 ---
def ocrspace_solver(img_url, session):
    resp = session_get(session, img_url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    data = {
        'apikey': OCRSPACE_API_KEY,
        'language': 'eng',
        'isOverlayRequired': False,
    }
    files = {'file': ('captcha.png', resp.content)}
    r = requests.post('https://api.ocr.space/parse/image', data=data, files=files)
    r.raise_for_status()
    res = r.json()
    if res.get("IsErroredOnProcessing"):
        raise RuntimeError(f"OCR.space 错误: {res.get('ErrorMessage')}")
    parsed_text = res['ParsedResults'][0]['ParsedText'].strip()
    log(f"[OCR] 识别内容: {parsed_text}")
    # 简单数学表达式计算
    if re.match(r"^\d+\s*[\+\-\*xX/]\s*\d+$", parsed_text):
        expr = parsed_text.replace('x', '*').replace('X', '*')
        try:
            val = str(eval(expr))
            log(f"[OCR] 计算结果: {val}")
            return val
        except:
            pass
    return parsed_text

# --- Mailparser PIN 获取 ---
def get_pin_from_mailparser(url_id):
    url = f"https://files.mailparser.io/d/{url_id}"
    r = requests.get(url, proxies=PROXIES)
    r.raise_for_status()
    json_data = r.json()
    pin = json_data[0].get("pin")
    if not pin:
        raise RuntimeError("从 Mailparser 获取 PIN 失败")
    return pin

# --- 登录 ---
def login(username, password, max_retry=5):
    login_url = "https://support.euserv.com/index.iphp"
    captcha_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    for attempt in range(1, max_retry+1):
        try:
            r0 = session_get(session, login_url)
            r0.raise_for_status()
            # 取 PHPSESSID
            cookies = session.cookies.get_dict()
            sess_id = cookies.get("PHPSESSID")
            if not sess_id:
                log("[Login] 未找到 PHPSESSID，重试...")
                continue
            # 提交登录表单
            data = {
                "email": username,
                "password": password,
                "form_selected_language": "en",
                "Submit": "Login",
                "subaction": "login",
                "sess_id": sess_id,
            }
            r1 = session_post(session, login_url, data=data)
            r1.raise_for_status()
            if "solve the following captcha" in r1.text:
                log("[Login] 需要验证码，调用 OCR.space 识别...")
                code = ocrspace_solver(captcha_url, session)
                log(f"[Login] 验证码结果: {code}")
                data.update({
                    "captcha_code": code,
                })
                r2 = session_post(session, login_url, data=data)
                r2.raise_for_status()
                if "solve the following captcha" in r2.text:
                    log("[Login] 验证码识别失败，重试...")
                    continue
            log("[Login] 登录成功")
            return sess_id, session
        except Exception as e:
            log(f"[Login] 尝试第 {attempt} 次失败: {e}")
            time.sleep(3)
    return None, None

# --- 获取服务器续期状态 ---
def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    r = session_get(session, url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    rows = soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr")
    for tr in rows:
        cols = tr.select("td.td-z1-sp1-kc")
        if not cols:
            continue
        order_id = cols[0].text.strip()
        action_col = tr.select_one("td.td-z1-sp2-kc .kc2_order_action_container")
        need_renew = action_col and ("Contract extension possible from" not in action_col.text)
        servers[order_id] = need_renew
    return servers

# --- 续期 ---
def renew(sess_id, session, password, order_id, mailparser_id):
    base_url = "https://support.euserv.com/index.iphp"
    headers = {
        "User-Agent": USER_AGENT,
        "Origin": "https://support.euserv.com",
        "Referer": f"{base_url}?sess_id={sess_id}",
    }
    # 选择订单续费
    session_post(session, base_url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    })
    # 调用 PIN 验证
    session_post(session, base_url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": f"kc2_customer_contract_details_extend_contract_{order_id}",
        "type": "1",
    })
    # 等待邮件 PIN
    time.sleep(15)
    pin = get_pin_from_mailparser(mailparser_id)
    log(f"[Renew] Mailparser PIN: {pin}")

    # 获取 token
    token_resp = session_post(session, base_url, headers=headers, data={
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": f"kc2_customer_contract_details_extend_contract_{order_id}",
        "type": "1",
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    })
    token_resp.raise_for_status()
    token_json = token_resp.json()
    if token_json.get("rs") != "success":
        log("[Renew] 令牌获取失败")
        return False
    token = token_json["token"]["value"]

    # 提交续期请求
    final_resp = session_post(session, base_url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    })
    time.sleep(5)
    if final_resp.status_code == 200:
        log("[Renew] 续期成功")
        return True
    else:
        log("[Renew] 续期失败")
        return False

# --- Telegram 通知 ---
def telegram_notify():
    if not (TG_BOT_TOKEN and TG_USER_ID):
        return
    msg = "\n\n".join(log_msgs)
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_USER_ID, "text": "EUserv续费日志\n\n" + msg}
    try:
        r = requests.post(url, data=data)
        if r.status_code == 200:
            print("[Telegram] 推送成功")
        else:
            print(f"[Telegram] 推送失败，状态码：{r.status_code}")
    except Exception as e:
        print(f"[Telegram] 推送异常: {e}")

# --- 邮件通知 ---
def send_email(subject, text):
    if not (RECEIVER_EMAIL and YD_EMAIL and YD_APP_PWD):
        return
    msg = MIMEMultipart()
    msg["From"] = YD_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain", "utf-8"))
    try:
        with SMTP_SSL("smtp.yandex.ru", 465) as smtp:
            smtp.login(YD_EMAIL, YD_APP_PWD)
            smtp.sendmail(YD_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print("[Email] 发送成功")
    except Exception as e:
        print(f"[Email] 发送失败: {e}")

# --- 主程序 ---
def main():
    if not all([USERNAME, PASSWORD, OCRSPACE_API_KEY, MAILPARSER_DOWNLOAD_URL_ID]):
        log("[Error] 缺少必要环境变量（USERNAME, PASSWORD, OCRSPACE_API_KEY, MAILPARSER_DOWNLOAD_URL_ID）")
        return

    users = USERNAME.split()
    pwds = PASSWORD.split()
    mail_ids = MAILPARSER_DOWNLOAD_URL_ID.split()
    if not (len(users) == len(pwds) == len(mail_ids)):
        log("[Error] 用户名、密码和 Mailparser ID 数量不匹配")
        return

    for idx, user in enumerate(users):
        log(f"==== 续费第 {idx+1} 个账号: {user} ====")
        sess_id, session = login(user, pwds[idx])
        if not sess_id:
            log("[Error] 登录失败，跳过此账号")
            continue
        servers = get_servers(sess_id, session)
        log(f"[Info] 检测到 {len(servers)} 台服务器")

        for order_id, need_renew in servers.items():
            if need_renew:
                log(f"[Renew] 续期服务器订单号 {order_id}")
                success = renew(sess_id, session, pwds[idx], order_id, mail_ids[idx])
                log(f"[Renew] 结果: {'成功' if success else '失败'}")
            else:
                log(f"[Info] 服务器订单号 {order_id} 无需续期")
            time.sleep(15)

    telegram_notify()
    send_email("EUserv续费日志", "\n\n".join(log_msgs))

if __name__ == "__main__":
    main()
