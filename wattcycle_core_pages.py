"""General-purpose energy and health pages."""
import tkinter as tk
from tkinter import ttk
import sqlite3
import time
from wattcycle_energy import energy_summary

def duration(hours):
    if hours is None:return "Not enough data"
    minutes=int(hours*60)
    h,m=divmod(minutes,60)
    return f"{h}h {m:02d}m"

class CorePages:
    def __init__(self,energy,health,monitor,path,config):
        self.m=monitor;self.path=path;self.cfg=config;self.last=0
        self.days=tk.StringVar(value="7")
        ttk.Label(energy,text="Energy & Usage",font=("Segoe UI",20,"bold")).pack(anchor="w")
        controls=ttk.Frame(energy);controls.pack(fill="x",pady=5)
        for n,label in ((1,"Today"),(7,"7 days"),(30,"30 days")):
            ttk.Radiobutton(controls,text=label,variable=self.days,value=str(n),command=self.refresh).pack(side="left",padx=5)
        self.metrics={}
        cards=ttk.Frame(energy);cards.pack(fill="x")
        for i,(key,label) in enumerate((("life","Typical full-charge life"),("reserve","Typical life to critical SOC"),("avg","Average monitored load"),("active","Average discharge load"),("used","Energy discharged"),("charged","Energy charged"))):
            f=ttk.Frame(cards,style="Card.TFrame",padding=12);f.grid(row=i//2,column=i%2,sticky="nsew",padx=3,pady=3)
            cards.columnconfigure(i%2,weight=1)
            ttk.Label(f,text=label,style="CardMuted.TLabel").pack(anchor="w")
            v=ttk.Label(f,text="--",style="Card.TLabel",font=("Segoe UI",18,"bold"));v.pack(anchor="w")
            self.metrics[key]=v
        self.coverage=ttk.Label(energy,text="Waiting for recorded telemetry",wraplength=650)
        self.coverage.pack(anchor="w",pady=6)
        self.chart=tk.Canvas(energy,height=195,highlightthickness=0,width=300)
        self.chart.pack(fill="x")
        self.chart.bind("<Configure>",lambda e:self.draw())
        self.buckets={}
        ttk.Label(energy,text="Green: charged   •   Blue: discharged",style="Muted.TLabel").pack(anchor="w")
        ttk.Label(health,text="Battery Health",font=("Segoe UI",20,"bold")).pack(anchor="w")
        self.health_text=ttk.Label(health,text="Waiting for telemetry",font=("Segoe UI",14),wraplength=650)
        self.health_text.pack(anchor="w",pady=10)
        ttk.Label(health,text="State of health: not yet measured",font=("Segoe UI",18,"bold")).pack(anchor="w")
        ttk.Label(health,text="A reliable capacity test is required before estimating health. BMS-reported capacity is not an independent health measurement.",wraplength=650).pack(anchor="w",pady=6)
        ttk.Button(health,text="View protection event log",command=self.events).pack(anchor="w",pady=12)
    def refresh(self):
        self.last=time.monotonic()
        try:
            self.buckets,t=energy_summary(self.path,int(self.days.get()),gap=max(30,float(self.cfg.get("log_interval_seconds",5))*4))
        except (sqlite3.Error,ValueError):
            self.coverage.config(text="History temporarily unavailable");return
        d=self.m.data
        a=t["average_a"];capacity=d.total_capacity if d else None
        # A historical extrapolation is only meaningful with sufficient coverage.
        valid=capacity and a and a>.001 and t["seconds"]>=3600 and t["coverage"]>=.75
        reserve=float(self.cfg.get("critical_soc",10))
        self.metrics["life"].config(text=duration(capacity/a if valid else None))
        self.metrics["reserve"].config(text=duration(capacity*(1-reserve/100)/a if valid else None))
        for key,field in (("avg","average_w"),("active","active_w"),("used","used"),("charged","charged")):
            v=t[field];self.metrics[key].config(text="--" if v is None or not t["seconds"] else f"{v:.1f} "+("Wh" if key in ("used","charged") else "W"))
        self.coverage.config(text=f"Recorded coverage: {t['coverage']:.0%} • Typical life requires at least one hour and 75% coverage. Charging and idle periods count as observed time; missing data does not.")
        if d:
            fresh=self.m.connected and self.m.last_data_time is not None and time.time()-self.m.last_data_time<=float(self.cfg.get("stale_seconds",30))
            delta=(max(d.cell_voltages)-min(d.cell_voltages))*1000 if d.cell_voltages else None
            self.health_text.config(text=("Live" if fresh else "Last known")+f" • BMS capacity: {d.total_capacity:.1f} Ah • Cell delta: "+(f"{delta:.1f} mV" if delta is not None else "--"))
        self.draw()
    def draw(self):
        c=self.chart;c.delete("all")
        bg,panel,fg,muted=self.m.display.colors;c.config(bg=panel)
        w,h=max(100,c.winfo_width()),max(100,c.winfo_height())
        if not self.buckets:return
        peak=max(1,max(max(b["used"],b["charged"]) for b in self.buckets.values()))
        step=(w-45)/len(self.buckets)
        for i,(date,b) in enumerate(self.buckets.items()):
            x=40+i*step
            for j,(key,color) in enumerate((("charged","#22a66a"),("used","#4096ed"))):
                value=b[key];height=(h-40)*value/peak
                c.create_rectangle(x+j*step*.42,h-22-height,x+(j+1)*step*.42,h-22,fill=color,outline="")
            if len(self.buckets)<=7 or i%5==0:c.create_text(x+step*.4,h-9,text=date.strftime("%m/%d"),fill=muted,font=("Segoe UI",8))
        c.create_text(4,8,text=f"{peak:.0f} Wh",anchor="nw",fill=muted)
    def events(self):
        win=tk.Toplevel(self.m.root);win.title("Protection events");win.geometry("700x360")
        tree=ttk.Treeview(win,columns=("name","started","cleared"),show="headings")
        for key,label,width in (("name","Protection",250),("started","First observed",200),("cleared","Clear observed",200)):
            tree.heading(key,text=label);tree.column(key,width=width)
        tree.pack(fill="both",expand=True)
        try:
            with sqlite3.connect(self.path,timeout=.2) as db:
                for row in db.execute("SELECT name,started,COALESCE(cleared,'Not yet observed') FROM protection_events ORDER BY id DESC LIMIT 1000"):tree.insert("", "end",values=row)
        except sqlite3.Error:pass
