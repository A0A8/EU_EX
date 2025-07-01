import os, re, time, base64, json, requests, ddddocr
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from smtplib import SMTP_SSL, SMTPDataError
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

USERNAME = os.environ.get("USERNAME")
PASSWORD = os.environ.get("PASSWORD")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID")
MAILPARSER_DOWNLOAD_BASE_URL = "https://files.mailparser.io/d/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")
TG_API_HOST = os.environ.get("TG_API_HOST", "https://api.telegram.org")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
YD_EMAIL = os.environ.get("YD_EMAIL", "")
YD_APP_PWD = os.environ.get("YD_APP_PWD", "")
OCR_SPACE_API_KEY = os.environ.get("OCR_API_KEY", "")
LOGIN_MAX_RETRY_COUNT = 5
WAITING_TIME_OF_PIN = 15
LAST_RUN_FILE = ".last_run"
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
desp = ""
ocr_local = ddddocr.DdddOcr()

def log(info): 
    global desp; 
    print(info); 
    desp += info + "\n"

def should_run():
    now = datetime.utcnow()
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r") as f:
                last = datetime.fromisoformat(f.read().strip())
            if now - last < timedelta(days=20):
                print(f"[调度] 上次运行 {last.isoformat()}，未满20天，跳过。")
                return False
        except:
            pass
    with open(LAST_RUN_FILE, "w") as f:
        f.write(now.isoformat())
    return True

def captcha_solver(captcha_image_url, session):
    image_bytes = session.get(captcha_image_url).content
    try:
        log("[Captcha] 使用 ddddocr...")
        result = ocr_local.classification(image_bytes)
        if result.strip(): 
            return {"result": result.strip()}
    except Exception as e:
        log(f"[ddddocr] 错误: {e}")
    try:
        log("[Captcha] 使用 OCR.space...")
        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "apikey": OCR_SPACE_API_KEY,
            "language": "eng",
            "isOverlayRequired": False,
            "base64Image": "data:image/png;base64," + b64,
        }
        r = requests.post("https://api.ocr.space/parse/image", data=payload)
        r.raise_for_status()
        parsed = r.json()["ParsedResults"][0]["ParsedText"].strip()
        return {"result": parsed}
    except Exception as e:
        log(f"[OCR.space] 错误: {e}")
        return {"result": ""}

def handle_captcha_solved_result(solved): return solved.get("result", "")

def login_retry(max_retry=3):
    def decorator(func):
        def wrapper(username, password):
            for i in range(max_retry):
                sid, sess = func(username, password)
                if sid != "-1":
                    return sid, sess
                log(f"[Login] 第 {i+1} 次失败")
            return "-1", sess
        return wrapper
    return decorator

@login_retry()
def login(username, password):
    headers = {"User-Agent": user_agent}
    session = requests.Session()
    url = "https://support.euserv.com/index.iphp"
    sid = re.search(r'PHPSESSID=(\w+);', str(session.get(url, headers=headers).headers)).group(1)
    data = {
        "email": username, "password": password, "form_selected_language": "en",
        "Submit": "Login", "subaction": "login", "sess_id": sid
    }
    r = session.post(url, headers=headers, data=data)
    if "captcha" in r.text:
        log("[Login] 需要验证码")
        solved = captcha_solver("https://support.euserv.com/securimage_show.php", session)
        code = handle_captcha_solved_result(solved)
        log(f"[Captcha] 识别结果: {code}")
        r2 = session.post(url, headers=headers, data={
            "subaction": "login", "sess_id": sid, "captcha_code": code
        })
        if "captcha" not in r2.text: 
            return sid, session
        return "-1", session
    return sid, session

def get_servers(sid, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sid}"
    r = session.get(url, headers={"User-Agent": user_agent})
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    for tr in soup.select("#kc2_order_customer_orders_tab_content_1 tr"):
        sid_tag = tr.select_one(".td-z1-sp1-kc")
        if sid_tag:
            server_id = sid_tag.text.strip()
            need = bool(tr.select_one("input[value='Extend contract']"))
            servers[server_id] = need
    return servers

def get_pin(mail_id):
    r = requests.get(f"{MAILPARSER_DOWNLOAD_URL_ID}{mail_id}")
    return r.json()[0]["pin"]

def renew(sid, session, pwd, order_id, mail_id):
    url = "https://support.euserv.com/index.iphp"
    headers = {"User-Agent": user_agent}
    session.post(url, headers=headers, data={
        "Submit": "Extend contract", "sess_id": sid, "ord_no": order_id,
        "subaction": "choose_order", "choose_order_subaction": "show_contract_details"
    })
    session.post(url, headers=headers, data={
        "sess_id": sid, "subaction": "show_kc2_security_password_dialog",
        "prefix": "kc2_customer_contract_details_extend_contract_", "type": "1"
    })
    time.sleep(WAITING_TIME_OF_PIN)
    pin = get_pin(mail_id)
    log(f"[PIN] {pin}")
    token_req = session.post(url, headers=headers, data={
        "auth": pin, "sess_id": sid, "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_", "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}"
    }).json()
    if token_req.get("rs") != "success":
        return False
    token = token_req["token"]["value"]
    session.post(url, headers=headers, data={
        "sess_id": sid, "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token
    })
    return True

def check(sid, session):
    failed = [k for k,v in get_servers(sid, session).items() if v]
    if failed:
        for f in failed:
            log(f"[Check] {f} 续期失败")
    else:
        log("[Check] 所有续期成功")

def telegram():
    data = {"chat_id": TG_USER_ID, "text": "EUserv续费日志"+desp}
    requests.post(f"{TG_API_HOST}/bot{TG_BOT_TOKEN}/sendMessage", data=data)

def send_mail():
    msg = MIMEMultipart(); msg["From"]=YD_EMAIL; msg["To"]=RECEIVER_EMAIL
    msg["Subject"]="EUserv续费日志"; msg.attach(MIMEText(desp,"plain","utf-8"))
    s = SMTP_SSL("smtp.yandex.ru",465); s.login(YD_EMAIL,YD_APP_PWD)
    s.sendmail(YD_EMAIL,RECEIVER_EMAIL,msg.as_string()); s.quit()
