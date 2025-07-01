#!/usr/bin/env python3
# 修复版 euserv_auto_renew.py - 更准确判断续期需求并记录日志

import os
import re
import time
import base64
import requests
import ddddocr
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP_SSL, SMTPDataError

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
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
desp = ""
ocr_local = ddddocr.DdddOcr()

def log(info: str):
    global desp
    print(info)
    desp += info + "\n"

def get_servers(sess_id, session):
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    r = session.get(url, headers={"User-Agent": user_agent})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    servers = {}
    rows = soup.select("#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr")
    for tr in rows:
        sid_els = tr.select(".td-z1-sp1-kc")
        if len(sid_els) != 1:
            continue
        server_id = sid_els[0].get_text().strip()
        need_renew = bool(tr.select('input[type="submit"][value="Extend contract"]'))
        servers[server_id] = need_renew
    return servers

# 其余函数逻辑与原始脚本相同，已省略
# 请将此函数替换进完整脚本中用于更精确地判断续期状态
