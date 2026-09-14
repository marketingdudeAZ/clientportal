#!/usr/bin/env python3
"""Mint a signed Workspace preview link for an internal demo.

The token is an HMAC-SHA256 signature over (email, expiry), signed with
WORKSPACE_LINK_SECRET. The Workspace page sends it on every API call as the
`X-Workspace-Link` header. The server honors it only when
WORKSPACE_SIGNED_LINKS_ENABLED=true, only for RPM internal emails, and for at
most 7 days. See webhook-server/skills/workspace_links.py.

Env required:
    WORKSPACE_LINK_SECRET   the same value the server has

Usage:
    python3 scripts/workspace_link.py dana@rpmliving.com
    python3 scripts/workspace_link.py dana@rpmliving.com --days 3

Prints the token on stdout and its expiry on stderr. The token is a credential
for that email until it expires: share it only with that person.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webhook-server"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a signed Workspace preview link.")
    parser.add_argument("email", help="RPM internal email the link is for")
    parser.add_argument("--days", type=int, default=7, help="lifetime in days, 1-7 (default 7)")
    args = parser.parse_args(argv)

    if not os.environ.get("WORKSPACE_LINK_SECRET"):
        print("error: WORKSPACE_LINK_SECRET is not set", file=sys.stderr)
        return 2

    from skills import workspace_links  # noqa: E402

    if not 1 <= args.days <= workspace_links.MAX_DAYS:
        print(f"error: --days must be between 1 and {workspace_links.MAX_DAYS}", file=sys.stderr)
        return 2
    now = time.time()
    try:
        token = workspace_links.mint(args.email, args.days, now=now)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    expires = datetime.fromtimestamp(now + args.days * 86400, tz=timezone.utc)
    print(token)
    print(f"expires {expires.strftime('%Y-%m-%d %H:%M UTC')}; send as header "
          f"{workspace_links.HEADER}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
