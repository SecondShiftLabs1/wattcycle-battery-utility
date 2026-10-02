import asyncio
import threading
import multiprocessing
import queue
import tkinter as tk
import sqlite3
import os
import time
import subprocess
import json
import sys
import tempfile
from collections import deque
from datetime import datetime
from tkinter import ttk, messagebox, simpledialog
from datetime import timedelta
from wattcycle_ble import WattcycleClient
from wattcycle_config import load_config, save_config

def ble_scan_process(result_queue, timeout=10.0):
    """Run direct Bleak discovery in a killable child process."""
    try:
        from bleak import BleakScanner

        async def run():
            rows = {}
            def detected(device, advertisement_data):
                address = getattr(device, "address", None) or ""
                if not address:
                    return
                name = getattr(advertisement_data, "local_name", None) or getattr(device, "name", None) or ""
                rows[address] = {"name": name, "address": address}
            scanner = BleakScanner(detection_callback=detected)
            await scanner.start()
            try:
                await asyncio.sleep(timeout)
            finally:
                await scanner.stop()
            return list(rows.values())

        result_queue.put(("results", asyncio.run(run())))
    except BaseException as e:
        try:
            result_queue.put(("error", f"{type(e).__name__}: {e}"))
        except Exception:
            pass


CONFIG = load_config()
ADDRESS = CONFIG.get("battery_address", "")
FRAME_HEAD = int(CONFIG.get("frame_head", 0x1E))
REFRESH_SECONDS = int(CONFIG.get("refresh_seconds", 5))
LOG_INTERVAL = int(CONFIG.get("log_interval_seconds", 5))
SESSION_START_AMPS = float(CONFIG.get("session_start_amps", 0.5))
SESSION_START_SECONDS = int(CONFIG.get("session_start_seconds", 120))
SESSION_END_SECONDS = int(CONFIG.get("session_end_seconds", 300))
DATA_DIR = os.path.expandvars(os.path.expanduser(CONFIG.get("data_directory", os.path.join("~", "Documents", "WattCycle"))))
DB_PATH = os.path.join(DATA_DIR, "WattCycle_Data.db")
NINA_STATE_PATH = os.path.join(DATA_DIR, "NINA_Battery_State.json")
NINA_COMMAND_PATH = os.path.join(DATA_DIR, "NINA_Battery_Command.json")
NINA_CRITICAL_SOC = int(CONFIG.get("critical_soc", 10))
NINA_EMERGENCY_SOC = int(CONFIG.get("emergency_soc", 5))
NINA_CRITICAL_RUNTIME_MIN = int(CONFIG.get("critical_runtime_minutes", 45))
MOS_CONTROL_ENABLED = bool(CONFIG.get("allow_mos_control", False))

# Exact commands captured from the official WattCycle Android app.
CHARGE_OFF = bytes.fromhex(
    "1E 00 01 06 00 82 00 01 00 1E B2 0D"
)
CHARGE_ON = bytes.fromhex(
    "1E 00 01 06 00 82 00 01 01 DE 73 0D"
)
DISCHARGE_OFF = bytes.fromhex(
    "1E 00 01 06 00 83 00 01 00 E2 B3 0D"
)
DISCHARGE_ON = bytes.fromhex(
    "1E 00 01 06 00 83 00 01 01 22 72 0D"
)

CHARGE_BIT = 0x02
DISCHARGE_BIT = 0x04


class WattCycleMonitor:

    def __init__(self, root):
        self.root = root
        self.root.title("WattCycle Battery Utility - Public Alpha")
        self.root.geometry("800x780")
        self.root.minsize(720, 620)

        self.running = True
        self.data = None
        self.warning = None
        self.product = None
        self.error = None

        self.charge_on = None
        self.discharge_on = None
        self.control_busy = False
        self.pending_command = None
        self.last_external_command_id = None

        os.makedirs(DATA_DIR, exist_ok=True)
        self.samples = deque(maxlen=720)
        self.last_log_time = 0

        # Automatic discharge-session tracking. A session begins after sustained
        # discharge and ends after the load has remained low for five minutes.
        self.session_active = False
        self.session_candidate_since = None
        self.candidate_ah = 0.0
        self.candidate_wh = 0.0
        self.candidate_peak_amps = 0.0
        self.candidate_peak_watts = 0.0
        self.candidate_last_time = None
        self.candidate_last_current = None
        self.candidate_last_voltage = None
        self.candidate_start_soc = None
        self.session_idle_since = None
        self.session_start_time = None
        self.session_start_soc = None
        self.session_ah = 0.0
        self.session_wh = 0.0
        self.session_peak_amps = 0.0
        self.session_peak_watts = 0.0
        self.session_last_sample_time = None
        self.session_last_current = None
        self.session_last_voltage = None
        self.last_session_summary = None

        # Alert state. Threshold alerts fire once per discharge crossing and
        # automatically re-arm after SOC rises above the threshold again.
        self.alerted_soc = set()
        self.bms_warning_alerted = False
        self.disconnect_started = None
        self.disconnect_alert_pending = False
        self.last_alert_text = "No active alerts"

        # Automation integration state. Publishes a machine-readable battery
        # safety state without ever switching a MOS automatically.
        self.nina_protection_armed = True
        self.nina_simulated_state = None
        self.nina_simulated_until = 0
        self.nina_state = "NORMAL"
        self.nina_reason = "Battery telemetry starting"
        self.nina_runtime_minutes = None

        if not self.ensure_battery_configured():
            self.running = False
            self.root.after(50, self.root.destroy)
            return

        self.init_database()

        self.build_gui()

        self.thread = threading.Thread(
            target=self.ble_thread,
            daemon=True
        )
        self.thread.start()

        self.update_gui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def ensure_battery_configured(self):
        global ADDRESS, CONFIG, MOS_CONTROL_ENABLED
        if ADDRESS:
            return True

        win = tk.Toplevel(self.root)
        win.title("WattCycle Battery Utility - First Run")
        win.geometry("660x470")
        win.transient(self.root)
        win.grab_set()
        result = {"ok": False}
        found = []
        scan_state = {"proc": None, "output_path": None, "deadline": 0.0}

        ttk.Label(win, text="Find your WattCycle battery", font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=18, pady=(18,4))
        ttk.Label(win, text="Scan nearby Bluetooth LE devices, select the WattCycle battery, then click Use Selected. The scan runs in an isolated process so it can be stopped if Windows Bluetooth stalls.", wraplength=615).pack(anchor="w", padx=18, pady=(0,12))
        status = tk.StringVar(value="Ready to scan. Scanner build: standalone-process v5")
        listbox = tk.Listbox(win, height=11)
        listbox.pack(fill="both", expand=True, padx=18, pady=6)
        ttk.Label(win, textvariable=status, wraplength=615).pack(anchor="w", padx=18, pady=4)

        def finish_scan(message):
            proc = scan_state.get("proc")
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            output_path = scan_state.get("output_path")
            scan_state["proc"] = None
            scan_state["output_path"] = None
            if output_path:
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            status.set(message)
            scan_btn.config(state="normal")

        def poll_scan():
            proc = scan_state.get("proc")
            output_path = scan_state.get("output_path")
            if proc is None:
                return

            if time.monotonic() >= scan_state["deadline"] and proc.poll() is None:
                finish_scan("Standalone BLE scan exceeded 15 seconds and was forcibly stopped.")
                return

            code = proc.poll()
            if code is None:
                self.root.after(100, poll_scan)
                return

            try:
                if not output_path or not os.path.exists(output_path):
                    finish_scan(f"Standalone scanner exited with code {code} but produced no diagnostic file.")
                    return
                with open(output_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                devices = payload.get("devices", [])
                diagnostics = payload.get("diagnostics", [])
                found.clear()
                found.extend(devices)
                listbox.delete(0, "end")
                for d in found:
                    likely = "LIKELY WATTCYCLE  |  " if d.get("likely_wattcycle") else ""
                    name = d.get("name") or "Unknown BLE device"
                    rssi = d.get("rssi")
                    signal = f"  |  RSSI {rssi}" if rssi is not None else ""
                    listbox.insert("end", f"{likely}{name}  |  {d.get('address','')}{signal}")
                detail = " | ".join(str(x) for x in diagnostics[-3:])
                if found:
                    finish_scan(f"Standalone scanner found {len(found)} BLE device(s). {detail}")
                else:
                    finish_scan(f"Standalone scanner found no BLE devices. {detail}")
            except Exception as e:
                finish_scan(f"Could not read standalone scan result: {type(e).__name__}: {e}")

        def do_scan():
            active_proc = scan_state.get("proc")
            if active_proc is not None and active_proc.poll() is None:
                return
            scan_btn.config(state="disabled")
            listbox.delete(0, "end")
            found.clear()

            scanner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wattcycle_ble_scan.py")
            if not os.path.exists(scanner_path):
                finish_scan("Scanner helper wattcycle_ble_scan.py is missing from the application folder.")
                return

            fd, output_path = tempfile.mkstemp(prefix="wattcycle_ble_", suffix=".json")
            os.close(fd)
            try:
                os.remove(output_path)
            except OSError:
                pass

            scan_state["output_path"] = output_path
            scan_state["deadline"] = time.monotonic() + 15.0
            status.set("STANDALONE SCANNER v4 — launching independent Python BLE scan...")
            try:
                proc = subprocess.Popen(
                    [sys.executable, scanner_path, "--timeout", "10", "--output", output_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                scan_state["proc"] = proc
                status.set("STANDALONE SCANNER v4 — scanning Bluetooth LE for 10 seconds...")
                self.root.after(100, poll_scan)
            except Exception as e:
                finish_scan(f"Could not launch standalone scanner: {type(e).__name__}: {e}")

        def use_selected():
            global ADDRESS, CONFIG
            sel = listbox.curselection()
            if not sel:
                return messagebox.showinfo("Select battery", "Select a discovered battery first.", parent=win)
            d = found[sel[0]]
            CONFIG["battery_address"] = d["address"]
            CONFIG["battery_name"] = d.get("name", "")
            save_config(CONFIG)
            ADDRESS = d["address"]
            result["ok"] = True
            win.destroy()

        def manual():
            global ADDRESS, CONFIG
            a = simpledialog.askstring("Battery address", "Enter the BLE address for the battery:", parent=win)
            if a:
                CONFIG["battery_address"] = a.strip()
                save_config(CONFIG)
                ADDRESS = a.strip()
                result["ok"] = True
                win.destroy()

        def close_setup():
            proc = scan_state.get("proc")
            if proc is not None and proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)
            win.destroy()

        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=18, pady=(6,18))
        scan_btn = ttk.Button(buttons, text="Scan", command=do_scan)
        scan_btn.pack(side="left")
        ttk.Button(buttons, text="Use Selected", command=use_selected).pack(side="left", padx=6)
        ttk.Button(buttons, text="Use Known Address", command=manual).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=close_setup).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close_setup)
        self.root.wait_window(win)
        return result["ok"]

    def init_database(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS battery_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    soc INTEGER, remaining_ah REAL, voltage REAL,
                    current REAL, power REAL,
                    cell1 REAL, cell2 REAL, cell3 REAL, cell4 REAL,
                    cell_delta_mv REAL, mos_temp REAL, pcb_temp REAL,
                    battery_temp REAL, charge_mos INTEGER,
                    discharge_mos INTEGER, battery_mode INTEGER,
                    warning1 INTEGER, warning2 INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_battery_timestamp ON battery_log(timestamp)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    start_soc INTEGER,
                    end_soc INTEGER,
                    amp_hours REAL,
                    watt_hours REAL,
                    avg_watts REAL,
                    peak_amps REAL,
                    peak_watts REAL,
                    duration_seconds REAL
                )
            """)

    def add_sample(self, d):
        self.samples.append((time.time(), d.current, d.module_voltage))

    def rolling_average(self, seconds):
        cutoff = time.time() - seconds
        values = [(c, v) for t, c, v in self.samples if t >= cutoff]
        if not values:
            return None, None
        return (
            sum(c for c, _ in values) / len(values),
            sum(v * c for c, v in values) / len(values)
        )

    def log_sample(self, d, w):
        now = time.time()
        if now - self.last_log_time < LOG_INTERVAL:
            return
        self.last_log_time = now
        cells = list(d.cell_voltages)
        while len(cells) < 4:
            cells.append(None)
        delta = ((max(d.cell_voltages) - min(d.cell_voltages)) * 1000
                 if d.cell_voltages else None)
        battery_temp = d.cell_temperatures[0] if d.cell_temperatures else None
        charge_mos = int(bool(w.status_register_3 & CHARGE_BIT)) if w else None
        discharge_mos = int(bool(w.status_register_3 & DISCHARGE_BIT)) if w else None
        battery_mode = w.battery_mode if w else None
        warning1 = w.warning_register_1 if w else None
        warning2 = w.warning_register_2 if w else None
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO battery_log (
                    timestamp, soc, remaining_ah, voltage, current, power,
                    cell1, cell2, cell3, cell4, cell_delta_mv, mos_temp,
                    pcb_temp, battery_temp, charge_mos, discharge_mos,
                    battery_mode, warning1, warning2
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.now().isoformat(timespec="seconds"), d.soc,
                d.remaining_capacity, d.module_voltage, d.current,
                d.module_voltage * d.current, cells[0], cells[1], cells[2],
                cells[3], delta, d.mos_temperature, d.pcb_temperature,
                battery_temp, charge_mos, discharge_mos, battery_mode,
                warning1, warning2
            ))

    def reset_session_candidate(self):
        self.session_candidate_since = None
        self.candidate_ah = 0.0
        self.candidate_wh = 0.0
        self.candidate_peak_amps = 0.0
        self.candidate_peak_watts = 0.0
        self.candidate_last_time = None
        self.candidate_last_current = None
        self.candidate_last_voltage = None
        self.candidate_start_soc = None

    def update_session(self, d):
        now = time.time()
        discharge_amps = max(0.0, -d.current)
        watts = discharge_amps * d.module_voltage
        under_load = discharge_amps >= SESSION_START_AMPS

        # During the two-minute qualification period, accumulate the energy too.
        # If the load qualifies, those first two minutes become part of the session
        # instead of being shown in duration but missing from Ah/Wh totals.
        if not self.session_active:
            if under_load:
                if self.session_candidate_since is None:
                    self.session_candidate_since = now
                    self.candidate_start_soc = d.soc
                    self.candidate_peak_amps = discharge_amps
                    self.candidate_peak_watts = watts
                    self.candidate_last_time = now
                    self.candidate_last_current = discharge_amps
                    self.candidate_last_voltage = d.module_voltage
                else:
                    dt = max(0.0, min(now - self.candidate_last_time, 30.0))
                    avg_amps = (self.candidate_last_current + discharge_amps) / 2.0
                    avg_voltage = (self.candidate_last_voltage + d.module_voltage) / 2.0
                    self.candidate_ah += avg_amps * dt / 3600.0
                    self.candidate_wh += avg_amps * avg_voltage * dt / 3600.0
                    self.candidate_peak_amps = max(self.candidate_peak_amps, discharge_amps)
                    self.candidate_peak_watts = max(self.candidate_peak_watts, watts)
                    self.candidate_last_time = now
                    self.candidate_last_current = discharge_amps
                    self.candidate_last_voltage = d.module_voltage

                    if now - self.session_candidate_since >= SESSION_START_SECONDS:
                        self.session_active = True
                        self.session_start_time = self.session_candidate_since
                        self.session_start_soc = self.candidate_start_soc
                        self.session_ah = self.candidate_ah
                        self.session_wh = self.candidate_wh
                        self.session_peak_amps = self.candidate_peak_amps
                        self.session_peak_watts = self.candidate_peak_watts
                        self.session_last_sample_time = now
                        self.session_last_current = discharge_amps
                        self.session_last_voltage = d.module_voltage
                        self.session_idle_since = None
                        self.reset_session_candidate()
            else:
                self.reset_session_candidate()
            return

        # Integrate actual discharge between samples. Charging/idle contributes zero.
        if self.session_last_sample_time is not None:
            dt = max(0.0, min(now - self.session_last_sample_time, 30.0))
            previous_amps = max(0.0, self.session_last_current or 0.0)
            avg_amps = (previous_amps + discharge_amps) / 2.0
            previous_voltage = self.session_last_voltage or d.module_voltage
            avg_voltage = (previous_voltage + d.module_voltage) / 2.0
            self.session_ah += avg_amps * dt / 3600.0
            self.session_wh += avg_amps * avg_voltage * dt / 3600.0

        self.session_last_sample_time = now
        self.session_last_current = discharge_amps
        self.session_last_voltage = d.module_voltage
        self.session_peak_amps = max(self.session_peak_amps, discharge_amps)
        self.session_peak_watts = max(self.session_peak_watts, watts)

        if under_load:
            self.session_idle_since = None
        else:
            if self.session_idle_since is None:
                self.session_idle_since = now
            elif now - self.session_idle_since >= SESSION_END_SECONDS:
                self.finish_session(d, self.session_idle_since)

    def finish_session(self, d, end_time=None):
        if not self.session_active or self.session_start_time is None:
            return
        end_time = end_time or time.time()
        duration = max(0.0, end_time - self.session_start_time)
        avg_watts = self.session_wh / (duration / 3600.0) if duration > 0 else 0.0
        start_text = datetime.fromtimestamp(self.session_start_time).isoformat(timespec="seconds")
        end_text = datetime.fromtimestamp(end_time).isoformat(timespec="seconds")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO sessions (
                    start_time, end_time, start_soc, end_soc, amp_hours,
                    watt_hours, avg_watts, peak_amps, peak_watts, duration_seconds
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                start_text, end_text, self.session_start_soc, d.soc,
                self.session_ah, self.session_wh, avg_watts,
                self.session_peak_amps, self.session_peak_watts, duration
            ))
        self.last_session_summary = (
            f"Last: {self.session_wh:.1f} Wh / {self.session_ah:.2f} Ah"
        )
        self.session_active = False
        self.reset_session_candidate()
        self.session_idle_since = None
        self.session_start_time = None
        self.session_last_sample_time = None
        self.session_last_current = None
        self.session_last_voltage = None

    def windows_notification(self, title, message):
        """Best-effort native Windows toast; failures never stop monitoring."""
        def worker():
            try:
                safe_title = title.replace("'", "''")
                safe_message = message.replace("'", "''")
                script = (
                    "$ErrorActionPreference='Stop'; "
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                    "$template=[Windows.UI.Notifications.ToastTemplateType]::ToastText02; "
                    "$xml=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template); "
                    f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{safe_title}')) > $null; "
                    f"$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('{safe_message}')) > $null; "
                    "$toast=[Windows.UI.Notifications.ToastNotification]::new($xml); "
                    "$notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('WattCycle Monitor'); "
                    "$notifier.Show($toast)"
                )
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def fire_alert(self, title, message):
        self.last_alert_text = f"{title}: {message}"
        self.windows_notification(title, message)

    def check_alerts(self, d, w):
        # Re-arm SOC thresholds after charging back above them.
        for threshold in (20, 10, 5):
            if d.soc > threshold + 2:
                self.alerted_soc.discard(threshold)

        # If several thresholds are crossed between polls, only fire the most
        # urgent one and mark the higher thresholds handled too.
        crossed = [t for t in (20, 10, 5) if d.soc <= t and t not in self.alerted_soc]
        if crossed:
            threshold = min(crossed)
            self.alerted_soc.update(t for t in (20, 10, 5) if d.soc <= t)
            if threshold <= 5:
                level = "EMERGENCY BATTERY"
            elif threshold <= 10:
                level = "CRITICAL BATTERY"
            else:
                level = "LOW BATTERY"
            self.fire_alert(
                f"WattCycle - {level}",
                f"Battery is at {d.soc}% ({d.remaining_capacity:.1f} Ah remaining)."
            )

        warning_active = bool(
            w and (w.warning_register_1 != 0 or w.warning_register_2 != 0)
        )
        if warning_active and not self.bms_warning_alerted:
            self.bms_warning_alerted = True
            self.fire_alert(
                "WattCycle - BMS Warning",
                f"BMS warning active: W1={w.warning_register_1}, W2={w.warning_register_2}."
            )
        elif not warning_active:
            self.bms_warning_alerted = False

    def calculate_nina_state(self, d):
        now = time.time()
        if self.nina_simulated_state and now < self.nina_simulated_until:
            return self.nina_simulated_state, f"SIMULATION: {self.nina_simulated_state} event"
        if self.nina_simulated_state and now >= self.nina_simulated_until:
            self.nina_simulated_state = None

        avg15_current, _ = self.rolling_average(900)
        runtime_min = None
        if avg15_current is not None and avg15_current < -0.10:
            runtime_min = (d.remaining_capacity / abs(avg15_current)) * 60.0
        self.nina_runtime_minutes = runtime_min

        if d.soc <= NINA_EMERGENCY_SOC:
            return "EMERGENCY", f"SOC {d.soc}% is at/below emergency threshold"
        if d.soc <= NINA_CRITICAL_SOC:
            return "CRITICAL", f"SOC {d.soc}% is at/below critical threshold"
        if runtime_min is not None and runtime_min <= NINA_CRITICAL_RUNTIME_MIN:
            return "CRITICAL", f"Estimated runtime {runtime_min:.0f} min is at/below reserve"
        if d.soc <= 20:
            return "LOW", f"SOC {d.soc}% - warning only"
        return "NORMAL", f"SOC {d.soc}%" + (f", estimated runtime {runtime_min/60:.1f} h" if runtime_min else "")

    def publish_nina_state(self, d):
        state, reason = self.calculate_nina_state(d)
        self.nina_state = state
        self.nina_reason = reason
        payload = {
            "version": 1,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "state": state,
            "safe": state in ("NORMAL", "LOW"),
            "critical": state in ("CRITICAL", "EMERGENCY"),
            "emergency": state == "EMERGENCY",
            "reason": reason,
            "soc": int(d.soc),
            "remaining_ah": round(float(d.remaining_capacity), 2),
            "voltage": round(float(d.module_voltage), 3),
            "current": round(float(d.current), 3),
            "estimated_runtime_minutes": round(self.nina_runtime_minutes, 1) if self.nina_runtime_minutes is not None else None,
            "protection_armed": bool(self.nina_protection_armed),
            "simulation": bool(self.nina_simulated_state),
            "mos_control_automatic": False,
            "charge_mos": self.charge_on,
            "discharge_mos": self.discharge_on
        }
        tmp = NINA_STATE_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, NINA_STATE_PATH)
        except Exception:
            pass

    def test_nina_event(self, state):
        self.nina_simulated_state = state
        self.nina_simulated_until = time.time() + 120
        if self.data is not None:
            self.publish_nina_state(self.data)
        self.fire_alert("WattCycle - N.I.N.A. TEST", f"Simulated {state} battery event for 2 minutes. MOS state is unchanged.")

    def clear_nina_test(self):
        self.nina_simulated_state = None
        self.nina_simulated_until = 0
        if self.data is not None:
            self.publish_nina_state(self.data)

    def open_nina_folder(self):
        try:
            os.startfile(DATA_DIR)
        except Exception:
            pass

    # ---------------------------------------------------------
    # GUI
    # ---------------------------------------------------------

    def build_gui(self):

        # Scrollable content keeps every control reachable without requiring
        # an enormous window as the monitor grows.
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        main = ttk.Frame(canvas, padding=14)
        window_id = canvas.create_window((0, 0), window=main, anchor="nw")
        main.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        ttk.Label(
            main,
            text="WATTCYCLE",
            font=("Segoe UI", 22, "bold")
        ).pack()

        ttk.Label(
            main,
            text="100Ah LiFePO4 Battery Monitor",
            font=("Segoe UI", 11)
        ).pack(pady=(0, 10))

        self.connection_label = ttk.Label(
            main,
            text="Connecting...",
            font=("Segoe UI", 10)
        )
        self.connection_label.pack()

        self.soc_label = ttk.Label(
            main,
            text="--%",
            font=("Segoe UI", 52, "bold")
        )
        self.soc_label.pack(pady=(8, 0))

        self.capacity_label = ttk.Label(
            main,
            text="-- Ah remaining",
            font=("Segoe UI", 11)
        )
        self.capacity_label.pack()

        self.progress = ttk.Progressbar(
            main,
            maximum=100,
            length=520
        )
        self.progress.pack(pady=10)

        # Electrical
        electrical = ttk.LabelFrame(
            main,
            text=" Electrical ",
            padding=12
        )
        electrical.pack(fill="x", pady=6)

        for i in range(4):
            electrical.columnconfigure(i, weight=1)

        self.voltage_value = self.make_readout(
            electrical, "Voltage", 0
        )
        self.current_value = self.make_readout(
            electrical, "Current", 1
        )
        self.power_value = self.make_readout(
            electrical, "Power", 2
        )
        self.runtime_value = self.make_readout(
            electrical, "Runtime", 3
        )

        # Rolling load history
        averages = ttk.LabelFrame(
            main,
            text=" Load History ",
            padding=10
        )
        averages.pack(fill="x", pady=6)

        for i in range(3):
            averages.columnconfigure(i, weight=1)

        self.avg5_value = self.make_readout(averages, "5 Min Avg", 0)
        self.avg15_value = self.make_readout(averages, "15 Min Avg", 1)
        self.avg_runtime_value = self.make_readout(averages, "Est. Runtime", 2)

        # Automatic session summary
        session = ttk.LabelFrame(
            main,
            text=" Astro Session ",
            padding=10
        )
        session.pack(fill="x", pady=6)

        for i in range(5):
            session.columnconfigure(i, weight=1)

        self.session_time_value = self.make_readout(session, "Session", 0)
        self.session_ah_value = self.make_readout(session, "Used", 1)
        self.session_wh_value = self.make_readout(session, "Energy", 2)
        self.session_avg_value = self.make_readout(session, "Avg Load", 3)
        self.session_peak_value = self.make_readout(session, "Peak", 4)

        session_buttons = ttk.Frame(session)
        session_buttons.grid(row=2, column=0, columnspan=5, pady=(8, 0))
        ttk.Button(
            session_buttons, text="View Session History", command=self.show_session_history
        ).pack(side="left", padx=4)
        ttk.Button(
            session_buttons, text="Session Analytics", command=self.show_session_analytics
        ).pack(side="left", padx=4)

        # Cells
        cells = ttk.LabelFrame(
            main,
            text=" Cell Voltages ",
            padding=12
        )
        cells.pack(fill="x", pady=6)

        for i in range(5):
            cells.columnconfigure(i, weight=1)

        self.cell_values = []

        for i in range(4):
            frame = ttk.Frame(cells)
            frame.grid(row=0, column=i, sticky="ew")

            ttk.Label(frame, text=f"Cell {i + 1}").pack()

            value = ttk.Label(
                frame,
                text="-.--- V",
                font=("Segoe UI", 13, "bold")
            )
            value.pack()

            self.cell_values.append(value)

        frame = ttk.Frame(cells)
        frame.grid(row=0, column=4, sticky="ew")

        ttk.Label(frame, text="Delta").pack()

        self.delta_value = ttk.Label(
            frame,
            text="-- mV",
            font=("Segoe UI", 13, "bold")
        )
        self.delta_value.pack()

        # Temperatures
        temperatures = ttk.LabelFrame(
            main,
            text=" Temperatures ",
            padding=12
        )
        temperatures.pack(fill="x", pady=6)

        for i in range(3):
            temperatures.columnconfigure(i, weight=1)

        self.mos_temp_value = self.make_readout(
            temperatures, "MOS", 0
        )
        self.pcb_temp_value = self.make_readout(
            temperatures, "PCB", 1
        )
        self.cell_temp_value = self.make_readout(
            temperatures, "Battery", 2
        )

        # BMS information
        bms = ttk.LabelFrame(
            main,
            text=" BMS Status ",
            padding=12
        )
        bms.pack(fill="x", pady=6)

        for i in range(3):
            bms.columnconfigure(i, weight=1)

        self.cycles_value = self.make_readout(
            bms, "Cycles", 0
        )
        self.balance_value = self.make_readout(
            bms, "Balancing", 1
        )
        self.warning_value = self.make_readout(
            bms, "Warnings", 2
        )

        # MOS control
        controls = ttk.LabelFrame(
            main,
            text=" BMS Control ",
            padding=12
        )
        controls.pack(fill="x", pady=6)

        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)

        # Charge
        charge = ttk.Frame(controls)
        charge.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=15
        )

        ttk.Label(
            charge,
            text="Charge MOS",
            font=("Segoe UI", 10)
        ).pack()

        self.charge_state = ttk.Label(
            charge,
            text="UNKNOWN",
            font=("Segoe UI", 15, "bold")
        )
        self.charge_state.pack(pady=3)

        self.charge_button = ttk.Button(
            charge,
            text="...",
            command=self.toggle_charge
        )
        self.charge_button.pack()

        # Discharge
        discharge = ttk.Frame(controls)
        discharge.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=15
        )

        ttk.Label(
            discharge,
            text="Discharge MOS",
            font=("Segoe UI", 10)
        ).pack()

        self.discharge_state = ttk.Label(
            discharge,
            text="UNKNOWN",
            font=("Segoe UI", 15, "bold")
        )
        self.discharge_state.pack(pady=3)

        self.discharge_button = ttk.Button(
            discharge,
            text="...",
            command=self.toggle_discharge
        )
        self.discharge_button.pack()

        if not MOS_CONTROL_ENABLED:
            self.charge_button.config(state="disabled")
            self.discharge_button.config(state="disabled")

        self.control_status = ttk.Label(
            controls,
            text=("MOS control enabled. Controls reflect actual BMS state." if MOS_CONTROL_ENABLED else "MOS control disabled in config.json (safe default)."),
            font=("Segoe UI", 9)
        )
        self.control_status.grid(
            row=1,
            column=0,
            columnspan=2,
            pady=(10, 0)
        )

        alert_frame = ttk.LabelFrame(main, text=" Alerts ", padding=8)
        alert_frame.pack(fill="x", pady=6)
        self.alert_status = ttk.Label(
            alert_frame, text="No active alerts", font=("Segoe UI", 9)
        )
        self.alert_status.pack(anchor="w")

        # N.I.N.A. integration. The monitor publishes NORMAL/LOW/CRITICAL/
        # EMERGENCY to a JSON state file for the N.I.N.A. safety bridge.
        nina = ttk.LabelFrame(main, text=" N.I.N.A. Integration ", padding=10)
        nina.pack(fill="x", pady=6)
        for i in range(4):
            nina.columnconfigure(i, weight=1)

        self.nina_state_value = self.make_readout(nina, "Battery State", 0)
        self.nina_protection_value = self.make_readout(nina, "Protection", 1)
        self.nina_critical_value = self.make_readout(nina, "Critical SOC", 2)
        self.nina_emergency_value = self.make_readout(nina, "Emergency SOC", 3)
        self.nina_critical_value.config(text=f"{NINA_CRITICAL_SOC}% / {NINA_CRITICAL_RUNTIME_MIN}m")
        self.nina_emergency_value.config(text=f"{NINA_EMERGENCY_SOC}%")

        self.nina_reason_label = ttk.Label(nina, text="Waiting for battery telemetry...", font=("Segoe UI", 9))
        self.nina_reason_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 2))
        nina_buttons = ttk.Frame(nina)
        nina_buttons.grid(row=2, column=0, columnspan=4, pady=(6, 0))
        ttk.Button(nina_buttons, text="Test Critical Event", command=lambda: self.test_nina_event("CRITICAL")).pack(side="left", padx=4)
        ttk.Button(nina_buttons, text="Test Emergency Event", command=lambda: self.test_nina_event("EMERGENCY")).pack(side="left", padx=4)
        ttk.Button(nina_buttons, text="Clear Test", command=self.clear_nina_test).pack(side="left", padx=4)
        ttk.Button(nina_buttons, text="Open N.I.N.A. State Folder", command=self.open_nina_folder).pack(side="left", padx=4)

        ttk.Label(
            nina,
            text="Test events change only the N.I.N.A. safety signal. They do NOT change Charge/Discharge MOS state.",
            font=("Segoe UI", 8)
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(7, 0))

        self.footer = ttk.Label(
            main,
            text="Waiting for battery...",
            font=("Segoe UI", 8)
        )
        self.footer.pack(pady=(8, 0))

    def show_session_history(self):
        win = tk.Toplevel(self.root)
        win.title("WattCycle Session History")
        win.geometry("900x420")
        win.minsize(760, 320)

        columns = ("start", "duration", "soc", "ah", "wh", "avg", "peak")
        tree = ttk.Treeview(win, columns=columns, show="headings", height=14)
        headings = {
            "start": "Start", "duration": "Duration", "soc": "SOC",
            "ah": "Used", "wh": "Energy", "avg": "Avg Load", "peak": "Peak"
        }
        widths = {"start": 160, "duration": 85, "soc": 90, "ah": 80, "wh": 85, "avg": 85, "peak": 85}
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="center")
        tree.column("start", anchor="w")

        scroll = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(10, 0), pady=10)

        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    """SELECT start_time, start_soc, end_soc, amp_hours, watt_hours,
                              avg_watts, peak_watts, duration_seconds
                       FROM sessions ORDER BY id DESC LIMIT 50"""
                ).fetchall()
            for start_time, start_soc, end_soc, ah, wh, avg_w, peak_w, duration in rows:
                total_minutes = int(round((duration or 0) / 60.0))
                hours, minutes = divmod(total_minutes, 60)
                duration_text = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
                soc_text = f"{start_soc}% → {end_soc}%"
                start_display = start_time.replace("T", " ") if start_time else "--"
                tree.insert("", "end", values=(
                    start_display, duration_text, soc_text, f"{ah:.2f} Ah",
                    f"{wh:.1f} Wh", f"{avg_w:.0f} W", f"{peak_w:.0f} W"
                ))
            if not rows:
                tree.insert("", "end", values=("No completed sessions yet", "", "", "", "", "", ""))
        except Exception as exc:
            tree.insert("", "end", values=(f"Could not read history: {exc}", "", "", "", "", "", ""))

    def show_session_analytics(self):
        win = tk.Toplevel(self.root)
        win.title("WattCycle Session Analytics")
        win.geometry("780x590")
        win.minsize(680, 500)

        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="SESSION ANALYTICS", font=("Segoe UI", 18, "bold")
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Calculated from completed sessions stored in WattCycle_Data.db",
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(0, 12))

        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    """SELECT start_time, end_time, start_soc, end_soc, amp_hours,
                              watt_hours, avg_watts, peak_amps, peak_watts,
                              duration_seconds
                       FROM sessions ORDER BY id"""
                ).fetchall()
        except Exception as exc:
            ttk.Label(outer, text=f"Could not read session database: {exc}").pack(anchor="w")
            return

        if not rows:
            ttk.Label(
                outer,
                text="No completed sessions yet. Finish a session and it will appear here.",
                font=("Segoe UI", 11)
            ).pack(anchor="w", pady=20)
            return

        count = len(rows)
        total_seconds = sum((r[9] or 0.0) for r in rows)
        total_hours = total_seconds / 3600.0
        total_ah = sum((r[4] or 0.0) for r in rows)
        total_wh = sum((r[5] or 0.0) for r in rows)
        avg_session_hours = total_hours / count if count else 0.0
        avg_wh_session = total_wh / count if count else 0.0
        weighted_avg_watts = total_wh / total_hours if total_hours > 0 else 0.0
        peak_watts = max((r[8] or 0.0) for r in rows)
        peak_amps = max((r[7] or 0.0) for r in rows)
        soc_used = sum(max(0, (r[2] or 0) - (r[3] or 0)) for r in rows)

        summary = ttk.LabelFrame(outer, text=" All Completed Sessions ", padding=12)
        summary.pack(fill="x")
        for i in range(4):
            summary.columnconfigure(i, weight=1)

        stats = [
            ("Sessions", f"{count}"),
            ("Run Time", f"{total_hours:.1f} h"),
            ("Energy Used", f"{total_wh:.1f} Wh"),
            ("Capacity Used", f"{total_ah:.2f} Ah"),
            ("Avg Load", f"{weighted_avg_watts:.1f} W"),
            ("Avg Session", f"{avg_session_hours:.1f} h"),
            ("Avg Energy/Session", f"{avg_wh_session:.1f} Wh"),
            ("Highest Peak", f"{peak_watts:.1f} W"),
        ]
        for idx, (label, value) in enumerate(stats):
            frame = ttk.Frame(summary)
            frame.grid(row=(idx // 4) * 2, column=idx % 4, sticky="ew", padx=5, pady=(2, 0))
            ttk.Label(frame, text=label).pack()
            ttk.Label(frame, text=value, font=("Segoe UI", 13, "bold")).pack()

        latest = rows[-1]
        latest_box = ttk.LabelFrame(outer, text=" Most Recent Session ", padding=12)
        latest_box.pack(fill="x", pady=(12, 0))
        for i in range(4):
            latest_box.columnconfigure(i, weight=1)

        latest_minutes = int(round((latest[9] or 0.0) / 60.0))
        lh, lm = divmod(latest_minutes, 60)
        latest_duration = f"{lh}h {lm:02d}m" if lh else f"{lm}m"
        latest_stats = [
            ("Duration", latest_duration),
            ("SOC", f"{latest[2]}% → {latest[3]}%"),
            ("Energy", f"{(latest[5] or 0.0):.1f} Wh"),
            ("Avg Load", f"{(latest[6] or 0.0):.1f} W"),
            ("Used", f"{(latest[4] or 0.0):.2f} Ah"),
            ("Peak", f"{(latest[8] or 0.0):.1f} W"),
            ("Peak Current", f"{(latest[7] or 0.0):.2f} A"),
            ("Started", (latest[0] or "--").replace("T", " ")),
        ]
        for idx, (label, value) in enumerate(latest_stats):
            frame = ttk.Frame(latest_box)
            frame.grid(row=(idx // 4) * 2, column=idx % 4, sticky="ew", padx=5, pady=(2, 4))
            ttk.Label(frame, text=label).pack()
            ttk.Label(frame, text=value, font=("Segoe UI", 11, "bold")).pack()

        efficiency = ttk.LabelFrame(outer, text=" Battery Use ", padding=12)
        efficiency.pack(fill="x", pady=(12, 0))
        efficiency.columnconfigure(0, weight=1)
        efficiency.columnconfigure(1, weight=1)
        efficiency.columnconfigure(2, weight=1)

        wh_per_soc = (total_wh / soc_used) if soc_used > 0 else None
        ah_per_soc = (total_ah / soc_used) if soc_used > 0 else None
        observed_full_wh = (wh_per_soc * 100.0) if wh_per_soc is not None else None

        battery_stats = [
            ("Recorded SOC Used", f"{soc_used}%"),
            ("Observed Wh / 1% SOC", f"{wh_per_soc:.1f} Wh" if wh_per_soc is not None else "Need SOC change"),
            ("Observed Ah / 1% SOC", f"{ah_per_soc:.2f} Ah" if ah_per_soc is not None else "Need SOC change"),
        ]
        for idx, (label, value) in enumerate(battery_stats):
            frame = ttk.Frame(efficiency)
            frame.grid(row=0, column=idx, sticky="ew", padx=5)
            ttk.Label(frame, text=label).pack()
            ttk.Label(frame, text=value, font=("Segoe UI", 12, "bold")).pack()

        if observed_full_wh is not None:
            ttk.Label(
                efficiency,
                text=(f"Observed full-battery energy equivalent: {observed_full_wh:.0f} Wh. "
                      "This is an estimate from recorded SOC movement, not a battery capacity test."),
                font=("Segoe UI", 9), wraplength=700
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        else:
            ttk.Label(
                efficiency,
                text="After sessions span measurable SOC changes, this section will estimate real Wh per SOC point.",
                font=("Segoe UI", 9), wraplength=700
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        ttk.Label(
            outer,
            text=f"Highest recorded current draw across completed sessions: {peak_amps:.2f} A",
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(12, 0))

    def make_readout(self, parent, title, column):

        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="ew")

        ttk.Label(frame, text=title).pack()

        value = ttk.Label(
            frame,
            text="--",
            font=("Segoe UI", 13, "bold")
        )
        value.pack()

        return value

    # ---------------------------------------------------------
    # BUTTONS
    # ---------------------------------------------------------

    def toggle_charge(self):

        if self.control_busy or self.charge_on is None:
            return

        if self.charge_on:

            if not messagebox.askyesno(
                "Disable Charging",
                "Turn OFF the Charge MOS?\n\n"
                "The battery will no longer accept charge "
                "until it is turned back on."
            ):
                return

            self.pending_command = (
                "charge",
                False,
                CHARGE_OFF
            )

        else:
            self.pending_command = (
                "charge",
                True,
                CHARGE_ON
            )

        self.control_busy = True
        self.control_status.config(
            text="Sending command..."
        )

    def toggle_discharge(self):

        if self.control_busy or self.discharge_on is None:
            return

        if self.discharge_on:

            if not messagebox.askyesno(
                "Disable Battery Output",
                "Turn OFF the Discharge MOS?\n\n"
                "This will remove battery output power.\n\n"
                "Any equipment powered by this battery "
                "may shut down immediately."
            ):
                return

            self.pending_command = (
                "discharge",
                False,
                DISCHARGE_OFF
            )

        else:
            self.pending_command = (
                "discharge",
                True,
                DISCHARGE_ON
            )

        self.control_busy = True
        self.control_status.config(
            text="Sending command..."
        )

    # ---------------------------------------------------------
    # BLE
    # ---------------------------------------------------------

    def ble_thread(self):
        asyncio.run(self.ble_loop())

    async def ble_loop(self):

        while self.running:

            client = WattcycleClient(ADDRESS)

            try:
                self.error = None

                await client.connect()
                client.frame_head = FRAME_HEAD
                if self.disconnect_started is not None:
                    outage = time.time() - self.disconnect_started
                    if outage >= 60:
                        self.fire_alert(
                            "WattCycle - Connection Restored",
                            f"Battery connection restored after {int(outage)} seconds."
                        )
                    self.disconnect_started = None

                self.product = (
                    await client.read_product_info()
                )

                while self.running and client.is_connected:

                    # Accept local commands from the N.I.N.A. Alpaca bridge.
                    # The bridge never opens its own BLE connection; this monitor
                    # remains the single owner of BMS communications.
                    if self.pending_command is None and not self.control_busy:
                        try:
                            if os.path.exists(NINA_COMMAND_PATH):
                                with open(NINA_COMMAND_PATH, "r", encoding="utf-8") as f:
                                    ext = json.load(f)
                                cmd_id = str(ext.get("id", ""))
                                if cmd_id and cmd_id != self.last_external_command_id:
                                    created = float(ext.get("created", 0) or 0)
                                    if created and time.time() - created <= 60:
                                        if ext.get("control") == "discharge":
                                            desired = bool(ext.get("state"))
                                            self.pending_command = (
                                                "discharge", desired,
                                                DISCHARGE_ON if desired else DISCHARGE_OFF
                                            )
                                            self.control_busy = True
                                            self.control_message = (
                                                "N.I.N.A. requested Rig Power "
                                                + ("ON" if desired else "OFF")
                                            )
                                    self.last_external_command_id = cmd_id
                        except Exception as e:
                            self.control_message = f"N.I.N.A. command error: {e}"

                    # Process GUI/N.I.N.A. command
                    if self.pending_command is not None and not MOS_CONTROL_ENABLED:
                        self.pending_command = None
                        self.control_message = "MOS control blocked by config.json"

                    if self.pending_command is not None:

                        control, desired, command = (
                            self.pending_command
                        )

                        self.pending_command = None

                        response = await client.send_command(
                            command,
                            timeout=3
                        )

                        # Give BMS time to update status.
                        await asyncio.sleep(1)

                        verify = (
                            await client.read_warning_info()
                        )

                        if verify is not None:

                            self.warning = verify

                            charge = bool(
                                verify.status_register_3
                                & CHARGE_BIT
                            )

                            discharge = bool(
                                verify.status_register_3
                                & DISCHARGE_BIT
                            )

                            self.charge_on = charge
                            self.discharge_on = discharge

                            actual = (
                                charge
                                if control == "charge"
                                else discharge
                            )

                            if actual == desired:
                                self.control_message = (
                                    f"{control.title()} MOS "
                                    f"{'ON' if desired else 'OFF'} "
                                    f"— verified by BMS"
                                )
                            else:
                                self.control_message = (
                                    f"{control.title()} MOS command "
                                    f"did not verify"
                                )

                        else:
                            self.control_message = (
                                "Unable to verify BMS state"
                            )

                        self.control_busy = False

                    # Normal polling
                    data = (
                        await client.read_analog_quantity()
                    )

                    warning = (
                        await client.read_warning_info()
                    )

                    if data is not None:
                        self.data = data
                        self.error = None
                        self.add_sample(data)
                        self.log_sample(data, warning)
                        self.update_session(data)
                        self.check_alerts(data, warning)
                        self.publish_nina_state(data)

                    if warning is not None:
                        self.warning = warning

                        self.charge_on = bool(
                            warning.status_register_3
                            & CHARGE_BIT
                        )

                        self.discharge_on = bool(
                            warning.status_register_3
                            & DISCHARGE_BIT
                        )

                    await asyncio.sleep(
                        REFRESH_SECONDS
                    )

            except Exception as e:
                self.error = str(e)
                self.control_busy = False
                if self.disconnect_started is None:
                    self.disconnect_started = time.time()

            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            if self.running:
                await asyncio.sleep(5)

    # ---------------------------------------------------------
    # GUI UPDATE
    # ---------------------------------------------------------

    def update_gui(self):

        if self.data is not None:

            d = self.data
            w = self.warning

            self.connection_label.config(
                text="● Connected"
            )

            self.soc_label.config(
                text=f"{d.soc}%"
            )

            self.progress["value"] = d.soc

            self.capacity_label.config(
                text=(
                    f"{d.remaining_capacity:.1f} / "
                    f"{d.total_capacity:.1f} Ah remaining"
                )
            )

            self.voltage_value.config(
                text=f"{d.module_voltage:.2f} V"
            )

            # Negative = discharge on this WattCycle BMS.
            current = d.current
            power = d.module_voltage * current

            if current < -0.10:
                state = "↓"
                display_current = abs(current)
            elif current > 0.10:
                state = "↑"
                display_current = current
            else:
                state = ""
                display_current = abs(current)

            self.current_value.config(
                text=f"{state} {display_current:.2f} A"
            )

            self.power_value.config(
                text=f"{abs(power):.1f} W"
            )

            # Runtime only while discharging.
            if current < -0.10:

                hours = (
                    d.remaining_capacity
                    / abs(current)
                )

                total_minutes = int(hours * 60)
                h, m = divmod(total_minutes, 60)

                self.runtime_value.config(
                    text=f"{h}h {m:02d}m"
                )

            else:
                self.runtime_value.config(
                    text="--"
                )

            avg5_current, avg5_power = self.rolling_average(300)
            avg15_current, avg15_power = self.rolling_average(900)

            if avg5_current is not None:
                self.avg5_value.config(
                    text=f"{abs(avg5_current):.2f} A / {abs(avg5_power):.0f} W"
                )

            if avg15_current is not None:
                self.avg15_value.config(
                    text=f"{abs(avg15_current):.2f} A / {abs(avg15_power):.0f} W"
                )

            if avg15_current is not None and avg15_current < -0.10:
                hours = d.remaining_capacity / abs(avg15_current)
                total_minutes = int(hours * 60)
                h, m = divmod(total_minutes, 60)
                self.avg_runtime_value.config(text=f"{h}h {m:02d}m")
            else:
                self.avg_runtime_value.config(text="--")

            # Automatic astro-session summary
            if self.session_active and self.session_start_time is not None:
                elapsed = max(0, int(time.time() - self.session_start_time))
                hh, rem = divmod(elapsed, 3600)
                mm, _ = divmod(rem, 60)
                avg_session_watts = (
                    self.session_wh / (elapsed / 3600.0)
                    if elapsed > 0 else 0.0
                )
                self.session_time_value.config(text=f"{hh}h {mm:02d}m")
                self.session_ah_value.config(text=f"{self.session_ah:.2f} Ah")
                self.session_wh_value.config(text=f"{self.session_wh:.1f} Wh")
                self.session_avg_value.config(text=f"{avg_session_watts:.0f} W")
                self.session_peak_value.config(text=f"{self.session_peak_watts:.0f} W")
            elif self.session_candidate_since is not None:
                remaining = max(0, int(SESSION_START_SECONDS - (time.time() - self.session_candidate_since)))
                self.session_time_value.config(text=f"Starting {remaining}s")
                self.session_ah_value.config(text="--")
                self.session_wh_value.config(text="--")
                self.session_avg_value.config(text="--")
                self.session_peak_value.config(text="--")
            else:
                self.session_time_value.config(text="Idle")
                if self.last_session_summary:
                    self.session_ah_value.config(text=self.last_session_summary.split(" / ")[1])
                    self.session_wh_value.config(text=self.last_session_summary.split(": ")[1].split(" / ")[0])
                else:
                    self.session_ah_value.config(text="--")
                    self.session_wh_value.config(text="--")
                self.session_avg_value.config(text="--")
                self.session_peak_value.config(text="--")

            # Cells
            for i, label in enumerate(
                self.cell_values
            ):

                if i < len(d.cell_voltages):
                    label.config(
                        text=(
                            f"{d.cell_voltages[i]:.3f} V"
                        )
                    )

            if d.cell_voltages:

                delta = (
                    max(d.cell_voltages)
                    - min(d.cell_voltages)
                ) * 1000

                self.delta_value.config(
                    text=f"{delta:.0f} mV"
                )

            # Temps
            self.mos_temp_value.config(
                text=self.temp_string(
                    d.mos_temperature
                )
            )

            self.pcb_temp_value.config(
                text=self.temp_string(
                    d.pcb_temperature
                )
            )

            if d.cell_temperatures:
                self.cell_temp_value.config(
                    text=self.temp_string(
                        d.cell_temperatures[0]
                    )
                )

            # BMS status
            self.cycles_value.config(
                text=str(d.cycle_number)
            )

            if w is not None:

                balancing = [
                    str(i + 1)
                    for i, active
                    in enumerate(w.balance_states)
                    if active
                ]

                self.balance_value.config(
                    text=(
                        "Cells " + ", ".join(balancing)
                        if balancing
                        else "None"
                    )
                )

                warnings_active = (
                    w.warning_register_1 != 0
                    or w.warning_register_2 != 0
                )

                self.warning_value.config(
                    text=(
                        "ACTIVE"
                        if warnings_active
                        else "None"
                    )
                )

            # Verified MOS states
            if self.charge_on is not None:

                self.charge_state.config(
                    text=(
                        "ON"
                        if self.charge_on
                        else "OFF"
                    )
                )

                self.charge_button.config(
                    text=(
                        "Turn OFF"
                        if self.charge_on
                        else "Turn ON"
                    )
                )

            if self.discharge_on is not None:

                self.discharge_state.config(
                    text=(
                        "ON"
                        if self.discharge_on
                        else "OFF"
                    )
                )

                self.discharge_button.config(
                    text=(
                        "Turn OFF"
                        if self.discharge_on
                        else "Turn ON"
                    )
                )

            if hasattr(self, "control_message"):
                self.control_status.config(
                    text=self.control_message
                )

            self.alert_status.config(text=self.last_alert_text)
            self.nina_state_value.config(text=self.nina_state)
            self.nina_protection_value.config(text="ARMED" if self.nina_protection_armed else "DISARMED")
            self.nina_reason_label.config(text=self.nina_reason)

            if self.product:

                self.footer.config(
                    text=(
                        f"{self.product.serial_number} | "
                        f"{self.product.firmware_version} | "
                        f"BLE {ADDRESS}"
                    )
                )

        elif self.error:

            self.connection_label.config(
                text="● Disconnected"
            )

            self.footer.config(
                text=(
                    "Automatic reconnect: "
                    + self.error
                )
            )

        self.root.after(
            500,
            self.update_gui
        )

    @staticmethod
    def temp_string(celsius):
        fahrenheit = celsius * 9 / 5 + 32
        return f"{fahrenheit:.1f}°F"

    def close(self):
        self.running = False
        # Preserve a real active session if the monitor is closed before the
        # normal five-minute idle timeout can finalize it.
        if self.session_active and self.data is not None:
            try:
                self.finish_session(self.data, time.time())
            except Exception:
                pass
        self.root.destroy()


root = tk.Tk()
app = WattCycleMonitor(root)
root.mainloop()