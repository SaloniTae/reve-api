from fastapi import FastAPI, HTTPException, Header
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
import os
import asyncio

app = FastAPI()

EMAIL = os.getenv("REVE_EMAIL")
PASSWORD = os.getenv("REVE_PASSWORD")
API_KEY = os.getenv("API_KEY")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "API is running"}

@app.get("/extract-session")
async def extract_session(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized API Key")

    if not EMAIL or not PASSWORD:
        raise HTTPException(status_code=500, detail="REVE_EMAIL or REVE_PASSWORD environment variables are missing.")

    async with async_playwright() as p:
        # Launch headless browser with anti-detect flags
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await stealth_async(page)

        extracted_data = {"auth_token": None}

        # Background traffic interceptor for the JWT Token
        async def handle_request(request):
            auth_header = request.headers.get("authorization", "")
            if "Bearer" in auth_header and "v2.login" in auth_header:
                extracted_data["auth_token"] = auth_header

        page.on("request", handle_request)

        try:
            # 1. Navigate to the clean main lander landing page
            print("[1/5] Loading root website...")
            await page.goto("https://app.reve.com", wait_until="networkidle")
            
            # 2. Click the top right "Start creating" button to open the Sign-up modal
            print("[2/5] Clicking 'Start creating' button...")
            start_creating_btn = page.locator('text="Start creating"').first
            await start_creating_btn.wait_for(state="visible", timeout=10000)
            await start_creating_btn.click()
            
            # 3. Toggle the view from "Sign up" to "Log in" via the bottom text link
            print("[3/5] Switching modal from Sign up to Log in...")
            await page.wait_for_selector('text="Already have an account?"', timeout=10000)
            # Targets the explicit "Log in" switch node at the very bottom
            login_toggle = page.locator('text="Log in"').last
            await login_toggle.click()
            
            # 4. Fill out credentials inside the now-visible Login fields
            print("[4/5] Entering user credentials...")
            await page.wait_for_selector('input[type="email"]', state="visible", timeout=10000)
            await page.fill('input[type="email"]', EMAIL)
            await page.fill('input[type="password"]', PASSWORD)
            
            # 5. Click the primary submission button
            print("[5/5] Submitting credentials...")
            login_submit_btn = page.locator('text="Log in with email"').first
            await login_submit_btn.click()
            
            # Wait for the transition to the internal home route
            print("⌛ Waiting for home dashboard routing to verify token capture...")
            await page.wait_for_url("**/home", timeout=25000)
            await asyncio.sleep(4) # Allow telemetry handshakes to settle
            
        except Exception as e:
            await browser.close()
            raise HTTPException(status_code=500, detail=f"Interface automation failed: {str(e)}")

        # Extract context cookies
        cookies = await context.cookies()
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        await browser.close()

        if not extracted_data["auth_token"]:
            raise HTTPException(status_code=500, detail="Authentication successful, but JWT network interception missed the token context.")

        return {
            "success": True,
            "data": {
                "authorization": extracted_data["auth_token"],
                "cookie": cookie_str
            }
        }
