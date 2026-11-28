"""Presentation helpers; no BLE or power-control side effects."""
import tkinter as tk
from tkinter import ttk, simpledialog
import time
import math
import customtkinter as ctk

def palette(root, dark):
    bg, panel, fg, muted = ("#101722", "#1b2635", "#e6edf6", "#a0aec0") if dark else ("#eef2f6", "#ffffff", "#172334", "#526174")
    style = ttk.Style(root)
    style.theme_use("clam")
    border = "#344256" if dark else "#cbd5e1"
    hover = "#30435a" if dark else "#dbeafe"
    accent = "#4da3ff" if dark else "#1766bb"
    style.configure(".", background=bg, foreground=fg, fieldbackground=panel,
                    bordercolor=border, lightcolor=border, darkcolor=border,
                    font=("Segoe UI", 10))
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label", "TNotebook", "TNotebook.Tab", "TCheckbutton", "TRadiobutton"):
        style.configure(name, background=bg, foreground=fg)
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Card.TFrame", background=panel)
    style.configure("Card.TLabel", background=panel, foreground=fg)
    style.configure("CardMuted.TLabel", background=panel, foreground=muted)
    style.configure("TLabelframe", borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", foreground=muted, font=("Segoe UI",10,"bold"))
    style.configure("TButton", background=panel, foreground=fg, padding=(10,6), borderwidth=1, relief="flat")
    style.map("TButton", background=[("pressed",hover),("active",hover)], foreground=[("disabled", muted)])
    style.configure("TNotebook", borderwidth=0, tabmargins=(0,4,0,0))
    style.configure("TNotebook.Tab", padding=(16,9), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected",panel),("active",hover)], foreground=[("selected",accent)])
    style.configure("Treeview", background=panel, fieldbackground=panel, foreground=fg, rowheight=28)
    style.configure("Treeview.Heading", background=bg, foreground=muted, padding=(5,6))
    style.configure("Horizontal.TProgressbar", background="#22a66a", troughcolor=bg, borderwidth=0, thickness=9)
    style.configure("Battery.Horizontal.TProgressbar", background="#22a66a", troughcolor=bg, borderwidth=0, thickness=9)
    root.configure(bg=bg)
    return bg, panel, fg, muted

def snapshot(m, cfg):
    d = m.data
    age = time.time() - m.last_data_time if m.last_data_time is not None else None
    fresh = bool(d is not None and m.connected and age is not None and 0 <= age <= float(cfg.get("stale_seconds", 30)))
    status = m.nina_state if fresh else "STALE" if d is not None else "CONNECTING"
    color = {"NORMAL":"#22a66a", "LOW":"#e5a323", "CRITICAL":"#ef4444", "EMERGENCY":"#ef4444"}.get(status, "#94a3b8")
    result = dict(fresh=fresh, status=status, color=color, age=age, soc="--", voltage="--", power="--", current="--", runtime="--", reserve="--", temp="--", delta="--", capacity="--", percent=0)
    if d is None:
        return result
    result.update(soc=f"{d.soc}%", percent=max(0,min(100,d.soc)), voltage=f"{d.module_voltage:.2f} V", power=f"{abs(d.current*d.module_voltage):.1f} W", current=f"{abs(d.current):.2f} A", capacity=f"{d.remaining_capacity:.1f} Ah", temp=m.temp_string(d.cell_temperatures[0]) if d.cell_temperatures else "--", delta=f"{(max(d.cell_voltages)-min(d.cell_voltages))*1000:.0f} mV" if d.cell_voltages else "--")
    result["direction"] = "Discharging" if d.current < -.1 else "Charging" if d.current > .1 else "Idle"
    if fresh and d.current < -.1:
        avg, _ = m.rolling_average(900, discharge_only=True)
        if avg is not None and abs(avg) > .1:
            def fmt(ah):
                h, mins = divmod(int(max(0,ah)/abs(avg)*60),60)
                return f"{h}h {mins:02d}m"
            result["runtime"] = fmt(d.remaining_capacity)
            result["reserve"] = fmt(d.remaining_capacity - d.total_capacity*float(cfg.get("critical_soc",10))/100)
    return result

class Display:
    def __init__(self, monitor, config, save):
        self.m, self.root, self.cfg, self.save = monitor, monitor.root, config, save
        self.dark = bool(config.get("ui_dark", True))
        self.colors = palette(self.root, self.dark)
        self.slim = None
        self.mode = "full"
        self.drag = None
        self.metrics = ["voltage","power","runtime","current","temp","delta"]
        self.chart = None
        self.last_graph = 0

    def persist(self):
        try:
            self.save(self.cfg)
        except OSError as exc:
            self.m.last_log_error = f"Display settings: {exc}"

    def toggle_theme(self):
        self.dark = not self.dark
        self.cfg["ui_dark"] = self.dark
        self.colors = palette(self.root, self.dark)
        if self.chart is not None:
            self.draw_graph()
        if self.slim is not None:
            mode = self.mode
            self.open(mode)
        self.persist()

    def open(self, mode):
        if self.slim is not None:
            self.remember()
            self.slim.destroy()
            self.slim = None
        self.mode = mode
        self.cfg["ui_mode"] = mode
        if mode == "full":
            self.root.deiconify()
            self.root.lift()
            self.persist()
            return
        win = self.slim = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", bool(self.cfg.get("ui_topmost", True)))
        win.attributes("-alpha", max(.5,min(1.,float(self.cfg.get("ui_opacity",1)))))
        w,h = (380,76) if mode == "horizontal" else (170,330)
        pos = self.cfg.get("ui_pos_"+mode, [win.winfo_screenwidth()-w-20,40])
        x,y = max(0,min(int(pos[0]),win.winfo_screenwidth()-w)),max(0,min(int(pos[1]),win.winfo_screenheight()-h))
        win.geometry(f"{w}x{h}+{x}+{y}")
        bg,panel,fg,muted = self.colors
        self.canvas = tk.Canvas(win, bg=panel, highlightthickness=1, highlightbackground=muted)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.move)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.canvas.bind("<Double-Button-1>", lambda e: self.open("full"))
        self.canvas.bind("<Button-3>", self.menu)
        win.bind("<Escape>", lambda e: self.open("full"))
        self.root.withdraw()
        self.persist()
        self.draw_slim()

    def remember(self):
        if self.slim is not None:
            self.cfg["ui_pos_"+self.mode] = [self.slim.winfo_x(),self.slim.winfo_y()]

    def start_drag(self,e):
        self.drag = (e.x_root-self.slim.winfo_x(),e.y_root-self.slim.winfo_y())

    def move(self,e):
        if self.drag and self.slim:
            x,y = e.x_root-self.drag[0],e.y_root-self.drag[1]
            self.slim.geometry(f"{x:+d}{y:+d}")

    def end_drag(self,e):
        if not self.slim: return
        win=self.slim
        x,y=win.winfo_x(),win.winfo_y()
        right=win.winfo_screenwidth()-win.winfo_width()
        bottom=win.winfo_screenheight()-win.winfo_height()
        x=max(0,min(right,x)); y=max(0,min(bottom,y))
        if x<18: x=0
        if right-x<18: x=right
        if y<18: y=0
        if bottom-y<18: y=bottom
        win.geometry(f"+{x}+{y}")
        win.update_idletasks()
        self.remember()
        self.persist()

    def menu(self,e):
        menu=tk.Menu(self.slim,tearoff=False)
        for name,mode in (("Full dashboard","full"),("Horizontal slim","horizontal"),("Vertical slim","vertical")):
            menu.add_command(label=name,command=lambda m=mode:self.open(m))
        menu.add_command(label="Toggle always on top",command=self.topmost)
        menu.add_command(label="Opacity...",command=self.opacity)
        menu.add_command(label="Select metrics...",command=self.select_metrics)
        menu.add_command(label="Toggle theme",command=self.toggle_theme)
        menu.add_separator()
        menu.add_command(label="Exit monitoring",command=self.m.close)
        menu.tk_popup(e.x_root,e.y_root)

    def topmost(self):
        self.cfg["ui_topmost"]=not self.cfg.get("ui_topmost",True)
        self.slim.attributes("-topmost",self.cfg["ui_topmost"])
        self.persist()

    def opacity(self):
        n=simpledialog.askinteger("Opacity","50–100 percent",initialvalue=int(self.cfg.get("ui_opacity",1)*100),minvalue=50,maxvalue=100,parent=self.slim)
        if n is not None:
            self.cfg["ui_opacity"]=n/100
            self.slim.attributes("-alpha",n/100)
            self.persist()

    def select_metrics(self):
        value=simpledialog.askstring("Slim metrics","Comma-separated metrics: voltage, power, runtime, current, temp, delta, capacity, reserve",initialvalue=", ".join(self.cfg.get("ui_metrics",self.metrics)),parent=self.slim)
        if value is not None:
            allowed=set(self.metrics+["capacity","reserve"])
            self.cfg["ui_metrics"]=list(dict.fromkeys(x.strip().lower() for x in value.split(",") if x.strip().lower() in allowed))
            self.persist()
            self.draw_slim()

    def draw_slim(self):
        if self.slim is None: return
        s=snapshot(self.m,self.cfg)
        c=self.canvas
        c.delete("all")
        bg,panel,fg,muted=self.colors
        color=s["color"]; ink=fg if s["fresh"] else muted
        status=s["status"] if s["fresh"] else ("STALE / last known" if self.m.data is not None else "Connecting")
        metrics=self.cfg.get("ui_metrics",self.metrics)
        def label(x,y,text,size=10,fill=ink,anchor="w"):
            c.create_text(x,y,text=text,fill=fill,font=("Segoe UI",size),anchor=anchor)
        if self.mode=="horizontal":
            label(10,18,s["soc"],21,color)
            c.create_rectangle(10,34,75,40,fill=bg,outline="")
            c.create_rectangle(10,34,10+65*s["percent"]/100,40,fill=color,outline="")
            label(88,14,status,10,color)
            label(365,14,"↗",13,fg,"e")
            label(88,35,"  ·  ".join(s.get(k,"--") for k in metrics[:3]),11)
            label(10,58,f"{s.get('direction','')}  |  "+(f"Age {max(0,int(s['age']))}s" if s["age"] is not None else "No data")+"  |  Right-click for options",9,muted)
        else:
            label(10,15,"WATTCYCLE",10,fg)
            label(155,15,"↗",13,fg,"e")
            label(10,48,s["soc"],28,color)
            c.create_rectangle(10,75,157,83,fill=bg,outline="")
            c.create_rectangle(10,75,10+147*s["percent"]/100,83,fill=color,outline="")
            for i,k in enumerate(metrics[:7]):
                label(10,106+i*27,k.title(),9,muted)
                label(156,106+i*27,s.get(k,"--"),11,ink,"e")
            label(10,305,status,9,color)
            label(10,319,"Right-click for options",8,muted)

    def update(self):
        if self.slim is not None:
            self.draw_slim()
        if self.chart is not None and self.mode=="full" and time.monotonic()-self.last_graph>5:
            self.last_graph=time.monotonic()
            self.draw_graph()

    def build_graph(self,parent):
        frame=ctk.CTkFrame(parent,fg_color="transparent")
        frame.pack(fill="both",expand=True,padx=12,pady=8)
        bar=ctk.CTkFrame(frame,fg_color="transparent")
        bar.pack(fill="x",pady=(0,4))
        ctk.CTkLabel(bar,text="Live telemetry",font=("Segoe UI",14,"bold")).pack(side="left")
        self.metric=tk.StringVar(value="Power")
        self.period=tk.StringVar(value="60 min")
        for var,values in ((self.period,["15 min","60 min","6 hours","24 hours"]),(self.metric,["Power","Current","Voltage","SOC","Temperature"])):
            cb=ctk.CTkOptionMenu(bar,variable=var,values=values,width=110,command=lambda value:self.draw_graph(),fg_color=("#e2e8f0","#344256"),button_color=("#cbd5e1","#526174"),text_color=("#172334","#e6edf6"))
            cb.pack(side="right",padx=3)
        self.chart=tk.Canvas(frame,height=150,highlightthickness=0,bg=self.colors[1])
        self.chart.pack(fill="both",expand=True)
        self.chart.bind("<Configure>",lambda e:self.draw_graph())

    def draw_graph(self):
        import sqlite3
        from datetime import datetime,timedelta
        if self.chart is None: return
        c=self.chart; c.delete("all")
        bg,panel,fg,muted=self.colors
        c.configure(bg=panel)
        width,height=max(100,c.winfo_width()),max(90,c.winfo_height())
        col={"Power":"power","Current":"current","Voltage":"voltage","SOC":"soc","Temperature":"battery_temp"}[self.metric.get()]
        mins={"15 min":15,"60 min":60,"6 hours":360,"24 hours":1440}[self.period.get()]
        end=datetime.now(); start=end-timedelta(minutes=mins)
        try:
            with sqlite3.connect(self.db_path,timeout=.2) as conn:
                rows=conn.execute(f"SELECT timestamp,{col} FROM battery_log WHERE timestamp>=? ORDER BY timestamp", (start.isoformat(timespec="seconds"),)).fetchall()
            pts=[((datetime.fromisoformat(t)-start).total_seconds(),float(v)) for t,v in rows if v is not None and math.isfinite(float(v))]
        except (sqlite3.Error,ValueError,TypeError):
            pts=[]
        if not pts:
            c.create_text(width/2,height/2,text="No recorded telemetry in this range",fill=muted); return
        lo,hi=min(v for _,v in pts),max(v for _,v in pts)
        if hi==lo: lo-=.5; hi+=.5
        left,top,right,bottom=52,10,width-12,height-22
        for i in range(3):
            y=top+(bottom-top)*i/2
            c.create_line(left,y,right,y,fill="#344256" if self.dark else "#dbe2ea")
            c.create_text(left-5,y,text=f"{hi-(hi-lo)*i/2:.1f}",anchor="e",fill=muted)
        prior=None
        for t,v in pts:
            x=left+(right-left)*t/(mins*60); y=bottom-(bottom-top)*(v-lo)/(hi-lo)
            if prior and t-prior[0] <= max(30,float(self.cfg.get("log_interval_seconds",5))*4):
                c.create_line(prior[1],prior[2],x,y,fill="#34d399" if self.dark else "#16834b",width=2)
            else: c.create_oval(x-1,y-1,x+1,y+1,fill="#34d399" if self.dark else "#16834b",outline="")
            prior=(t,x,y)
        c.create_text(left,height-8,text=self.period.get()+" ago",anchor="w",fill=muted)
        c.create_text(right,height-8,text="Now",anchor="e",fill=muted)
