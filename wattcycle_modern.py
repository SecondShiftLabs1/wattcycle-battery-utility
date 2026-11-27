"""Modern dashboard widgets. No battery writes or BLE operations."""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

class ModernNavigation(ttk.Frame):
    def __init__(self,parent,dark):
        super().__init__(parent)
        self.body=ttk.Frame(self)
        self.body.pack(fill="both",expand=True)
        self.nav=ctk.CTkFrame(self,corner_radius=10)
        self.nav.pack(fill="x",pady=(8,0))
        self.pages={}; self.buttons={}; self.active=None; self.dark=None
        self.set_theme(dark)
    def add(self,name):
        page=ttk.Frame(self.body,padding=4)
        self.pages[name]=page
        button=ctk.CTkButton(self.nav,text=name,corner_radius=8,height=36,width=70,command=lambda:self.select(name))
        button.pack(side="left",fill="x",expand=True,padx=3,pady=5)
        self.buttons[name]=button
        if self.active is None: self.select(name)
        else: self.paint()
        return page
    def select(self,name):
        if self.active is not None: self.pages[self.active].pack_forget()
        self.active=name
        self.pages[name].pack(fill="both",expand=True)
        self.paint()
    def paint(self):
        for name,b in self.buttons.items():
            b.configure(fg_color=("#244a48" if self.dark else "#d1fae5") if name==self.active else "transparent",
                        text_color=("#8af0c5" if self.dark else "#065f46") if name==self.active else ("#aab8c9" if self.dark else "#475569"),
                        hover_color="#344256" if self.dark else "#e2e8f0")
    def set_theme(self,dark):
        if self.dark==dark:return
        self.dark=dark
        ctk.set_appearance_mode("dark" if dark else "light")
        self.nav.configure(fg_color="#1b2635" if dark else "#ffffff")
        self.paint()

class ModernOverview:
    def __init__(self,parent,m):
        self.m=m
        self.dark=None
        self.hero=ctk.CTkFrame(parent,corner_radius=16)
        self.hero.pack(fill="x",pady=(0,8))
        self.ring=tk.Canvas(self.hero,width=154,height=154,highlightthickness=0)
        self.ring.pack(side="left",padx=18,pady=12)
        info=ctk.CTkFrame(self.hero,fg_color="transparent")
        info.pack(side="left",fill="both",expand=True,padx=(0,12),pady=12)
        self.capacity=ctk.CTkLabel(info,text="--",font=("Segoe UI",22,"bold"),anchor="w")
        ctk.CTkLabel(info,text="Remaining capacity",anchor="w",text_color=("#526174","#a0aec0")).pack(fill="x")
        self.capacity.pack(fill="x")
        self.estimate_title=ctk.CTkLabel(info,text="Battery status",anchor="w",text_color=("#526174","#a0aec0"))
        self.estimate_title.pack(fill="x",pady=(8,0))
        self.estimate=ctk.CTkLabel(info,text="Waiting for data",font=("Segoe UI",25,"bold"),anchor="w")
        self.estimate.pack(fill="x")
        self.detail=ctk.CTkLabel(info,text="",anchor="w",text_color=("#526174","#a0aec0"))
        self.detail.pack(fill="x")
        # Compatibility widgets preserve the original update_gui contract.
        hidden=ttk.Frame(parent)
        m.soc_label=ttk.Label(hidden);m.capacity_label=ttk.Label(hidden)
        m.progress=ttk.Progressbar(hidden,maximum=100)
        m.primary_runtime=ttk.Label(hidden);m.reserve_runtime=ttk.Label(hidden)
        m.runtime_value=ttk.Label(hidden);m.avg_runtime_value=ttk.Label(hidden)
        m.avg5_value=ttk.Label(hidden);m.avg15_value=ttk.Label(hidden)
        m.voltage_value=ttk.Label(hidden);m.current_value=ttk.Label(hidden);m.power_value=ttk.Label(hidden)
        metrics=ttk.Frame(parent);metrics.pack(fill="x",pady=(0,8))
        self.cards=[];self.values=[];self.titles=[]
        for i,title in enumerate(("Pack voltage","Current","Power")):
            metrics.columnconfigure(i,weight=1,uniform="metrics")
            card=ctk.CTkFrame(metrics,corner_radius=12)
            card.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 4,0 if i==2 else 4))
            label=ctk.CTkLabel(card,text=title,text_color=("#526174","#a0aec0"))
            label.pack(pady=(8,0))
            val=ctk.CTkLabel(card,text="--",font=("Segoe UI",25,"bold"))
            val.pack(pady=(0,8))
            self.cards.append(card);self.values.append(val);self.titles.append(label)
        self.graph_parent=ctk.CTkFrame(parent,corner_radius=14)
        self.graph_parent.pack(fill="both",expand=True)
        self.averages=ctk.CTkLabel(parent,text="5 min: --     15 min: --",anchor="w",text_color=("#526174","#a0aec0"))
        self.averages.pack(fill="x",pady=(5,0))
        self.health=ctk.CTkLabel(parent,text="Waiting for battery status",anchor="w",wraplength=650)
        self.health.pack(fill="x")
    def refresh(self,s,dark,reserve):
        m=self.m
        panel="#1b2635" if dark else "#ffffff"
        fg="#e6edf6" if dark else "#172334"
        muted="#a0aec0" if dark else "#526174"
        if dark!=self.dark:
            self.dark=dark
            for card in [self.hero,self.graph_parent,*self.cards]:card.configure(fg_color=panel)
            self.ring.configure(bg=panel)
        color=s["color"];c=self.ring;c.delete("all")
        c.create_oval(12,12,142,142,outline="#344256" if dark else "#e2e8f0",width=10)
        percent=s["percent"]
        if percent>=100:c.create_oval(12,12,142,142,outline=color,width=10)
        elif percent>0:c.create_arc(12,12,142,142,start=90,extent=-percent*3.6,style="arc",outline=color,width=10)
        direction=s.get("direction","Idle") if s["fresh"] else "Last known" if m.data is not None else "Connecting"
        c.create_text(77,72,text=s["soc"],font=("Segoe UI",30,"bold"),fill=fg if s["fresh"] else muted)
        c.create_text(77,101,text=direction.upper(),font=("Segoe UI",9,"bold"),fill=color)
        d=m.data
        self.capacity.configure(text=m.capacity_label.cget("text"))
        if s["fresh"] and direction=="Charging":
            avg,_=m.rolling_average(300)
            rate=avg if avg is not None and avg>.1 else d.current
            minutes=int(max(0,d.total_capacity-d.remaining_capacity)/rate*60)
            h,mi=divmod(minutes,60)
            self.estimate_title.configure(text="Estimated time to full")
            self.estimate.configure(text=f"{h}h {mi:02d}m")
            self.detail.configure(text="Constant-current estimate; taper may extend charging")
        elif s["fresh"] and direction=="Discharging":
            self.estimate_title.configure(text="Estimated remaining runtime")
            self.estimate.configure(text=s["runtime"])
            self.detail.configure(text=f"Until {reserve}% reserve: {s['reserve']}")
        else:
            self.estimate_title.configure(text="Battery status")
            self.estimate.configure(text=direction)
            self.detail.configure(text="No active charge or discharge" if s["fresh"] else "Waiting for fresh telemetry")
        for i,key in enumerate(("voltage","current","power")):
            self.values[i].configure(text=s[key],text_color=fg if s["fresh"] else muted)
        self.titles[1].configure(text="Charge current" if direction=="Charging" else "Discharge current" if direction=="Discharging" else "Current")
        self.titles[2].configure(text="Input power" if direction=="Charging" else "Output power" if direction=="Discharging" else "Power")
        self.averages.configure(text=f"5 min avg: {m.avg5_value.cget('text')}     •     15 min avg: {m.avg15_value.cget('text')}")
        text=m.protection_label.cget("text") if hasattr(m,"protection_label") else "Protection status unknown"
        self.health.configure(text=text+"  |  "+m.last_alert_text if m.last_alert_text!="No active alerts" else text,text_color=color if "None" in text else ("#e5a323" if s["fresh"] else muted))
