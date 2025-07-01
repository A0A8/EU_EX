#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import re
import requests
import ddddocr
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys

# === 配置项 ===
USERNAME = os.environ.get("USERNAME", "")
PASSWORD = os.environ.get("PASSWORD", "")
MAILPARSER_DOWNLOAD_URL_ID = os.environ.get("MAILPARSER_DOWNLOAD_URL_ID", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_USER_ID = os.environ.get("TG_USER_ID", "")

# === 函数定义 ===

def log(msg):
    print(msg)

def tg_notify(msg):
    if TG_BOT_TOKEN and TG_USER_ID:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data={"chat_id": TG_USER_ID, "text": msg}
        )

def get_latest_pin(mailparser_id):
    try:
        url = f"https://files.mailparser.io/d/{mailparser_id}"
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        return data[0].get('pin', '')
    except Exception as e:
        log(f"[PIN] 获取失败: {e}")
        return ""

def solve_captcha(image_bytes):
    ocr = ddddocr.DdddOcr()
    try:
        text = ocr.classification(image_bytes)
        log(f"[OCR] 原始: {text}")
        expr = text.replace("x", "*").replace("X", "*").replace(" ", "")
        if re.match(r"^[\d\+\-\*/]+$", expr):
            result = eval(expr)
            return str(int(result))
    except Exception as e:
        log(f"[OCR] 识别失败: {e}")
    return ""

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def login_and_renew(driver, username, password, mailparser_id):
    log("[Main] 打开 EUserv 登录页")
    driver.get("https://support.euserv.com/index.iphp")

    log("[Main] 输入用户名密码")
    driver.find_element(By.NAME, "email").send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)

    if driver.page_source.find("captcha_code") != -1:
        log("[Captcha] 检测到验证码")
        img_elem = driver.find_element(By.XPATH, "//img[contains(@src, 'securimage_show.php')]")
        captcha_bytes = img_elem.screenshot_as_png
        solved = solve_captcha(captcha_bytes)
        log(f"[Captcha] 输入验证码: {solved}")
        driver.find_element(By.NAME, "captcha_code").send_keys(solved)

    driver.find_element(By.NAME, "Submit").click()
    time.sleep(2)

    if "Hello" not in driver.page_source:
        log("[Login] 登录失败")
        return

    log("[Login] 登录成功")

    # 检查 VPS 列表
    if "No active contracts found" in driver.page_source:
        log("[VPS] 未发现可续费 VPS")
        return

    # 进入 VPS 合同页
    links = driver.find_elements(By.PARTIAL_LINK_TEXT, "Contract")
    if not links:
        log("[VPS] 没找到合同链接")
        return

    for idx, link in enumerate(links):
        try:
            log(f"[VPS] 第 {idx+1} 台尝试续费")
            link.click()
            time.sleep(2)

            extend_btn = driver.find_element(By.XPATH, "//input[@value='Extend contract']")
            extend_btn.click()
            time.sleep(2)

            pin_btn = driver.find_element(By.NAME, "kc2_security_password_send_pin")
            pin_btn.click()
            log("[PIN] 请求已发送")

            time.sleep(15)
            pin = get_latest_pin(mailparser_id)
            log(f"[PIN] 获取: {pin}")
            if not re.match(r"^\d{6}$", pin):
                log("[PIN] 无效")
                return

            driver.find_element(By.NAME, "auth").send_keys(pin)
            driver.find_element(By.NAME, "kc2_security_password_get_token").click()
            time.sleep(2)

            token_input = driver.find_element(By.NAME, "token")
            token = token_input.get_attribute("value")
            if not token:
                log("[Token] 获取失败")
                return

            driver.find_element(By.NAME, "kc2_customer_contract_details_extend_contract_term").click()
            time.sleep(2)

            if "successfully extended" in driver.page_source.lower():
                log("[Success] VPS续费成功")
                tg_notify(f"EUserv 账号 {username} VPS 续费成功")
            else:
                log("[Failed] VPS续费可能失败")
                tg_notify(f"EUserv 账号 {username} VPS 续费可能失败")
        except Exception as e:
            log(f"[Error] VPS续费异常: {e}")
        finally:
            driver.get("https://support.euserv.com/index.iphp")  # 回主页准备下一个

def main():
    users = USERNAME.strip().split()
    pwds = PASSWORD.strip().split()
    pins = MAILPARSER_DOWNLOAD_URL_ID.strip().split()

    if not (len(users) == len(pwds) == len(pins)):
        log("[Main] 用户数与PIN数量不匹配")
        return

    driver = setup_driver()

    for i in range(len(users)):
        log("=" * 30)
        log(f"[Main] 开始续费第 {i+1} 个账号：{users[i]}")
        try:
            login_and_renew(driver, users[i], pwds[i], pins[i])
        except Exception as e:
            log(f"[Main] 账号 {users[i]} 执行异常：{e}")

    driver.quit()
    log("[Main] 所有任务完成")

if __name__ == "__main__":
    main()
