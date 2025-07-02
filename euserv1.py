import requests
from bs4 import BeautifulSoup
import time
import os
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import requests.exceptions
import ddddocr
import json

# Initialize a global session for consistent state management
session = requests.Session()

# Headers for HTTP requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Origin": "https://www.euserv.com",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://support.euserv.com/"
}

def log(info: str):
    """Log messages with timestamp."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {info}")

def send_telegram_message(bot_token: str, user_id: str, message: str):
    """Send a message via Telegram if credentials are provided."""
    if bot_token and user_id:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {"chat_id": user_id, "text": message}
            response = session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            log("[Telegram] Message sent successfully")
        except Exception as e:
            log(f"[Telegram] Failed to send message: {str(e)}")

@retry(stop=stop_after_attempt(5), wait=wait_fixed(5), retry=retry_if_exception_type(requests.exceptions.RequestException))
def solve_captcha(ocr_api_key: str) -> str:
    """Solve captcha using OCR.space API."""
    try:
        # Fetch captcha image
        captcha_url = "https://support.euserv.com/index.iphp?captcha"
        response = session.get(captcha_url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        
        # Use ddddocr as fallback if OCR.space is not preferred
        ocr = ddddocr.DdddOcr()
        captcha_code = ocr.classification(response.content)
        log(f"[Captcha Solver] Identified captcha code: {captcha_code}")
        return captcha_code
    except Exception as e:
        log(f"[Captcha Solver] Error solving captcha: {str(e)}")
        raise

@retry(stop=stop_after_attempt(5), wait=wait_fixed(5), retry=retry_if_exception_type(requests.exceptions.RequestException))
def login(username: str, password: str, ocr_api_key: str) -> str:
    """Log into EUserv and return session ID."""
    url = "https://support.euserv.com/index.iphp"
    attempt = 0
    max_attempts = 5
    
    while attempt < max_attempts:
        attempt += 1
        log(f"[AutoEUServerless] Login attempt {attempt}")
        try:
            # Get login page
            response = session.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Solve captcha
            captcha_code = solve_captcha(ocr_api_key)
            
            # Submit login form
            data = {
                "email": username,
                "password": password,
                "captcha_code": captcha_code,
                "subaction": "login",
                "submit": "Login"
            }
            response = session.post(url, headers=HEADERS, data=data, timeout=20)
            response.raise_for_status()
            
            # Check if login was successful
            soup = BeautifulSoup(response.text, "html.parser")
            if "logout" in response.text.lower():
                sess_id = soup.find("input", {"name": "sess_id"}).get("value") if soup.find("input", {"name": "sess_id"}) else ""
                log("[AutoEUServerless] Login successful")
                return sess_id
            else:
                log("[Captcha Solver] Captcha verification failed")
                if attempt == max_attempts:
                    raise Exception("Max login attempts reached")
                time.sleep(5)
        except Exception as e:
            log(f"[AutoEUServerless] Login error: {str(e)}")
            if attempt == max_attempts:
                raise
            time.sleep(5)
    return ""

def get_pin_from_mailparser(mailparser_dl_url_id: str) -> str:
    """Retrieve PIN from Mailparser API."""
    try:
        url = f"https://api.mailparser.io/v1/download/{mailparser_dl_url_id}"
        response = session.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        pin = response.json().get("pin", "")  # Adjust based on actual API response
        log(f"[MailParser] PIN: {pin}")
        return pin
    except Exception as e:
        log(f"[MailParser] Error retrieving PIN: {str(e)}")
        return ""

def get_servers(sess_id: str) -> dict:
    """Retrieve server list from EUserv."""
    url = f"https://support.euserv.com/index.iphp?sess_id={sess_id}"
    try:
        response = session.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        
        # Save HTML for debugging
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        log("[AutoEUServerless] Saved HTML to debug.html for inspection")
        
        soup = BeautifulSoup(response.text, "html.parser")
        # Update selector based on current HTML structure (inspect manually)
        table = soup.select_one("#kc2_order_customer_orders_tab_content_1")  # Placeholder
        if not table:
            log("[AutoEUServerless] HTML structure changed, cannot find order table")
            return {}
        
        servers = {}
        for tr in table.select("tr"):
            server_id = tr.select_one(".td-z1-sp1-kc")
            if not server_id:
                continue
            flag = "Contract extension possible from" not in tr.select_one(".td-z1-sp2-kc .kc2_order_action_container").text
            servers[server_id.text] = flag
        return servers
    except Exception as e:
        log(f"[AutoEUServerless] Failed to get server list: {str(e)}")
        return {}

def renew(sess_id: str, password: str, order_id: str, mailparser_dl_url_id: str) -> bool:
    """Attempt to renew a server with the given PIN."""
    url = "https://support.euserv.com/index.iphp"
    try:
        # Get PIN from Mailparser
        pin = get_pin_from_mailparser(mailparser_dl_url_id)
        if not pin:
            log("[AutoEUServerless] Failed to retrieve PIN")
            return False
        
        # Submit renewal request
        data = {
            "sess_id": sess_id,
            "ord_id": order_id,
            "subaction": "kc2_customer_contract_details_extend_contract_term",
            "pin": pin
        }
        response = session.post(url, headers=HEADERS, data=data, timeout=20)
        response.raise_for_status()
        
        # Log response for debugging
        with open("renew_response.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        log(f"[AutoEUServerless] Renewal response saved to renew_response.html")
        log(f"[AutoEUServerless] Renewal response status: {response.status_code}")
        
        # Wait for server to process
        time.sleep(20)
        
        # Check renewal status
        servers = get_servers(sess_id)
        if order_id in servers and not servers[order_id]:
            log(f"[AutoEUServerless] ServerID: {order_id} renewed successfully!")
            return True
        else:
            log(f"[AutoEUServerless] ServerID: {order_id} renewal not effective!")
            return False
    except Exception as e:
        log(f"[AutoEUServerless] Renewal error: {str(e)}")
        return False

def main():
    """Main function to orchestrate the renewal process."""
    log("******************************")
    log("[AutoEUServerless] Starting renewal for account 1")
    
    # Load environment variables
    username = os.getenv("EUSERV_USERNAME")
    password = os.getenv("EUSERV_PASSWORD")
    ocr_api_key = os.getenv("OCR_SPACE_API_KEY")
    mailparser_dl_url_id = os.getenv("MAILPARSER_DOWNLOAD_URL_ID")
    tg_bot_token = os.getenv("TG_BOT_TOKEN")
    tg_user_id = os.getenv("TG_USER_ID")
    
    if not all([username, password, ocr_api_key, mailparser_dl_url_id]):
        log("[AutoEUServerless] Missing environment variables")
        send_telegram_message(tg_bot_token, tg_user_id, "EUserv renewal failed: Missing environment variables")
        return
    
    # Login
    sess_id = login(username, password, ocr_api_key)
    if not sess_id:
        log("[AutoEUServerless] Login failed")
        send_telegram_message(tg_bot_token, tg_user_id, "EUserv renewal failed: Login unsuccessful")
        return
    
    # Get server list
    servers = get_servers(sess_id)
    if not servers:
        log("[AutoEUServerless] No servers found or parsing failed")
        send_telegram_message(tg_bot_token, tg_user_id, "EUserv renewal failed: Unable to retrieve server list")
        return
    
    log(f"[AutoEUServerless] Detected {len(servers)} VPS for account 1, attempting renewal")
    
    # Attempt renewal for each server
    for server_id, can_renew in servers.items():
        if can_renew:
            success = renew(sess_id, password, server_id, mailparser_dl_url_id)
            if success:
                send_telegram_message(tg_bot_token, tg_user_id, f"EUserv renewal succeeded for ServerID: {server_id}")
            else:
                send_telegram_message(tg_bot_token, tg_user_id, f"EUserv renewal failed for ServerID: {server_id}")
    
    # Final status check
    log("[AutoEUServerless] Checking renewal status...")
    servers = get_servers(sess_id)
    if not servers:
        log("[AutoEUServerless] Unable to retrieve server list, check failed")
        send_telegram_message(tg_bot_token, tg_user_id, "EUserv renewal status check failed")
    log("******************************")

if __name__ == "__main__":
    main()
