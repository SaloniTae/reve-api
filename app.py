from fastapi import FastAPI, HTTPException, Header
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
import os
import time

app = FastAPI()

EMAIL = os.getenv("REVE_EMAIL")
PASSWORD = os.getenv("REVE_PASSWORD")
API_KEY = os.getenv("API_KEY")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "API is running (Sync Mode)"}

@app.get("/extract-session")
def extract_session(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized API Key")

    if not EMAIL or not PASSWORD:
        raise HTTPException(status_code=500, detail="REVE_EMAIL or REVE_PASSWORD environment variables are missing.")

    # Using standard Sync Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # Apply the synchronous stealth patch
        stealth_sync(page)

        extracted_data = {"auth_token": None}

        # Background traffic interceptor
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
            
            print("[4/5] Entering user credentials...")
            page.wait_for_selector('input[type="email"]', state="visible", timeout=10000)
            page.fill('input[type="email"]', EMAIL)
            page.fill('input[type="password"]', PASSWORD)
            
            print("[5/5] Submitting credentials...")
            login_submit_btn = page.locator('text="Log in with email"').first
            login_submit_btn.click()
            
            print("⌛ Waiting for home dashboard routing to verify token capture...")
            page.wait_for_url("**/home", timeout=25000)
            time.sleep(4) # Standard Python time sleep
            
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=f"Interface automation failed: {str(e)}")

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
