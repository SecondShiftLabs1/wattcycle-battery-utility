import argparse
import asyncio
import json
import sys
from pathlib import Path

from bleak import BleakScanner


async def scan(timeout):
    diagnostics = ["Standalone Bleak advertisement scanner started."]
    seen = {}
    advertisement_count = 0

    def detected(device, adv):
        nonlocal advertisement_count
        advertisement_count += 1
        address = getattr(device, "address", None) or ""
        if not address:
            return

        row = seen.setdefault(address, {
            "name": "",
            "address": address,
            "rssi": None,
            "manufacturer_data": {},
            "service_uuids": [],
            "likely_wattcycle": False,
        })

        # Keep useful information seen in ANY packet during the scan.
        name = getattr(adv, "local_name", None) or getattr(device, "name", None) or ""
        if name:
            row["name"] = name

        rssi = getattr(adv, "rssi", None)
        if rssi is None:
            rssi = getattr(device, "rssi", None)
        if rssi is not None:
            row["rssi"] = rssi

        for key, value in (getattr(adv, "manufacturer_data", None) or {}).items():
            try:
                row["manufacturer_data"][str(key)] = bytes(value).hex()
            except Exception:
                row["manufacturer_data"][str(key)] = str(value)

        for uuid in (getattr(adv, "service_uuids", None) or []):
            if uuid not in row["service_uuids"]:
                row["service_uuids"].append(uuid)

        lname = row["name"].lower()
        row["likely_wattcycle"] = (
            lname.startswith("wt")
            or "wattcycle" in lname
            or "xdzn" in lname
            or "54976" in row["manufacturer_data"]
        )

    scanner = BleakScanner(detection_callback=detected)
    await scanner.start()
    try:
        await asyncio.sleep(timeout)
    finally:
        await scanner.stop()

    rows = list(seen.values())
    rows.sort(key=lambda d: (
        0 if d.get("likely_wattcycle") else 1,
        -(d.get("rssi") if isinstance(d.get("rssi"), (int, float)) else -999),
        d.get("name") or "",
    ))
    diagnostics.append(
        f"Captured {advertisement_count} advertisement(s) from {len(rows)} unique BLE device(s)."
    )
    diagnostics.append(
        f"Flagged {sum(1 for d in rows if d.get('likely_wattcycle'))} likely WattCycle device(s)."
    )
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
