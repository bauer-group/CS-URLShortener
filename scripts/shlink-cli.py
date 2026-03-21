#!/usr/bin/env python3
# =============================================================================
# shlink-cli.py — Manage Shlink from the command line
# =============================================================================
# CLI for managing short URLs, tags, visits, and API keys via the Shlink
# REST API. API key management uses docker exec (keys are not in the API).
#
# Usage:
#   python scripts/shlink-cli.py list [--tag TAG]
#   python scripts/shlink-cli.py create <long-url> [--slug SLUG] [--tag TAG]
#   python scripts/shlink-cli.py info <short-code>
#   python scripts/shlink-cli.py update <short-code> [--url URL] [--title TITLE]
#   python scripts/shlink-cli.py delete <short-code> [--yes]
#   python scripts/shlink-cli.py visits <short-code> [--detail]
#   python scripts/shlink-cli.py tags
#   python scripts/shlink-cli.py tag-rename <old> <new>
#   python scripts/shlink-cli.py tag-delete <tag> [--yes]
#   python scripts/shlink-cli.py health
#   python scripts/shlink-cli.py keys
#   python scripts/shlink-cli.py key-add [--name NAME] [--expiration DATE]
#   python scripts/shlink-cli.py key-disable <api-key> [--yes]
#
# Configuration is auto-detected from .env file.
# Override with --server, --key, and --container options.
#
# Requirements: Python 3.6+ (no external dependencies)
# =============================================================================

import argparse
import http.client
import json
import ssl
import subprocess
import sys
import urllib.parse
from pathlib import Path


# ── Configuration ─────────────────────────────────────────────────────────


def _parse_env_file(path: Path) -> dict:
    """Parse a .env file into a dict."""
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
        "url": getattr(args, "server", None),
        "key": getattr(args, "key", None),
        "container": getattr(args, "container", None),
    }

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return config

    env = _parse_env_file(env_path)

    if not config["url"]:
        domain = env.get("SHLINK_DOMAIN", "")
        is_https = env.get("SHLINK_IS_HTTPS", "true").lower() == "true"
        if domain:
            config["url"] = f"{'https' if is_https else 'http'}://{domain}"

    if not config["key"]:
        config["key"] = env.get("SHLINK_API_KEY", "")

    if not config["container"]:
        stack = env.get("STACK_NAME", "url-shortener")
        config["container"] = f"{stack}_SERVER"

    return config


# ── API Client ────────────────────────────────────────────────────────────


def api(config: dict, method: str, path: str, body: dict = None, params: dict = None) -> tuple[int, dict]:
    """
    Shlink REST API call via http.client.

    Fresh TCP connection per request to avoid urllib hang issues on Windows.
    """
    parsed = urllib.parse.urlparse(config["url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    api_path = f"/rest/v3{path}"
    if params:
        api_path += f"?{urllib.parse.urlencode(params)}"

    headers = {"X-Api-Key": config["key"], "Connection": "close"}
    payload = None
    if body:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=10,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, port, timeout=10)

        conn.request(method, api_path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        status = resp.status
        conn.close()

        if not raw.strip():
            return status, {}
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, {"detail": raw[:200]}

    except Exception as e:
        print(f"  Connection error: {e}", file=sys.stderr)
        sys.exit(1)


# ── Docker Exec ───────────────────────────────────────────────────────────


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
        print("  ✗ docker not found. Run this from the Docker host.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("  ✗ Command timed out.", file=sys.stderr)
        sys.exit(1)


# ── Commands ──────────────────────────────────────────────────────────────


def cmd_list(config: dict, args: argparse.Namespace) -> None:
    """List all short URLs."""
    page = 1
    total_pages = 1
    count = 0

    while page <= total_pages:
        params = {"page": page, "itemsPerPage": 50}
        if args.tag:
            params["tags[]"] = args.tag

        status, data = api(config, "GET", "/short-urls", params=params)
        if status != 200:
            print(f"  Error: {data.get('detail', f'HTTP {status}')}", file=sys.stderr)
            sys.exit(1)

        short_urls = data.get("shortUrls", {})
        items = short_urls.get("data", [])
        pagination = short_urls.get("pagination", {})
        total_pages = pagination.get("pagesCount", 1)
        total_items = pagination.get("totalItems", 0)

        if page == 1:
            print(f"  {total_items} short URLs\n")

        for item in items:
            code = item.get("shortCode", "?")
            url = item.get("longUrl", "?")
            title = item.get("title") or ""
            tags = ", ".join(item.get("tags", []))
            visits = item.get("visitsSummary", {}).get("total", 0)

            line = f"  /{code:<20s} → {url[:60]}"
            if title:
                line += f"\n  {'':<23s}  {title[:60]}"
            meta = []
            if tags:
                meta.append(f"tags: {tags}")
            if visits:
                meta.append(f"{visits} visits")
            if meta:
                line += f"\n  {'':<23s}  [{', '.join(meta)}]"
            print(line)
            count += 1

        page += 1

    if count == 0:
        print("  No short URLs found.")


def cmd_create(config: dict, args: argparse.Namespace) -> None:
    """Create a new short URL."""
    body = {
        "longUrl": args.long_url,
        "validateUrl": False,
    }
    if args.slug:
        body["customSlug"] = args.slug
    if args.title:
        body["title"] = args.title
    if args.tag:
        body["tags"] = args.tag

    status, data = api(config, "POST", "/short-urls", body=body)

    if status in (200, 201):
        code = data.get("shortCode", "?")
        short_url = data.get("shortUrl", f"{config['url']}/{code}")
        print(f"  ✓ {short_url}")
    else:
        detail = data.get("detail", f"HTTP {status}")
        print(f"  ✗ {detail}", file=sys.stderr)
        sys.exit(1)


def cmd_info(config: dict, args: argparse.Namespace) -> None:
    """Show details for a short URL."""
    status, data = api(config, "GET", f"/short-urls/{args.short_code}")

    if status == 404:
        print(f"  ✗ /{args.short_code} not found", file=sys.stderr)
        sys.exit(1)
    if status != 200:
        print(f"  ✗ {data.get('detail', f'HTTP {status}')}", file=sys.stderr)
        sys.exit(1)

    print(f"  Short Code:  /{data.get('shortCode')}")
    print(f"  Short URL:   {data.get('shortUrl')}")
    print(f"  Long URL:    {data.get('longUrl')}")
    if data.get("title"):
        print(f"  Title:       {data['title']}")
    if data.get("tags"):
        print(f"  Tags:        {', '.join(data['tags'])}")
    print(f"  Created:     {data.get('dateCreated', '?')}")

    meta = data.get("meta", {})
    if meta.get("validSince"):
        print(f"  Valid Since: {meta['validSince']}")
    if meta.get("validUntil"):
        print(f"  Valid Until: {meta['validUntil']}")
    if meta.get("maxVisits"):
        print(f"  Max Visits:  {meta['maxVisits']}")

    visits = data.get("visitsSummary", {})
    print(f"  Visits:      {visits.get('total', 0)} (bots: {visits.get('bots', 0)})")
    print(f"  Crawlable:   {data.get('crawlable', False)}")
    print(f"  Fwd Query:   {data.get('forwardQuery', True)}")


def cmd_update(config: dict, args: argparse.Namespace) -> None:
    """Update an existing short URL."""
    body = {}
    if args.url:
        body["longUrl"] = args.url
    if args.title:
        body["title"] = args.title
    if args.tag:
        body["tags"] = args.tag

    if not body:
        print("  Nothing to update. Use --url, --title, or --tag.", file=sys.stderr)
        sys.exit(1)

    status, data = api(config, "PATCH", f"/short-urls/{args.short_code}", body=body)

    if status == 200:
        print(f"  ✓ /{args.short_code} updated")
    elif status == 404:
        print(f"  ✗ /{args.short_code} not found", file=sys.stderr)
        sys.exit(1)
    else:
        detail = data.get("detail", f"HTTP {status}")
        print(f"  ✗ {detail}", file=sys.stderr)
        sys.exit(1)


def cmd_delete(config: dict, args: argparse.Namespace) -> None:
    """Delete a short URL."""
    if not args.yes:
        confirm = input(f"  Delete /{args.short_code}? [y/N] ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

    status, data = api(config, "DELETE", f"/short-urls/{args.short_code}")

    if status == 204:
        print(f"  ✓ /{args.short_code} deleted")
    elif status == 404:
        print(f"  ✗ /{args.short_code} not found", file=sys.stderr)
        sys.exit(1)
    else:
        detail = data.get("detail", f"HTTP {status}")
        print(f"  ✗ {detail}", file=sys.stderr)
        sys.exit(1)


# ── Visits & Tags ─────────────────────────────────────────────────────────


def cmd_visits(config: dict, args: argparse.Namespace) -> None:
    """Show visit statistics for a short URL."""
    code = args.short_code
    status, data = api(config, "GET", f"/short-urls/{code}")
    if status == 404:
        print(f"  ✗ /{code} not found", file=sys.stderr)
        sys.exit(1)
    if status != 200:
        print(f"  ✗ {data.get('detail', f'HTTP {status}')}", file=sys.stderr)
        sys.exit(1)

    summary = data.get("visitsSummary", {})
    print(f"  /{code}")
    print(f"  Total:     {summary.get('total', 0)}")
    print(f"  Non-bots:  {summary.get('nonBots', 0)}")
    print(f"  Bots:      {summary.get('bots', 0)}")

    if not args.detail:
        return

    # Fetch individual visits
    print()
    page = 1
    total_pages = 1
    count = 0
    while page <= total_pages:
        params = {"page": page, "itemsPerPage": 20}
        vs, vdata = api(config, "GET", f"/short-urls/{code}/visits", params=params)
        if vs != 200:
            break
        visits = vdata.get("visits", {})
        items = visits.get("data", [])
        pagination = visits.get("pagination", {})
        total_pages = pagination.get("pagesCount", 1)

        for v in items:
            date = v.get("date", "?")[:19]
            referer = v.get("referer") or "-"
            ua = v.get("userAgent") or "-"
            location = ""
            loc = v.get("visitLocation") or {}
            if loc.get("cityName"):
                location = f"{loc['cityName']}, {loc.get('countryName', '')}"
            elif loc.get("countryName"):
                location = loc["countryName"]

            print(f"  {date}  {referer[:30]:<30s}  {location[:20]:<20s}")
            count += 1
            if count >= args.limit:
                remaining = pagination.get("totalItems", 0) - count
                if remaining > 0:
                    print(f"  ... {remaining} more (use --limit to show more)")
                return

        page += 1


def cmd_tags(config: dict, args: argparse.Namespace) -> None:
    """List all tags with stats."""
    params = {"itemsPerPage": -1}
    status, data = api(config, "GET", "/tags/stats", params=params)
    if status != 200:
        print(f"  ✗ {data.get('detail', f'HTTP {status}')}", file=sys.stderr)
        sys.exit(1)

    tags = data.get("tags", {}).get("data", [])
    if not tags:
        print("  No tags found.")
        return

    print(f"  {len(tags)} tags\n")
    for t in sorted(tags, key=lambda x: x.get("tag", "")):
        name = t.get("tag", "?")
        urls = t.get("shortUrlsCount", 0)
        visits = t.get("visitsSummary", {}).get("total", 0)
        print(f"  {name:<30s}  {urls:>4d} URLs  {visits:>6d} visits")


def cmd_tag_rename(config: dict, args: argparse.Namespace) -> None:
    """Rename a tag."""
    body = {"oldName": args.old_name, "newName": args.new_name}
    status, data = api(config, "PUT", "/tags", body=body)

    if status in (200, 204):
        print(f"  ✓ '{args.old_name}' → '{args.new_name}'")
    elif status == 404:
        print(f"  ✗ Tag '{args.old_name}' not found", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"  ✗ {data.get('detail', f'HTTP {status}')}", file=sys.stderr)
        sys.exit(1)


def cmd_tag_delete(config: dict, args: argparse.Namespace) -> None:
    """Delete one or more tags."""
    if not args.yes:
        names = ", ".join(args.tags)
        confirm = input(f"  Delete tags [{names}]? [y/N] ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

    params = {f"tags[]": args.tags}
    # DELETE /tags expects tags as query params
    param_str = "&".join(f"tags[]={urllib.parse.quote(t)}" for t in args.tags)
    status, data = api(config, "DELETE", f"/tags?{param_str}")

    if status in (200, 204):
        print(f"  ✓ {len(args.tags)} tag(s) deleted")
    else:
        print(f"  ✗ {data.get('detail', f'HTTP {status}')}", file=sys.stderr)
        sys.exit(1)


def cmd_health(config: dict, args: argparse.Namespace) -> None:
    """Check server health status."""
    parsed = urllib.parse.urlparse(config["url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=5,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, port, timeout=5)

        conn.request("GET", "/rest/health", headers={"Connection": "close"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        status = resp.status
        conn.close()

        data = json.loads(raw) if raw.strip() else {}
        health = data.get("status", "unknown")
        version = data.get("version", "?")
        links = data.get("links", {})

        if status == 200 and health == "pass":
            print(f"  ✓ Healthy")
            print(f"  Version: {version}")
            if links.get("about"):
                print(f"  About:   {links['about']}")
        else:
            print(f"  ✗ Unhealthy (status: {health})", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"  ✗ Unreachable: {e}", file=sys.stderr)
        sys.exit(1)


# ── API Key Commands (docker exec) ────────────────────────────────────────


def cmd_keys(config: dict, args: argparse.Namespace) -> None:
    """List all API keys."""
    rc, output = _docker_exec(config["container"], "shlink", "api-key:list")
    if rc != 0:
        print(f"  ✗ {output}", file=sys.stderr)
        sys.exit(1)
    print(output)


def cmd_key_add(config: dict, args: argparse.Namespace) -> None:
    """Generate a new API key."""
    cmd = ["shlink", "api-key:generate"]
    if args.name:
        cmd.extend(["--name", args.name])
    if args.expiration:
        cmd.extend(["--expiration-date", args.expiration])

    rc, output = _docker_exec(config["container"], *cmd)
    if rc != 0:
        print(f"  ✗ {output}", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ {output}")


def cmd_key_disable(config: dict, args: argparse.Namespace) -> None:
    """Disable an API key."""
    if not args.yes:
        confirm = input(f"  Disable API key {args.api_key[:8]}...? [y/N] ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

    rc, output = _docker_exec(config["container"], "shlink", "api-key:disable", args.api_key)
    if rc != 0:
        print(f"  ✗ {output}", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ API key disabled")


# ── CLI ───────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage Shlink short URLs, tags, visits, and API keys.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/shlink-cli.py list
  python scripts/shlink-cli.py list --tag yourls
  python scripts/shlink-cli.py create https://example.com --slug test --tag marketing
  python scripts/shlink-cli.py info test
  python scripts/shlink-cli.py update test --url https://new.example.com --title "New Title"
  python scripts/shlink-cli.py delete test --yes
  python scripts/shlink-cli.py visits test --detail --limit 50
  python scripts/shlink-cli.py tags
  python scripts/shlink-cli.py tag-rename oldname newname
  python scripts/shlink-cli.py tag-delete obsolete-tag --yes
  python scripts/shlink-cli.py health
  python scripts/shlink-cli.py keys
  python scripts/shlink-cli.py key-add --name "Marketing Team"
  python scripts/shlink-cli.py key-disable e2336c75-ac55-4f07-bf75-e98e6c29ef6e
        """,
    )

    parser.add_argument("--server", help="Shlink server URL (auto-detected from .env)")
    parser.add_argument("--key", help="Shlink API key (auto-detected from .env)")
    parser.add_argument("--container", help="Docker container name (auto-detected from .env)")

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    ls = sub.add_parser("list", help="List all short URLs")
    ls.add_argument("--tag", help="Filter by tag")

    # create
    cr = sub.add_parser("create", help="Create a short URL")
    cr.add_argument("long_url", help="Target URL")
    cr.add_argument("--slug", "-s", help="Custom short code")
    cr.add_argument("--title", "-t", help="Title")
    cr.add_argument("--tag", action="append", help="Tag (repeatable)")

    # info
    inf = sub.add_parser("info", help="Show short URL details")
    inf.add_argument("short_code", help="Short code to inspect")

    # update
    up = sub.add_parser("update", help="Update a short URL")
    up.add_argument("short_code", help="Short code to update")
    up.add_argument("--url", "-u", help="New target URL")
    up.add_argument("--title", "-t", help="New title")
    up.add_argument("--tag", action="append", help="Replace tags (repeatable)")

    # delete
    dl = sub.add_parser("delete", help="Delete a short URL")
    dl.add_argument("short_code", help="Short code to delete")
    dl.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    # visits
    vi = sub.add_parser("visits", help="Show visit statistics for a short URL")
    vi.add_argument("short_code", help="Short code to inspect")
    vi.add_argument("--detail", "-d", action="store_true", help="Show individual visits")
    vi.add_argument("--limit", "-l", type=int, default=20, help="Max visits to show (default: 20)")

    # tags
    sub.add_parser("tags", help="List all tags with stats")

    tr = sub.add_parser("tag-rename", help="Rename a tag")
    tr.add_argument("old_name", help="Current tag name")
    tr.add_argument("new_name", help="New tag name")

    td = sub.add_parser("tag-delete", help="Delete tags")
    td.add_argument("tags", nargs="+", help="Tag name(s) to delete")
    td.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    # health
    sub.add_parser("health", help="Check server health status")

    # keys (API key management via docker exec)
    sub.add_parser("keys", help="List all API keys")

    ka = sub.add_parser("key-add", help="Generate a new API key")
    ka.add_argument("--name", "-n", help="Human-readable name")
    ka.add_argument("--expiration", "-e", help="Expiration date (YYYY-MM-DD)")

    kd = sub.add_parser("key-disable", help="Disable an API key")
    kd.add_argument("api_key", help="API key to disable")
    kd.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args)

    # API key commands only need docker access, not REST API credentials
    key_commands = {
        "keys": cmd_keys,
        "key-add": cmd_key_add,
        "key-disable": cmd_key_disable,
    }

    if args.command in key_commands:
        key_commands[args.command](config, args)
        return

    if not config["url"] or not config["key"]:
        print("✗ Shlink URL and API key are required.", file=sys.stderr)
        print("  Set in .env or pass --server and --key", file=sys.stderr)
        sys.exit(1)

    commands = {
        "list": cmd_list,
        "create": cmd_create,
        "info": cmd_info,
        "update": cmd_update,
        "delete": cmd_delete,
        "visits": cmd_visits,
        "tags": cmd_tags,
        "tag-rename": cmd_tag_rename,
        "tag-delete": cmd_tag_delete,
        "health": cmd_health,
    }
    commands[args.command](config, args)


if __name__ == "__main__":
    main()
