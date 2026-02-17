#!/usr/bin/env python3
"""
Push all variables from .env to aldar-middleware Web App Application Settings.

Usage:
    python scripts/sync-env-to-webapp.py
    python scripts/sync-env-to-webapp.py --env-file .env
    python scripts/sync-env-to-webapp.py --dry-run
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse .env file; skip comments and empty lines; support values with '='."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # First '=' is the separator (value can contain '=')
        idx = line.index("=") if "=" in line else -1
        if idx < 0:
            continue
        key = line[:idx].strip()
        value = line[idx + 1 :].strip()
        if key.startswith("#"):
            continue
        # Remove surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Push .env to aldar-middleware Web App settings")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Print settings without pushing")
    parser.add_argument("--webapp", default="aldar-middleware", help="Web app name")
    parser.add_argument("--resource-group", default="Aldar-POC", help="Resource group name")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"Error: {env_path} not found")
        sys.exit(1)

    settings = parse_env_file(env_path)
    if not settings:
        print("No variables found in .env")
        sys.exit(0)

    print(f"Loaded {len(settings)} variables from {env_path}")

    if args.dry_run:
        for k, v in sorted(settings.items()):
            display = v[:60] + "..." if len(v) > 60 else v
            print(f"  {k}={display}")
        print("\nDry run — no changes made. Run without --dry-run to push.")
        return

    # Build key=value list for Azure CLI (value as-is; no shell so no escaping needed)
    settings_list = [f"{k}={v}" for k, v in settings.items()]
    cmd = [
        "az",
        "webapp",
        "config",
        "appsettings",
        "set",
        "--name",
        args.webapp,
        "--resource-group",
        args.resource_group,
        "--settings",
        *settings_list,
        "--output",
        "none",
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"Successfully pushed {len(settings)} settings to {args.webapp}")
    except subprocess.CalledProcessError as e:
        print(f"Error: az command failed with exit code {e.returncode}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: Azure CLI (az) not found. Install it and run 'az login' first.")
        sys.exit(1)


if __name__ == "__main__":
    main()
