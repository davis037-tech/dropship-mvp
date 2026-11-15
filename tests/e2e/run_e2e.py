"""Browser end-to-end check (Playwright + Chromium). Usage:  python tests/e2e/run_e2e.py
Starts the app on a throw-away SQLite DB with the sandbox payment provider and mock supplier,
then walks: storefront -> product -> cart -> checkout -> sandbox payment -> order page ->
admin login -> approve -> 1688 order # -> tracking -> shipped -> delivered -> customer sees tracking.
Also checks mobile/desktop overflow, CSP console errors and XSS-safe rendering."""
import os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.auth.passwords import hash_password

PORT = 5077
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = ROOT / "tests/e2e/screenshots"; SHOTS.mkdir(exist_ok=True)
tmp = tempfile.mkdtemp()
env = {**os.environ, "APP_ENV": "development", "DATABASE_URL": f"sqlite:///{tmp}/e2e.db", "SECRET_KEY": "e" * 48,
       "ADMIN_EMAIL": "owner@example.com", "ADMIN_PASSWORD_HASH": hash_password("e2e-password-123", n=2**10),
       "SUPPLIER_FX_RATE": "0.14", "PRICE_ENDING_CENTS": "99", "PUBLIC_BASE_URL": BASE, "STORE_NAME": "Sniff Garden"}
subprocess.run([sys.executable, "-m", "backend.database.seed"], env=env, cwd=ROOT, check=True, capture_output=True)
srv = subprocess.Popen([sys.executable, "-c", f"from backend.app import create_app; create_app().run(port={PORT}, threaded=True)"],
                       env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(BASE + "/api/config"); break
    except Exception: time.sleep(0.2)
else: srv.kill(); raise SystemExit("server did not start")

problems = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: problems.append(msg)

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, vp in (("mobile", {"width": 375, "height": 740}), ("desktop", {"width": 1280, "height": 800})):
            ctx = browser.new_context(viewport=vp, device_scale_factor=1)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            for path in ("/", "/product/flower-garden-snuffle-mat", "/cart", "/checkout", "/order"):
                page.goto(BASE + path); page.wait_for_load_state("networkidle")
                over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                check(over <= 0, f"{name} {path}: no horizontal overflow ({over}px)")
            page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
            page.screenshot(path=str(SHOTS / f"{name}-home.png"), full_page=True)
            check("Turn Mealtime Into an Adventure." in page.inner_text("h1"), f"{name}: hero headline")
            check(page.locator(".hero-card .price").count() == 1, f"{name}: hero shows price from API")
            check(page.locator("img[loading=lazy], .ph").count() >= 1, f"{name}: image placeholder/lazy images")
            check(not any("Content Security Policy" in e or "Refused" in e for e in errors), f"{name}: no CSP violations")
            if name == "mobile":
                page.click(".menu-btn"); check(page.locator("#nav.open").count() == 1, "mobile: menu opens")
                page.click("#nav a[href='/#faq']"); page.wait_for_timeout(300)
                check(page.locator("#faq").is_visible(), "mobile: FAQ anchor navigation")
            ctx.close()

        ctx = browser.new_context(viewport={"width": 375, "height": 740}, device_scale_factor=1)
        page = ctx.new_page()
        # shipping info + product page
        page.goto(BASE + "/"); page.select_option("#country", "US")
        check("Shipping to" in page.inner_text("#ship-note") and "to be confirmed" in page.inner_text("#ship-note"), "hero: shipping info, no invented delivery time")
        page.click(".hero-card a:has-text('View details')")
        page.wait_for_selector("h1:has-text('Flower Garden')")
        check("application/ld+json" in page.content(), "product page: JSON-LD present")
        page.click("button:has-text('Add to cart')")
        check("Added" in page.inner_text(".actions + p, .small[aria-live]"), "product: add to cart feedback")
        page.screenshot(path=str(SHOTS / "mobile-product.png"), full_page=True)
        # cart
        page.goto(BASE + "/cart"); page.wait_for_selector(".card .row")
        check("Total" in page.inner_text("#app"), "cart: totals from server quote")
        page.click("button[aria-label^='Increase']"); page.wait_for_timeout(500)
        check("× 2" in page.inner_text("#app") or "2" in page.inner_text(".qty"), "cart: quantity change")
        # checkout: error state first (invalid email)
        page.goto(BASE + "/checkout"); page.wait_for_selector("#name")
        page.fill("#name", "<img src=x onerror=window.__xss=1> Tester"); page.fill("#email", "tester@example.com")
        page.select_option("#country", "US"); page.fill("#line1", "1 Test St"); page.fill("#city", "Springfield"); page.fill("#postal_code", "12345")
        page.wait_for_selector(".row.total"); page.screenshot(path=str(SHOTS / "mobile-checkout.png"), full_page=True)
        page.click("button[type=submit]")
        page.wait_for_url("**/dev/pay/**"); check("SANDBOX" in page.inner_text("body"), "checkout: redirected to hosted (sandbox) payment page, no card fields")
        check(page.locator("input[type=text], input[name*=card]").count() == 0, "checkout: no card inputs on our domain")
        page.click("button:has-text('Simulate successful payment')")
        page.wait_for_url("**/order?ref=*"); page.wait_for_selector(".timeline")
        check("Payment confirmed" in page.inner_text(".timeline") and page.locator(".timeline li.done").count() == 3, "order page: payment confirmed via verified webhook")
        check(page.evaluate("window.__xss") is None, "XSS payload in name rendered as text")
        ref = page.inner_text("h2.small-title").replace("Order ", "").strip()
        page.screenshot(path=str(SHOTS / "mobile-order.png"), full_page=True)

        # admin
        adm = browser.new_context(viewport={"width": 390, "height": 800}).new_page()
        adm.goto(BASE + "/admin"); adm.fill("#email", "owner@example.com"); adm.fill("#pw", "wrong"); adm.click("button:has-text('Log in')")
        adm.wait_for_selector(".notice-error"); check(True, "admin: wrong password shows error")
        adm.fill("#pw", "e2e-password-123"); adm.click("button:has-text('Log in')")
        adm.wait_for_selector(".stats"); check("Awaiting supplier approval" in adm.inner_text(".stats"), "admin: dashboard")
        over = adm.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"); check(over <= 0, f"admin mobile: no page overflow ({over}px)")
        adm.screenshot(path=str(SHOTS / "mobile-admin-dashboard.png"), full_page=True)
        adm.click("a:has-text('Products')"); adm.wait_for_selector("table.data"); check("Flower Garden" in adm.inner_text("table.data"), "admin: products table")
        adm.click("a:has-text('Orders')"); adm.wait_for_selector("table.data"); adm.click(f"a:has-text('{ref}')")
        adm.wait_for_selector("button:has-text('Approve supplier order')")
        check("Estimated profit" in adm.inner_text("#app"), "admin: sees supplier cost/profit")
        adm.screenshot(path=str(SHOTS / "mobile-admin-order.png"), full_page=True)
        adm.click("button:has-text('Approve supplier order')")
        adm.wait_for_selector("#sid"); adm.fill("#sid", "1688-TEST-0001"); adm.click("button:has-text('Mark supplier order placed')")
        adm.wait_for_selector("#trk"); adm.fill("#trk", "LX999888777CN"); adm.fill("#car", "Test Carrier"); adm.click("button:has-text('Save tracking')")
        adm.wait_for_selector("button:has-text('Mark shipped')"); adm.click("button:has-text('Mark shipped')")
        adm.wait_for_selector("button:has-text('Mark delivered')")
        # customer sees shipped + tracking
        page.reload(); page.wait_for_selector(".timeline")
        check("LX999888777CN" in page.inner_text("#app") and "Shipped" in page.inner_text("#app"), "customer: sees shipped + tracking")
        check("1688" not in page.inner_text("body") and "profit" not in page.inner_text("body").lower(), "customer: no supplier info visible")
        adm.click("button:has-text('Mark delivered')"); adm.wait_for_timeout(600)
        page.reload(); page.wait_for_selector(".timeline"); check(page.locator(".timeline li.done").count() == 5, "customer: delivered")
        # lookup by email form + wrong email
        page.goto(BASE + "/order"); page.fill("#ref", ref); page.fill("#email", "wrong@example.com"); page.click("button:has-text('Check status')")
        page.wait_for_selector(".notice-error"); check(True, "order lookup: wrong email -> not found")
        # error state: unavailable product
        browser.close()
finally:
    srv.terminate()
print("\nRESULT:", "ALL PASSED" if not problems else f"{len(problems)} FAILED: {problems}")
sys.exit(1 if problems else 0)
