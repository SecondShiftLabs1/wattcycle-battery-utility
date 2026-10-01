import json, threading, socket, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from wattcycle_config import load_config

CONFIG=load_config()
PORT=int(CONFIG.get("alpaca_port",11111))
DISCOVERY_PORT=32227
_DATA_DIR=Path(str(CONFIG.get("data_directory","~/Documents/WattCycle")).replace("~",str(Path.home()),1))
STATE_FILE=_DATA_DIR/"NINA_Battery_State.json"
COMMAND_FILE=_DATA_DIR/"NINA_Battery_Command.json"
SAFETY_UID="b9280874-4215-4a4d-8c4a-2a8c8cf7e531"
SWITCH_UID="61dfd31d-c7f8-4db0-a343-5b7f52a41872"
SAFETY_NAME="WattCycle Battery Safety Monitor"
SWITCH_NAME="WattCycle Battery + Rig Power"
STALE_SECONDS=float(CONFIG.get("stale_seconds",30.0))
MOS_CONTROL_ENABLED=bool(CONFIG.get("allow_mos_control",False))
server_tx=0
safety_connected=True
switch_connected=True
last_error=""
last_request_time=0.0

# Read-only ASCOM Switch channels. Values come from Monitor v7's JSON state.
CHANNELS=[
    ("State of Charge (%)", "Battery state of charge", 0.0, 100.0, 1.0, "soc"),
    ("Battery Voltage (V)", "Battery terminal voltage", 0.0, 20.0, 0.01, "voltage"),
    ("Battery Current (A)", "Signed BMS current: negative=discharge, positive=charge", -150.0, 150.0, 0.01, "current"),
    ("Battery Power (W)", "Signed power: negative=discharge, positive=charge", -2500.0, 2500.0, 0.1, "power"),
    ("Remaining Capacity (Ah)", "BMS remaining capacity", 0.0, 100.0, 0.01, "remaining_ah"),
    ("Estimated Runtime (hr)", "Runtime from WattCycle Monitor 15-minute load estimate", 0.0, 100.0, 0.01, "runtime_hours"),
    ("Safety (1=Safe)", "1=Safe, 0=Unsafe", 0.0, 1.0, 1.0, "safe_numeric"),
    ("Data Age (sec)", "Age of WattCycle Monitor state data", 0.0, 3600.0, 1.0, "data_age"),
    ("Rig Power / Discharge MOS", "Master 12V battery output. OFF removes power from equipment connected to the WattCycle output.", 0.0, 1.0, 1.0, "discharge_mos"),
]

def read_state():
    global last_error
    try:
        with open(STATE_FILE,"r",encoding="utf-8") as f:
            d=json.load(f)
        st=str(d.get("state",d.get("battery_state","UNKNOWN"))).upper()
        age=999999.0
        ts=d.get("timestamp")
        if ts:
            try: age=max(0.0,(datetime.now()-datetime.fromisoformat(ts)).total_seconds())
            except Exception: pass
        if age==999999.0:
            try: age=max(0.0,time.time()-STATE_FILE.stat().st_mtime)
            except Exception: pass
        # Fail safe: stale data is unsafe even if the last state said NORMAL.
        safe=(st in ("NORMAL","LOW")) and age <= STALE_SECONDS
        if age > STALE_SECONDS:
            display_state="STALE"
        else:
            display_state=st
        d["_age_seconds"]=age
        d["_effective_safe"]=safe
        d["_display_state"]=display_state
        last_error=""
        return safe,display_state,d
    except Exception as e:
        last_error=str(e)
        return False,"NO DATA",{"_age_seconds":999999.0,"_effective_safe":False}

def telemetry_value(idx,d):
    key=CHANNELS[idx][5]
    if key=="power":
        return float(d.get("voltage",0.0))*float(d.get("current",0.0))
    if key=="runtime_hours":
        v=d.get("estimated_runtime_minutes")
        return 0.0 if v is None else float(v)/60.0
    if key=="safe_numeric": return 1.0 if d.get("_effective_safe",False) else 0.0
    if key=="data_age": return min(3600.0,float(d.get("_age_seconds",3600.0)))
    if key=="discharge_mos": return 1.0 if d.get("discharge_mos") is True else 0.0
    v=d.get(key)
    return 0.0 if v is None else float(v)

def request_discharge(state):
    # Atomic local IPC to WattCycle Monitor v8. The monitor owns BLE and
    # verifies the MOS state directly from the BMS after sending the command.
    COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload={"id":f"{time.time_ns()}","created":time.time(),"control":"discharge","state":bool(state),"source":"NINA_ASCOM_Switch"}
    tmp=COMMAND_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    tmp.replace(COMMAND_FILE)

def envelope(value=None,err=0,msg="",client=0):
    global server_tx
    server_tx+=1
    r={"ClientTransactionID":client,"ServerTransactionID":server_tx,"ErrorNumber":err,"ErrorMessage":msg}
    if value is not None:r["Value"]=value
    return r

def qval(q,name,default=None):
    for k,v in q.items():
        if k.lower()==name.lower(): return v[0] if isinstance(v,list) else v
    return default

def parse_id(q):
    try:return int(qval(q,"Id","-1"))
    except:return -1

class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def params(self):
        p=urlparse(self.path);q=parse_qs(p.query)
        if self.command=="PUT":
            n=int(self.headers.get("Content-Length","0") or 0);body=self.rfile.read(n).decode(errors="ignore")
            q.update(parse_qs(body))
        return p.path.lower(),q
    def sendj(self,obj,code=200):
        b=json.dumps(obj).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):self.handle_req()
    def do_PUT(self):self.handle_req()
    def handle_req(self):
        global safety_connected,switch_connected,last_request_time
        last_request_time=time.time()
        path,q=self.params();client=int(qval(q,"ClientTransactionID","0") or 0)
        if path=="/management/apiversions":return self.sendj(envelope([1],client=client))
        if path=="/management/v1/description":return self.sendj(envelope({"ServerName":"WattCycle NINA Bridge","Manufacturer":"Local","ManufacturerVersion":"3.0","Location":"Windows mini PC"},client=client))
        if path=="/management/v1/configureddevices":
            return self.sendj(envelope([
                {"DeviceName":SAFETY_NAME,"DeviceType":"SafetyMonitor","DeviceNumber":0,"UniqueID":SAFETY_UID},
                {"DeviceName":SWITCH_NAME,"DeviceType":"Switch","DeviceNumber":0,"UniqueID":SWITCH_UID}
            ],client=client))

        sb="/api/v1/safetymonitor/0/"
        if path.startswith(sb):
            prop=path[len(sb):]
            if prop=="connected":
                if self.command=="PUT":safety_connected=str(qval(q,"Connected","true")).lower()=="true"
                return self.sendj(envelope(safety_connected,client=client))
            if prop=="issafe":
                safe,_,_=read_state();return self.sendj(envelope(bool(safe and safety_connected),client=client))
            vals={"name":SAFETY_NAME,"description":"WattCycle battery protection state","driverinfo":"WattCycle JSON to ASCOM Alpaca SafetyMonitor bridge","driverversion":"3.0","interfaceversion":1,"supportedactions":[]}
            if prop in vals:return self.sendj(envelope(vals[prop],client=client))
            return self.sendj(envelope(err=1024,msg="Unknown member",client=client),404)

        wb="/api/v1/switch/0/"
        if path.startswith(wb):
            prop=path[len(wb):]
            if prop=="connected":
                if self.command=="PUT":switch_connected=str(qval(q,"Connected","true")).lower()=="true"
                return self.sendj(envelope(switch_connected,client=client))
            vals={"name":SWITCH_NAME,"description":"WattCycle battery telemetry with writable Rig Power output","driverinfo":"WattCycle JSON to ASCOM Alpaca Switch bridge with Rig Power control","driverversion":"3.0","interfaceversion":2,"supportedactions":[],"maxswitch":len(CHANNELS)}
            if prop in vals:return self.sendj(envelope(vals[prop],client=client))
            idx=parse_id(q)
            if prop in ("getswitch","getswitchvalue","getswitchname","getswitchdescription","canwrite","minswitchvalue","maxswitchvalue","switchstep"):
                if idx<0 or idx>=len(CHANNELS):return self.sendj(envelope(err=1025,msg="Invalid switch ID",client=client))
                name,desc,mn,mx,step,key=CHANNELS[idx]
                _,_,d=read_state();v=telemetry_value(idx,d)
                if prop=="getswitch":value=bool(v>0.5)
                elif prop=="getswitchvalue":value=max(mn,min(mx,v))
                elif prop=="getswitchname":value=name
                elif prop=="getswitchdescription":value=desc
                elif prop=="canwrite":value=(key=="discharge_mos" and MOS_CONTROL_ENABLED)
                elif prop=="minswitchvalue":value=mn
                elif prop=="maxswitchvalue":value=mx
                else:value=step
                return self.sendj(envelope(value,client=client))
            if prop in ("setswitch","setswitchvalue"):
                idx=parse_id(q)
                if idx<0 or idx>=len(CHANNELS):return self.sendj(envelope(err=1025,msg="Invalid switch ID",client=client))
                if CHANNELS[idx][5] != "discharge_mos":
                    return self.sendj(envelope(err=1026,msg="This telemetry channel is read-only",client=client))
                if prop=="setswitch":
                    raw=str(qval(q,"State","false")).lower(); desired=raw in ("true","1","yes","on")
                else:
                    try: desired=float(qval(q,"Value","0")) >= 0.5
                    except: return self.sendj(envelope(err=1025,msg="Invalid switch value",client=client))
                try:
                    request_discharge(desired)
                    return self.sendj(envelope(client=client))
                except Exception as e:
                    return self.sendj(envelope(err=1026,msg=f"Unable to queue Rig Power command: {e}",client=client))
            return self.sendj(envelope(err=1024,msg="Unknown member",client=client),404)
        return self.sendj(envelope(err=1024,msg="Unknown endpoint",client=client),404)

def discovery(stop):
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try:s.bind(("",DISCOVERY_PORT))
    except Exception:return
    s.settimeout(.5)
    while not stop.is_set():
        try:
            data,addr=s.recvfrom(1024)
            if data.decode(errors="ignore").strip().lower()=="alpacadiscovery1":s.sendto(json.dumps({"AlpacaPort":PORT}).encode(),addr)
        except socket.timeout:pass
        except Exception:pass
    s.close()

stop=threading.Event();httpd=ThreadingHTTPServer(("0.0.0.0",PORT),H)
threading.Thread(target=httpd.serve_forever,daemon=True).start();threading.Thread(target=discovery,args=(stop,),daemon=True).start()

root=tk.Tk();root.title("WattCycle Battery Utility → ASCOM Alpaca");root.geometry("590x460")
frm=ttk.Frame(root,padding=16);frm.pack(fill="both",expand=True)
ttk.Label(frm,text="WattCycle Battery Utility Alpaca Bridge",font=("Segoe UI",16,"bold")).pack(anchor="w")
ttk.Label(frm,text="ASCOM Alpaca SafetyMonitor + telemetry + Rig Power control").pack(anchor="w",pady=(2,14))
state=tk.StringVar();safev=tk.StringVar();socv=tk.StringVar();voltv=tk.StringVar();loadv=tk.StringVar();runtimev=tk.StringVar();agev=tk.StringVar();ninav=tk.StringVar()
for label,var in [("Battery state",state),("Safety Monitor",safev),("State of charge",socv),("Voltage",voltv),("Load",loadv),("Estimated runtime",runtimev),("Data age",agev),("N.I.N.A./Alpaca",ninav)]:
    row=ttk.Frame(frm);row.pack(fill="x",pady=3);ttk.Label(row,text=label+":",width=22).pack(side="left");ttk.Label(row,textvariable=var).pack(side="left")
ttk.Separator(frm).pack(fill="x",pady=12)
ttk.Label(frm,text="N.I.N.A. devices exposed:",font=("Segoe UI",10,"bold")).pack(anchor="w")
ttk.Label(frm,text="• WattCycle Battery Safety Monitor\n• WattCycle Battery + Rig Power (Switch)",justify="left").pack(anchor="w",pady=(3,8))
ttk.Label(frm,text="Telemetry is read-only except Rig Power / Discharge MOS. Turning it OFF removes the WattCycle 12V output.",wraplength=550).pack(anchor="w")
ttk.Label(frm,text=f"Fail-safe: state data older than {int(STALE_SECONDS)} seconds is reported UNSAFE.",wraplength=550).pack(anchor="w",pady=(3,0))

def tick():
    safe,st,d=read_state();state.set(st);safev.set("SAFE" if safe and safety_connected else "UNSAFE")
    socv.set(f"{d.get('soc','--')}%")
    voltv.set(f"{float(d.get('voltage',0)):.2f} V" if 'voltage' in d else "--")
    if 'voltage' in d and 'current' in d:
        p=float(d['voltage'])*float(d['current']);loadv.set(f"{abs(p):.1f} W  ({float(d['current']):+.2f} A)")
    else:loadv.set("--")
    rm=d.get('estimated_runtime_minutes');runtimev.set("--" if rm is None else f"{int(rm)//60}h {int(rm)%60:02d}m")
    age=float(d.get('_age_seconds',999999));agev.set("NO DATA" if age>9999 else f"{age:.1f} sec")
    if last_request_time: ninav.set(f"Active • last request {max(0,time.time()-last_request_time):.1f}s ago")
    else:ninav.set("Waiting for N.I.N.A.")
    root.after(1000,tick)
tick()
def close():stop.set();httpd.shutdown();root.destroy()
root.protocol("WM_DELETE_WINDOW",close);root.mainloop()