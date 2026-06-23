"""
scout_agent — finds bestselling Etsy products via DuckDuckGo search.

Etsy's search pages are protected by Cloudflare and block all scrapers.
DuckDuckGo indexes Etsy listings and has no bot protection, so we search
there instead to get product titles, URLs, prices, and review counts.
"""

import re
import time
from utils.helpers import log_action

# Search queries targeting high-review, bestselling Etsy products
SEARCH_QUERIES = [
    "etsy funny tshirt gift bestseller",
    "etsy bestseller wall art poster print",
    "etsy popular shirt quote novelty",
    "etsy tshirt bestseller dog mom",
    "etsy tshirt bestseller nurse teacher",
    "etsy retro vintage poster print bestseller",
]


def _parse_price(text: str) -> float:
    m = re.search(r'\$\s*(\d+\.?\d*)', text)
    return float(m.group(1)) if m else 0.0


def _parse_reviews(text: str) -> int:
    m = re.search(r'([\d,]+)\s*(?:reviews?|ratings?|sales?|sold)', text, re.I)
    if m:
        return int(m.group(1).replace(',', ''))
    # Also catch patterns like "★4.9 (2,341)"
    m = re.search(r'\(([\d,]+)\)', text)
    if m:
        return int(m.group(1).replace(',', ''))
    return 0


def _ddg_search(query: str, max_results: int = 15) -> list[dict]:
    """Search DuckDuckGo and return Etsy listing results."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    products = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=25))

        for r in results:
            href = r.get('href', '')
            # Accept listing pages and shop pages — both carry useful product data
            if 'etsy.com' not in href:
                continue

            title   = r.get('title', '').replace(' | Etsy', '').strip()
            snippet = r.get('body', '')

            price        = _parse_price(snippet)
            review_count = _parse_reviews(snippet)
            is_bestseller = (
                'bestseller' in snippet.lower()
                or 'best seller' in snippet.lower()
                or 'bestseller' in title.lower()
            )

            products.append({
                'title':         title,
                'url':           href.split('?')[0],
                'price':         price,
                'review_count':  review_count,
                'is_bestseller': is_bestseller,
                'thumbnail_url': '',
                'tags':          [],
            })

    except Exception as exc:
        log_action('scout_agent', f'DDG search failed for "{query}": {exc}', 'warning')

    return products


def scout_node(state: dict) -> dict:
    log_action('scout_agent', 'Searching DuckDuckGo for Etsy bestsellers')
    errors  = list(state.get('errors', []))
    all_products: list[dict] = []
    seen_urls: set[str] = set()

    for query in SEARCH_QUERIES:
        log_action('scout_agent', f'  Query: {query}')
        results = _ddg_search(query, max_results=15)
        log_action('scout_agent', f'  → {len(results)} Etsy listings found')

        for p in results:
            if p['url'] not in seen_urls:
                seen_urls.add(p['url'])
                all_products.append(p)

        # Polite delay between queries so DDG doesn't rate-limit us
        time.sleep(1.5)

    # Prefer bestseller-badged and high-review products
    qualifying = [p for p in all_products if p['review_count'] > 50 or p['is_bestseller']]
    qualifying.sort(key=lambda x: x['review_count'], reverse=True)
    top = qualifying[:10]

    if not top:
        log_action('scout_agent', 'No high-review products found — using all DDG results', 'warning')
        top = all_products[:10]

    log_action('scout_agent', f'Returning {len(top)} products for analysis')
    for i, p in enumerate(top, 1):
        log_action('scout_agent', f'  {i}. {p["title"][:60]} | ${p["price"]} | {p["review_count"]} reviews')

    return {**state, 'raw_products': top, 'errors': errors}
