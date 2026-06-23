"""
scout_agent — scrapes Etsy for bestselling t-shirts / posters using Playwright.

Returns up to 10 qualifying products with >100 reviews or a bestseller badge.
Falls back to BeautifulSoup on static HTML if Playwright times out.
"""

import asyncio
import re
from typing import Optional

from bs4 import BeautifulSoup
import httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from utils.helpers import log_action

SEARCH_URLS = [
    "https://www.etsy.com/search?q=bestseller+tshirt&sort_on=score&explicit=1",
    "https://www.etsy.com/search?q=bestseller+poster&sort_on=score&explicit=1",
    "https://www.etsy.com/c/clothing/unisex-adult-clothing/shirts-and-tees?ref=catnav-1055&sort_on=score",
]

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Injected into every page to hide Playwright's automation fingerprint
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = {runtime: {}};
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_review_count(text: str) -> int:
    """Extract integer review count from strings like '(1,234)' or '1234 reviews'."""
    digits = re.sub(r"[^\d]", "", text.split("(")[-1].split(")")[0])
    return int(digits) if digits else 0


def _clean_price(text: str) -> float:
    digits = re.sub(r"[^\d.]", "", text)
    try:
        return float(digits)
    except ValueError:
        return 0.0


# ── Playwright scraper ────────────────────────────────────────────────────────

async def _scrape_with_playwright(url: str, max_cards: int = 40) -> list[dict]:
    products: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1440,900",
            ],
        )
        ctx = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
                "sec-ch-ua-platform": '"macOS"',
            },
        )
        # Inject stealth script before any page load
        await ctx.add_init_script(_STEALTH_JS)
        page = await ctx.new_page()

        try:
            # Warm up with the homepage first to get cookies (avoids cold-start blocks)
            await page.goto("https://www.etsy.com", wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(2_000)

            # Accept cookie banner if present
            try:
                await page.click("[data-gdpr-single-choice-accept]", timeout=3_000)
            except Exception:
                pass

            # Try multiple card selectors — Etsy A/B tests layouts frequently
            card_selectors = [
                "[data-listing-id]",
                ".listing-link",
                "li.wt-list-unstyled",
                "div[data-palette-listing-id]",
            ]

            cards = []
            for sel in card_selectors:
                cards = await page.query_selector_all(sel)
                if cards:
                    break

            log_action("scout_agent", f"Playwright found {len(cards)} raw cards at {url}")

            for card in cards[:max_cards]:
                try:
                    product = await _extract_card_playwright(card)
                    if product:
                        products.append(product)
                except Exception as exc:
                    log_action("scout_agent", f"Card parse error: {exc}", "warning")

        except PWTimeout:
            log_action("scout_agent", f"Playwright timeout on {url}", "warning")
        except Exception as exc:
            log_action("scout_agent", f"Playwright error: {exc}", "error")
        finally:
            await browser.close()

    return products


async def _extract_card_playwright(card) -> Optional[dict]:
    # Title
    title_el = await card.query_selector(
        "h3, [class*='title'], [class*='listing-title'], .wt-text-caption"
    )
    title = (await title_el.inner_text()).strip() if title_el else ""

    # URL
    link_el = await card.query_selector("a[href*='/listing/']")
    if not link_el:
        link_el = await card.query_selector("a")
    href = (await link_el.get_attribute("href") or "") if link_el else ""
    if not href:
        return None
    if not href.startswith("http"):
        href = "https://www.etsy.com" + href

    # Price
    price_el = await card.query_selector(
        "[class*='currency-value'], [class*='price'], [data-buy-box-region]"
    )
    price_text = (await price_el.inner_text()) if price_el else "0"
    price = _clean_price(price_text)

    # Review count
    review_el = await card.query_selector(
        "[class*='rating'], [class*='review'], [class*='star']"
    )
    review_text = (await review_el.inner_text()) if review_el else "0"
    review_count = _parse_review_count(review_text)

    # Bestseller badge
    badge_el = await card.query_selector(
        "[class*='badge'], [class*='bestseller'], [class*='best-seller']"
    )
    badge_text = (await badge_el.inner_text()).lower() if badge_el else ""
    is_bestseller = "bestseller" in badge_text or "best seller" in badge_text

    # Thumbnail
    img_el = await card.query_selector("img")
    thumbnail = ""
    if img_el:
        thumbnail = (
            await img_el.get_attribute("src")
            or await img_el.get_attribute("data-src")
            or ""
        )

    if not title and not href:
        return None

    return {
        "title":         title,
        "url":           href.split("?")[0],  # strip query params
        "price":         price,
        "review_count":  review_count,
        "is_bestseller": is_bestseller,
        "thumbnail_url": thumbnail,
        "tags":          [],
    }


# ── BS4 fallback ──────────────────────────────────────────────────────────────

def _scrape_with_bs4(url: str) -> list[dict]:
    """Lightweight fallback when Playwright is unavailable."""
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except Exception as exc:
        log_action("scout_agent", f"BS4 fetch failed: {exc}", "error")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    products: list[dict] = []

    for card in soup.select("[data-listing-id]")[:40]:
        try:
            title_el = card.select_one("h3") or card.select_one("[class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""

            link_el = card.select_one("a[href*='/listing/']") or card.select_one("a")
            href = link_el["href"] if link_el else ""
            if not href:
                continue
            if not href.startswith("http"):
                href = "https://www.etsy.com" + href

            price_el = card.select_one("[class*='currency-value']")
            price = _clean_price(price_el.get_text() if price_el else "0")

            review_el = card.select_one("[class*='rating']")
            review_count = _parse_review_count(
                review_el.get_text() if review_el else "0"
            )

            badge_el = card.select_one("[class*='badge']")
            badge_text = badge_el.get_text(strip=True).lower() if badge_el else ""
            is_bestseller = "bestseller" in badge_text

            img_el = card.select_one("img")
            thumbnail = (img_el.get("src") or img_el.get("data-src") or "") if img_el else ""

            products.append(
                {
                    "title":         title,
                    "url":           href.split("?")[0],
                    "price":         price,
                    "review_count":  review_count,
                    "is_bestseller": is_bestseller,
                    "thumbnail_url": thumbnail,
                    "tags":          [],
                }
            )
        except Exception as exc:
            log_action("scout_agent", f"BS4 card error: {exc}", "warning")

    return products


# ── node ──────────────────────────────────────────────────────────────────────

def scout_node(state: dict) -> dict:
    log_action("scout_agent", "Starting Etsy product scrape")
    errors = list(state.get("errors", []))
    all_products: list[dict] = []

    for url in SEARCH_URLS:
        log_action("scout_agent", f"Scraping: {url}")
        try:
            scraped = asyncio.run(_scrape_with_playwright(url))
            if not scraped:
                log_action("scout_agent", "Playwright returned nothing — trying BS4 fallback")
                scraped = _scrape_with_bs4(url)
            all_products.extend(scraped)
        except Exception as exc:
            msg = f"scout_agent URL error ({url}): {exc}"
            log_action("scout_agent", msg, "error")
            errors.append(msg)

    # Deduplicate by URL
    seen: set[str] = set()
    unique: list[dict] = []
    for p in all_products:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)

    # Prefer bestseller-badged + high-review products
    qualifying = [p for p in unique if p["review_count"] > 100 or p["is_bestseller"]]
    qualifying.sort(key=lambda x: x["review_count"], reverse=True)
    top = qualifying[:10]

    if not top:
        log_action(
            "scout_agent",
            "No high-review products found — using top raw results as fallback",
            "warning",
        )
        top = unique[:10]

    log_action("scout_agent", f"Returning {len(top)} products for analysis")
    return {**state, "raw_products": top, "errors": errors}
