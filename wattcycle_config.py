import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "WattCycleBatteryUtility"
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "battery_address": "",
    "battery_name": "",
    "frame_head": 0x1E,
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
    "alpaca_port": 11111,
}

def load_config():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg

def save_config(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged
