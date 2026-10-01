import asyncio, json, os
from pathlib import Path
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
        try: cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception: pass
    save_config(cfg)
    return cfg

def save_config(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp=CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg,indent=2),encoding="utf-8")
    tmp.replace(CONFIG_PATH)

async def scan_devices(timeout=10.0):
    devices=await WattcycleClient.scan(timeout=timeout)
    out=[]
    for d in devices:
        name=getattr(d,"name",None) or ""
        address=getattr(d,"address",None) or ""
        # Upstream scan is already intended for WattCycle/XDZN devices; retain
        # all returned candidates and show identifying info to the user.
        out.append({"name":name,"address":address})
    return out