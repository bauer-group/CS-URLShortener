#!/usr/bin/env python3
# =============================================================================
# generate-env.py — Generate .env from .env.example
# =============================================================================
# Reads .env.example, auto-generates passwords and API keys, and writes a
# ready-to-use .env file.
#
# Usage: python scripts/generate-env.py
#
# Requirements: Python 3.6+ (no external dependencies)
# =============================================================================

import secrets
import sys
import uuid
from pathlib import Path


# ── Secret Generation ───────────────────────────────────────────────────────


def generate_password(length: int = 32) -> str:
    """Generate a cryptographically secure hex password."""
    return secrets.token_hex(length // 2)


def generate_api_key() -> str:
    """Generate a UUID-based API key."""
    return str(uuid.uuid4())


# ── .env Generation ─────────────────────────────────────────────────────────

# Variables that are auto-generated (key → generator function)
AUTO_GENERATE = {
    "POSTGRES_PASSWORD": lambda: generate_password(32),
    "SHLINK_API_KEY": generate_api_key,
}


def parse_env_example(path: Path) -> list[tuple[str, str]]:
    """
    Parse .env.example and return list of (raw_line, var_name_or_none) tuples.
    var_name is set only for empty variables that need a value.
    """
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()

        # Comment or empty line — keep as-is
        if not stripped or stripped.startswith("#"):
            lines.append((line, None))
            continue

        # Variable assignment
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip()
            if value.strip() == "":
                # Empty value — candidate for generation
                lines.append((line, key))
            else:
                # Has a default value — keep as-is
                lines.append((line, None))
        else:
            lines.append((line, None))

    return lines


def generate_env(example_path: Path, output_path: Path) -> None:
    """Generate .env file from .env.example template."""

    if output_path.exists():
        print(f"⚠ {output_path.name} already exists.")
        answer = input("  Overwrite? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)
        print()

    lines = parse_env_example(example_path)
    generated_values = {}
    output_lines = []

    print("╔═══════════════════════════════════════════════════════╗")
    print("║         URLShortener — Environment Generator         ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()

    # Phase 1: Auto-generate secrets
    print("── Auto-generated Secrets ─────────────────────────────")
    for key, generator in AUTO_GENERATE.items():
        value = generator()
        generated_values[key] = value
        # Show truncated for security
        display = value[:8] + "..." if len(value) > 11 else value
        print(f"  ✓ {key}={display}")

    # Phase 2: Build output
    for raw_line, var_name in lines:
        if var_name and var_name in generated_values:
            output_lines.append(f"{var_name}={generated_values[var_name]}")
        else:
            output_lines.append(raw_line)

    # Phase 3: Write .env with restricted permissions
    output_path.write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
    )
    try:
        output_path.chmod(0o600)
    except OSError:
        pass  # Windows does not support Unix permissions

    print()
    print("── Summary ────────────────────────────────────────────")
    print(f"  ✓ Written to: {output_path}")
    print(f"  ✓ {len(generated_values)} secrets generated")
    print()
    print("  Next steps:")
    print("    1. Review .env and adjust defaults if needed")
    print("    2. docker compose -f docker-compose.development.yml up -d")
    print()


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    # Resolve paths relative to project root (script is in scripts/)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    example_path = project_root / ".env.example"
    output_path = project_root / ".env"

    if not example_path.exists():
        print(f"✗ {example_path} not found.", file=sys.stderr)
        sys.exit(1)

    generate_env(example_path, output_path)


if __name__ == "__main__":
    main()
