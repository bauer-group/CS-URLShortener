#!/usr/bin/env python3
# =============================================================================
# import-yourls.py — Migrate short URLs from YOURLS to Shlink
# =============================================================================
# Fetches all short URLs from a running YOURLS instance via its API and
# imports them into Shlink via the REST API, preserving custom slugs and
# titles.
#
# Usage:
#   python scripts/import-yourls.py \
#     --yourls-url https://go.example.com/yourls-api.php \
#     --yourls-signature YOUR_SIGNATURE_TOKEN \
#     --shlink-url https://go.bauer-group.com \
#     --shlink-key YOUR_SHLINK_API_KEY
#
# Or interactively:
#   python scripts/import-yourls.py
#
# Features:
#   - Fetches all URLs from YOURLS (paginated)
#   - Creates short URLs in Shlink with identical custom slugs
#   - Preserves titles
#   - Skips already existing slugs (idempotent)
#   - Dry-run mode for previewing changes
#   - JSON export of fetched data for backup
#
# Requirements: Python 3.6+ (no external dependencies)
# =============================================================================

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass
from pathlib import Path


# ── YOURLS API Client ───────────────────────────────────────────────────────


def yourls_request(api_url: str, params: dict) -> dict:
    """Make a request to the YOURLS API and return parsed JSON."""
    params["format"] = "json"
    url = f"{api_url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "import-yourls/1.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"YOURLS API error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"YOURLS connection failed: {e.reason}") from e


def yourls_get_stats(api_url: str, auth: dict) -> int:
    """Get total number of links from YOURLS."""
    data = yourls_request(api_url, {**auth, "action": "db-stats"})
    return int(data.get("db-stats", {}).get("total_links", 0))


def yourls_fetch_all(api_url: str, auth: dict, batch_size: int = 100) -> list[dict]:
    """
    Fetch all short URLs from YOURLS in batches.

    Returns list of dicts with keys: keyword, url, title, clicks, timestamp.
    """
    total = yourls_get_stats(api_url, auth)
    if total == 0:
        return []

    print(f"  Found {total} URLs in YOURLS")
    all_links = []
    offset = 0

    while offset < total:
        data = yourls_request(api_url, {
            **auth,
            "action": "stats",
            "filter": "top",
            "limit": batch_size,
            "start": offset,
        })

        links = data.get("links", {})
        if not links:
            break

        for key, link in links.items():
            if isinstance(link, dict) and "shorturl" in link:
                all_links.append({
                    "keyword": link.get("keyword", "") or link.get("shorturl", "").rstrip("/").rsplit("/", 1)[-1],
                    "url": link.get("url", ""),
                    "title": link.get("title", ""),
                    "clicks": int(link.get("clicks", 0)),
                    "timestamp": link.get("timestamp", ""),
                })

        fetched = len(all_links)
        print(f"  Fetched {fetched}/{total} URLs...", end="\r")
        offset += batch_size

    print(f"  Fetched {len(all_links)}/{total} URLs    ")
    return all_links


# ── Shlink API Client ──────────────────────────────────────────────────────


def shlink_create_short_url(
    shlink_url: str,
    api_key: str,
    long_url: str,
    custom_slug: str,
    title: str = None,
    tags: list[str] = None,
) -> tuple[bool, str]:
    """
    Create a short URL in Shlink.

    Returns (success, message) tuple.
    """
    endpoint = f"{shlink_url.rstrip('/')}/rest/v3/short-urls"

    body = {
        "longUrl": long_url,
        "customSlug": custom_slug,
        "findIfExists": True,
        "validateUrl": False,
    }
    if title:
        body["title"] = title
    if tags:
        body["tags"] = tags

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", api_key)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return True, f"/{custom_slug} (empty response, HTTP {resp.status})"
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                preview = raw[:200].replace("\n", " ")
                return False, f"HTTP {resp.status} non-JSON response: {preview}"
            short_code = result.get("shortCode", custom_slug)
            return True, f"/{short_code}"
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(raw)
            detail = err.get("detail", err.get("title", raw))
        except json.JSONDecodeError:
            detail = raw[:200] or f"HTTP {e.code} (empty response)"
        return False, detail


# ── Interactive Configuration ───────────────────────────────────────────────


def prompt_config(args: argparse.Namespace) -> dict:
    """Fill missing configuration from interactive prompts."""
    config = {}

    # YOURLS
    config["yourls_url"] = args.yourls_url
    if not config["yourls_url"]:
        print()
        print("── YOURLS Configuration ───────────────────────────────")
        config["yourls_url"] = input("  API URL (e.g. https://go.example.com/yourls-api.php): ").strip()

    config["yourls_auth"] = {}
    if args.yourls_signature:
        config["yourls_auth"]["signature"] = args.yourls_signature
    elif args.yourls_username:
        config["yourls_auth"]["username"] = args.yourls_username
        config["yourls_auth"]["password"] = args.yourls_password or ""
    else:
        print()
        print("  Authentication (signature token OR username/password):")
        sig = input("  Signature token [leave empty for username/password]: ").strip()
        if sig:
            config["yourls_auth"]["signature"] = sig
        else:
            config["yourls_auth"]["username"] = input("  Username: ").strip()
            config["yourls_auth"]["password"] = getpass("  Password: ")

    # Shlink
    config["shlink_url"] = args.shlink_url
    config["shlink_key"] = args.shlink_key

    if not config["shlink_url"] or not config["shlink_key"]:
        # Try reading from .env
        env_path = Path(__file__).resolve().parent.parent / ".env"
        env_vars = _parse_env_file(env_path) if env_path.exists() else {}

        if not config["shlink_url"]:
            domain = env_vars.get("SHLINK_DOMAIN", "")
            is_https = env_vars.get("SHLINK_IS_HTTPS", "true").lower() == "true"
            if domain:
                proto = "https" if is_https else "http"
                default_url = f"{proto}://{domain}"
                config["shlink_url"] = default_url
                print(f"\n  Shlink URL (from .env): {default_url}")
            else:
                print()
                print("── Shlink Configuration ───────────────────────────────")
                config["shlink_url"] = input("  Shlink URL (e.g. https://go.bauer-group.com): ").strip()

        if not config["shlink_key"]:
            key = env_vars.get("SHLINK_API_KEY", "")
            if key:
                config["shlink_key"] = key
                print(f"  Shlink API Key (from .env): {key[:8]}...")
            else:
                config["shlink_key"] = input("  Shlink API Key: ").strip()

    return config


def _parse_env_file(path: Path) -> dict:
    """Parse a .env file into a dict (ignoring comments and empty lines)."""
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            # Undo docker-compose $$ escaping
            result[key.strip()] = value.strip().replace("$$", "$")
    return result


# ── Main Import Logic ───────────────────────────────────────────────────────


def run_import(config: dict, dry_run: bool = False, export_path: str = None) -> None:
    """Execute the YOURLS → Shlink migration."""

    print()
    print("╔═══════════════════════════════════════════════════════╗")
    print("║         YOURLS → Shlink Migration                    ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()

    # Phase 1: Fetch from YOURLS
    print("── Phase 1: Fetching URLs from YOURLS ─────────────────")
    links = yourls_fetch_all(config["yourls_url"], config["yourls_auth"])

    if not links:
        print("  No URLs found. Nothing to import.")
        return

    # Optional: Export fetched data as JSON backup
    if export_path:
        export = Path(export_path)
        export.write_text(json.dumps(links, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Exported {len(links)} URLs to {export}")

    # Phase 2: Import into Shlink
    print()
    if dry_run:
        print("── Phase 2: Dry Run (no changes) ──────────────────────")
    else:
        print("── Phase 2: Importing into Shlink ─────────────────────")

    success = 0
    skipped = 0
    failed = 0
    errors = []

    for i, link in enumerate(links, 1):
        keyword = link["keyword"]
        url = link["url"]
        title = link["title"]

        # Add missing URL scheme
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        if dry_run:
            print(f"  [{i}/{len(links)}] /{keyword} → {url[:60]}")
            success += 1
            continue

        ok, msg = shlink_create_short_url(
            config["shlink_url"],
            config["shlink_key"],
            long_url=url,
            custom_slug=keyword,
            title=title,
            tags=config.get("tags"),
        )

        if ok:
            success += 1
            print(f"  ✓ [{i}/{len(links)}] /{keyword} → {url[:60]}")
        else:
            if "already exists" in msg.lower() or "already in use" in msg.lower():
                skipped += 1
                print(f"  ○ [{i}/{len(links)}] /{keyword} (already exists)")
            else:
                failed += 1
                errors.append({"keyword": keyword, "url": url, "error": msg})
                print(f"  ✗ [{i}/{len(links)}] /{keyword} — {msg}")

        # Rate limiting: small delay between requests
        if i % 10 == 0:
            time.sleep(0.1)

    # Summary
    print()
    print("── Summary ────────────────────────────────────────────")
    print(f"  Total:   {len(links)}")
    if dry_run:
        print(f"  Preview: {success} URLs would be imported")
    else:
        print(f"  Created: {success}")
        print(f"  Skipped: {skipped} (already exist)")
        if failed:
            print(f"  Failed:  {failed}")
            print()
            print("  Failed URLs:")
            for err in errors:
                print(f"    /{err['keyword']} — {err['error']}")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate short URLs from YOURLS to Shlink.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (prompts for all values)
  python scripts/import-yourls.py

  # With signature auth + dry run
  python scripts/import-yourls.py \\
    --yourls-url https://old.example.com/yourls-api.php \\
    --yourls-signature abc123 \\
    --dry-run

  # With username/password auth + JSON export
  python scripts/import-yourls.py \\
    --yourls-url https://old.example.com/yourls-api.php \\
    --yourls-username admin --yourls-password secret \\
    --export yourls-backup.json

  # Shlink connection auto-detected from .env file
  python scripts/import-yourls.py --yourls-url ... --yourls-signature ...
        """,
    )

    yourls = parser.add_argument_group("YOURLS")
    yourls.add_argument("--yourls-url", help="YOURLS API URL (yourls-api.php endpoint)")
    yourls.add_argument("--yourls-signature", help="YOURLS signature token")
    yourls.add_argument("--yourls-username", help="YOURLS username (alternative to signature)")
    yourls.add_argument("--yourls-password", help="YOURLS password")

    shlink = parser.add_argument_group("Shlink")
    shlink.add_argument("--shlink-url", help="Shlink server URL (auto-detected from .env)")
    shlink.add_argument("--shlink-key", help="Shlink API key (auto-detected from .env)")

    parser.add_argument("--tag", action="append", default=[], help="Tag(s) to add to all imported URLs (repeatable, default: yourls-import)")
    parser.add_argument("--dry-run", action="store_true", help="Preview import without making changes")
    parser.add_argument("--export", metavar="FILE", help="Export fetched YOURLS data as JSON backup")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = prompt_config(args)

    if not config["yourls_url"]:
        print("✗ YOURLS API URL is required.", file=sys.stderr)
        sys.exit(1)
    if not config["shlink_url"] or not config["shlink_key"]:
        print("✗ Shlink URL and API key are required.", file=sys.stderr)
        sys.exit(1)

    config["tags"] = args.tag or ["yourls-import"]
    run_import(config, dry_run=args.dry_run, export_path=args.export)


if __name__ == "__main__":
    main()
