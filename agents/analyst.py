"""
analyst_agent — uses Claude (claude-sonnet-4-6) with structured tool_use to
analyse scraped Etsy products and produce 3 design briefs.
"""

import json
import os

import anthropic

from utils.helpers import log_action

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


# ── tool schema ───────────────────────────────────────────────────────────────

_BRIEF_TOOL = {
    "name": "create_design_briefs",
    "description": (
        "Analyse bestselling Etsy products and return the top 3 print-on-demand "
        "design opportunities as structured briefs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "analysis": {
                "type": "string",
                "description": "2-3 sentence summary of recurring patterns and niches.",
            },
            "briefs": {
                "type": "array",
                "description": "Exactly 3 design briefs ranked by opportunity.",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": [
                        "niche",
                        "style",
                        "suggested_text",
                        "color_palette",
                        "product_type",
                        "price_point",
                        "seo_title",
                        "seo_description",
                        "seo_tags",
                        "dalle_prompt_hint",
                    ],
                    "properties": {
                        "niche": {
                            "type": "string",
                            "description": "Market niche (e.g. 'funny dog lover', 'retro hiking').",
                        },
                        "style": {
                            "type": "string",
                            "description": "Visual style (e.g. 'bold minimalist', 'vintage distressed').",
                        },
                        "suggested_text": {
                            "type": "string",
                            "description": "Exact copy/phrase to appear on the design.",
                        },
                        "color_palette": {
                            "type": "string",
                            "description": "Comma-separated hex codes or colour names.",
                        },
                        "size": {
                            "type": "string",
                            "description": "Target print size.",
                            "default": "5000x5000px",
                        },
                        "product_type": {
                            "type": "string",
                            "enum": ["t-shirt", "poster"],
                        },
                        "price_point": {
                            "type": "number",
                            "description": "Recommended retail price in USD.",
                        },
                        "seo_title": {
                            "type": "string",
                            "description": "SEO-optimised Etsy listing title, max 140 chars, front-loaded with the strongest keywords.",
                        },
                        "seo_description": {
                            "type": "string",
                            "description": "3-4 paragraph Etsy product description with bullet points, keywords woven in naturally, ending with a call to action.",
                        },
                        "seo_tags": {
                            "type": "array",
                            "description": "Exactly 13 Etsy SEO tags, mix of broad and long-tail, each max 20 chars.",
                            "items": {"type": "string", "maxLength": 20},
                            "minItems": 13,
                            "maxItems": 13,
                        },
                        "dalle_prompt_hint": {
                            "type": "string",
                            "description": (
                                "One-line hint for the DALL-E prompt describing the graphic "
                                "element(s) to generate (no brand names, no copyrighted IP)."
                            ),
                        },
                    },
                },
            },
        },
        "required": ["analysis", "briefs"],
    },
}

_SYSTEM_PROMPT = """You are a seasoned Etsy seller and print-on-demand expert.
Your job: analyse a list of bestselling product titles and metadata, identify
repeating design patterns and profitable niches, then create 3 ready-to-execute
design briefs for a print-on-demand store — including full Etsy listing content.

Rules:
- Designs must be original and not infringe any copyright or trademark.
- Prefer niches with proven demand (many reviews, bestseller badges).
- Suggest copy that is punchy and relatable, not generic.
- DALL-E prompt hints must be purely descriptive visual directions.
- seo_title: max 140 chars, start with the strongest keyword phrase.
- seo_description: 3-4 paragraphs, include bullet-point features, end with CTA.
- seo_tags: exactly 13 tags, each max 20 chars, mix broad + long-tail keywords.
"""


# ── node ──────────────────────────────────────────────────────────────────────

def analyst_node(state: dict) -> dict:
    log_action("analyst_agent", "Analysing scraped products with Claude")
    errors = list(state.get("errors", []))
    products = state.get("raw_products", [])

    if not products:
        log_action("analyst_agent", "No scraped products — using Claude's trend knowledge as fallback.", "warning")

    # Build a compact product summary to stay within context limits
    summary_rows = []
    for i, p in enumerate(products, 1):
        summary_rows.append(
            f"{i}. \"{p.get('title', '(no title)')}\" | "
            f"${p.get('price', 0):.2f} | "
            f"{p.get('review_count', 0)} reviews | "
            f"{'BESTSELLER' if p.get('is_bestseller') else ''}"
        )
    product_summary = "\n".join(summary_rows)

    if products:
        user_message = f"""Here are {len(products)} trending Etsy products:

{product_summary}

Identify the top 3 print-on-demand design opportunities and return structured briefs."""
    else:
        user_message = """No live product data was scraped (Etsy blocked the request).

Use your knowledge of current Etsy bestsellers and print-on-demand trends to identify
the top 3 high-demand design opportunities right now. Focus on niches with proven
consistent sales: funny quotes, pet lovers, professions, nature/outdoors, retro styles.

Return 3 ready-to-execute design briefs as if you had scraped the data yourself."""

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[_BRIEF_TOOL],
            tool_choice={"type": "tool", "name": "create_design_briefs"},
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract the tool_use block
        tool_input = None
        for block in response.content:
            if block.type == "tool_use":
                tool_input = block.input
                break

        if not tool_input:
            raise ValueError("Claude did not return a tool_use block.")

        briefs = tool_input.get("briefs", [])
        for brief in briefs:
            brief.setdefault("size", "5000x5000px")

        analysis_summary = tool_input.get("analysis", "")
        log_action("analyst_agent", f"Analysis: {analysis_summary[:200]}")
        log_action("analyst_agent", f"Generated {len(briefs)} design brief(s)")

        for idx, b in enumerate(briefs, 1):
            log_action(
                "analyst_agent",
                f"  Brief {idx}: [{b['product_type']}] {b['niche']} — \"{b['suggested_text'][:60]}\"",
            )

        return {**state, "design_briefs": briefs, "errors": errors}

    except Exception as exc:
        msg = f"analyst_agent failed: {exc}"
        log_action("analyst_agent", msg, "error")
        errors.append(msg)
        return {**state, "design_briefs": [], "errors": errors}
