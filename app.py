from fastapi import FastAPI, HTTPException, Header
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
import os
import time
import boto3
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

@app.get("/")
def health_check():
    return {"status": "ok", "message": "API is running (Sync Mode + R2)"}

@app.get("/extract-session")
def extract_session(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized API Key")

    if not EMAIL or not PASSWORD:
        raise HTTPException(status_code=500, detail="REVE_EMAIL or REVE_PASSWORD environment variables are missing.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
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

        page.on("request", handle_request)

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
            
            print("[4/5] Entering user credentials (Strict-Bypass Mode)...")
            
            # The :visible flag forces it to ignore the hidden Sign Up inputs
            email_field = page.locator('input[type="email"]:visible')
            pass_field = page.locator('input[type="password"]:visible')
            
            email_field.wait_for(timeout=10000)
            
            # Focus the element, then use raw hardware-level keyboard typing
            email_field.focus()
            page.keyboard.type(EMAIL, delay=100)
            
            pass_field.focus()
            page.keyboard.type(PASSWORD, delay=100)
            
            print("[5/5] Submitting credentials...")
            login_submit_btn = page.locator('text="Log in with email":visible').first
            login_submit_btn.click()
            
            print("⌛ Waiting for home dashboard routing to verify token capture...")
            page.wait_for_url("**/home", timeout=25000)
            time.sleep(4) 
            
        except Exception as e:
            print(f"\n❌ CRASH REASON: {str(e)}\n") # <--- This will print the exact error to your terminal!
            print(f"Taking a screenshot and uploading to R2...")
            
            screenshot_path = "error.png"
            page.screenshot(path=screenshot_path)
            r2_filename = f"reve_debug/error_{int(time.time())}.png"
            
            try:
                s3_client.upload_file(screenshot_path, BUCKET_NAME, r2_filename)
                r2_status = f"Screenshot uploaded to R2 as '{r2_filename}'"
            except Exception as upload_err:
                r2_status = f"R2 Upload Failed: {str(upload_err)}"
            
            browser.close()
            raise HTTPException(status_code=500, detail=f"Blocking occurred. {r2_status}. Check docker logs for exact error.")
            

        cookies = context.cookies()
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        browser.close()

        if not extracted_data["auth_token"]:
            raise HTTPException(status_code=500, detail="Authentication successful, but JWT network interception missed the token context.")

        return {
            "success": True,
            "data": {
                "authorization": extracted_data["auth_token"],
                "cookie": cookie_str
            }
        }
