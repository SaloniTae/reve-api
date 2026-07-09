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
MAX_RETRIES = 5 # How many proxies it will burn through before giving up

s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name='auto',
    config=Config(signature_version='s3v4')
)

def get_random_proxy(file_path="rotatingproxies.txt"):
    """Reads the rotatingproxies.txt file, ignores comments, and formats for Playwright."""
    if not os.path.exists(file_path):
        return None
        
    with open(file_path, "r") as f:
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
    return {"status": "ok", "message": "API is running (Auto-Retry Proxy Mode)"}

@app.get("/extract-session")
def extract_session(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized API Key")

    if not EMAIL or not PASSWORD:
        raise HTTPException(status_code=500, detail="Credentials missing.")

    # 🚀 THE RETRY LOOP
    for attempt in range(MAX_RETRIES):
        print(f"\n==========================================", flush=True)
        print(f"🔄 STARTING ATTEMPT {attempt + 1} OF {MAX_RETRIES}", flush=True)
        print(f"==========================================\n", flush=True)
        
        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
            }
            
            # Fetch a fresh proxy for every single attempt
            proxy_config = get_random_proxy("rotatingproxies.txt")
            if proxy_config:
                launch_args["proxy"] = proxy_config
                print(f"🌍 Using Proxy: {proxy_config['username']}@{proxy_config['server']}", flush=True)

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()
            stealth_sync(page)

            extracted_data = {"auth_token": None}

            def handle_request(request):
                auth_header = request.headers.get("authorization", "")
                if "Bearer" in auth_header and "v2.login" in auth_header:
                    extracted_data["auth_token"] = auth_header

            def handle_response(response):
                try:
                    if "verify_recaptcha" in response.url:
                        print(f"🛡️ [RECAPTCHA] Server responded with Status: {response.status}", flush=True)
                except Exception:
                    pass

            page.on("request", handle_request)
            page.on("response", handle_response)

            try:
                print("[1/5] Loading root website...", flush=True)
                page.goto("https://app.reve.com", wait_until="networkidle", timeout=35000)
                
                print("[2/5] Opening Login Modal...", flush=True)
                page.locator('text="Start creating"').first.click(timeout=10000)
                page.locator('text="Log in"').last.click(timeout=10000)
                
                print("[3/5] Typing Credentials...", flush=True)
                email_field = page.locator('#form-login input[type="email"]')
                pass_field = page.locator('#form-login input[type="password"]')
                email_field.wait_for(state="visible", timeout=10000)
                
                email_field.focus()
                page.keyboard.type(EMAIL, delay=100)
                pass_field.focus()
                page.keyboard.type(PASSWORD, delay=100)
                
                print("[4/5] Submitting...", flush=True)
                page.locator('#form-login button[type="submit"]').click()
                page.wait_for_url("**/home", timeout=25000)
                time.sleep(2)
                
                print("[5/5] Triggering CAPTCHA environment...", flush=True)
                page.goto("https://app.reve.com/albums/new", wait_until="networkidle", timeout=30000)
                
                print("🐁 Injecting human telemetry (Smooth Drag)...", flush=True)
                page.mouse.move(100, 200)
                page.mouse.down() # Simulate holding left click
                page.mouse.move(400, 500, steps=15) # Smoothly drag across screen
                page.mouse.up()
                page.mouse.wheel(0, 400) # Scroll down
                time.sleep(1)
                page.mouse.click(600, 300) # Random click
                
                print("⌛ Scanning for captcha_id...", flush=True)
                captcha_found = False
                for _ in range(12): 
                    cookies = context.cookies()
                    if any(c['name'] == 'captcha_id' for c in cookies):
                        captcha_found = True
                        print(f"✅ SUCCESS! captcha_id secured on this proxy!", flush=True)
                        break
                    time.sleep(1)
                    
                if not captcha_found or not extracted_data["auth_token"]:
                    raise Exception("Proxy IP was blocked by reCAPTCHA (No cookie returned).")

                # If it made it here without raising an exception, WE WON!
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in context.cookies()])
                browser.close()
                
                return {
                    "success": True,
                    "data": {
                        "authorization": extracted_data["auth_token"],
                        "cookie": cookie_str
                    }
                }

            except Exception as e:
                print(f"❌ ATTEMPT FAILED: {str(e)}", flush=True)
                
                # Only upload a screenshot to R2 if ALL 5 proxy attempts fail to save storage space
                if attempt == MAX_RETRIES - 1:
                    print("📸 Maximum retries hit. Uploading failure screenshot to R2...", flush=True)
                    try:
                        screenshot_path = "error.png"
                        page.screenshot(path=screenshot_path)
                        r2_filename = f"reve_debug/error_{int(time.time())}.png"
                        s3_client.upload_file(screenshot_path, BUCKET_NAME, r2_filename)
                    except Exception:
                        pass
                
                browser.close()
                time.sleep(2) # Give the VPS a 2-second breather before opening the next browser

    raise HTTPException(status_code=500, detail="All 5 proxy attempts failed. Check R2 bucket for final screenshot.")
