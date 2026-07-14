"""
MLFF Monitoring – Desktop aplikacija
Prati status uređaja na https://ot.sdn.rs/portali/
i šalje obaveštenja na email i WhatsApp pri promeni statusa.
"""

import json
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from typing import Dict, List, Optional

from scraper import fetch_devices, Device
from notifier import (
    send_email, send_telegram,
    get_telegram_updates, build_notification_text,
)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
MONITOR_URL = "https://mlff.sdn.rs"
CHECK_INTERVAL_SEC = 60

# Uređaji koji su uvek DOWN i ne treba da se alarmiraju
EXCLUDED_HOSTNAMES = {"SCPA1046-L-UPS", "SCPA1046-L-IOL"}

DEFAULT_CONFIG = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    # lista email adresa primalaca
    "email_recipients": ["ognjen.petar.todorovic@oriontelekom.rs"],
    # telegram
    "telegram_bot_token": "",
    "telegram_chat_ids": [],   # lista string chat_id vrednosti
    "notify_email": True,
    "notify_telegram": True,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # migracija starih konfiguracija (single → list)
        if "email_recipient" in cfg and "email_recipients" not in cfg:
            cfg["email_recipients"] = [cfg.pop("email_recipient")]
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Reusable recipients list widget
# ─────────────────────────────────────────────

class EmailRecipientsWidget(ttk.Frame):
    """Lista email primalaca sa dodavanjem i uklanjanjem."""

    def __init__(self, parent, initial: List[str], **kw):
        super().__init__(parent, **kw)
        self._build(initial)

    def _build(self, initial):
        ttk.Label(self, text="Email primaoci:").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(4, 2)
        )

        self._listbox = tk.Listbox(self, height=4, width=42,
                                   bg="#313244", fg="#cdd6f4",
                                   selectbackground="#585b70",
                                   font=("Consolas", 9))
        self._listbox.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 4))

        sb = ttk.Scrollbar(self, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=2, sticky="ns")

        for addr in initial:
            self._listbox.insert("end", addr)

        self._entry_var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self._entry_var, width=36)
        entry.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        entry.bind("<Return>", lambda _: self._add())

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        ttk.Button(btn_frame, text="+ Dodaj", width=8,
                   command=self._add).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="– Ukloni", width=8,
                   command=self._remove).pack(side="left", padx=2)

    def _add(self):
        val = self._entry_var.get().strip()
        if val and "@" in val:
            self._listbox.insert("end", val)
            self._entry_var.set("")
        else:
            messagebox.showwarning("Greška", "Upiši ispravnu email adresu.", parent=self)

    def _remove(self):
        sel = self._listbox.curselection()
        if sel:
            self._listbox.delete(sel[0])

    def get(self) -> List[str]:
        return list(self._listbox.get(0, "end"))


class TelegramRecipientsWidget(ttk.Frame):
    """Bot token + lista chat_id primalaca sa auto-detekcijom."""

    def __init__(self, parent, bot_token: str, chat_ids: List[str], **kw):
        super().__init__(parent, **kw)
        self._build(bot_token, chat_ids)

    def _build(self, bot_token: str, chat_ids: List[str]):
        pad = {"padx": 2, "pady": 2}

        ttk.Label(self, text="Bot token:").grid(row=0, column=0, sticky="e", **pad)
        self._token_var = tk.StringVar(value=bot_token)
        ttk.Entry(self, textvariable=self._token_var, width=46).grid(
            row=0, column=1, columnspan=3, sticky="w", **pad
        )

        ttk.Label(self, text="Chat ID primaoci:").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(6, 2)
        )

        self._listbox = tk.Listbox(self, height=3, width=42,
                                   bg="#313244", fg="#cdd6f4",
                                   selectbackground="#585b70",
                                   font=("Consolas", 9))
        self._listbox.grid(row=2, column=0, columnspan=3, sticky="ew", padx=(0, 4))
        sb = ttk.Scrollbar(self, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=sb.set)
        sb.grid(row=2, column=3, sticky="ns")

        for cid in chat_ids:
            self._listbox.insert("end", cid)

        inp = ttk.Frame(self)
        inp.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        self._id_var = tk.StringVar()
        ttk.Entry(inp, textvariable=self._id_var, width=20).pack(side="left", padx=(0, 4))
        ttk.Button(inp, text="+ Dodaj ID", command=self._add).pack(side="left", padx=2)
        ttk.Button(inp, text="– Ukloni", command=self._remove).pack(side="left", padx=2)
        ttk.Button(inp, text="Dohvati moj Chat ID",
                   command=self._fetch_chat_id).pack(side="left", padx=(8, 2))

        self._info_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._info_var,
                  font=("Segoe UI", 8), foreground="#a6e3a1",
                  wraplength=420).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(2, 0)
        )

    def _add(self):
        val = self._id_var.get().strip()
        if val:
            if val not in self._listbox.get(0, "end"):
                self._listbox.insert("end", val)
            self._id_var.set("")

    def _remove(self):
        sel = self._listbox.curselection()
        if sel:
            self._listbox.delete(sel[0])

    def _fetch_chat_id(self):
        token = self._token_var.get().strip()
        if not token:
            self._info_var.set("Upiši bot token prvo.")
            return
        self._info_var.set("Tražim Chat ID...")

        def run():
            try:
                updates = get_telegram_updates(token)
                if not updates:
                    self.after(0, lambda: self._info_var.set(
                        "Nema poruka. Posalji /start svom botu pa pokusaj ponovo."
                    ))
                    return
                found = []
                for u in updates:
                    msg = u.get("message") or u.get("channel_post") or {}
                    chat = msg.get("chat", {})
                    cid = str(chat.get("id", ""))
                    if cid and cid not in found:
                        found.append(cid)
                        self.after(0, lambda c=cid: self._listbox.insert("end", c)
                                   if c not in self._listbox.get(0, "end") else None)
                self.after(0, lambda: self._info_var.set(
                    f"Pronadjeno {len(found)} chat ID(a): {', '.join(found)}"
                ))
            except Exception as e:
                self.after(0, lambda: self._info_var.set(f"Greska: {e}"))

        threading.Thread(target=run, daemon=True).start()

    def get_token(self) -> str:
        return self._token_var.get().strip()

    def get_chat_ids(self) -> List[str]:
        return list(self._listbox.get(0, "end"))


# ─────────────────────────────────────────────
# Settings Dialog
# ─────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, config: dict, on_save):
        super().__init__(parent)
        self.title("Podesavanja")
        self.resizable(False, False)
        self.grab_set()
        self._config = dict(config)
        self._on_save = on_save
        self._build()
        self.transient(parent)

    def _build(self):
        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(self, padding=12)
        f.grid(sticky="nsew")

        row = 0

        # ── SMTP ──
        ttk.Label(f, text="── Email (SMTP) ──",
                  font=("Segoe UI", 9, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1

        smtp_fields = [
            ("smtp_host", "SMTP server:"),
            ("smtp_port", "SMTP port:"),
            ("smtp_user", "Email posiljalac:"),
            ("smtp_password", "Lozinka (App Password):"),
        ]
        self._vars: Dict[str, tk.Variable] = {}
        for key, label in smtp_fields:
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="e", **pad)
            var = tk.StringVar(value=str(self._config.get(key, "")))
            self._vars[key] = var
            show = "*" if "password" in key else ""
            ttk.Entry(f, textvariable=var, width=38, show=show).grid(
                row=row, column=1, sticky="w", **pad
            )
            row += 1

        # ── Email primaoci ──
        self._email_widget = EmailRecipientsWidget(
            f, self._config.get("email_recipients", [])
        )
        self._email_widget.grid(row=row, column=0, columnspan=2,
                                sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=6
        )
        row += 1

        # ── Telegram primaoci ──
        ttk.Label(f, text="── Telegram Bot ──",
                  font=("Segoe UI", 9, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1

        self._tg_widget = TelegramRecipientsWidget(
            f,
            bot_token=self._config.get("telegram_bot_token", ""),
            chat_ids=self._config.get("telegram_chat_ids", []),
        )
        self._tg_widget.grid(row=row, column=0, columnspan=2,
                             sticky="ew", padx=8, pady=4)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=6
        )
        row += 1

        # ── Notifikacije on/off ──
        self._vars["notify_email"] = tk.BooleanVar(
            value=bool(self._config.get("notify_email", True))
        )
        self._vars["notify_telegram"] = tk.BooleanVar(
            value=bool(self._config.get("notify_telegram", True))
        )
        ttk.Checkbutton(f, text="Slanje email obavestenja",
                        variable=self._vars["notify_email"]).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1
        ttk.Checkbutton(f, text="Slanje Telegram obavestenja",
                        variable=self._vars["notify_telegram"]).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1

        # ── Dugmad ──
        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="Sacuvaj", command=self._save).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Testiraj slanje",
                   command=self._test_send).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Otkazi", command=self.destroy).pack(
            side="left", padx=4
        )
        row += 1

        self._test_status_var = tk.StringVar(value="")
        self._lbl_test = ttk.Label(
            f, textvariable=self._test_status_var,
            font=("Segoe UI", 8), foreground="#a6e3a1",
            wraplength=460, justify="left"
        )
        self._lbl_test.grid(row=row, column=0, columnspan=2,
                            sticky="w", padx=8, pady=(0, 4))

    def _collect(self) -> dict:
        cfg = dict(self._config)
        for key, var in self._vars.items():
            val = var.get()
            if key == "smtp_port":
                try:
                    val = int(val)
                except ValueError:
                    val = 587
            cfg[key] = val
        cfg["email_recipients"] = self._email_widget.get()
        cfg["telegram_bot_token"] = self._tg_widget.get_token()
        cfg["telegram_chat_ids"] = self._tg_widget.get_chat_ids()
        return cfg

    def _save(self):
        cfg = self._collect()
        save_config(cfg)
        self._config = cfg
        self._on_save(cfg)
        self.destroy()

    def _test_send(self):
        cfg = self._collect()
        self._test_status_var.set("Saljem test poruke...")
        self._lbl_test.config(foreground="#f9e2af")

        def run():
            results = []

            # Test email – svim primaocima
            recipients = cfg.get("email_recipients", [])
            if cfg.get("smtp_user") and cfg.get("smtp_password") and recipients:
                for addr in recipients:
                    try:
                        send_email(
                            cfg["smtp_host"], int(cfg["smtp_port"]),
                            cfg["smtp_user"], cfg["smtp_password"],
                            addr,
                            subject="MLFF Monitor – test email",
                            body=(
                                "Ovo je test poruka iz MLFF Monitoring aplikacije.\n"
                                "Email notifikacije rade ispravno."
                            ),
                        )
                        results.append(f"Email OK -> {addr}")
                    except Exception as e:
                        results.append(f"Email GRESKA ({addr}): {e}")
            else:
                results.append("Email: nije konfigurisan")

            # Test Telegram – svim chat_id primaocima
            tg_token = cfg.get("telegram_bot_token", "")
            tg_ids = cfg.get("telegram_chat_ids", [])
            if tg_token and tg_ids:
                for cid in tg_ids:
                    try:
                        send_telegram(
                            tg_token, cid,
                            "MLFF Monitor – test poruka. Telegram notifikacije rade ispravno.",
                        )
                        results.append(f"Telegram OK -> {cid}")
                    except Exception as e:
                        results.append(f"Telegram GRESKA ({cid}): {e}")
            elif not tg_token:
                results.append("Telegram: nije upisан bot token")
            else:
                results.append("Telegram: nema primalaca (dodaj Chat ID)")

            summary = "\n".join(results)
            all_ok = all("GRESKA" not in r and "nije konfigurisan" not in r
                         and "nema API" not in r and "nema primalaca" not in r
                         for r in results)
            color = "#a6e3a1" if all_ok else "#f38ba8"
            self.after(0, lambda: self._test_status_var.set(summary))
            self.after(0, lambda: self._lbl_test.config(foreground=color))

        threading.Thread(target=run, daemon=True).start()


# ─────────────────────────────────────────────
# Main Application Window
# ─────────────────────────────────────────────

class MLFFMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MLFF Monitoring – Obilaznica Beograd")
        self.geometry("1050x660")
        self.minsize(900, 560)
        self.configure(bg="#1e1e2e")

        self._config = load_config()
        self._monitoring = False
        self._timer: Optional[threading.Timer] = None
        self._last_statuses: Dict[str, str] = {}
        self._lock = threading.Lock()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Construction ──────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        bg = "#1e1e2e"
        fg = "#cdd6f4"
        row_bg = "#313244"
        sel_bg = "#585b70"
        style.configure(".", background=bg, foreground=fg, font=("Segoe UI", 9))
        style.configure("Treeview", background=row_bg, foreground=fg,
                        fieldbackground=row_bg, rowheight=22)
        style.configure("Treeview.Heading", background="#45475a", foreground=fg,
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", sel_bg)])
        style.configure("TButton", padding=4)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TCheckbutton", background=bg, foreground=fg)

        # ── Top bar ──
        top = ttk.Frame(self, padding=(10, 6))
        top.pack(fill="x")

        self._lbl_up = tk.Label(
            top, text="UP: –", font=("Segoe UI", 22, "bold"),
            fg="#a6e3a1", bg=bg, width=10, anchor="center"
        )
        self._lbl_up.pack(side="left", padx=(0, 4))

        self._lbl_down = tk.Label(
            top, text="DOWN: –", font=("Segoe UI", 22, "bold"),
            fg="#f38ba8", bg=bg, width=12, anchor="center"
        )
        self._lbl_down.pack(side="left", padx=4)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10, pady=4)

        self._btn_toggle = ttk.Button(top, text="▶  Pokreni monitoring",
                                      command=self._toggle_monitoring)
        self._btn_toggle.pack(side="left", padx=4)

        ttk.Button(top, text="⟳  Proveri odmah",
                   command=self._check_now).pack(side="left", padx=4)
        ttk.Button(top, text="⚙  Podesavanja",
                   command=self._open_settings).pack(side="left", padx=4)

        self._status_var = tk.StringVar(value="Nije pokrenuto.")
        ttk.Label(top, textvariable=self._status_var,
                  font=("Segoe UI", 9), foreground="#a6adc8").pack(
            side="right", padx=8
        )

        # ── Device table ──
        tbl_frame = ttk.Frame(self)
        tbl_frame.pack(fill="both", expand=True, padx=10, pady=(4, 0))

        cols = ("portal_id", "hostname", "ip", "status", "duration", "last_change")
        self._tree = ttk.Treeview(tbl_frame, columns=cols, show="headings",
                                  selectmode="browse")
        headers = {
            "portal_id": ("Portal ID", 70),
            "hostname": ("Hostname", 200),
            "ip": ("IP adresa", 120),
            "status": ("Status", 80),
            "duration": ("Trajanje", 160),
            "last_change": ("Poslednja promena", 160),
        }
        for col, (label, width) in headers.items():
            self._tree.heading(col, text=label)
            self._tree.column(col, width=width, minwidth=50)

        self._tree.tag_configure("up", foreground="#a6e3a1")
        self._tree.tag_configure("down", foreground="#f38ba8")
        self._tree.tag_configure("excluded", foreground="#6c7086")

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # ── Log ──
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="x", padx=10, pady=(4, 6))

        ttk.Label(log_frame, text="Log promena:",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self._log = scrolledtext.ScrolledText(
            log_frame, height=8, font=("Consolas", 8),
            bg="#181825", fg="#cdd6f4", insertbackground="#cdd6f4",
            state="disabled", wrap="word"
        )
        self._log.pack(fill="x")

    # ── Monitoring logic ─────────────────────

    def _toggle_monitoring(self):
        if self._monitoring:
            self._stop_monitoring()
        else:
            self._start_monitoring()

    def _start_monitoring(self):
        self._monitoring = True
        self._btn_toggle.config(text="⏹  Zaustavi monitoring")
        self._log_line("Monitoring pokrenut.")
        self._schedule_next()

    def _stop_monitoring(self):
        self._monitoring = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._btn_toggle.config(text="▶  Pokreni monitoring")
        self._status_var.set("Zaustavljeno.")
        self._log_line("Monitoring zaustavljen.")

    def _schedule_next(self):
        if not self._monitoring:
            return
        self._do_check()
        if self._monitoring:
            self._timer = threading.Timer(CHECK_INTERVAL_SEC, self._schedule_next)
            self._timer.daemon = True
            self._timer.start()

    def _check_now(self):
        threading.Thread(target=self._do_check, daemon=True).start()

    def _do_check(self):
        now = datetime.now().strftime("%H:%M:%S")
        self._set_status(f"Proveravam... [{now}]")
        try:
            devices = fetch_devices(MONITOR_URL)
        except Exception as e:
            self._log_line(f"[{now}] Greska pri dohvatanju: {e}")
            self._set_status(f"Greska: {e}")
            return

        with self._lock:
            self._process_devices(devices, now)

    def _process_devices(self, devices, now: str):
        active = [d for d in devices if d.hostname not in EXCLUDED_HOSTNAMES]
        all_down = [d for d in active if not d.is_up]
        up_count = sum(1 for d in active if d.is_up)

        changed: List[Device] = []
        new_statuses: Dict[str, str] = {}

        for d in active:
            new_statuses[d.key] = d.status
            old = self._last_statuses.get(d.key)
            if old is not None and old.upper() != d.status.upper():
                changed.append(d)

        first_run = not self._last_statuses
        self._last_statuses = new_statuses

        self.after(0, lambda: self._update_table(devices, up_count, len(all_down)))

        if changed and not first_run:
            self._log_line(
                f"[{now}] Promena statusa: "
                + ", ".join(f"{d.hostname} -> {d.status}" for d in changed)
            )
            self._send_notifications(changed, all_down, up_count)
        else:
            self._set_status(
                f"OK  ·  UP: {up_count}  DOWN: {len(all_down)}"
                f"  ·  Sledeca provera za {CHECK_INTERVAL_SEC}s  [{now}]"
            )

    def _update_table(self, devices, up_count: int, down_count: int):
        self._lbl_up.config(text=f"UP: {up_count}")
        self._lbl_down.config(text=f"DOWN: {down_count}")
        self._tree.delete(*self._tree.get_children())
        for d in devices:
            if d.hostname in EXCLUDED_HOSTNAMES:
                tag = "excluded"
            elif d.is_up:
                tag = "up"
            else:
                tag = "down"
            self._tree.insert(
                "", "end",
                values=(d.portal_id, d.hostname, d.ip, d.status,
                        d.duration, d.last_change),
                tags=(tag,),
            )

    # ── Notifications ────────────────────────

    def _send_notifications(self, changed, all_down, up_count: int):
        cfg = self._config
        subject = (
            f"MLFF ALARM – {len([d for d in changed if not d.is_up])} uredjaj(a) DOWN"
            if any(not d.is_up for d in changed)
            else "MLFF – Uredjaj(i) ponovo UP"
        )
        body = build_notification_text(changed, all_down, up_count)

        if cfg.get("notify_email") and cfg.get("smtp_user") and cfg.get("smtp_password"):
            for addr in cfg.get("email_recipients", []):
                try:
                    send_email(
                        cfg["smtp_host"], int(cfg["smtp_port"]),
                        cfg["smtp_user"], cfg["smtp_password"],
                        addr, subject, body,
                    )
                    self._log_line(f"  Email -> {addr}")
                except Exception as e:
                    self._log_line(f"  Email GRESKA ({addr}): {e}")
        elif cfg.get("notify_email"):
            self._log_line("  Email nije konfigurisan (otvori Podesavanja)")

        if cfg.get("notify_telegram"):
            tg_token = cfg.get("telegram_bot_token", "")
            for cid in cfg.get("telegram_chat_ids", []):
                if not tg_token:
                    self._log_line("  Telegram: nije upisан bot token")
                    break
                try:
                    send_telegram(tg_token, cid, body)
                    self._log_line(f"  Telegram -> {cid}")
                except Exception as e:
                    self._log_line(f"  Telegram GRESKA ({cid}): {e}")

        now = datetime.now().strftime("%H:%M:%S")
        self._set_status(
            f"ALARM [{now}]  ·  Promena: {len(changed)}  ·  DOWN: {len(all_down)}"
        )

    # ── Helpers ──────────────────────────────

    def _log_line(self, text: str):
        def _do():
            self._log.config(state="normal")
            self._log.insert("end", text + "\n")
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _set_status(self, text: str):
        self.after(0, lambda: self._status_var.set(text))

    def _open_settings(self):
        SettingsDialog(self, self._config, self._on_config_updated)

    def _on_config_updated(self, new_cfg: dict):
        self._config = new_cfg
        self._log_line("Podesavanja sacuvana.")

    def _on_close(self):
        self._stop_monitoring()
        self.destroy()


# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = MLFFMonitorApp()
    app.mainloop()
