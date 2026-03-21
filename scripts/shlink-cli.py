#!/usr/bin/env python3
# =============================================================================
# shlink-cli.py — Manage Shlink from the command line
# =============================================================================
# CLI for managing short URLs, tags, visits, and API keys via the Shlink
# REST API. API key management uses docker exec (keys are not in the API).
#
# Usage:
#   python scripts/shlink-cli.py list [--tag TAG]
#   python scripts/shlink-cli.py create <url> [--slug SLUG] [--tag TAG]
#   python scripts/shlink-cli.py info <code>
#   python scripts/shlink-cli.py update <code> [--url URL] [--title TITLE]
#   python scripts/shlink-cli.py delete <code> [--yes]
#   python scripts/shlink-cli.py visits <code> [--detail]
#   python scripts/shlink-cli.py tag list
#   python scripts/shlink-cli.py tag rename <old> <new>
#   python scripts/shlink-cli.py tag delete <tag>
#   python scripts/shlink-cli.py health
#   python scripts/shlink-cli.py key list
#   python scripts/shlink-cli.py key add [--name NAME] [--expiration DATE]
#   python scripts/shlink-cli.py key disable <key>
#
# Install:  pip install typer rich
# Config:   auto-detected from .env file
#
# Requirements: Python 3.9+, typer, rich
# =============================================================================

import http.client
import json
import ssl
import subprocess
import sys
import urllib.parse
from pathlib import Path

try:
    import typer
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box as rbox
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install typer rich")
    sys.exit(1)

console = Console()

# ── Typer Apps ────────────────────────────────────────────────────────────

app = typer.Typer(
    name="shlink",
    help="Manage Shlink short URLs, tags, visits, and API keys.",
    no_args_is_help=True,
)

tag_app = typer.Typer(
    name="tag",
    help="Tag management (list, rename, delete).",
    no_args_is_help=True,
)
app.add_typer(tag_app, name="tag")

key_app = typer.Typer(
    name="key",
    help="API key management via docker exec (list, add, disable).",
    no_args_is_help=True,
)
app.add_typer(key_app, name="key")


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


def _load_config(
    server: str | None = None,
    key: str | None = None,
    container: str | None = None,
) -> dict:
    """Build configuration from args and .env file."""
    config = {"url": server, "key": key, "container": container}

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


def _require_api(config: dict) -> None:
    """Exit if API credentials are missing."""
    if not config["url"] or not config["key"]:
        console.print("[red]✗[/red] Shlink URL and API key are required.")
        console.print("  Set in .env or pass --server and --key")
        raise typer.Exit(1)


# ── API Client ────────────────────────────────────────────────────────────


def _api(config: dict, method: str, path: str, body: dict = None, params: dict = None) -> tuple:
    """Shlink REST API call. Fresh TCP connection per request (Windows safe)."""
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
        console.print(f"[red]✗[/red] Connection error: {e}")
        raise typer.Exit(1)


# ── Docker Exec ───────────────────────────────────────────────────────────


def _find_container(hint: str | None = None) -> str:
    """Find the running Shlink container by image or name hint.

    Coolify generates dynamic container names (e.g. shlink-server-a12x...),
    so we resolve the actual name via `docker ps --filter ancestor=...`.
    """
    try:
        # Find by image (works with any container name)
        result = subprocess.run(
            ["docker", "ps", "--filter", "ancestor=shlinkio/shlink",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            names = result.stdout.strip().splitlines()
            if len(names) == 1:
                return names[0]
            # Multiple matches — prefer the one matching the hint
            if hint:
                for name in names:
                    if hint.lower() in name.lower():
                        return name
            return names[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to hint or default
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
        console.print("[red]✗[/red] docker not found. Run this from the Docker host.")
        raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        console.print("[red]✗[/red] Command timed out.")
        raise typer.Exit(1)


# ── Global Options (callback) ────────────────────────────────────────────
# Typer stores global options via a callback on the main app.

_global_config: dict = {}


@app.callback()
def main(
    server: str = typer.Option(None, "--server", "-s", help="Shlink server URL (auto-detected from .env)"),
    key: str = typer.Option(None, "--key", "-k", help="API key (auto-detected from .env)"),
    container: str = typer.Option(None, "--container", "-c", help="Docker container name (auto-detected from .env)"),
):
    """Manage Shlink short URLs, tags, visits, and API keys."""
    global _global_config
    _global_config = _load_config(server, key, container)


# ── Short URL Commands ───────────────────────────────────────────────────


@app.command("list")
def cmd_list(
    tag: str = typer.Option(None, "--tag", "-t", help="Filter by tag"),
):
    """List all short URLs."""
    _require_api(_global_config)
    page = 1
    total_pages = 1
    rows = []

    while page <= total_pages:
        params = {"page": page, "itemsPerPage": 50}
        if tag:
            params["tags[]"] = tag

        status, data = _api(_global_config, "GET", "/short-urls", params=params)
        if status != 200:
            console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
            raise typer.Exit(1)

        short_urls = data.get("shortUrls", {})
        items = short_urls.get("data", [])
        pagination = short_urls.get("pagination", {})
        total_pages = pagination.get("pagesCount", 1)

        for item in items:
            code = item.get("shortCode", "?")
            url = item.get("longUrl", "?")
            title = item.get("title") or ""
            tags = ", ".join(item.get("tags", []))
            visits = item.get("visitsSummary", {}).get("total", 0)
            rows.append((f"/{code}", url[:60], title[:40], tags[:30], str(visits)))

        page += 1

    if not rows:
        console.print("  No short URLs found.")
        return

    tbl = Table(title=f"{len(rows)} Short URLs", box=rbox.SIMPLE_HEAVY, title_style="bold")
    tbl.add_column("Code", style="cyan")
    tbl.add_column("Target URL")
    tbl.add_column("Title", style="dim")
    tbl.add_column("Tags", style="yellow")
    tbl.add_column("Visits", justify="right", style="green")
    for row in rows:
        tbl.add_row(*row)
    console.print(tbl)


@app.command()
def create(
    long_url: str = typer.Argument(..., help="Target URL"),
    slug: str = typer.Option(None, "--slug", "-s", help="Custom short code"),
    title: str = typer.Option(None, "--title", "-t", help="Title"),
    tag: list[str] = typer.Option(None, "--tag", help="Tag (repeatable)"),
):
    """Create a new short URL."""
    _require_api(_global_config)
    body = {"longUrl": long_url, "validateUrl": False}
    if slug:
        body["customSlug"] = slug
    if title:
        body["title"] = title
    if tag:
        body["tags"] = tag

    status, data = _api(_global_config, "POST", "/short-urls", body=body)

    if status in (200, 201):
        short_url = data.get("shortUrl", f"{_global_config['url']}/{data.get('shortCode', '?')}")
        console.print(f"[green]✓[/green] {short_url}")
    else:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)


@app.command()
def info(
    short_code: str = typer.Argument(..., help="Short code to inspect"),
):
    """Show details for a short URL."""
    _require_api(_global_config)
    status, data = _api(_global_config, "GET", f"/short-urls/{short_code}")

    if status == 404:
        console.print(f"[red]✗[/red] /{short_code} not found")
        raise typer.Exit(1)
    if status != 200:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)

    visits = data.get("visitsSummary", {})
    meta = data.get("meta", {})

    tbl = Table(box=rbox.SIMPLE, show_header=False, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")

    tbl.add_row("Short Code", f"/{data.get('shortCode')}")
    tbl.add_row("Short URL", data.get("shortUrl", ""))
    tbl.add_row("Target URL", data.get("longUrl", ""))
    if data.get("title"):
        tbl.add_row("Title", data["title"])
    if data.get("tags"):
        tbl.add_row("Tags", ", ".join(data["tags"]))
    tbl.add_row("Created", data.get("dateCreated", "?")[:19])
    if meta.get("validSince"):
        tbl.add_row("Valid Since", meta["validSince"][:19])
    if meta.get("validUntil"):
        tbl.add_row("Valid Until", meta["validUntil"][:19])
    if meta.get("maxVisits"):
        tbl.add_row("Max Visits", str(meta["maxVisits"]))
    tbl.add_row("Visits", f"{visits.get('total', 0)} (bots: {visits.get('bots', 0)})")
    tbl.add_row("Crawlable", str(data.get("crawlable", False)))
    tbl.add_row("Fwd Query", str(data.get("forwardQuery", True)))

    console.print(Panel(tbl, title=f"/{short_code}", border_style="cyan"))


@app.command()
def update(
    short_code: str = typer.Argument(..., help="Short code to update"),
    url: str = typer.Option(None, "--url", "-u", help="New target URL"),
    title: str = typer.Option(None, "--title", "-t", help="New title"),
    tag: list[str] = typer.Option(None, "--tag", help="Replace tags (repeatable)"),
):
    """Update an existing short URL."""
    _require_api(_global_config)
    body = {}
    if url:
        body["longUrl"] = url
    if title:
        body["title"] = title
    if tag:
        body["tags"] = tag

    if not body:
        console.print("[red]✗[/red] Nothing to update. Use --url, --title, or --tag.")
        raise typer.Exit(1)

    status, data = _api(_global_config, "PATCH", f"/short-urls/{short_code}", body=body)

    if status == 200:
        console.print(f"[green]✓[/green] /{short_code} updated")
    elif status == 404:
        console.print(f"[red]✗[/red] /{short_code} not found")
        raise typer.Exit(1)
    else:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)


@app.command()
def delete(
    short_code: str = typer.Argument(..., help="Short code to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a short URL."""
    _require_api(_global_config)
    if not yes:
        confirm = typer.confirm(f"  Delete /{short_code}?", default=False)
        if not confirm:
            console.print("  Cancelled.")
            return

    status, data = _api(_global_config, "DELETE", f"/short-urls/{short_code}")

    if status == 204:
        console.print(f"[green]✓[/green] /{short_code} deleted")
    elif status == 404:
        console.print(f"[red]✗[/red] /{short_code} not found")
        raise typer.Exit(1)
    else:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)


# ── Visit Commands ───────────────────────────────────────────────────────


@app.command()
def visits(
    short_code: str = typer.Argument(..., help="Short code to inspect"),
    detail: bool = typer.Option(False, "--detail", "-d", help="Show individual visits"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max visits to show"),
):
    """Show visit statistics for a short URL."""
    _require_api(_global_config)
    status, data = _api(_global_config, "GET", f"/short-urls/{short_code}")
    if status == 404:
        console.print(f"[red]✗[/red] /{short_code} not found")
        raise typer.Exit(1)
    if status != 200:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)

    summary = data.get("visitsSummary", {})
    console.print(f"  [dim]Total:[/dim]     {summary.get('total', 0)}")
    console.print(f"  [dim]Non-bots:[/dim]  {summary.get('nonBots', 0)}")
    console.print(f"  [dim]Bots:[/dim]      {summary.get('bots', 0)}")

    if not detail:
        return

    # Fetch individual visits
    page = 1
    total_pages = 1
    rows = []
    total_items = 0
    while page <= total_pages:
        params = {"page": page, "itemsPerPage": limit}
        vs, vdata = _api(_global_config, "GET", f"/short-urls/{short_code}/visits", params=params)
        if vs != 200:
            break
        vobj = vdata.get("visits", {})
        items = vobj.get("data", [])
        pagination = vobj.get("pagination", {})
        total_pages = pagination.get("pagesCount", 1)
        total_items = pagination.get("totalItems", 0)

        for v in items:
            date = v.get("date", "?")[:19]
            referer = v.get("referer") or "-"
            loc = v.get("visitLocation") or {}
            location = ""
            if loc.get("cityName"):
                location = f"{loc['cityName']}, {loc.get('countryName', '')}"
            elif loc.get("countryName"):
                location = loc["countryName"]
            rows.append((date, referer[:40], location[:25]))
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break
        page += 1

    if rows:
        suffix = f" (showing {len(rows)}/{total_items})" if total_items > len(rows) else ""
        tbl = Table(title=f"Visits for /{short_code}{suffix}", box=rbox.SIMPLE_HEAVY, title_style="bold")
        tbl.add_column("Date", style="dim")
        tbl.add_column("Referrer")
        tbl.add_column("Location", style="cyan")
        for row in rows:
            tbl.add_row(*row)
        console.print(tbl)


# ── Tag Commands ─────────────────────────────────────────────────────────


@tag_app.command("list")
def tag_list():
    """List all tags with stats."""
    _require_api(_global_config)
    status, data = _api(_global_config, "GET", "/tags/stats", params={"itemsPerPage": -1})
    if status != 200:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)

    tags = data.get("tags", {}).get("data", [])
    if not tags:
        console.print("  No tags found.")
        return

    tbl = Table(title=f"{len(tags)} Tags", box=rbox.SIMPLE_HEAVY, title_style="bold")
    tbl.add_column("Tag", style="yellow")
    tbl.add_column("URLs", justify="right")
    tbl.add_column("Visits", justify="right", style="green")
    for t in sorted(tags, key=lambda x: x.get("tag", "")):
        tbl.add_row(
            t.get("tag", "?"),
            str(t.get("shortUrlsCount", 0)),
            str(t.get("visitsSummary", {}).get("total", 0)),
        )
    console.print(tbl)


@tag_app.command("rename")
def tag_rename(
    old_name: str = typer.Argument(..., help="Current tag name"),
    new_name: str = typer.Argument(..., help="New tag name"),
):
    """Rename a tag."""
    _require_api(_global_config)
    body = {"oldName": old_name, "newName": new_name}
    status, data = _api(_global_config, "PUT", "/tags", body=body)

    if status in (200, 204):
        console.print(f"[green]✓[/green] '{old_name}' → '{new_name}'")
    elif status == 404:
        console.print(f"[red]✗[/red] Tag '{old_name}' not found")
        raise typer.Exit(1)
    else:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)


@tag_app.command("delete")
def tag_delete(
    tags: list[str] = typer.Argument(..., help="Tag name(s) to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete one or more tags."""
    _require_api(_global_config)
    if not yes:
        names = ", ".join(tags)
        confirm = typer.confirm(f"  Delete tags [{names}]?", default=False)
        if not confirm:
            console.print("  Cancelled.")
            return

    param_str = "&".join(f"tags[]={urllib.parse.quote(t)}" for t in tags)
    status, data = _api(_global_config, "DELETE", f"/tags?{param_str}")

    if status in (200, 204):
        console.print(f"[green]✓[/green] {len(tags)} tag(s) deleted")
    else:
        console.print(f"[red]✗[/red] {data.get('detail', f'HTTP {status}')}")
        raise typer.Exit(1)


# ── Server Commands ──────────────────────────────────────────────────────


@app.command()
def health():
    """Check server health status."""
    if not _global_config["url"]:
        console.print("[red]✗[/red] Shlink URL is required. Set in .env or pass --server")
        raise typer.Exit(1)

    parsed = urllib.parse.urlparse(_global_config["url"])
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
        health_status = data.get("status", "unknown")
        version = data.get("version", "?")

        if status == 200 and health_status == "pass":
            console.print(f"[green]✓[/green] Healthy")
            console.print(f"  [dim]Version:[/dim] {version}")
        else:
            console.print(f"[red]✗[/red] Unhealthy (status: {health_status})")
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]✗[/red] Unreachable: {e}")
        raise typer.Exit(1)


# ── API Key Commands (docker exec) ────────────────────────────────────────


@key_app.command("list")
def key_list():
    """List all API keys."""
    container = _find_container(_global_config.get("container"))
    rc, output = _docker_exec(container, "shlink", "api-key:list")
    if rc != 0:
        console.print(f"[red]✗[/red] {output}")
        raise typer.Exit(1)
    console.print(output)


@key_app.command("add")
def key_add(
    name: str = typer.Option(None, "--name", "-n", help="Human-readable name"),
    expiration: str = typer.Option(None, "--expiration", "-e", help="Expiration date (YYYY-MM-DD)"),
):
    """Generate a new API key."""
    cmd = ["shlink", "api-key:generate"]
    if name:
        cmd.extend(["--name", name])
    if expiration:
        cmd.extend(["--expiration-date", expiration])

    container = _find_container(_global_config.get("container"))
    rc, output = _docker_exec(container, *cmd)
    if rc != 0:
        console.print(f"[red]✗[/red] {output}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] {output}")


@key_app.command("disable")
def key_disable(
    api_key: str = typer.Argument(..., help="API key to disable"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Disable an API key."""
    if not yes:
        confirm = typer.confirm(f"  Disable API key {api_key[:8]}...?", default=False)
        if not confirm:
            console.print("  Cancelled.")
            return

    container = _find_container(_global_config.get("container"))
    rc, output = _docker_exec(container, "shlink", "api-key:disable", api_key)
    if rc != 0:
        console.print(f"[red]✗[/red] {output}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] API key disabled")


if __name__ == "__main__":
    app()
