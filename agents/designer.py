"""
designer_agent — generates print-ready PNG designs using OpenAI DALL-E 3.

DALL-E 3 max resolution is 1024×1024 (HD quality).  After download the image
is upscaled to 4096×4096 with Lanczos resampling using Pillow — sufficient for
most print-on-demand providers (300 DPI at ~13×13 inches).  True 5000×5000px
requires a dedicated upscaling service; swap `_upscale` to use one if needed.
"""

import io
import os
import re
from pathlib import Path

import httpx
from openai import OpenAI
from PIL import Image

from utils.helpers import log_action

DESIGNS_DIR = Path(os.getenv("DATA_DIR", ".")) / "designs"
DESIGNS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_SIZE = (4096, 4096)   # closest achievable with PIL Lanczos

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(brief: dict) -> str:
    niche        = brief.get("niche", "")
    style        = brief.get("style", "")
    text         = brief.get("suggested_text", "")
    colors       = brief.get("color_palette", "vibrant")
    product_type = brief.get("product_type", "t-shirt")
    hint         = brief.get("dalle_prompt_hint", "")

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

def _download_image(url: str) -> bytes:
    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _upscale(data: bytes, target: tuple[int, int] = TARGET_SIZE) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    return img.resize(target, Image.LANCZOS)


def _safe_filename(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len]


# ── node ──────────────────────────────────────────────────────────────────────

def designer_node(state: dict) -> dict:
    log_action("designer_agent", "Generating designs with DALL-E 3")
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

            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="hd",
                n=1,
                response_format="url",
            )

            image_url      = response.data[0].url
            revised_prompt = response.data[0].revised_prompt or prompt

            raw_bytes = _download_image(image_url)
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
