"""
etsy_agent — updates published Etsy listings with Claude-generated SEO copy
(title, description, 13 tags) using the Etsy API v3.

Authentication: Etsy API v3 uses OAuth 2.0.  Supply a valid ETSY_ACCESS_TOKEN
(obtained via the OAuth flow outside this script) in your .env file.
"""

import os

import anthropic
import httpx

from utils.db import is_duplicate, track_listing
from utils.helpers import log_action

ETSY_BASE = "https://openapi.etsy.com/v3/application"

_anthropic_client: anthropic.Anthropic | None = None


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _etsy_headers() -> dict:
    api_key = os.getenv("ETSY_API_KEY", "")
    token   = os.getenv("ETSY_ACCESS_TOKEN", "")
    if not api_key or not token:
        raise EnvironmentError("ETSY_API_KEY and ETSY_ACCESS_TOKEN must be set.")
    return {
        "x-api-key":     api_key,
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


# ── Claude SEO generation ─────────────────────────────────────────────────────

_SEO_TOOL = {
    "name": "etsy_listing_content",
    "description": "Generate SEO-optimised Etsy listing title, description, and tags.",
    "input_schema": {
        "type": "object",
        "required": ["title", "description", "tags"],
        "properties": {
            "title": {
                "type": "string",
                "description": "SEO-optimised listing title, max 140 chars, front-loaded with primary keywords.",
            },
            "description": {
                "type": "string",
                "description": (
                    "3-4 paragraph product description. Open with the key benefit, "
                    "include bullet points for features, end with a call to action. "
                    "Naturally incorporate SEO keywords."
                ),
            },
            "tags": {
                "type": "array",
                "description": "Exactly 13 Etsy tags (max 20 chars each, single or two-word phrases).",
                "items": {"type": "string", "maxLength": 20},
                "minItems": 13,
                "maxItems": 13,
            },
        },
    },
}


def generate_seo_content(brief: dict) -> dict:
    niche        = brief.get("niche", "")
    style        = brief.get("style", "")
    text         = brief.get("suggested_text", "")
    product_type = brief.get("product_type", "t-shirt")
    price        = brief.get("price_point", 24.99)
    existing_tags = brief.get("seo_tags", [])

    user_msg = f"""Create an Etsy listing for this product:

Product type  : {product_type}
Niche         : {niche}
Style         : {style}
Design text   : {text}
Price         : ${price:.2f}
Analyst tags  : {', '.join(existing_tags[:5]) if existing_tags else 'none'}

Requirements:
- Title: max 140 chars, start with the strongest keyword
- Description: engaging, keyword-rich, 3–4 paragraphs + bullet features
- Tags: exactly 13 tags, mix of broad + long-tail, each ≤20 chars
- Tone: friendly and enthusiastic, not spammy"""

    client = _get_anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        tools=[_SEO_TOOL],
        tool_choice={"type": "tool", "name": "etsy_listing_content"},
        messages=[{"role": "user", "content": user_msg}],
    )

    for block in response.content:
        if block.type == "tool_use":
            data = block.input
            # Enforce limits
            data["title"] = data["title"][:140]
            data["tags"]  = [t[:20] for t in data["tags"][:13]]
            return data

    # Graceful fallback
    fallback_title = f"{niche} {product_type} — {style} design"[:140]
    return {
        "title":       fallback_title,
        "description": f"A beautiful {niche} {product_type} featuring a {style} design. Perfect gift!",
        "tags":        (existing_tags + [niche, style, product_type])[:13],
    }


# ── Etsy API ──────────────────────────────────────────────────────────────────

def update_listing(listing_id: str, content: dict) -> dict:
    r = httpx.patch(
        f"{ETSY_BASE}/listings/{listing_id}",
        headers=_etsy_headers(),
        json={
            "title":       content["title"],
            "description": content["description"],
            "tags":        content["tags"],
            "state":       "active",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_listing_url(listing_id: str) -> str:
    return f"https://www.etsy.com/listing/{listing_id}"


# ── node ──────────────────────────────────────────────────────────────────────

def etsy_node(state: dict) -> dict:
    log_action("etsy_agent", "Updating Etsy listings with SEO content")
    errors   = list(state.get("errors", []))
    products = state.get("printify_products", [])
    listings = []

    if not products:
        log_action("etsy_agent", "No Printify products to update — skipping.", "warning")
        return {**state, "etsy_listings": [], "errors": errors}

    for product in products:
        brief      = product.get("brief", {})
        listing_id = product.get("etsy_listing_id", "")
        printify_id = product.get("printify_product_id", "")
        niche      = brief.get("niche", "unknown")

        if not listing_id:
            log_action(
                "etsy_agent",
                f"No Etsy listing ID for '{niche}' — skipping update.",
                "warning",
            )
            continue

        # Skip duplicates already tracked in our DB
        if is_duplicate(listing_id):
            log_action("etsy_agent", f"Listing {listing_id} already tracked — skipping.", "info")
            continue

        log_action("etsy_agent", f"Generating SEO content for listing {listing_id} ({niche})")

        try:
            content = generate_seo_content(brief)

            log_action("etsy_agent", f"  Title: {content['title'][:80]}…")
            log_action("etsy_agent", f"  Tags : {', '.join(content['tags'])}")

            update_listing(listing_id, content)

            url = get_listing_url(listing_id)
            log_action("etsy_agent", f"  Listing updated → {url}")

            track_listing(
                listing_id=listing_id,
                url=url,
                printify_id=printify_id,
                title=content["title"],
                niche=niche,
            )

            listings.append(
                {
                    "listing_id":  listing_id,
                    "listing_url": url,
                    "title":       content["title"],
                    "brief":       brief,
                }
            )

        except Exception as exc:
            msg = f"etsy_agent failed for listing {listing_id} ({niche}): {exc}"
            log_action("etsy_agent", msg, "error")
            errors.append(msg)

    log_action("etsy_agent", f"Updated {len(listings)}/{len(products)} Etsy listings")
    return {**state, "etsy_listings": listings, "errors": errors}
