# SPDX-License-Identifier: GPL-3.0-or-later

"""
euserv 自動續期腳本
功能:
* 使用 OCR.space 和 ddddocr 自動識別驗證碼
* 发送通知到 Telegram
* 增加登录失败重试机制
* 日志信息格式化
"""
import os
import re
import json
import time
import base64
import requests
from bs4 import BeautifulSoup
import ddddocr  # 導入 ddddocr

# 帳戶信息
USERNAME = os.getenv('EUSERV_USERNAME')
PASSWORD = os.getenv('EUSERV_PASSWORD')

# OCR.space API 配置
OCR_SPACE_API_KEY = os.getenv('OCR_SPACE_API_KEY')

# Mailparser 配置
MAILPARSER_DOWNLOAD_URL_ID = os.getenv('MAILPARSER_DOWNLOAD_URL_ID')
MAILPARSER_DOWNLOAD_BASE_URL = "https://files.mailparser.io/d/"

# Telegram Bot 配置
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_USER_ID = os.getenv('TG_USER_ID')
TG_API_HOST = "https://api.telegram.org"

# 代理設置（可選）
PROXIES = {"http": "http://127.0.0.1:10808", "https": "http://127.0.0.1:10808"}

# 最大登錄重試次數
LOGIN_MAX_RETRY_COUNT = 5

# 接收 PIN 的等待時間（秒）
WAITING_TIME_OF_PIN = 15

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/95.0.4638.69 Safari/537.36"
)
desp = ""  # 日志信息

def log(info: str):
    emoji_map = {
        "正在續費": "🔄",
        "檢測到": "🔍",
        "ServerID": "🔗",
        "無需更新": "✅",
        "續訂錯誤": "⚠️",
        "已成功續訂": "🎉",
        "所有工作完成": "🏁",
        "登陸失敗": "❗",
        "驗證通過": "✔️",
        "驗證失敗": "❌",
        "驗證碼是": "🔢",
        "登錄嘗試": "🔑",
        "[MailParser]": "📧",
        "[Captcha Solver]": "🧩",
        "[AutoEUServerless]": "🌐",
    }
    for key, emoji in emoji_map.items():
        if key in info:
            info = emoji + " " + info
            break
    print(info)
    global desp
    desp += info + "\n\n"

# 登錄重試裝飾器
def login_retry(*args, **kwargs):
    def wrapper(func):
        def inner(username, password):
            ret, ret_session = func(username, password)
            max_retry = kwargs.get("max_retry", 3)
            number = 0
            if ret == "-1":
                while number < max_retry:
                    number += 1
                    if number > 1:
                        log("[AutoEUServerless] 登錄嘗試第 {} 次".format(number))
                    sess_id, session = func(username, password)
                    if sess_id != "-1":
                        return sess_id, session
                    elif number == max_retry:
                        return sess_id, session
            return ret, ret_session
        return inner
    return wrapper

# 驗證碼解決器
def captcha_solver(captcha_image_url: str, session: requests.session) -> dict:
    def ocr_space_recognize(image_data: bytes) -> str:
        api_key = os.getenv('OCR_SPACE_API_KEY')
        if not api_key:
            raise ValueError("OCR_SPACE_API_KEY 未設置")
        url = "https://api.ocr.space/parse/image"
        payload = {
            "apikey": api_key,
            "language": "eng",
            "isOverlayRequired": False,
            "base64Image": "data:image/jpeg;base64," + base64.b64encode(image_data).decode(),
            "isTable": False,
            "scale": True,
            "OCREngine": 2
        }
        response = requests.post(url, data=payload)
        response.raise_for_status()
        result = response.json()
        if "ParsedResults" in result and len(result["ParsedResults"]) > 0:
            return result["ParsedResults"][0]["ParsedText"].strip()
        else:
            raise Exception("OCR.space 無法識別文本")

    def ddddocr_recognize(image_data: bytes) -> str:
        ocr = ddddocr.DdddOcr()
        result = ocr.classification(image_data)
        return result

    try:
        response = session.get(captcha_image_url)
        image_data = response.content
        try:
            ocr_space_result = ocr_space_recognize(image_data)
            if ocr_space_result:
                return {"result": ocr_space_result}
        except Exception as e:
            log(f"[Captcha Solver] OCR.space 失敗: {e}")
        try:
            ddddocr_result = ddddocr_recognize(image_data)
            if ddddocr_result:
                return {"result": ddddocr_result}
        except Exception as e:
            log(f"[Captcha Solver] ddddocr 失敗: {e}")
        return {"error": "兩種 OCR 服務均無法識別驗證碼"}
    except Exception as e:
        log(f"[Captcha Solver] 下載圖像時出錯: {e}")
        return {"error": "無法下載驗證碼圖像"}

# 處理驗證碼解決結果
def handle_captcha_solved_result(solved: dict) -> str:
    if "result" in solved:
        text = solved["result"]
        operators = ["X", "x", "+", "-"]
        if any(x in text for x in operators):
            for operator in operators:
                operator_pos = text.find(operator)
                if operator == "x" or operator == "X":
                    operator = "*"
                if operator_pos != -1:
                    left_part = text[:operator_pos]
                    right_part = text[operator_pos + 1:]
                    if left_part.isdigit() and right_part.isdigit():
                        return eval(
                            "{left} {operator} {right}".format(
                                left=left_part, operator=operator, right=right_part
                            )
                        )
                    else:
                        return text
        else:
            return text
    else:
        print(solved)
        raise KeyError("未找到解析結果。")

# 從 Mailparser 獲取 PIN
def get_pin_from_mailparser(url_id: str) -> str:
    response = requests.get(
        f"{MAILPARSER_DOWNLOAD_BASE_URL}{url_id}",
    )
    pin = response.json()[0]["pin"]
    return pin

# 登錄函數
@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username: str, password: str) -> (str, requests.session):
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()

    sess = session.get(url, headers=headers)
    sess_id = re.findall("PHPSESSID=(\\w{10,100});", str(sess.headers))[0]
    session.get("https://support.euserv.com/pic/logo_small.png", headers=headers)

    login_data = {
        "email": username,
        "password": password,
        "form_selected_language": "en",
        "Submit": "Login",
        "subaction": "login",
        "sess_id": sess_id,
    }
    f = session.post(url, headers=headers, data=login_data)
    f.raise_for_status()

    if "Hello" not in f.text and "Confirm or change your customer data here" not in f.text:
        if "To finish the login process please solve the following captcha." not in f.text:
            return "-1", session
        else:
            log("[Captcha Solver] 正在進行驗證碼識別...")
            solved = captcha_solver(captcha_image_url, session)
            if "error" in solved:
                log(f"[Captcha Solver] {solved['error']}")
                return "-1", session
            else:
                try:
                    captcha_code = handle_captcha_solved_result(solved)
                    log("[Captcha Solver] 識別的驗證碼是: {}".format(captcha_code))
                except Exception as e:
                    log(f"[Captcha Solver] 處理驗證碼結果失敗: {e}")
                    return "-1", session

                f2 = session.post(
                    url,
                    headers=headers,
                    data={
                        "subaction": "login",
                        "sess_id": sess_id,
                        "captcha_code": captcha_code,
                    },
                )
                if "To finish the login process please solve the following captcha." not in f2.text:
                    log("[Captcha Solver] 驗證通過")
                    return sess_id, session
                else:
                    log("[Captcha Solver] 驗證失敗")
                    return "-1", session
    else:
        return sess_id, session

# 獲取服務器列表
def get_servers(sess_id: str, session: requests.session) -> {}:
    d = {}
    url = "https://support.euserv.com/index.iphp?sess_id=" + sess_id
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    f = session.get(url=url, headers=headers)
    f.raise_for_status()
    soup = BeautifulSoup(f.text, "html.parser")
    for tr in soup.select(
        "#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"
    ):
        server_id = tr.select(".td-z1-sp1-kc")
        if not len(server_id) == 1:
            continue
        flag = (
            True
            if tr.select(".td-z1-sp2-kc .kc2_order_action_container")[0]
            .get_text()
            .find("Contract extension possible from")
            == -1
            else False
        )
        d[server_id[0].get_text()] = flag
    return d

# 續期操作
def renew(
    sess_id: str, session: requests.session, password: str, order_id: str, mailparser_dl_url_id: str
) -> bool:
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "user-agent": user_agent,
        "Host": "support.euserv.com",
        "origin": "https://support.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }
    data = {
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }
    session.post(url, headers=headers, data=data)

    # 弹出 'Security Check' 窗口，将自动触发 '发送 PIN'。
    session.post(
        url,
        headers=headers,
        data={
            "sess_id": sess_id,
            "subaction": "show_kc2_security_password_dialog",
            "prefix": "kc2_customer_contract_details_extend_contract_",
            "type": "1",
        },
    )

    # 等待邮件解析器解析出 PIN
    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin_from_mailparser(mailparser_dl_url_id)
    log(f"[MailParser] PIN: {pin}")

    # 使用 PIN 获取 token
    data = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    f = session.post(url, headers=headers, data=data)
    f.raise_for_status()
    if not json.loads(f.text)["rs"] == "success":
        return False
    token = json.loads(f.text)["token"]["value"]
    data = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    session.post(url, headers=headers, data=data)
    time.sleep(5)
    return True

# 檢查續期狀態
def check(sess_id: str, session: requests.session):
    print("Checking.......")
    d = get_servers(sess_id, session)
    flag = True
    for key, val in d.items():
        if val:
            flag = False
            log("[AutoEUServerless] ServerID: %s 續期失敗!" % key)

    if flag:
        log("[AutoEUServerless] 所有工作完成！尽情享受~")

# 發送 Telegram 通知
def telegram():
    message = (
        "<b>AutoEUServerless 日志</b>\n\n" + desp +
        "\n<b>版权声明：</b>\n"
        "本脚本基于 GPL-3.0 许可协议，版权所有。\n\n"
        
        "<b>致谢：</b>\n"
        "特别感谢 <a href='https://github.com/lw9726/eu_ex'>eu_ex</a> 的贡献和启发, 本项目在此基础整理。\n"
        "开发者：<a href='https://github.com/WizisCool'>WizisCool</a>\n"
        "<a href='https://www.nodeseek.com/space/8902#/general'>个人Nodeseek主页</a>\n"
        "<a href='https://dooo.ng'>个人小站Dooo.ng</a>\n\n"
        "<b>支持项目：</b>\n"
        "⭐️ 给我们一个 GitHub Star! ⭐️\n"
        "<a href='https://github.com/WizisCool/AutoEUServerless'>访问 GitHub 项目</a>"
    )

    data = {
        "chat_id": TG_USER_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }
    response = requests.post(
        TG_API_HOST + "/bot" + TG_BOT_TOKEN + "/sendMessage", data=data
    )
    if response.status_code != 200:
        print("Telegram Bot 推送失败")
    else:
        print("Telegram Bot 推送成功")

def main_handler(event, context):
    if not USERNAME or not PASSWORD:
        log("[AutoEUServerless] 你没有添加任何账户")
        exit(1)
    user_list = USERNAME.strip().split()
    passwd_list = PASSWORD.strip().split()
    mailparser_dl_url_id_list = MAILPARSER_DOWNLOAD_URL_ID.strip().split()
    if len(user_list) != len(passwd_list):
        log("[AutoEUServerless] 用户名和密码数量不匹配!")
        exit(1)
    if len(mailparser_dl_url_id_list) != len(user_list):
        log("[AutoEUServerless] mailparser_dl_url_ids 和用户名的数量不匹配!")
        exit(1)
    for i in range(len(user_list)):
        print("*" * 30)
        log("[AutoEUServerless] 正在续费第 %d 个账号" % (i + 1))
        sessid, s = login(user_list[i], passwd_list[i])
        if sessid == "-1":
            log("[AutoEUServerless] 第 %d 个账号登陆失败，请检查登录信息" % (i + 1))
            continue
        SERVERS = get_servers(sessid, s)
        log("[AutoEUServerless] 检测到第 {} 个账号有 {} 台 VPS，正在尝试续期".format(i + 1, len(SERVERS)))
        for k, v in SERVERS.items():
            if v:
                if not renew(sessid, s, passwd_list[i], k, mailparser_dl_url_id_list[i]):
                    log("[AutoEUServerless] ServerID: %s 续订错误!" % k)
                else:
                    log("[AutoEUServerless] ServerID: %s 已成功续订!" % k)
            else:
                log("[AutoEUServerless] ServerID: %s 无需更新" % k)
        time.sleep(15)
        check(sessid, s)
        time.sleep(5)

    # 發送 Telegram 通知
    if TG_BOT_TOKEN and TG_USER_ID and TG_API_HOST:
        telegram()

    print("*" * 30)

if __name__ == "__main__":
    main_handler(None, None)