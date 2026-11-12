"""WebPilot MCP server: drive the user's own Chrome/Edge over CDP (--remote-debugging-port=9222)."""
from __future__ import annotations

import io

from mcp.server.fastmcp import FastMCP, Image

from host import webpilot_impl as W
from host.mcp_servers import _common as C

mcp = FastMCP("nyro-webpilot")
_pw = None
_browser = None


async def _page():
    global _pw, _browser
    from playwright.async_api import async_playwright
    if _browser is None or not _browser.is_connected():
        _pw = _pw or await async_playwright().start()
        _browser = await _pw.chromium.connect_over_cdp(C.config()["webpilot"]["cdp_url"])
    ctx = _browser.contexts[0] if _browser.contexts else await _browser.new_context()
    return ctx.pages[-1] if ctx.pages else await ctx.new_page()


@mcp.tool()
async def open_url(url: str) -> str:
    """Navigate the active tab to an http(s) URL."""
    try:
        cfg = C.config()
        url = W.check_url(url, cfg["webpilot"]["allowed_schemes"])
        from urllib.parse import urlparse
        if W.is_private_host(urlparse(url).hostname or ""):
            raise C.Denied("Private/local network addresses are blocked.")
        C.guard("open_url", {"url": url}, cfg=cfg)
        page = await _page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Opened {page.url} - title: {await page.title()}"
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
async def click_element(selector: str) -> str:
    """Click an element (CSS or text selector). Submit/Pay/Send-style buttons need phone approval."""
    try:
        page = await _page()
        loc = page.locator(selector).first
        label = " ".join(filter(None, [
            (await loc.inner_text(timeout=5000))[:80],
            await loc.get_attribute("value") or "", await loc.get_attribute("aria-label") or "", selector]))
        C.guard("click_element", {"selector": label}, f"Click: {label[:120]}")
        await loc.click(timeout=8000)
        return "Clicked."
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
async def type_text(selector: str, text: str) -> str:
    """Type into an input. Password/card/OTP fields are refused: the user types those."""
    try:
        page = await _page()
        loc = page.locator(selector).first
        t = await loc.get_attribute("type")
        nm = (await loc.get_attribute("name")) or (await loc.get_attribute("id"))
        if W.is_sensitive_field(t, nm):
            raise C.Denied("Sensitive field: ask the user to type this themselves.")
        C.guard("type_text", {"selector": selector})
        await loc.fill(text, timeout=8000)
        return "Typed."
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
async def extract_dom_text() -> str:
    """Visible text of the active page (truncated to 8000 chars)."""
    try:
        C.guard("extract_dom_text")
        page = await _page()
        txt = await page.inner_text("body", timeout=8000)
        return txt[:W.MAX_DOM_TEXT]
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
async def capture_tab_screenshot():
    """Screenshot of the active tab (800px WebP), returned inline as an image."""
    try:
        C.guard("capture_tab_screenshot")
        from PIL import Image
        from host.overlay.scaler import downscale_to_webp
        import time
        page = await _page()
        png = await page.screenshot(type="png")
        cap = downscale_to_webp(Image.open(io.BytesIO(png)))
        p = C.data_dir() / f"tab_{int(time.time() * 1000)}.webp"
        p.write_bytes(cap.webp)
        return [f"Image is {cap.scaled_w}x{cap.scaled_h} px.", Image(data=cap.webp, format="webp")]
    except Exception as e:
        return C.as_error(e)


if __name__ == "__main__":
    mcp.run()
