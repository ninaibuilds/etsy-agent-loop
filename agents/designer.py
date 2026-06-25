"""
designer_agent — generates print-ready PNG designs using OpenAI gpt-image-1.

gpt-image-1 returns images as base64 at 1024×1024.  The image is then
upscaled to 4096×4096 with Lanczos resampling using Pillow — sufficient for
most print-on-demand providers (300 DPI at ~13×13 inches).
"""

import base64
import io
import os
import re
from pathlib import Path

from openai import OpenAI
from PIL import Image

from utils.helpers import log_action

DESIGNS_DIR = Path(os.getenv("DATA_DIR", ".")) / "designs"
DESIGNS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_SIZE = (4096, 4096)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


# Words that trigger OpenAI content moderation in print design prompts
_MODERATION_WORDS = {"stupid", "idiot", "dumb", "hate", "kill", "die", "dead",
                     "drunk", "wasted", "high", "stoned", "drugs", "crap", "ass"}


def _sanitize(text: str) -> str:
    """Remove words that commonly trip the moderation filter."""
    words = text.split()
    cleaned = [w for w in words if w.lower().strip(".,!?\"'") not in _MODERATION_WORDS]
    return " ".join(cleaned)


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(brief: dict, sanitize: bool = False) -> str:
    niche        = brief.get("niche", "")
    style        = brief.get("style", "")
    text         = brief.get("suggested_text", "")
    colors       = brief.get("color_palette", "vibrant")
    product_type = brief.get("product_type", "t-shirt")
    hint         = brief.get("dalle_prompt_hint", "")

    if sanitize:
        text = _sanitize(text)
        hint = _sanitize(hint)

    text_instruction = (
        f'Include the text "{text}" in a bold, legible font.' if text else ""
    )
    hint_instruction = f"Graphic concept: {hint}." if hint else ""

    return (
        f"A high-quality, print-ready graphic design for a {product_type}. "
        f"Style: {style}. Niche: {niche}. "
        f"{text_instruction} "
        f"{hint_instruction} "
        f"Color palette: {colors}. "
        "White or transparent background. "
        "Bold, well-balanced composition suitable for fabric or paper printing. "
        "No photorealistic faces, no brand logos, no copyrighted characters. "
        "Clean edges, high contrast, professional quality."
    ).strip()


# ── image helpers ─────────────────────────────────────────────────────────────

def _upscale(data: bytes, target: tuple[int, int] = TARGET_SIZE) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    return img.resize(target, Image.LANCZOS)


def _safe_filename(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len]


# ── node ──────────────────────────────────────────────────────────────────────

def designer_node(state: dict) -> dict:
    log_action("designer_agent", "Generating designs with gpt-image-1")
    errors    = list(state.get("errors", []))
    briefs    = state.get("design_briefs", [])
    loop_n    = state.get("loop_count", 0)
    generated = []

    if not briefs:
        log_action("designer_agent", "No design briefs — skipping.", "warning")
        return {**state, "generated_designs": [], "errors": errors}

    client = _get_client()

    for idx, brief in enumerate(briefs, 1):
        niche = brief.get("niche", "design")
        log_action("designer_agent", f"  [{idx}/{len(briefs)}] Generating: {niche}")

        try:
            prompt = _build_prompt(brief)
            log_action("designer_agent", f"  Prompt: {prompt[:120]}…")

            try:
                response = client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt,
                    size="1024x1024",
                    quality="high",
                    n=1,
                )
            except Exception as mod_err:
                if "moderation" in str(mod_err).lower() or "safety" in str(mod_err).lower():
                    # Retry with sanitized prompt (removes flagged words)
                    prompt = _build_prompt(brief, sanitize=True)
                    log_action("designer_agent", f"  Moderation block — retrying with sanitized prompt: {prompt[:100]}…", "warning")
                    response = client.images.generate(
                        model="gpt-image-1",
                        prompt=prompt,
                        size="1024x1024",
                        quality="high",
                        n=1,
                    )
                else:
                    raise

            b64_data       = response.data[0].b64_json
            revised_prompt = response.data[0].revised_prompt or prompt

            raw_bytes = base64.b64decode(b64_data)
            img       = _upscale(raw_bytes, TARGET_SIZE)

            slug     = _safe_filename(niche)
            filename = f"loop{loop_n:03d}_{idx:02d}_{slug}.png"
            filepath = DESIGNS_DIR / filename
            img.save(filepath, "PNG", optimize=True)

            log_action("designer_agent", f"  Saved → {filepath}  ({img.size[0]}×{img.size[1]}px)")

            generated.append(
                {
                    "design_path":    str(filepath),
                    "brief":          brief,
                    "revised_prompt": revised_prompt,
                }
            )

        except Exception as exc:
            msg = f"designer_agent brief #{idx} ({niche}) failed: {exc}"
            log_action("designer_agent", msg, "error")
            errors.append(msg)

    log_action("designer_agent", f"Generated {len(generated)}/{len(briefs)} designs")
    return {**state, "generated_designs": generated, "errors": errors}
