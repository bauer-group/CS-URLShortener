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
# Install:  pip install typer rich
# Config:   auto-detected from .env file
#
# Requirements: Python 3.9+, typer, rich
# =============================================================================

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

try:
    import typer
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich import box as rbox
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install typer rich")
    sys.exit(1)

console = Console()

app = typer.Typer(
    name="shlink-backup",
    help="Backup & Restore Shlink data via REST API.",
    no_args_is_help=True,
)


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


def _load_config(
    url: str | None = None,
    key: str | None = None,
    container: str | None = None,
) -> dict:
    """Build configuration from args and .env file."""
    config = {"url": url, "key": key, "container": container}

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        env = _parse_env_file(env_path)

        if not config["url"]:
            domain = env.get("SHLINK_DOMAIN", "")
            is_https = env.get("SHLINK_IS_HTTPS", "true").lower() == "true"
            if domain:
                proto = "https" if is_https else "http"
                config["url"] = f"{proto}://{domain}"
                console.print(f"  [dim]Server (from .env):[/dim] {config['url']}")

        if not config["key"]:
            api_key = env.get("SHLINK_API_KEY", "")
            if api_key:
                config["key"] = api_key
                console.print(f"  [dim]API Key (from .env):[/dim] {api_key[:8]}...")

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
) -> tuple:
    """Make a Shlink REST API request. Returns (status_code, response_body)."""
    url = f"{base_url.rstrip('/')}/rest/v3{path}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"

    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Api-Key", api_key)
    req.add_header("User-Agent", "shlink-backup/2.0")
    if body:
        req.add_header("Content-Type", "application/json")

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


# ── Fetch Functions ──────────────────────────────────────────────────────


def _fetch_all_short_urls(base_url: str, api_key: str) -> list:
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

        all_urls.extend(items)
        page += 1

    return all_urls


def _fetch_visits_for_url(base_url: str, api_key: str, short_code: str) -> list:
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


def _fetch_redirect_rules(base_url: str, api_key: str, short_code: str) -> list:
    """Fetch redirect rules for a short URL."""
    status, data = _api_request(
        base_url, api_key, "GET", f"/short-urls/{short_code}/redirect-rules",
    )
    if status != 200:
        return []
    return data.get("redirectRules", [])


def _fetch_domains(base_url: str, api_key: str) -> list:
    """Fetch all configured domains with their redirect settings."""
    status, data = _api_request(base_url, api_key, "GET", "/domains")
    if status != 200:
        return []
    return data.get("domains", {}).get("data", [])


def _fetch_tags(base_url: str, api_key: str) -> list:
    """Fetch all tags with stats."""
    status, data = _api_request(
        base_url, api_key, "GET", "/tags/stats",
        params={"itemsPerPage": -1},
    )
    if status != 200:
        return []
    return data.get("tags", {}).get("data", [])


# ── Docker Exec (API Keys) ───────────────────────────────────────────────


def _find_container(hint: str | None = None) -> str:
    """Find the running Shlink container by image or name hint.

    Coolify generates dynamic container names (e.g. shlink-server-a12x...),
    so we resolve the actual name via `docker ps --filter ancestor=...`.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "ancestor=shlinkio/shlink",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            names = result.stdout.strip().splitlines()
            if len(names) == 1:
                return names[0]
            if hint:
                for name in names:
                    if hint.lower() in name.lower():
                        return name
            return names[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return hint or "shlink-server"


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


def _fetch_api_keys(container: str) -> list:
    """Fetch API key metadata via docker exec (keys are hashed, not exportable)."""
    rc, output = _docker_exec(container, "shlink", "api-key:list")
    if rc != 0:
        return []

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


# ── Create / Restore Helpers ─────────────────────────────────────────────


def _create_short_url(base_url: str, api_key: str, entry: dict) -> tuple:
    """Create a short URL from a backup entry. Returns (success, message)."""
    body = {
        "longUrl": entry["longUrl"],
        "customSlug": entry["shortCode"],
        "findIfExists": False,
        "validateUrl": False,
    }

    for field in ("tags", "title", "domain", "crawlable"):
        if entry.get(field):
            body[field] = entry[field]
    if entry.get("forwardQuery") is not None:
        body["forwardQuery"] = entry["forwardQuery"]
    for meta_field in ("validSince", "validUntil", "maxVisits"):
        if entry.get(meta_field):
            body[meta_field] = entry[meta_field]

    device_urls = entry.get("deviceLongUrls", {})
    if device_urls and any(v for v in device_urls.values()):
        body["deviceLongUrls"] = device_urls

    status, data = _api_request(base_url, api_key, "POST", "/short-urls", body=body)

    if status in (200, 201):
        return True, f"/{data.get('shortCode', entry['shortCode'])}"
    return False, data.get("detail", data.get("title", f"HTTP {status}"))


def _restore_redirect_rules(
    base_url: str, api_key: str, short_code: str, rules: list,
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


def _restore_domain_redirects(base_url: str, api_key: str, domain: dict) -> tuple:
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


# ── Normalize ─────────────────────────────────────────────────────────────


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


# ── Backup Command ───────────────────────────────────────────────────────


@app.command()
def backup(
    output: str = typer.Option(None, "--output", "-o", help="Output file path (default: auto-generated)"),
    compress: bool = typer.Option(False, "--compress", "-c", help="Compress with gzip"),
    include_visits: bool = typer.Option(False, "--include-visits", help="Include visit data (archival, not restorable)"),
    url: str = typer.Option(None, "--url", help="Shlink server URL (auto-detected from .env)"),
    key: str = typer.Option(None, "--key", help="API key (auto-detected from .env)"),
    container: str = typer.Option(None, "--container", help="Docker container name (auto-detected from .env)"),
):
    """Export all Shlink data to a JSON file."""
    config = _load_config(url, key, container)

    if not config["url"] or not config["key"]:
        console.print("[red]✗[/red] Shlink URL and API key are required.")
        console.print("  Set in .env or pass --url and --key")
        raise typer.Exit(1)

    console.print(Panel("[bold]Shlink Backup[/bold]", box=rbox.HEAVY))

    # ── Short URLs ────────────────────────────────────────────
    console.print("\n[bold]── Short URLs ──[/bold]")
    with console.status("Fetching short URLs..."):
        raw_urls = _fetch_all_short_urls(config["url"], config["key"])

    if not raw_urls:
        console.print("  No short URLs found. Nothing to back up.")
        return

    entries = [_normalize_entry(u) for u in raw_urls]
    console.print(f"  {len(entries)} URLs fetched")

    # ── Redirect Rules ────────────────────────────────────────
    console.print("\n[bold]── Redirect Rules ──[/bold]")
    rules_count = 0
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"), console=console) as progress:
        task = progress.add_task("Fetching rules...", total=len(entries))
        for entry in entries:
            rules = _fetch_redirect_rules(config["url"], config["key"], entry["shortCode"])
            if rules:
                entry["redirectRules"] = rules
                rules_count += len(rules)
            progress.advance(task)
    console.print(f"  {rules_count} redirect rules")

    # ── Visits (optional) ─────────────────────────────────────
    total_visits = 0
    if include_visits:
        console.print("\n[bold]── Visits (archival) ──[/bold]")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                      TextColumn("{task.completed}/{task.total}"), console=console) as progress:
            task = progress.add_task("Fetching visits...", total=len(entries))
            for entry in entries:
                visits = _fetch_visits_for_url(config["url"], config["key"], entry["shortCode"])
                entry["visits"] = visits
                total_visits += len(visits)
                progress.advance(task)
        console.print(f"  {total_visits} visits")

    # ── Domains ───────────────────────────────────────────────
    console.print("\n[bold]── Domains ──[/bold]")
    with console.status("Fetching domains..."):
        domains = _fetch_domains(config["url"], config["key"])
    console.print(f"  {len(domains)} domains")

    # ── Tags ──────────────────────────────────────────────────
    console.print("\n[bold]── Tags ──[/bold]")
    with console.status("Fetching tags..."):
        tags = _fetch_tags(config["url"], config["key"])
    console.print(f"  {len(tags)} tags")

    # ── API Keys ──────────────────────────────────────────────
    api_keys = []
    if config.get("container"):
        console.print("\n[bold]── API Keys (informational) ──[/bold]")
        container = _find_container(config.get("container"))
        api_keys = _fetch_api_keys(container)
        if api_keys:
            console.print(f"  {len(api_keys)} API keys (hashed — not restorable)")
        else:
            console.print("  [dim]Skipped (docker not available or container not running)[/dim]")

    # ── Build & write ─────────────────────────────────────────
    backup_doc = {
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

    if not output:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        ext = ".json.gz" if compress else ".json"
        output = f"shlink-backup_{timestamp}{ext}"

    console.print("\n[bold]── Writing backup ──[/bold]")
    content = json.dumps(backup_doc, indent=2, ensure_ascii=False).encode("utf-8")

    out_path = Path(output)
    if compress:
        if not out_path.suffix.endswith(".gz"):
            out_path = Path(f"{output}.gz")
        out_path.write_bytes(gzip.compress(content))
    else:
        out_path.write_bytes(content)

    size_kb = out_path.stat().st_size / 1024
    console.print(f"[green]✓[/green] {len(entries)} short URLs saved to {out_path} ({size_kb:.1f} KB)\n")


# ── Restore Command ──────────────────────────────────────────────────────


@app.command()
def restore(
    input_file: str = typer.Option(..., "--input", "-i", help="Backup file to restore from"),
    skip_existing: bool = typer.Option(False, "--skip-existing", help="Silently skip existing URLs"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without making changes"),
    url: str = typer.Option(None, "--url", help="Shlink server URL (auto-detected from .env)"),
    key: str = typer.Option(None, "--key", help="API key (auto-detected from .env)"),
    container: str = typer.Option(None, "--container", help="Docker container name (auto-detected from .env)"),
):
    """Restore Shlink data from a JSON backup file."""
    config = _load_config(url, key, container)

    if not config["url"] or not config["key"]:
        console.print("[red]✗[/red] Shlink URL and API key are required.")
        console.print("  Set in .env or pass --url and --key")
        raise typer.Exit(1)

    title = "Shlink Restore (DRY RUN)" if dry_run else "Shlink Restore"
    console.print(Panel(f"[bold]{title}[/bold]", box=rbox.HEAVY))

    # ── Read backup ───────────────────────────────────────────
    console.print("\n[bold]── Reading backup ──[/bold]")
    in_path = Path(input_file)
    if not in_path.exists():
        console.print(f"[red]✗[/red] File not found: {in_path}")
        raise typer.Exit(1)

    raw = in_path.read_bytes()
    if in_path.suffix == ".gz" or in_path.suffixes[-2:] == [".json", ".gz"]:
        raw = gzip.decompress(raw)
    backup_data = json.loads(raw.decode("utf-8"))

    if "shortUrls" not in backup_data:
        console.print("[red]✗[/red] Invalid backup file: 'shortUrls' key missing.")
        raise typer.Exit(1)

    entries = backup_data["shortUrls"]
    domains = backup_data.get("domains", [])
    meta = backup_data.get("metadata", {})
    version = meta.get("version", "1.0")

    console.print(f"  [dim]Format:[/dim]  v{version}")
    console.print(f"  [dim]Source:[/dim]  {meta.get('server', 'unknown')}")
    console.print(f"  [dim]Created:[/dim] {meta.get('created', 'unknown')}")
    console.print(f"  [dim]URLs:[/dim]    {len(entries)}")
    if domains:
        console.print(f"  [dim]Domains:[/dim] {len(domains)}")
    if meta.get("apiKeysIncluded"):
        console.print(f"  [dim]API Keys:[/dim] {meta['apiKeysIncluded']} (informational, not restorable)")

    if not entries and not domains:
        console.print("  Nothing to restore.")
        return

    # ── Restore short URLs ────────────────────────────────────
    label = "Preview (no changes)" if dry_run else "Restoring short URLs"
    console.print(f"\n[bold]── {label} ──[/bold]")

    created = 0
    skipped = 0
    failed = 0
    errors = []

    for i, entry in enumerate(entries, 1):
        code = entry.get("shortCode", "?")
        entry_url = entry.get("longUrl", "?")
        url_short = entry_url[:60] + ("..." if len(entry_url) > 60 else "")

        if dry_run:
            rules = entry.get("redirectRules", [])
            suffix = f" (+{len(rules)} rules)" if rules else ""
            console.print(f"  [{i}/{len(entries)}] [cyan]/{code}[/cyan] → {url_short}{suffix}")
            created += 1
            continue

        ok, msg = _create_short_url(config["url"], config["key"], entry)

        if ok:
            created += 1
            console.print(f"  [green]✓[/green] [{i}/{len(entries)}] /{code} → {url_short}")
        elif "already" in msg.lower() or "slug" in msg.lower():
            skipped += 1
            console.print(f"  [dim]○[/dim] [{i}/{len(entries)}] /{code} (exists, skipped)")
        else:
            failed += 1
            errors.append({"shortCode": code, "longUrl": entry_url, "error": msg})
            console.print(f"  [red]✗[/red] [{i}/{len(entries)}] /{code} — {msg}")

        if i % 10 == 0:
            time.sleep(0.1)

    # ── Restore redirect rules ────────────────────────────────
    rules_with_data = [e for e in entries if e.get("redirectRules")]
    if rules_with_data and not dry_run:
        console.print("\n[bold]── Restoring redirect rules ──[/bold]")
        rules_ok = 0
        rules_fail = 0
        for entry in rules_with_data:
            code = entry["shortCode"]
            ok, msg = _restore_redirect_rules(
                config["url"], config["key"], code, entry["redirectRules"],
            )
            if ok:
                rules_ok += 1
                console.print(f"  [green]✓[/green] /{code}: {msg}")
            else:
                rules_fail += 1
                console.print(f"  [red]✗[/red] /{code}: {msg}")
        console.print(f"  {rules_ok} restored, {rules_fail} failed")

    # ── Restore domain redirects ──────────────────────────────
    non_default_domains = [d for d in domains if not d.get("isDefault", False)]
    if non_default_domains and not dry_run:
        console.print("\n[bold]── Restoring domain redirects ──[/bold]")
        for domain in non_default_domains:
            ok, msg = _restore_domain_redirects(config["url"], config["key"], domain)
            if ok:
                console.print(f"  [green]✓[/green] {msg}")
            else:
                console.print(f"  [red]✗[/red] {domain.get('authority', '?')}: {msg}")

    # ── Summary ───────────────────────────────────────────────
    console.print("\n[bold]── Summary ──[/bold]")
    console.print(f"  [bold]Total:[/bold]   {len(entries)}")
    if dry_run:
        console.print(f"  Preview: {created} URLs would be restored")
        if rules_with_data:
            console.print(f"  Rules:   {sum(len(e['redirectRules']) for e in rules_with_data)} redirect rules")
        if non_default_domains:
            console.print(f"  Domains: {len(non_default_domains)} domain redirects")
    else:
        console.print(f"  [green]Created:[/green] {created}")
        console.print(f"  [dim]Skipped:[/dim] {skipped} (already exist)")
        if failed:
            console.print(f"  [red]Failed:[/red]  {failed}")
            console.print()
            console.print("  Failed URLs:")
            for err in errors:
                console.print(f"    /{err['shortCode']} — {err['error']}")
    console.print()


if __name__ == "__main__":
    app()
