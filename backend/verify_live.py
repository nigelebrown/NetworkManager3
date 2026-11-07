"""Read-only probe of a real Aruba Instant Virtual Controller.

Usage (PowerShell, from the project root, venv active):
    python -m backend.verify_live --host 10.190.2.71 --user admin
    (password is prompted; or set ARUBA_PASSWORD in the environment)

Optional:
    --insecure         skip TLS verification (lab only; warns)
    --probe            also try candidate RF/ARM commands and report which this firmware accepts
    --save probe.json  write raw responses to a file (review before sharing: contains MAC/IPs)

It prints a PASS/FAIL checklist and what the parsers extracted, so we can confirm the
exact response format on your AP-505 before relying on it.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .aruba import configuration, parsers
from .aruba.client import ArubaClient, ArubaTarget, READ_ONLY_COMMANDS
from .aruba.errors import ArubaError

PROBE_COMMANDS = [
    "show ap arm rf-summary", "show arm rf-summary", "show ap bss-table",
    "show ap monitor ap-list", "show radio-stats", "show ap debug radio-stats",
    "show ap debug client-table", "show ap debug system-status", "show summary",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=4343)
    ap.add_argument("--user", required=True)
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--save")
    args = ap.parse_args()

    password = os.environ.get("ARUBA_PASSWORD") or getpass.getpass("AP password: ")
    raw: dict = {}
    ok = True

    def step(label: str, fn):
        nonlocal ok
        try:
            result = fn()
            print(f"[PASS] {label}")
            return result
        except ArubaError as exc:
            ok = False
            print(f"[FAIL] {label}: {exc}")
            return None

    c = ArubaClient(
        ArubaTarget(args.host, args.port, args.user, password),
        timeout=10, verify=not args.insecure,
    )
    try:
        step("Login (POST /rest/login)", c.login)
        if not c.logged_in:
            return 1

        r = step("show aps", lambda: c.show("show aps"))
        if r is not None:
            raw["show aps"] = r
            aps = parsers.normalize_aps(parsers.extract_rows(r, "show aps"))
            print(f"       parsed {len(aps)} AP(s)")
            for a in aps:
                print(f"       - {a['name']} {a['ip']} {a['mac']} model={a['model']} "
                      f"clients={a['client_count']} uptime_s={a['uptime_seconds']}")
            if not aps:
                print("       ! no AP rows recognized - send me the saved JSON so I can fix the parser")

        r = step("show clients", lambda: c.show("show clients"))
        if r is not None:
            raw["show clients"] = r
            cl = parsers.normalize_clients(parsers.extract_rows(r, "show clients"))
            print(f"       parsed {len(cl)} client(s)")

        r = step("show version", lambda: c.show("show version"))
        if r is not None:
            raw["show version"] = r
            print(f"       firmware parsed: {parsers.parse_version(r)}")

        r = step("show running-config (SSIDs / VLANs)", lambda: c.show("show running-config"))
        if r is not None:
            text = parsers.output_text(r) or ""
            raw["show running-config"] = {"Command output": configuration.redact(text)}
            cfg = configuration.parse_running_config(text)
            print(f"       parsed {len(cfg['ssids'])} SSID profile(s), VLANs used: {cfg['vlans']}")
            for sdef in cfg["ssids"]:
                print(f"       - {sdef['profile']}: essid={sdef['essid']} vlan={sdef['vlan']} opmode={sdef['opmode']} enabled={sdef['enabled']}")
            if not cfg["ssids"]:
                print("       ! no SSID profiles recognized - send me the saved JSON so I can fix the parser")

        if args.probe:
            print("\nProbing optional commands (read-only):")
            for cmd in PROBE_COMMANDS:
                assert cmd in READ_ONLY_COMMANDS or cmd.startswith(READ_ONLY_COMMANDS)
                try:
                    raw[cmd] = c.show(cmd)
                    print(f"  [supported] {cmd}")
                except ArubaError as exc:
                    print(f"  [not available] {cmd}: {exc}")
    finally:
        c.close()

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        print(f"\nRaw responses saved to {args.save} (contains MACs/IPs - review before sharing).")

    print("\nRESULT:", "all required checks passed" if ok else "some checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
