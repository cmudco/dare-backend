#!/usr/bin/env python3
"""
Basic connectivity test for the CMU-hosted Weaviate instance.

Purpose: verify we can reach the cluster and authenticate once the client's
team whitelists our outbound IP. This intentionally does NOT write/read data —
it just opens a connection, runs is_ready()/meta checks, and lists collections.

Config is read from this backend's .env (same as config/env.py). Relevant keys:

    WEAVIATE_URL=weaviate.hss.cmu.edu
    WEAVIATE_GRPC_URL=grpc-weaviate.hss.cmu.edu
    WEAVIATE_API_KEY=...your key...     # required for auth
    WEAVIATE_HTTP_PORT=443              # optional, defaults assume TLS on 443
    WEAVIATE_GRPC_PORT=443
    WEAVIATE_SECURE=true

Usage (from dare-backend so the venv's weaviate-client is on the path):
    ./venv/bin/python test_weaviate_connection.py
"""

import os
import sys

import environ
import weaviate
from weaviate.classes.init import Auth

# Load this backend's .env the same way config/env.py does.
environ.Env.read_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def _bool(env_value: str, default: bool = True) -> bool:
    if env_value is None:
        return default
    return env_value.strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    http_host = os.environ.get("WEAVIATE_URL", "weaviate.hss.cmu.edu")
    grpc_host = os.environ.get("WEAVIATE_GRPC_URL", "grpc-weaviate.hss.cmu.edu")
    http_port = int(os.environ.get("WEAVIATE_HTTP_PORT", "443"))
    grpc_port = int(os.environ.get("WEAVIATE_GRPC_PORT", "443"))
    secure = _bool(os.environ.get("WEAVIATE_SECURE"), default=True)
    api_key = os.environ.get("WEAVIATE_API_KEY")

    print("=" * 60)
    print("Weaviate connectivity test")
    print("=" * 60)
    print(f"  HTTP : {http_host}:{http_port}  (secure={secure})")
    print(f"  gRPC : {grpc_host}:{grpc_port}  (secure={secure})")
    print(f"  Auth : {'API key' if api_key else 'NONE (anonymous)'}")
    print("-" * 60)

    if not api_key:
        print("WARNING: WEAVIATE_API_KEY not set — connecting anonymously.")
        print("         If the cluster requires auth this will fail with 401.\n")

    client = None
    try:
        client = weaviate.connect_to_custom(
            http_host=http_host,
            http_port=http_port,
            http_secure=secure,
            grpc_host=grpc_host,
            grpc_port=grpc_port,
            grpc_secure=secure,
            auth_credentials=Auth.api_key(api_key) if api_key else None,
            # Skip the startup gRPC health check so a connection still opens even
            # if gRPC pass-through isn't fully configured yet. REST is verified below.
            skip_init_checks=True,
        )
    except Exception as e:
        print(f"✗ CONNECT FAILED: {type(e).__name__}: {e}")
        print("\nMost likely causes if this hangs/times out: IP not yet")
        print("whitelisted, or wrong port/TLS setting.")
        return 1

    # Each check is independent and read-only — no data is ever written.
    rest_ok = False
    try:
        print(f"✓ is_ready(): {client.is_ready()}")
        meta = client.get_meta()
        print(f"✓ Server version: {meta.get('version', 'unknown')}")
        rest_ok = True
    except Exception as e:
        print(f"✗ REST check failed: {type(e).__name__}: {e}")

    # read_collections grant — listing names is read-only.
    try:
        collections = client.collections.list_all(simple=True)
        names = list(collections.keys()) if isinstance(collections, dict) else list(collections)
        print(f"✓ Collections visible ({len(names)}): {names if names else '<none>'}")
    except Exception as e:
        print(f"✗ list collections failed: {type(e).__name__}: {e}")

    # Minimal read-only probe of the granted collection (uses gRPC under v4).
    # limit=1, no vectors, no writes — just confirms data reads work end-to-end.
    target = os.environ.get("WEAVIATE_TEST_COLLECTION", "CivilWarPensionPage")
    grpc_ok = False
    try:
        coll = client.collections.get(target)
        result = coll.query.fetch_objects(limit=1)
        print(f"✓ Read '{target}': fetched {len(result.objects)} object(s) [gRPC working]")
        grpc_ok = True
    except Exception as e:
        print(f"✗ Read '{target}' failed (likely gRPC not proxied yet): {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if rest_ok and grpc_ok:
        print("CONNECTION OK — REST + gRPC both working, reads confirmed")
        rc = 0
    elif rest_ok:
        print("PARTIAL — REST/auth OK; gRPC reads not available yet")
        rc = 2
    else:
        print("CONNECTION FAILED")
        rc = 1
    print("=" * 60)

    client.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
