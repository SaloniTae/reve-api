from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import os
import time

app = FastAPI()

EMAIL = os.getenv("REVE_EMAIL")
PASSWORD = os.getenv("REVE_PASSWORD")
API_KEY = os.getenv("API_KEY")

# ==========================================
# THE CAPTCHA POOL (Stored in RAM)
# ==========================================
# Stores dicts: {"id": "...", "timestamp": 1234567890.12}
captcha_pool = []
MAX_TOKEN_AGE_SECONDS = 180 # 3 minutes max before a token goes bad

class CaptchaPayload(BaseModel):
    captcha_id: str

@app.post("/pool/submit")
def submit_captcha(payload: CaptchaPayload, x_api_key: str = Header(None)):
    """Your phone hits this endpoint to deposit fresh CAPTCHAs."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Store the token with the exact time it arrived
    captcha_pool.append({
        "id": payload.captcha_id,
        "timestamp": time.time()
    })
    return {"status": "success", "pool_size": len(captcha_pool)}

@app.get("/pool/status")
def pool_status():
    """Check how many tokens are in the pool from your browser."""
    return {"total_captchas": len(captcha_pool)}

# ==========================================
# THE PLAYWRIGHT CONSUMER
# ==========================================
@app.get("/extract-session")
def extract_session(x_api_key: str = Header(None)):
    global captcha_pool
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized API Key")

    # 1. CLEAN THE POOL: Remove any tokens older than 3 minutes
    current_time = time.time()
    captcha_pool = [c for c in captcha_pool if (current_time - c["timestamp"]) < MAX_TOKEN_AGE_SECONDS]

    # 2. CHECK THE POOL: Ensure we have at least one fresh token
    if len(captcha_pool) == 0:
        raise HTTPException(status_code=503, detail="Captcha pool is empty or all tokens expired. Waiting for phone harvester.")

    # 3. POP THE FRESHEST TOKEN
    # Grab the newest token at the end of the list and remove it so it can't be reused
    fresh_captcha = captcha_pool.pop(-1)
    captcha_id = fresh_captcha["id"]
    print(f"💉 Injecting fresh CAPTCHA from pool: {captcha_id}", flush=True)

    # 4. RUN PLAYWRIGHT
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

        extracted_data = {"auth_token": None}

        def handle_request(request):
            auth_header = request.headers.get("authorization", "")
            if "Bearer" in auth_header and "v2.login" in auth_header:
                extracted_data["auth_token"] = auth_header

        page.on("request", handle_request)

        try:
            print("[1/3] Loading root website...", flush=True)
            page.goto("https://app.reve.com", wait_until="networkidle")
            
            print("[2/3] Opening Login Modal...", flush=True)
            page.locator('text="Start creating"').first.click(timeout=10000)
            page.locator('text="Log in"').last.click(timeout=10000)
            
            print("[3/3] Typing Credentials & Submitting...", flush=True)
            email_field = page.locator('#form-login input[type="email"]')
            pass_field = page.locator('#form-login input[type="password"]')
            email_field.wait_for(state="visible", timeout=10000)
            
            email_field.fill(EMAIL)
            pass_field.fill(PASSWORD)
            
            page.locator('#form-login button[type="submit"]').click()
            page.wait_for_url("**/home", timeout=25000)
            
        except Exception as e:
            browser.close()
            raise HTTPException(status_code=500, detail=f"Login automation failed: {str(e)}")

        # 5. STITCH THE SESSION TOGETHER
        cookies = context.cookies()
        auth_cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        # Combine the Playwright auth cookies with the Phone CAPTCHA cookie
        final_cookie_str = f"{auth_cookie_str}; captcha_id={captcha_id}"
        
        browser.close()

        if not extracted_data["auth_token"]:
            raise HTTPException(status_code=500, detail="Authentication failed to capture Bearer token.")

        return {
            "success": True,
            "data": {
                "authorization": extracted_data["auth_token"],
                "cookie": final_cookie_str
            }
        }
