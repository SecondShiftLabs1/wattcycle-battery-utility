import argparse
import asyncio
import json
import sys
from pathlib import Path

from bleak import BleakScanner

async def scan(timeout):
    diagnostics = ["Isolated Bleak scanner process started."]
    devices = await BleakScanner.discover(timeout=timeout)
    rows = []
    seen = set()
    for d in devices:
        address = getattr(d, "address", None) or ""
        if not address or address in seen:
            continue
        seen.add(address)
        rows.append({
            "name": getattr(d, "name", None) or "",
            "address": address,
        })
    diagnostics.append(f"Bleak returned {len(rows)} unique BLE device(s).")
    return rows, diagnostics

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = {"devices": [], "diagnostics": []}
    try:
        devices, diagnostics = asyncio.run(scan(args.timeout))
        payload["devices"] = devices
        payload["diagnostics"] = diagnostics
        code = 0
    except Exception as e:
        payload["diagnostics"] = [f"{type(e).__name__}: {e}"]
        code = 1
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return code

if __name__ == "__main__":
    sys.exit(main())
