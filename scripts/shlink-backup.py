#!/usr/bin/env python3
# =============================================================================
# shlink-backup.py — Backup & Restore Shlink short URLs
# =============================================================================
# Exports all short URL definitions from a Shlink instance via REST API to a
# JSON file (optionally gzip-compressed) and can restore them to the same or
# a different instance.
#
# Usage:
#   python scripts/shlink-backup.py backup [--compress] [--output FILE]
#   python scripts/shlink-backup.py restore --input FILE [--skip-existing] [--dry-run]
#
# Configuration is auto-detected from .env file.
# Override with --url and --key options.
#
# Note: Only short URL definitions are backed up (shortCode, longUrl, tags,
#       title, meta, etc.). Visit statistics are NOT included — they are
#       analytical data and can be regenerated from access logs.
#
# Requirements: Python 3.6+ (no external dependencies)
# =============================================================================

import argparse
import gzip
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ── Configuration ─────────────────────────────────────────────────────────


def _parse_env_file(path: Path) -> dict:
    """Parse a .env file into a dict (ignoring comments and empty lines)."""
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def load_config(args: argparse.Namespace) -> dict:
    """Build configuration from CLI args and .env file."""
    config = {"url": args.url, "key": args.key}

    if config["url"] and config["key"]:
        return config

    # Auto-detect from .env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return config

    env = _parse_env_file(env_path)

    if not config["url"]:
        domain = env.get("SHLINK_DOMAIN", "")
        is_https = env.get("SHLINK_IS_HTTPS", "true").lower() == "true"
        if domain:
            proto = "https" if is_https else "http"
            config["url"] = f"{proto}://{domain}"
            print(f"  Server (from .env): {config['url']}")

    if not config["key"]:
        key = env.get("SHLINK_API_KEY", "")
        if key:
            config["key"] = key
            print(f"  API Key (from .env): {key[:8]}...")

    return config


# ── Shlink API Client ────────────────────────────────────────────────────


def _api_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    body: dict = None,
    params: dict = None,
) -> tuple[int, dict]:
    """
    Make a Shlink REST API request.

    Returns (status_code, response_body) tuple.
    """
    url = f"{base_url.rstrip('/')}/rest/v3{path}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"

    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Api-Key", api_key)
    req.add_header("User-Agent", "shlink-backup/1.0")
    if body:
        req.add_header("Content-Type", "application/json")

    # Allow self-signed certificates in development
    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def fetch_all_short_urls(base_url: str, api_key: str) -> list[dict]:
    """Fetch all short URLs from Shlink (handles pagination)."""
    all_urls = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        status, data = _api_request(
            base_url, api_key, "GET", "/short-urls",
            params={"page": page, "itemsPerPage": 50},
        )

        if status != 200:
            detail = data.get("detail", data.get("title", "Unknown error"))
            raise RuntimeError(f"API error {status}: {detail}")

        short_urls = data.get("shortUrls", {})
        items = short_urls.get("data", [])
        pagination = short_urls.get("pagination", {})
        total_pages = pagination.get("pagesCount", 1)
        total_items = pagination.get("totalItems", 0)

        all_urls.extend(items)
        print(f"  Fetched {len(all_urls)}/{total_items} URLs...", end="\r")
        page += 1

    print(f"  Fetched {len(all_urls)} URLs              ")
    return all_urls


def create_short_url(base_url: str, api_key: str, entry: dict) -> tuple[bool, str]:
    """
    Create a short URL in Shlink from a backup entry.

    Returns (success, message) tuple.
    """
    body = {
        "longUrl": entry["longUrl"],
        "customSlug": entry["shortCode"],
        "findIfExists": False,
        "validateUrl": False,
    }

    # Optional fields
    if entry.get("tags"):
        body["tags"] = entry["tags"]
    if entry.get("title"):
        body["title"] = entry["title"]
    if entry.get("domain"):
        body["domain"] = entry["domain"]
    if entry.get("crawlable"):
        body["crawlable"] = entry["crawlable"]
    if entry.get("forwardQuery") is not None:
        body["forwardQuery"] = entry["forwardQuery"]

    # Meta fields (validSince, validUntil, maxVisits)
    if entry.get("validSince"):
        body["validSince"] = entry["validSince"]
    if entry.get("validUntil"):
        body["validUntil"] = entry["validUntil"]
    if entry.get("maxVisits"):
        body["maxVisits"] = entry["maxVisits"]

    # Device-specific long URLs
    device_urls = entry.get("deviceLongUrls", {})
    if device_urls and any(v for v in device_urls.values()):
        body["deviceLongUrls"] = device_urls

    status, data = _api_request(base_url, api_key, "POST", "/short-urls", body=body)

    if status in (200, 201):
        return True, f"/{data.get('shortCode', entry['shortCode'])}"
    else:
        detail = data.get("detail", data.get("title", f"HTTP {status}"))
        return False, detail


# ── Backup ────────────────────────────────────────────────────────────────


def _normalize_entry(raw: dict) -> dict:
    """Extract backup-relevant fields from a Shlink API response entry."""
    meta = raw.get("meta", {})
    return {
        "shortCode": raw.get("shortCode", ""),
        "longUrl": raw.get("longUrl", ""),
        "title": raw.get("title"),
        "tags": raw.get("tags", []),
        "domain": raw.get("domain"),
        "crawlable": raw.get("crawlable", False),
        "forwardQuery": raw.get("forwardQuery", True),
        "validSince": meta.get("validSince"),
        "validUntil": meta.get("validUntil"),
        "maxVisits": meta.get("maxVisits"),
        "deviceLongUrls": raw.get("deviceLongUrls", {}),
        "dateCreated": raw.get("dateCreated", ""),
        "visitsSummary": raw.get("visitsSummary", {}),
    }


def run_backup(config: dict, output: str, compress: bool) -> None:
    """Export all short URLs to a JSON file."""

    print()
    print("╔═══════════════════════════════════════════════════════╗")
    print("║         Shlink Backup                                 ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()

    # Fetch all short URLs
    print("── Fetching short URLs ─────────────────────────────────")
    raw_urls = fetch_all_short_urls(config["url"], config["key"])

    if not raw_urls:
        print("  No short URLs found. Nothing to back up.")
        return

    # Normalize entries
    entries = [_normalize_entry(u) for u in raw_urls]

    # Build backup document
    backup = {
        "metadata": {
            "version": "1.0",
            "created": datetime.now(timezone.utc).isoformat(),
            "server": config["url"],
            "totalUrls": len(entries),
            "tool": "shlink-backup.py",
        },
        "shortUrls": entries,
    }

    # Determine output path
    if not output:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        ext = ".json.gz" if compress else ".json"
        output = f"shlink-backup_{timestamp}{ext}"

    # Write file
    print()
    print("── Writing backup ─────────────────────────────────────")
    content = json.dumps(backup, indent=2, ensure_ascii=False).encode("utf-8")

    out_path = Path(output)
    if compress:
        if not out_path.suffix.endswith(".gz"):
            out_path = Path(f"{output}.gz")
        out_path.write_bytes(gzip.compress(content))
    else:
        out_path.write_bytes(content)

    size_kb = out_path.stat().st_size / 1024
    print(f"  ✓ {len(entries)} short URLs saved to {out_path} ({size_kb:.1f} KB)")
    print()


# ── Restore ───────────────────────────────────────────────────────────────


def run_restore(
    config: dict,
    input_file: str,
    skip_existing: bool,
    dry_run: bool,
) -> None:
    """Restore short URLs from a backup file."""

    print()
    print("╔═══════════════════════════════════════════════════════╗")
    if dry_run:
        print("║         Shlink Restore (DRY RUN)                     ║")
    else:
        print("║         Shlink Restore                                ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()

    # Read backup file
    print("── Reading backup ─────────────────────────────────────")
    in_path = Path(input_file)
    if not in_path.exists():
        print(f"  ✗ File not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    raw = in_path.read_bytes()
    if in_path.suffix == ".gz" or in_path.suffixes[-2:] == [".json", ".gz"]:
        raw = gzip.decompress(raw)
    backup = json.loads(raw.decode("utf-8"))

    # Validate format
    if "shortUrls" not in backup:
        print("  ✗ Invalid backup file: 'shortUrls' key missing.", file=sys.stderr)
        sys.exit(1)

    entries = backup["shortUrls"]
    meta = backup.get("metadata", {})
    print(f"  Source:  {meta.get('server', 'unknown')}")
    print(f"  Created: {meta.get('created', 'unknown')}")
    print(f"  URLs:    {len(entries)}")

    if not entries:
        print("  Nothing to restore.")
        return

    # Restore
    print()
    if dry_run:
        print("── Preview (no changes) ───────────────────────────────")
    else:
        print("── Restoring short URLs ───────────────────────────────")

    created = 0
    skipped = 0
    failed = 0
    errors = []

    for i, entry in enumerate(entries, 1):
        code = entry.get("shortCode", "?")
        url = entry.get("longUrl", "?")
        url_short = url[:60] + ("..." if len(url) > 60 else "")

        if dry_run:
            print(f"  [{i}/{len(entries)}] /{code} → {url_short}")
            created += 1
            continue

        ok, msg = create_short_url(config["url"], config["key"], entry)

        if ok:
            created += 1
            print(f"  ✓ [{i}/{len(entries)}] /{code} → {url_short}")
        elif "already" in msg.lower() or "slug" in msg.lower():
            skipped += 1
            if skip_existing:
                print(f"  ○ [{i}/{len(entries)}] /{code} (exists, skipped)")
            else:
                print(f"  ○ [{i}/{len(entries)}] /{code} (already exists)")
        else:
            failed += 1
            errors.append({"shortCode": code, "longUrl": url, "error": msg})
            print(f"  ✗ [{i}/{len(entries)}] /{code} — {msg}")

        # Rate limiting
        if i % 10 == 0:
            time.sleep(0.1)

    # Summary
    print()
    print("── Summary ────────────────────────────────────────────")
    print(f"  Total:   {len(entries)}")
    if dry_run:
        print(f"  Preview: {created} URLs would be restored")
    else:
        print(f"  Created: {created}")
        print(f"  Skipped: {skipped} (already exist)")
        if failed:
            print(f"  Failed:  {failed}")
            print()
            print("  Failed URLs:")
            for err in errors:
                print(f"    /{err['shortCode']} — {err['error']}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backup & Restore Shlink short URLs via REST API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backup all short URLs (auto-detects config from .env)
  python scripts/shlink-backup.py backup

  # Backup with gzip compression
  python scripts/shlink-backup.py backup --compress

  # Backup to specific file
  python scripts/shlink-backup.py backup --output /backups/shlink.json

  # Preview restore (no changes)
  python scripts/shlink-backup.py restore --input shlink-backup_2026-03-20.json --dry-run

  # Restore, skipping existing URLs
  python scripts/shlink-backup.py restore --input shlink-backup_2026-03-20.json --skip-existing

  # Restore to a different Shlink instance
  python scripts/shlink-backup.py restore --input backup.json \\
    --url https://new.example.com --key NEW_API_KEY
        """,
    )

    # Global options
    parser.add_argument("--url", help="Shlink server URL (auto-detected from .env)")
    parser.add_argument("--key", help="Shlink API key (auto-detected from .env)")

    sub = parser.add_subparsers(dest="command", required=True)

    # backup subcommand
    bp = sub.add_parser("backup", help="Export all short URLs to JSON")
    bp.add_argument("--output", "-o", help="Output file path (default: auto-generated)")
    bp.add_argument("--compress", "-c", action="store_true", help="Compress with gzip")

    # restore subcommand
    rp = sub.add_parser("restore", help="Import short URLs from JSON backup")
    rp.add_argument("--input", "-i", required=True, help="Backup file to restore from")
    rp.add_argument("--skip-existing", action="store_true", help="Silently skip existing URLs")
    rp.add_argument("--dry-run", action="store_true", help="Preview without making changes")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args)

    if not config["url"] or not config["key"]:
        print("✗ Shlink URL and API key are required.", file=sys.stderr)
        print("  Set in .env or pass --url and --key", file=sys.stderr)
        sys.exit(1)

    if args.command == "backup":
        run_backup(config, args.output, args.compress)
    elif args.command == "restore":
        run_restore(config, args.input, args.skip_existing, args.dry_run)


if __name__ == "__main__":
    main()
