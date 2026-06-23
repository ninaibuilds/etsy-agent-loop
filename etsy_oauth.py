"""
One-time script to get an Etsy OAuth 2.0 access token + refresh token.

Run this once:
    python etsy_oauth.py

It will:
  1. Open your browser to Etsy's authorization page
  2. Ask you to paste the redirect URL from your browser after authorizing
  3. Automatically write ETSY_ACCESS_TOKEN and ETSY_REFRESH_TOKEN into your .env
"""

import base64
import hashlib
import os
import secrets
import urllib.parse
import webbrowser

import httpx
from dotenv import load_dotenv, set_key

load_dotenv()

API_KEY  = os.getenv("ETSY_API_KEY", "")
SECRET   = os.getenv("ETSY_SHARED_SECRET", "")
REDIRECT = "https://localhost/callback"
SCOPES   = "listings_w listings_r transactions_r shops_r"

if not API_KEY or not SECRET:
    print("\n[!] ETSY_API_KEY and ETSY_SHARED_SECRET must be in your .env before running this.\n")
    raise SystemExit(1)


def _make_code_verifier() -> str:
    return secrets.token_urlsafe(64)

def _make_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def main():
    verifier  = _make_code_verifier()
    challenge = _make_code_challenge(verifier)
    state     = secrets.token_urlsafe(16)

    auth_url = (
        "https://www.etsy.com/oauth/connect?"
        + urllib.parse.urlencode({
            "response_type":         "code",
            "redirect_uri":          REDIRECT,
            "scope":                 SCOPES,
            "client_id":             API_KEY,
            "state":                 state,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        })
    )

    print("\nOpening your browser to authorize with Etsy...")
    print("If it doesn't open automatically, visit this URL manually:\n")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)

    print("After you click 'Allow Access' on Etsy, your browser will redirect")
    print("to a page that won't load (that's normal).")
    print("Copy the FULL URL from your browser's address bar and paste it below.\n")

    redirected_url = input("Paste the full redirect URL here: ").strip()

    parsed = urllib.parse.urlparse(redirected_url)
    params = urllib.parse.parse_qs(parsed.query)
    code   = params.get("code", [""])[0]

    if not code:
        print("\n[!] Could not find an authorization code in that URL. Try again.")
        raise SystemExit(1)

    print("\nCode received. Exchanging for tokens...")

    r = httpx.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "client_id":     API_KEY,
            "redirect_uri":  REDIRECT,
            "code":          code,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    r.raise_for_status()
    tokens = r.json()

    access_token  = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")
    expires_in    = tokens.get("expires_in", 3600)

    print(f"\n{'='*60}")
    print("  SUCCESS — tokens written to .env")
    print(f"{'='*60}")
    print(f"  access_token  : {access_token[:30]}…")
    if refresh_token:
        print(f"  refresh_token : {refresh_token[:30]}…")
    print(f"  expires_in    : {expires_in}s ({expires_in // 3600}h)")
    print(f"{'='*60}\n")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    set_key(env_path, "ETSY_ACCESS_TOKEN", access_token)
    if refresh_token:
        set_key(env_path, "ETSY_REFRESH_TOKEN", refresh_token)

    print("ETSY_ACCESS_TOKEN written to .env.")
    print("You're ready to run:  python main.py --dry-run\n")


if __name__ == "__main__":
    main()
