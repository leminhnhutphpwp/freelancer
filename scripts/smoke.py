#!/usr/bin/env python3
"""Read-only smoke test against the Freelancer.com API.

Verifies (without writing anything):
  1. The PAT authenticates (GET /users/0.1/self/)
  2. Portfolio listing works
  3. Professional profiles are readable
  4. A project search returns results

Safe to run any time — performs no bids, no messages, no writes.
"""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.freelancer.client import FreelancerClient, FreelancerError


async def main() -> int:
    settings = get_settings()
    if not settings.freelancer_access_token:
        print("✗ FREELANCER_ACCESS_TOKEN is not set (see .env)")
        return 1

    client = FreelancerClient(
        access_token=settings.freelancer_access_token,
        base_url=settings.freelancer_base_url,
        timeout=settings.freelancer_timeout_seconds,
    )
    failures = 0

    async def step(name: str, coro):
        nonlocal failures
        try:
            result = await coro
            print(f"✓ {name}")
            return result
        except FreelancerError as exc:
            failures += 1
            print(f"✗ {name}: {exc} (kind={exc.kind}, code={exc.error_code})")
            return None

    try:
        me = await step("self (PAT auth)", client.get_self(display_info=True))
        if me:
            uid = me.get("id") or me.get("user_id")
            print(f"    user_id={uid} username={me.get('username')} display={me.get('display_name')}")

        if me:
            uid = me.get("id") or me.get("user_id")
            if uid:
                portfolios = await step("portfolio list", client.list_portfolios(int(uid), limit=5))
                if portfolios is not None:
                    print(f"    {len(portfolios)} portfolio item(s)")

                profiles = await step("profiles list", client.list_profiles(user_id=int(uid)))
                if profiles is not None:
                    print(f"    {len(profiles)} professional profile(s)")

        search = await step(
            "project search (query='python', limit=3)",
            client.search_projects({"query": "python", "limit": 3, "full_description": True}),
        )
        if search is not None:
            projects = search.get("projects") or []
            print(f"    {len(projects)} project(s) returned")
            for p in projects[:3]:
                print(f"      - #{p.get('id')} {p.get('title')}")

    finally:
        await client.aclose()

    print()
    if failures:
        print(f"Smoke test finished with {failures} failure(s).")
        return 1
    print("Smoke test passed — token is valid and read endpoints work.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))