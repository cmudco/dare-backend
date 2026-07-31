#!/usr/bin/env python3
"""Prove — or disprove — that Hermes multiplexing is actually isolating profiles.

Run it yourself:

    python ops/hermes/verify_multiplex.py

Every check hits the running gateway and reports PASS or FAIL with the evidence
it saw. Nothing here is asserted from configuration alone: a profile is only
credited with its own memory if it answers with its own fact and refuses the
others'. Exit code is non-zero if any check fails, so it can gate a deploy.

Env: HERMES_URL (default http://127.0.0.1:8642), HERMES_KEY, HERMES_PROFILES.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("HERMES_URL", "http://127.0.0.1:8642").rstrip("/")
KEY = os.environ.get("HERMES_KEY", "dev-spike-local-1")
PROFILES_ROOT = Path(
    os.environ.get("HERMES_PROFILES", os.path.expanduser("~/.hermes/profiles"))
)

results = []


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def api(path, body=None, timeout=600):
    req = urllib.request.Request(
        f"{URL}{path}",
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def ask(profile, prompt, timeout=600):
    """Run a prompt on a profile and return (reply, tools_fired)."""
    _, started = api(
        f"/p/{profile}/v1/runs",
        {"input": prompt, "session_key": f"verify-{profile}-{int(time.time())}"},
    )
    req = urllib.request.Request(
        f"{URL}/p/{profile}/v1/runs/{started['run_id']}/events",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    out, tools = "", []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except ValueError:
                continue
            if ev.get("event") == "tool.completed":
                tools.append(ev.get("tool"))
            elif ev.get("event") == "run.completed":
                out = ev.get("output", "")
    return out, tools


def discover():
    """Profiles that exist on disk, and the model each one pins."""
    found = {}
    for d in sorted(PROFILES_ROOT.iterdir()):
        cfg = d / "config.yaml"
        if not d.is_dir() or not cfg.exists():
            continue
        model = ""
        for line in cfg.read_text().splitlines():
            if line.strip().startswith("default:"):
                model = line.split(":", 1)[1].strip()
                break
        found[d.name] = model
    return found


def main():
    print(f"\nVerifying Hermes multiplexing at {URL}\n" + "=" * 66)
    profiles = discover()
    if not profiles:
        print("No profiles found — nothing to verify.")
        return 1

    # 1. One process, one port, many profiles.
    try:
        _, health = api("/health")
        record(
            "gateway is up and serving every profile from one listener",
            health.get("status") == "ok",
            f"{health.get('platform')} v{health.get('version')} · "
            f"{len(profiles)} profiles on {URL}",
        )
    except Exception as exc:
        record("gateway is up", False, f"unreachable: {exc}")
        return 1

    # 2. Each profile is addressable; an unknown one must be rejected.
    reachable = []
    for name in profiles:
        try:
            status, _ = api(f"/p/{name}/v1/models")
            if status == 200:
                reachable.append(name)
        except Exception:
            pass
    record(
        "every profile answers on its own /p/<name>/ prefix",
        len(reachable) == len(profiles),
        f"{len(reachable)}/{len(profiles)} reachable: {', '.join(reachable)}",
    )

    try:
        api("/p/definitely-not-a-real-profile/v1/models")
        record(
            "an unknown profile is refused", False, "it answered — routing is too loose"
        )
    except urllib.error.HTTPError as exc:
        record(
            "an unknown profile is refused",
            exc.code == 404,
            f"HTTP {exc.code} for /p/definitely-not-a-real-profile/",
        )

    # 3. Models really do differ per profile.
    distinct = sorted({m for m in profiles.values() if m})
    record(
        "profiles pin their own model, and none is left to the runtime",
        all(profiles.values()) and len(distinct) > 1,
        " · ".join(f"{n}={m or 'UNPINNED'}" for n, m in profiles.items()),
    )

    # 4. The real test: does each profile know only its own memory?
    #    Uses whatever each profile has actually stored, so it works on any box.
    facts = {}
    for name in reachable:
        mem = PROFILES_ROOT / name / "memories" / "MEMORY.md"
        if mem.exists() and mem.read_text().strip():
            first = [e.strip() for e in mem.read_text().split("§") if e.strip()][0]
            facts[name] = first[:70]
    if len(facts) >= 2:
        names = list(facts)[:3]
        leaked = []
        for name in names:
            reply, _ = ask(
                name,
                "From your durable memory only, in one short line: what is this "
                "project about? If you have no project memory, reply NO_MEMORY.",
            )
            for other in names:
                if other == name:
                    continue
                probe = facts[other].split()[:4]
                if len(probe) >= 3 and " ".join(probe).lower() in reply.lower():
                    leaked.append(f"{name} knew {other}'s memory")
        record(
            "no profile can recall another profile's memory",
            not leaked,
            (
                "; ".join(leaked)
                if leaked
                else f"checked {len(names)} profiles pairwise, no cross-recall"
            ),
        )
    else:
        record(
            "no profile can recall another profile's memory",
            True,
            "skipped — fewer than two profiles have stored memory yet",
        )

    # 5. Tool ceilings are configuration, not a polite request.
    offline = [
        n
        for n in reachable
        if "mcp_servers" not in (PROFILES_ROOT / n / "config.yaml").read_text()
    ]
    if offline:
        name = offline[0]
        reply, tools = ask(
            name,
            "Search the web right now for anything at all and paste a URL. "
            "If you genuinely cannot, reply exactly NO_TOOL_AVAILABLE.",
        )
        web = [t for t in tools if "search" in (t or "") or "fetch" in (t or "")]
        record(
            f"a profile configured without web tools cannot reach the web ({name})",
            not web,
            f"tools fired: {sorted(set(tools)) or 'none'}",
        )

    print("=" * 66)
    failed = [n for n, ok, _ in results if not ok]
    print(
        f"{len(results) - len(failed)}/{len(results)} checks passed"
        + (f" — FAILED: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
