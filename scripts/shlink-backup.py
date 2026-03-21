#!/usr/bin/env python3
# =============================================================================
# shlink-backup.py — Backup & Restore Shlink configuration and data
# =============================================================================
# Exports all Shlink data via REST API (+ docker exec for API keys) to a
# JSON file (optionally gzip-compressed) and can restore to the same or
# a different instance.
#
# Backed up:
#   - Short URLs (shortCode, longUrl, tags, title, meta, device URLs)
#   - Redirect rules per short URL (conditional redirects)
#   - Domain redirect settings (not-found redirects per domain)
#   - Tags with stats
#   - API key metadata (informational — keys are hashed, not restorable)
#   - Visit data (optional, archival reference only)
#
# Usage:
#   python scripts/shlink-backup.py backup [--compress] [--output FILE]
#   python scripts/shlink-backup.py backup --include-visits --compress
#   python scripts/shlink-backup.py restore --input FILE [--skip-existing] [--dry-run]
#
# Configuration is auto-detected from .env file.
# Override with --url, --key, and --container options.
#
# Requirements: Python 3.6+ (no external dependencies)
# =============================================================================

import argparse
import gzip
import json
import ssl
import subprocess
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
    config = {
        "url": args.url,
        "key": args.key,
        "container": getattr(args, "container", None),
    }

    # Auto-detect from .env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
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

        if not config["container"]:
            stack = env.get("STACK_NAME", "url-shortener")
            config["container"] = f"{stack}_SERVER"

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


def fetch_visits_for_url(base_url: str, api_key: str, short_code: str) -> list[dict]:
    """Fetch all visits for a short URL (handles pagination)."""
    all_visits = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        status, data = _api_request(
            base_url, api_key, "GET", f"/short-urls/{short_code}/visits",
            params={"page": page, "itemsPerPage": 100},
        )

        if status != 200:
            break

        visits = data.get("visits", {})
        items = visits.get("data", [])
        pagination = visits.get("pagination", {})
        total_pages = pagination.get("pagesCount", 1)

        all_visits.extend(items)
        page += 1

    return all_visits


def fetch_redirect_rules(base_url: str, api_key: str, short_code: str) -> list[dict]:
    """Fetch redirect rules for a short URL."""
    status, data = _api_request(
        base_url, api_key, "GET", f"/short-urls/{short_code}/redirect-rules",
    )
    if status != 200:
        return []
    return data.get("redirectRules", [])


def fetch_domains(base_url: str, api_key: str) -> list[dict]:
    """Fetch all configured domains with their redirect settings."""
    status, data = _api_request(base_url, api_key, "GET", "/domains")
    if status != 200:
        return []
    return data.get("domains", {}).get("data", [])


def fetch_tags(base_url: str, api_key: str) -> list[dict]:
    """Fetch all tags with stats."""
    status, data = _api_request(
        base_url, api_key, "GET", "/tags/stats",
        params={"itemsPerPage": -1},
    )
    if status != 200:
        return []
    return data.get("tags", {}).get("data", [])


# ── Docker Exec (API Keys) ───────────────────────────────────────────────


def _docker_exec(container: str, *cmd: str) -> tuple:
    """Run a command inside the Shlink container via docker exec."""
    try:
        result = subprocess.run(
            ["docker", "exec", container, *cmd],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            output = result.stderr.strip() or output
        return result.returncode, output
    except FileNotFoundError:
        return -1, "docker not found"
    except subprocess.TimeoutExpired:
        return -1, "command timed out"


def fetch_api_keys(container: str) -> list[dict]:
    """Fetch API key metadata via docker exec (keys are hashed, not exportable)."""
    rc, output = _docker_exec(container, "shlink", "api-key:list")
    if rc != 0:
        return []

    # Parse the Symfony console table output
    keys = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("+") or line.startswith("|") and "Key" in line:
            continue
        if line.startswith("|"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 4:
                keys.append({
                    "key_hint": cols[0][:8] + "..." if len(cols[0]) > 8 else cols[0],
                    "name": cols[1] if cols[1] != "-" else None,
                    "expiration": cols[2] if cols[2] != "-" else None,
                    "enabled": "+++" in cols[3] or "Yes" in cols[3],
                })
    return keys


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


def restore_redirect_rules(
    base_url: str, api_key: str, short_code: str, rules: list[dict],
) -> tuple:
    """Restore redirect rules for a short URL. Returns (success, message)."""
    if not rules:
        return True, "no rules"
    status, data = _api_request(
        base_url, api_key, "POST",
        f"/short-urls/{short_code}/redirect-rules",
        body={"redirectRules": rules},
    )
    if status in (200, 201, 204):
        return True, f"{len(rules)} rules"
    return False, data.get("detail", f"HTTP {status}")


def restore_domain_redirects(
    base_url: str, api_key: str, domain: dict,
) -> tuple:
    """Restore redirect settings for a domain. Returns (success, message)."""
    redirects = domain.get("redirects", {})
    if not redirects or not any(redirects.values()):
        return True, "no redirects"

    body = {"domain": domain["authority"]}
    body.update(redirects)

    status, data = _api_request(
        base_url, api_key, "PATCH", "/domains/redirects", body=body,
    )
    if status in (200, 204):
        return True, domain["authority"]
    return False, data.get("detail", f"HTTP {status}")


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


def run_backup(config: dict, args: argparse.Namespace) -> None:
    """Export all Shlink data to a JSON file."""
    output = args.output
    compress = args.compress
    include_visits = args.include_visits

    print()
    print("╔═══════════════════════════════════════════════════════╗")
    print("║         Shlink Backup                                 ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()

    # ── Short URLs ────────────────────────────────────────────
    print("── Fetching short URLs ─────────────────────────────────")
    raw_urls = fetch_all_short_urls(config["url"], config["key"])

    if not raw_urls:
        print("  No short URLs found. Nothing to back up.")
        return

    entries = [_normalize_entry(u) for u in raw_urls]

    # ── Redirect Rules ────────────────────────────────────────
    print()
    print("── Fetching redirect rules ────────────────────────────")
    rules_count = 0
    for i, entry in enumerate(entries, 1):
        code = entry["shortCode"]
        rules = fetch_redirect_rules(config["url"], config["key"], code)
        if rules:
            entry["redirectRules"] = rules
            rules_count += len(rules)
        print(f"  {i}/{len(entries)}...", end="\r")
    print(f"  {rules_count} redirect rules across {len(entries)} URLs        ")

    # ── Visits (optional, archival) ───────────────────────────
    total_visits = 0
    if include_visits:
        print()
        print("── Fetching visits (archival) ──────────────────────────")
        for i, entry in enumerate(entries, 1):
            code = entry["shortCode"]
            visits = fetch_visits_for_url(config["url"], config["key"], code)
            entry["visits"] = visits
            total_visits += len(visits)
            print(f"  {i}/{len(entries)} /{code}: {len(visits)} visits", end="\r")
        print(f"  Total: {total_visits} visits across {len(entries)} URLs        ")

    # ── Domains ───────────────────────────────────────────────
    print()
    print("── Fetching domains ───────────────────────────────────")
    domains = fetch_domains(config["url"], config["key"])
    print(f"  {len(domains)} domains")

    # ── Tags ──────────────────────────────────────────────────
    print()
    print("── Fetching tags ──────────────────────────────────────")
    tags = fetch_tags(config["url"], config["key"])
    print(f"  {len(tags)} tags")

    # ── API Keys (informational, via docker exec) ─────────────
    api_keys = []
    container = config.get("container")
    if container:
        print()
        print("── Fetching API keys (informational) ──────────────────")
        api_keys = fetch_api_keys(container)
        if api_keys:
            print(f"  {len(api_keys)} API keys (hashed — not restorable)")
        else:
            print("  Skipped (docker not available or container not running)")

    # ── Build backup document ─────────────────────────────────
    backup = {
        "metadata": {
            "version": "2.0",
            "created": datetime.now(timezone.utc).isoformat(),
            "server": config["url"],
            "totalUrls": len(entries),
            "totalRedirectRules": rules_count,
            "totalDomains": len(domains),
            "totalTags": len(tags),
            "includesVisits": include_visits,
            "totalVisits": total_visits if include_visits else None,
            "apiKeysIncluded": len(api_keys),
            "tool": "shlink-backup.py",
        },
        "shortUrls": entries,
        "domains": domains,
        "tags": tags,
        "apiKeys": api_keys if api_keys else None,
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


def run_restore(config: dict, args: argparse.Namespace) -> None:
    """Restore Shlink data from a backup file."""
    input_file = args.input
    skip_existing = args.skip_existing
    dry_run = args.dry_run

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
    domains = backup.get("domains", [])
    meta = backup.get("metadata", {})
    version = meta.get("version", "1.0")

    print(f"  Format:  v{version}")
    print(f"  Source:  {meta.get('server', 'unknown')}")
    print(f"  Created: {meta.get('created', 'unknown')}")
    print(f"  URLs:    {len(entries)}")
    if domains:
        print(f"  Domains: {len(domains)}")
    if meta.get("apiKeysIncluded"):
        print(f"  API Keys: {meta['apiKeysIncluded']} (informational, not restorable)")

    if not entries and not domains:
        print("  Nothing to restore.")
        return

    # ── Restore short URLs ────────────────────────────────────
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
            rules = entry.get("redirectRules", [])
            suffix = f" (+{len(rules)} rules)" if rules else ""
            print(f"  [{i}/{len(entries)}] /{code} → {url_short}{suffix}")
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

    # ── Restore redirect rules ────────────────────────────────
    rules_with_data = [e for e in entries if e.get("redirectRules")]
    if rules_with_data and not dry_run:
        print()
        print("── Restoring redirect rules ───────────────────────────")
        rules_ok = 0
        rules_fail = 0
        for entry in rules_with_data:
            code = entry["shortCode"]
            ok, msg = restore_redirect_rules(
                config["url"], config["key"], code, entry["redirectRules"],
            )
            if ok:
                rules_ok += 1
                print(f"  ✓ /{code}: {msg}")
            else:
                rules_fail += 1
                print(f"  ✗ /{code}: {msg}")
        print(f"  {rules_ok} restored, {rules_fail} failed")

    # ── Restore domain redirects ──────────────────────────────
    non_default_domains = [d for d in domains if not d.get("isDefault", False)]
    if non_default_domains and not dry_run:
        print()
        print("── Restoring domain redirects ─────────────────────────")
        for domain in non_default_domains:
            ok, msg = restore_domain_redirects(
                config["url"], config["key"], domain,
            )
            if ok:
                print(f"  ✓ {msg}")
            else:
                print(f"  ✗ {domain.get('authority', '?')}: {msg}")

    # ── Summary ───────────────────────────────────────────────
    print()
    print("── Summary ────────────────────────────────────────────")
    print(f"  Total:   {len(entries)}")
    if dry_run:
        print(f"  Preview: {created} URLs would be restored")
        if rules_with_data:
            print(f"  Rules:   {sum(len(e['redirectRules']) for e in rules_with_data)} redirect rules")
        if non_default_domains:
            print(f"  Domains: {len(non_default_domains)} domain redirects")
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
        description="Backup & Restore Shlink data via REST API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Backed up data:
  - Short URLs (definitions, tags, meta, device URLs)
  - Redirect rules per short URL
  - Domain redirect settings
  - Tags with stats
  - API key metadata (informational, requires docker access)
  - Visit data (optional, archival reference)

Examples:
  # Full backup (auto-detects config from .env)
  python scripts/shlink-backup.py backup --compress

  # Full backup with visit archive
  python scripts/shlink-backup.py backup --include-visits --compress

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
    parser.add_argument("--container", help="Docker container name (auto-detected from .env)")

    sub = parser.add_subparsers(dest="command", required=True)

    # backup subcommand
    bp = sub.add_parser("backup", help="Export all Shlink data to JSON")
    bp.add_argument("--output", "-o", help="Output file path (default: auto-generated)")
    bp.add_argument("--compress", "-c", action="store_true", help="Compress with gzip")
    bp.add_argument("--include-visits", action="store_true", help="Include visit data (archival reference, not restorable)")

    # restore subcommand
    rp = sub.add_parser("restore", help="Restore Shlink data from JSON backup")
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
        run_backup(config, args)
    elif args.command == "restore":
        run_restore(config, args)


if __name__ == "__main__":
    main()
