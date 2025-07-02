# SPDX-License-Identifier: GPL-3.0-or-later

"""
euserv 自動續期腳本
功能:
* 使用 OCR.space 和 ddddocr 自動識別驗證碼
* 發送通知到 Telegram
* 增加登錄失敗重試機制
* 日誌資訊格式化
* 支援 UTF-8 編碼以避免 UnicodeEncodeError
* 增強會話管理和錯誤處理
"""

import os
import re
import json
import time
import base64
import requests
from bs4 import BeautifulSoup
import ddddocr

# 環境變數
USERNAME = os.getenv('EUSERV_USERNAME', '').encode().decode('utf-8', errors='replace')
PASSWORD = os.getenv('EUSERV_PASSWORD', '').encode().decode('utf-8', errors='replace')
OCR_SPACE_API_KEY = os.getenv('OCR_SPACE_API_KEY', '').encode().decode('utf-8', errors='replace')
MAILPARSER_DOWNLOAD_URL_ID = os.getenv('MAILPARSER_DOWNLOAD_URL_ID', '').encode().decode('utf-8', errors='replace')
MAILPARSER_DOWNLOAD_BASE_URL = "https://files.mailparser.io/d/"
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '').encode().decode('utf-8', errors='replace')
TG_USER_ID = os.getenv('TG_USER_ID', '').encode().decode('utf-8', errors='replace')
TG_API_HOST = "https://api.telegram.org"

# 最大登錄重試次數
LOGIN_MAX_RETRY_COUNT = 10
# 接收 PIN 的等待時間（秒）
WAITING_TIME_OF_PIN = 30
# 驗證碼識別最大嘗試次數
CAPTCHA_MAX_RETRY_COUNT = 3

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/95.0.4638.69 Safari/537.36"
)
desp = ""  # 日誌資訊

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
    info = info.encode('utf-8', errors='replace').decode('utf-8')
    for key, emoji in emoji_map.items():
        if key in info:
            info = emoji + " " + info
            break
    print(info)
    global desp
    desp += info + "\n\n"

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
                        log(f"[AutoEUServerless] 登錄嘗試第 {number} 次")
                    sess_id, session = func(username, password)
                    if sess_id != "-1":
                        return sess_id, session
                    elif number == max_retry:
                        return sess_id, session
            return ret, ret_session
        return inner
    return wrapper

def captcha_solver(captcha_image_url: str, session: requests.Session) -> dict:
    def ocr_space_recognize(image_data: bytes) -> str:
        api_key = os.getenv('OCR_SPACE_API_KEY', '').encode().decode('utf-8', errors='replace')
        if not api_key:
            raise ValueError("OCR_SPACE_API_KEY 未設置")
        url = "https://api.ocr.space/parse/image"
        payload = {
            "apikey": api_key,
            "language": "eng",
            "isOverlayRequired": False,
            "base64Image": "data:image/jpeg;base64," + base64.b64encode(image_data).decode('utf-8'),
            "isTable": False,
            "scale": True,
            "OCREngine": 2
        }
        try:
            response = session.post(url, data=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            if "ParsedResults" in result and len(result["ParsedResults"]) > 0:
                return result["ParsedResults"][0]["ParsedText"].strip()
            else:
                raise Exception("OCR.space 無法識別文本")
        except Exception as e:
            raise Exception(f"OCR.space 錯誤: {e}")

    def ddddocr_recognize(image_data: bytes) -> str:
        try:
            ocr = ddddocr.DdddOcr()
            result = ocr.classification(image_data)
            return result.strip()
        except Exception as e:
            raise Exception(f"ddddocr 錯誤: {e}")

    for attempt in range(CAPTCHA_MAX_RETRY_COUNT):
        try:
            response = session.get(captcha_image_url, timeout=10)
            response.raise_for_status()
            image_data = response.content
            log(f"[Captcha Solver] 驗證碼圖片下載成功 (嘗試 {attempt + 1}/{CAPTCHA_MAX_RETRY_COUNT})")
            
            # 嘗試 OCR.space
            try:
                ocr_space_result = ocr_space_recognize(image_data)
                if ocr_space_result:
                    log(f"[Captcha Solver] OCR.space 識別結果: {ocr_space_result}")
                    return {"result": ocr_space_result}
            except Exception as e:
                log(f"[Captcha Solver] OCR.space 失敗: {e}")

            # 嘗試 ddddocr
            try:
                ddddocr_result = ddddocr_recognize(image_data)
                if ddddocr_result:
                    log(f"[Captcha Solver] ddddocr 識別結果: {ddddocr_result}")
                    return {"result": ddddocr_result}
            except Exception as e:
                log(f"[Captcha Solver] ddddocr 失敗: {e}")
                
            log(f"[Captcha Solver] 驗證碼識別失敗，正在重試 (嘗試 {attempt + 1}/{CAPTCHA_MAX_RETRY_COUNT})")
        except Exception as e:
            log(f"[Captcha Solver] 下載圖像失敗: {e}")
        
        if attempt < CAPTCHA_MAX_RETRY_COUNT - 1:
            time.sleep(2)  # 等待 2 秒後重試
            
    return {"error": "兩種 OCR 服務均無法識別驗證碼"}

def handle_captcha_solved_result(solved: dict) -> str:
    if "result" in solved:
        text = str(solved["result"]).strip().encode('utf-8', errors='replace').decode('utf-8')
        log(f"[Captcha Solver] 原始識別結果: {text}")
        
        # 移除非字母數字字符，僅保留可能有效的驗證碼
        cleaned_text = re.sub(r'[^a-zA-Z0-9]', '', text)
        if cleaned_text:
            return cleaned_text
        
        # 如果識別結果包含運算符，嘗試計算
        operators = ["X", "x", "+", "-", "*"]
        for operator in operators:
            operator_pos = text.find(operator)
            if operator_pos != -1:
                left_part = text[:operator_pos].strip()
                right_part = text[operator_pos + 1:].strip()
                if left_part.isdigit() and right_part.isdigit():
                    operator = "*" if operator.lower() == "x" else operator
                    try:
                        return str(eval(f"{left_part} {operator} {right_part}"))
                    except:
                        pass
        return cleaned_text or text
    else:
        log(f"[Captcha Solver] 無效的解析結果: {solved}")
        raise KeyError("未找到解析結果。")

def get_pin_from_mailparser(url_id: str) -> str:
    for attempt in range(3):
        try:
            response = requests.get(
                f"{MAILPARSER_DOWNLOAD_BASE_URL}{url_id}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if data and isinstance(data, list) and "pin" in data[0]:
                pin = str(data[0]["pin"]).encode('utf-8', errors='replace').decode('utf-8')
                return pin
            else:
                raise ValueError("無效的 Mailparser 響應")
        except Exception as e:
            log(f"[MailParser] PIN 獲取失敗 (嘗試 {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(10)
    raise ValueError("多次嘗試後無法獲取 PIN")

@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username: str, password: str) -> (str, requests.Session):
    headers = {
        "user-agent": user_agent,
        "origin": "https://www.euserv.com",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()

    try:
        sess = session.get(url, headers=headers, timeout=10)
        sess.raise_for_status()
        sess_id = re.findall("PHPSESSID=(\\w{10,100});", str(sess.headers))[0]
        session.get("https://support.euserv.com/pic/logo_small.png", headers=headers, timeout=10)

        login_data = {
            "email": username.encode('utf-8', errors='replace').decode('utf-8'),
            "password": password.encode('utf-8', errors='replace').decode('utf-8'),
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
        }
        f = session.post(url, headers=headers, data=login_data, timeout=10)
        f.raise_for_status()

        if "Hello" not in f.text and "Confirm or change your customer data here" not in f.text:
            if "To finish the login process please solve the following captcha." not in f.text:
                log("[AutoEUServerless] 登錄失敗，無驗證碼提示")
                return "-1", session
            else:
                log("[Captcha Solver] 正在進行驗證碼識別...")
                solved = captcha_solver(captcha_image_url, session)
                if "error" in solved:
                    log(f"[Captcha Solver] {solved['error']}")
                    return "-1", session
                try:
                    captcha_code = handle_captcha_solved_result(solved)
                    log(f"[Captcha Solver] 識別的驗證碼是: {captcha_code}")
                except Exception as e:
                    log(f"[Captcha Solver] 處理驗證碼結果失敗: {e}")
                    return "-1", session

                f2 = session.post(
                    url,
                    headers=headers,
                    data={
                        "subaction": "login",
                        "sess_id": sess_id,
                        "captcha_code": captcha_code.encode('utf-8', errors='replace').decode('utf-8'),
                    },
                    timeout=10
                )
                f2.raise_for_status()
                if "To finish the login process please solve the following captcha." not in f2.text:
                    log("[Captcha Solver] 驗證通過")
                    return sess_id, session
                else:
                    log("[Captcha Solver] 驗證失敗

")

                    return "-1", session
        else:
            return sess_id, session
    except Exception as e:
        log(f"[AutoEUServerless] 登錄過程中出錯: {e}")
        return "-1", session

def get_servers(sess_id: str, session: requests.Session) -> dict:
    try:
        d = {}
        url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
        headers = {
            "user-agent": user_agent,
            "origin": "https://www.euserv.com",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
        f = session.get(url=url, headers=headers, timeout=10)
        f.raise_for_status()
        soup = BeautifulSoup(f.text.encode('utf-8', errors='replace').decode('utf-8'), "html.parser")
        # 檢查 HTML 結構
        if not soup.select("#kc2_order_customer_orders_tab_content_1"):
            log("[AutoEUServerless] HTML 結構變化，無法找到訂單表格")
            return {}
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
            d[server_id[0].get_text().encode('utf-8', errors='replace').decode('utf-8')] = flag
        return d
    except Exception as e:
        log(f"[AutoEUServerless] 獲取服務器列表失敗: {e}")
        return {}

def renew(
    sess_id: str, session: requests.Session, password: str, order_id: str, mailparser_dl_url_id: str
) -> bool:
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "user-agent": user_agent,
        "Host": "support.euserv.com",
        "origin": "https://www.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        # 選擇續期訂單
        data = {
            "Submit": "Extend contract",
            "sess_id": sess_id,
            "ord_no": order_id,
            "subaction": "choose_order",
            "choose_order_subaction": "show_contract_details",
        }
        response = session.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()

        # 觸發 PIN 發送
        response = session.post(
            url,
            headers=headers,
            data={
                "sess_id": sess_id,
                "subaction": "show_kc2_security_password_dialog",
                "prefix": "kc2_customer_contract_details_extend_contract_",
                "type": "1",
            },
            timeout=10
        )
        response.raise_for_status()

        # 等待並獲取 PIN
        time.sleep(WAITING_TIME_OF_PIN)
        try:
            pin = get_pin_from_mailparser(mailparser_dl_url_id)
            log(f"[MailParser] PIN: {pin}")
        except Exception as e:
            log(f"[MailParser] PIN 獲取失敗: {e}")
            return False

        # 使用 PIN 獲取 token
        data = {
            "auth": pin.encode('utf-8', errors='replace').decode('utf-8'),
            "sess_id": sess_id,
            "subaction": "kc2_security_password_get_token",
            "prefix": "kc2_customer_contract_details_extend_contract_",
            "type": "1",
            "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
        }
        response = session.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        response_data = json.loads(response.text.encode('utf-8', errors='replace').decode('utf-8'))
        if response_data.get("rs") != "success":
            log(f"[AutoEUServerless] token 獲取失敗: {response_data}")
            return False
        token = response_data["token"]["value"]

        # 提交續期請求
        data = {
            "sess_id": sess_id,
            "ord_id": order_id,
            "subaction": "kc2_customer_contract_details_extend_contract_term",
            "token": token,
        }
        response = session.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        log(f"[AutoEUServerless] 續期請求響應: {response.text[:200]}")  # 記錄部分響應內容

        # 增加等待時間，確保續期生效
        time.sleep(10)

        # 驗證續期是否成功
        servers = get_servers(sess_id, session)
        if order_id in servers and not servers[order_id]:
            log(f"[AutoEUServerless] ServerID: {order_id} 已成功續訂!")
            return True
        else:
            log(f"[AutoEUServerless] ServerID: {order_id} 續訂未生效!")
            return False
    except UnicodeEncodeError as e:
        log(f"[AutoEUServerless] 編碼錯誤: {e}")
        return False
    except Exception as e:
        log(f"[AutoEUServerless] 續期過程中出錯: {e}")
        return False

def check(sess_id: str, session: requests.Session):
    try:
        log("[AutoEUServerless] 正在檢查續期狀態...")
        servers = get_servers(sess_id, session)
        if not servers:
            log("[AutoEUServerless] 無法獲取服務器列表，檢查失敗")
            return
        flag = True
        for key, val in servers.items():
            if val:
                flag = False
                log(f"[AutoEUServerless] ServerID: {key} 續期失敗!")
            else:
                log(f"[AutoEUServerless] ServerID: {key} 無需更新或已續期")
        if flag:
            log("[AutoEUServerless] 所有工作完成！")
    except Exception as e:
        log(f"[AutoEUServerless] 檢查狀態失敗: {e}")

def telegram():
    message = (
        "<b>AutoEUServerless 日誌</b>\n\n" + desp +
        "\n<b>版權聲明：</b>\n"
        "本腳本基於 GPL-3.0 許可協議，版權所有。\n\n"
        "<b>致謝：</b>\n"
        "特別感謝 <a href='https://github.com/lw9726/eu_ex'>eu_ex</a> 的貢獻和啟發，本項目在此基礎整理。\n"
        "開發者：<a href='https://github.com/WizisCool'>WizisCool</a>\n"
        "<a href='https://www.nodeseek.com/space/8902#/general'>個人Nodeseek主頁</a>\n"
        "<a href='https://dooo.ng'>個人小站Dooo.ng</a>\n\n"
        "<b>支持項目：</b>\n"
        "⭐️ 給我們一個 GitHub Star! ⭐️\n"
        "<a href='https://github.com/WizisCool/AutoEUServerless'>訪問 GitHub 項目</a>"
    )
    message = message.encode('utf-8', errors='replace').decode('utf-8')
    data = {
        "chat_id": TG_USER_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }
    try:
        response = requests.post(
            TG_API_HOST + "/bot" + TG_BOT_TOKEN + "/sendMessage", data=data, timeout=10
        )
        response.raise_for_status()
        log("Telegram Bot 推送成功")
    except Exception as e:
        log(f"Telegram Bot 推送失敗: {e}")

def main_handler(event, context):
    if not USERNAME or not PASSWORD or not MAILPARSER_DOWNLOAD_URL_ID:
        log("[AutoEUServerless] 缺少必要的環境變量")
        exit(1)
    user_list = USERNAME.strip().split()
    passwd_list = PASSWORD.strip().split()
    mailparser_dl_url_id_list = MAILPARSER_DOWNLOAD_URL_ID.strip().split()
    if len(user_list) != len(passwd_list):
        log("[AutoEUServerless] 用戶名和密碼數量不匹配!")
        exit(1)
    if len(mailparser_dl_url_id_list) != len(user_list):
        log("[AutoEUServerless] mailparser_dl_url_ids 和用戶名的數量不匹配!")
        exit(1)
    for i in range(len(user_list)):
        print("*" * 30)
        log(f"[AutoEUServerless] 正在續費第 {i + 1} 個賬號")
        sessid, s = login(user_list[i], passwd_list[i])
        if sessid == "-1":
            log(f"[AutoEUServerless] 第 {i + 1} 個賬號登錄失敗，請檢查登錄資訊")
            continue
        servers = get_servers(sessid, s)
        log(f"[AutoEUServerless] 檢測到第 {i + 1} 個賬號有 {len(servers)} 台 VPS，正在嘗試續期")
        for k, v in servers.items():
            if v:
                if not renew(sessid, s, passwd_list[i], k, mailparser_dl_url_id_list[i]):
                    log(f"[AutoEUServerless] ServerID: {k} 續訂錯誤!")
                else:
                    log(f"[AutoEUServerless] ServerID: {k} 已成功續訂!")
            else:
                log(f"[AutoEUServerless] ServerID: {k} 無需更新")
        time.sleep(15)
        check(sessid, s)
        time.sleep(5)

    if TG_BOT_TOKEN and TG_USER_ID and TG_API_HOST:
        telegram()

    print("*" * 30)

if __name__ == "__main__":
    main_handler(None, None)
