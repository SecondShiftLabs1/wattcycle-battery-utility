import asyncio
import json
import os
from pathlib import Path

from bleak import BleakScanner
from wattcycle_ble import WattcycleClient

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "WattCycleBatteryUtility"
CONFIG_PATH = APP_DIR / "config.json"
DEFAULTS = {
  "battery_address": "",
  "battery_name": "",
  "frame_head": 30,
  "refresh_seconds": 5,
  "log_interval_seconds": 5,
  "data_directory": "~/Documents/WattCycle",
  "session_start_amps": 0.5,
  "session_start_seconds": 120,
  "session_end_seconds": 300,
  "critical_soc": 10,
  "emergency_soc": 5,
  "critical_runtime_minutes": 45,
  "stale_seconds": 30,
  "allow_mos_control": False,
  "enable_ascom_alpaca": True,
  "alpaca_port": 11111
}

def load_config():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    cfg=dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    save_config(cfg)
    return cfg

def save_config(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp=CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg,indent=2),encoding="utf-8")
    tmp.replace(CONFIG_PATH)

def _device_row(device):
    return {
        "name": getattr(device, "name", None) or "",
        "address": getattr(device, "address", None) or "",
    }

async def _upstream_scan(timeout):
    # WattcycleClient.scan() normally accepts a timeout, but an outer asyncio
    # timeout prevents a backend/library stall from leaving first-run setup
    # stuck forever.
    task = asyncio.create_task(WattcycleClient.scan(timeout=timeout))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout + 2.0)
    except asyncio.TimeoutError:
        task.cancel()
        raise

async def _bleak_scan(timeout):
    # Direct Windows/Bleak fallback. WattCycle advertisements do not always
    # expose enough metadata for strict pre-filtering, so return named BLE
    # candidates and let the user choose the battery.
    devices = await asyncio.wait_for(
        BleakScanner.discover(timeout=timeout),
        timeout=timeout + 2.0,
    )
    return [_device_row(d) for d in devices if getattr(d, "address", None)]

async def scan_devices(timeout=10.0, progress=None):
    diagnostics=[]
    try:
        if progress:
            progress("Scanning with WattCycle discovery...")
        devices=await _upstream_scan(timeout)
        rows=[_device_row(d) for d in devices]
        diagnostics.append(f"WattCycle discovery returned {len(rows)} device(s).")
        return rows, diagnostics
    except asyncio.TimeoutError:
        diagnostics.append("WattCycle discovery exceeded its hard timeout.")
    except Exception as e:
        diagnostics.append(f"WattCycle discovery failed: {type(e).__name__}: {e}")

    try:
        if progress:
            progress("WattCycle scan did not return. Trying direct Windows BLE scan...")
        rows=await _bleak_scan(timeout)
        diagnostics.append(f"Direct BLE discovery returned {len(rows)} device(s).")
        return rows, diagnostics
    except asyncio.TimeoutError:
        diagnostics.append("Direct Windows BLE discovery exceeded its hard timeout.")
    except Exception as e:
        diagnostics.append(f"Direct BLE discovery failed: {type(e).__name__}: {e}")

    return [], diagnostics
