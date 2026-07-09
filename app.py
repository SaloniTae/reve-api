from fastapi import FastAPI, HTTPException, Header
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
import os
import time
import boto3
import random
from urllib.parse import urlparse
from botocore.config import Config

app = FastAPI()

EMAIL = os.getenv("REVE_EMAIL")
PASSWORD = os.getenv("REVE_PASSWORD")
API_KEY = os.getenv("API_KEY")

# Cloudflare R2 Configuration
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ENDPOINT = "https://912665bde4a5e0c8559acb3b0b1cd8e9.r2.cloudflarestorage.com"
BUCKET_NAME = "oor-ad"

# Initialize S3 Client for Cloudflare R2
s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name='auto',
    config=Config(signature_version='s3v4')
)

def get_random_proxy(file_path="proxies.txt"):
    """Reads the proxy txt file, ignores comments, and formats for Playwright."""
    if not os.path.exists(file_path):
        print(f"⚠️ Proxy file '{file_path}' not found. Running without proxy.", flush=True)
        return None
        
    with open(file_path, "r") as f:
        # Extract only valid URI lines, ignoring # comments and empty spaces
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
    if not lines:
        return None
        
    raw_proxy = random.choice(lines)
    parsed = urlparse(raw_proxy)
    
    return {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        "username": parsed.username,
        "password": parsed.password
    }

@app.get("/")
def health_check():
    return {"status": "ok", "message": "API is running (Sync Mode + R2 + Proxies)"}

@app.get("/extract-session")
def extract_session(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized API Key")

    if not EMAIL or not PASSWORD:
        raise HTTPException(status_code=500, detail="REVE_EMAIL or REVE_PASSWORD environment variables are missing.")

    with sync_playwright() as p:
        
        # Configure Launch Arguments
        launch_args = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        }
        
        # Inject Webshare Proxy
        proxy_config = get_random_proxy("proxies.txt")
        if proxy_config:
            launch_args["proxy"] = proxy_config
            print(f"🌍 Launching browser via proxy server: {proxy_config['server']}", flush=True)

        browser = p.chromium.launch(**launch_args)
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()

        # Ensure stealth is ON!
        stealth_sync(page)

        extracted_data = {"auth_token": None}

        def handle_request(request):
            auth_header = request.headers.get("authorization", "")
            if "Bearer" in auth_header and "v2.login" in auth_header:
                extracted_data["auth_token"] = auth_header

        # Upgraded Sniffer to watch the exact anti-bot endpoint
        def handle_response(response):
            try:
                if "verify_recaptcha" in response.url:
                    print(f"🛡️ [RECAPTCHA API] Server responded with Status: {response.status}", flush=True)
                    if response.status == 200:
                        print("   ✅ Bot check passed!", flush=True)
                    elif response.status == 403:
                        print("   ❌ Bot check failed (403 Forbidden)!", flush=True)

                headers = response.headers
                if "set-cookie" in headers:
                    cookie_data = headers["set-cookie"]
                    if "captcha_id" in cookie_data:
                        print(f"🚨 BINGO! SERVER HANDED OVER CAPTCHA_ID!", flush=True)
            except Exception:
                pass

        page.on("request", handle_request)
        page.on("response", handle_response)

        try:
            print("[1/5] Loading root website...")
            page.goto("https://app.reve.com", wait_until="networkidle")
            
            print("[2/5] Clicking 'Start creating' button...")
            start_creating_btn = page.locator('text="Start creating"').first
            start_creating_btn.wait_for(state="visible", timeout=10000)
            start_creating_btn.click()
            
            print("[3/5] Switching modal from Sign up to Log in...")
            page.wait_for_selector('text="Already have an account?"', timeout=10000)
            login_toggle = page.locator('text="Log in"').last
            login_toggle.click()
            
            print("[4/5] Entering user credentials...")
            email_field = page.locator('#form-login input[type="email"]')
            pass_field = page.locator('#form-login input[type="password"]')
            email_field.wait_for(state="visible", timeout=10000)
            
            email_field.focus()
            page.keyboard.type(EMAIL, delay=100)
            pass_field.focus()
            page.keyboard.type(PASSWORD, delay=100)
            
            print("[5/6] Submitting credentials...")
            login_submit_btn = page.locator('#form-login button[type="submit"]')
            login_submit_btn.click()
            
            print("⌛ Waiting for home dashboard routing...")
            page.wait_for_url("**/home", timeout=25000)
            time.sleep(2) 
            
            print("[6/6] Navigating to /albums/new to trigger CAPTCHA...", flush=True)
            page.goto("https://app.reve.com/albums/new", wait_until="networkidle")
            
            print("🐁 Generating human mouse telemetry for trust score...", flush=True)
            page.mouse.move(100, 200)
            time.sleep(0.5)
            page.mouse.move(500, 400)
            time.sleep(0.5)
            page.mouse.move(300, 600)
            time.sleep(0.5)
            page.mouse.click(300, 600)
            
            print("⌛ Waiting for verify_recaptcha background API...", flush=True)
            captcha_found = False
            for attempt in range(15): 
                cookies = context.cookies()
                if any(c['name'] == 'captcha_id' for c in cookies):
                    captcha_found = True
                    print(f"✅ captcha_id safely secured in cookie jar!", flush=True)
                    break
                time.sleep(1)
                
            if not captcha_found:
                print("❌ Failed to secure captcha_id. Server likely threw a 403.", flush=True)

        except Exception as e:
            print(f"\n❌ CRASH REASON: {str(e)}\n")
            browser.close()
            raise HTTPException(status_code=500, detail="Automation crashed. Check logs.")

        # Final Extraction
        cookies = context.cookies()
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        browser.close()

        if not extracted_data["auth_token"]:
            raise HTTPException(status_code=500, detail="Missed Bearer token.")

        return {
            "success": True,
            "data": {
                "authorization": extracted_data["auth_token"],
                "cookie": cookie_str
            }
        }
