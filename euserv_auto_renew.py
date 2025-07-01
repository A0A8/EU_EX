#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, time, requests
from bs4 import BeautifulSoup
import ddddocr

# 配置区
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")
USE_PROXY = False
PROXIES = {"http": "http://127.0.0.1:7890","https":"http://127.0.0.1:7890"} if USE_PROXY else {}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)..."

def log(msg):
    print(msg)

# 简易 get/post
def get(url, **kw): return requests.get(url, headers={"User-Agent":UA}, proxies=PROXIES, **kw)
def post(url, **kw): return requests.post(url, headers={"User-Agent":UA}, proxies=PROXIES, **kw)

def login(user, pwd):
    BASE = "https://support.euserv.com/index.iphp"
    sess = requests.Session()
    # 1. GET 登录页
    r0 = sess.get(BASE)
    r0.raise_for_status()
    soup = BeautifulSoup(r0.text, "html.parser")

    # 2. 找到正确的 form：必须同时包含 email/password 输入
    form = None
    for f in soup.find_all("form"):
        names = {inp.get("name") for inp in f.find_all("input")}
        if "email" in names and "password" in names:
            form = f; break
    if not form:
        log("[Login] 未找到含 email/password 的表单"); return None, sess

    # 3. 提取 sess_id（隐藏字段或 Cookie）
    sess_id = sess.cookies.get("PHPSESSID", "")
    hidden = form.find("input", {"name":"sess_id"})
    if hidden and hidden.get("value"): sess_id = hidden["value"]
    if not sess_id:
        log("[Login] 无 sess_id"); return None, sess
    log(f"[Login] sess_id={sess_id}")

    # 4. 构造 POST URL
    action = form.get("action") or BASE
    if "?sess_id=" not in action:
        action = action + ("&" if "?" in action else "?") + f"sess_id={sess_id}"
    action = requests.compat.urljoin(BASE, action)
    log(f"[Login] POST 到: {action}")

    # 5. 收集所有 input
    data = {}
    for inp in form.find_all("input"):
        nm = inp.get("name"); val = inp.get("value","")
        if not nm: continue
        data[nm] = val
    # 覆盖必须字段
    data["email"], data["password"] = user, pwd
    data["subaction"] = "login"
    # 保证含 Submit
    if "Submit" not in data:
        data["Submit"] = "Login"

    # 6. 提交
    r1 = sess.post(action, data=data)
    snippet = r1.text[:300].replace("\n"," ")
    log(f"[Login] 响应: {snippet}")

    # 7. 判断
    if re.search(r"Hello|logout|My Contracts", r1.text, re.IGNORECASE):
        log("[Login] 成功"); return sess_id, sess
    log("[Login] 失败"); return None, sess

if __name__=="__main__":
    sid, session = login("your@example.com","password")
    if not sid:
        log("登陆仍失败，请检查表单结构或网络")  
    else:
        log("登录 OK =", sid)

def renew(sess_id, session, password, order_id, mailparser_id):
    url = "https://support.euserv.com/index.iphp"
    headers = {"User-Agent": USER_AGENT, "Referer": f"https://support.euserv.com/index.iphp?sess_id={sess_id}"}
    session.post(url, headers=headers, data={
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details"
    }, proxies=PROXIES)
    prefix = f"kc2_customer_contract_details_extend_contract_{order_id}"
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "show_kc2_security_password_dialog",
        "prefix": prefix,
        "type": 1
    }, proxies=PROXIES)
    session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "subaction": "kc2_security_password_send_pin",
        "ident": prefix
    }, proxies=PROXIES)
    pin = ''
    for i in range(5):
        time.sleep(WAITING_TIME_OF_PIN)
        pin = get_pin_from_mailparser(mailparser_id)
        log(f"[Mailparser] 第 {i+1} 次 PIN: {pin}")
        if re.match(r'^\d{6}$', pin):
            break
    if not re.match(r'^\d{6}$', pin):
        log("[Renew] PIN 无效")
        return False
    rtok = session.post(url, headers=headers, data={
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": prefix,
        "type": 1,
        "ident": prefix
    }, proxies=PROXIES)
    rtok.raise_for_status()
    try:
        tokj = rtok.json()
        token = tokj.get('token', {}).get('value', '')
    except:
        log(f"[Renew] token 获取失败: {rtok.text[:300]}")
        return False
    if not token:
        log(f"[Renew] token 无效: {rtok.text[:300]}")
        return False
    final = session.post(url, headers=headers, data={
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token
    }, proxies=PROXIES)
    final.raise_for_status()
    t = final.text.lower()
    if "contract has been extended" in t or "successfully extended" in t:
        log(f"[Renew] {order_id} 成功")
        return True
    snippet = final.text[:300].replace("\n", " ")
    log(f"[Renew] 未检测到成功: {snippet}")
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
        log("[Main] 登录成功后续处理暂未实现 get_servers() 方法")
        # 示例：伪代码
        # servers = get_servers(sess_id, session)
        # for srv_id, need_renew in servers.items():
        #     ...
        time.sleep(10)
    print("=" * 20)
    log("[Main] 续费任务结束")

if __name__ == "__main__":
    main()
