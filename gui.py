# -*- coding: utf-8 -*-
"""מתרגם תוספי אוצריא — ממשק גרפי."""
import os, io, sys, json, threading, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import translator_core as core

# כשרצים כ-EXE ארוז, PyInstaller פורס את הנתונים ל-sys._MEIPASS
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(APP_DIR, "i18n_runtime.js")
CFG     = os.path.join(os.path.expanduser("~"), ".otzaria_translator.json")

VERSION = "1.3"

LANGS = {"אנגלית": ("en", "English"), "צרפתית": ("fr", "French"),
         "ספרדית": ("es", "Spanish"), "רוסית": ("ru", "Russian")}


def load_cfg():
    try:    return json.load(io.open(CFG, encoding="utf-8"))
    except Exception: return {}


def save_cfg(d):
    try:    io.open(CFG, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
    except Exception: pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"מתרגם תוספי אוצריא — גרסה {VERSION}")
        self.geometry("980x680")
        cfg = load_cfg()

        self.plugin_dir = tk.StringVar(value=cfg.get("plugin_dir", ""))
        self.provider   = tk.StringVar(value=cfg.get("provider", "claude"))
        self.api_key    = tk.StringVar(value=cfg.get("api_key", ""))
        self.lang_name  = tk.StringVar(value=cfg.get("lang", "אנגלית"))
        self.do_verify  = tk.BooleanVar(value=cfg.get("verify", True))
        self.model      = tk.StringVar(value=cfg.get("model", ""))
        self.strings    = {}
        self.pairs      = {}

        self._build()
        self.say(f"מתרגם תוספי אוצריא, גרסה {VERSION}")

    # ─────────────── פריסה ───────────────
    def _build(self):
        pad = dict(padx=8, pady=4)

        top = ttk.LabelFrame(self, text=" תוסף ")
        top.pack(fill="x", **pad)
        ttk.Entry(top, textvariable=self.plugin_dir).pack(
            side="right", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(top, text="בחר תיקייה…", command=self.pick).pack(
            side="right", padx=6, pady=6)

        cfgf = ttk.LabelFrame(self, text=" תרגום ")
        cfgf.pack(fill="x", **pad)
        r = ttk.Frame(cfgf); r.pack(fill="x", padx=6, pady=6)
        ttk.Label(r, text="מנוע:").pack(side="right")
        ttk.Combobox(r, textvariable=self.provider, values=["claude", "gemini"],
                     width=10, state="readonly").pack(side="right", padx=6)
        ttk.Label(r, text="שפה:").pack(side="right", padx=(14, 0))
        ttk.Combobox(r, textvariable=self.lang_name, values=list(LANGS),
                     width=10, state="readonly").pack(side="right", padx=6)
        ttk.Label(r, text="מפתח API:").pack(side="right", padx=(14, 0))
        ttk.Entry(r, textvariable=self.api_key, show="•", width=42).pack(
            side="right", padx=6)
        ttk.Checkbutton(r, text="אמת מול Google Translate",
                        variable=self.do_verify).pack(side="right", padx=14)

        r2 = ttk.Frame(cfgf); r2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(r2, text="מודל:").pack(side="right")
        self.cb_model = ttk.Combobox(r2, textvariable=self.model, width=34)
        self.cb_model.pack(side="right", padx=6)
        ttk.Button(r2, text="רענן רשימת מודלים",
                   command=self.load_models).pack(side="right", padx=6)
        ttk.Label(r2, text="(ריק = בחירה אוטומטית)",
                  foreground="#666").pack(side="right", padx=6)

        btns = ttk.Frame(self); btns.pack(fill="x", **pad)
        ttk.Button(btns, text="1 · חלץ מחרוזות", command=self.do_extract).pack(side="right", padx=4)
        ttk.Button(btns, text="2 · תרגם", command=self.do_translate).pack(side="right", padx=4)
        self.b_auto  = ttk.Button(btns, text="3 · בנה חבילה (לפי שפת אוצריא · 0.9.97)",
                                  command=lambda: self.do_build("auto"))
        self.b_auto.pack(side="right", padx=4)
        self.b_force = ttk.Button(btns, text="בנה גרסת בדיקה בשפה אחת · 0.9.96",
                                  command=lambda: self.do_build("force"))
        self.b_force.pack(side="right", padx=4)

        cols = ("he", "en", "back", "score", "fwd")
        heads = {"he": "מקור", "en": "תרגום", "back": "תרגום חוזר",
                 "score": "חוזר", "fwd": "ישיר"}
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=260, anchor="e")
        self.tree.column("score", width=60, anchor="center")
        self.tree.column("fwd", width=60, anchor="center")
        self.tree.tag_configure("suspect", background="#ffe8e8")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", self.edit_cell)

        ttk.Label(self, text="לחיצה כפולה על שורה — עריכת התרגום. שורות אדומות: "
                             "שתי בדיקות האימות נכשלו — כדאי לבדוק ידנית.",
                  foreground="#666").pack(anchor="e", padx=10)

        logf = ttk.Frame(self); logf.pack(fill="x", padx=8, pady=(4, 8))
        bar = ttk.Frame(logf); bar.pack(fill="x")
        ttk.Button(bar, text="העתק לוג", command=self.copy_log).pack(side="left", padx=2)
        ttk.Button(bar, text="נקה", command=self.clear_log).pack(side="left", padx=2)
        self.log = tk.Text(logf, height=7, wrap="word")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="x", expand=True)

    # ─────────────── עזר ───────────────
    def say(self, msg):
        self.log.insert("end", msg + "\n"); self.log.see("end"); self.update_idletasks()

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end-1c"))
        self.say("— הלוג הועתק ללוח —")

    def clear_log(self):
        self.log.delete("1.0", "end")

    def lang(self):
        return LANGS[self.lang_name.get()]

    def persist(self):
        save_cfg({"plugin_dir": self.plugin_dir.get(), "provider": self.provider.get(),
                  "api_key": self.api_key.get(), "lang": self.lang_name.get(),
                  "verify": self.do_verify.get(), "model": self.model.get()})

    def load_models(self):
        key = self.api_key.get().strip()
        if not key:
            messagebox.showwarning("", "הזן מפתח API תחילה."); return
        def job():
            self.say(f"שולף מודלים זמינים מ-{self.provider.get()}…")
            try:
                models = core.list_models(self.provider.get(), key)
            except Exception as e:
                self.say(f"שליפת המודלים נכשלה: {e}")
                messagebox.showerror("שגיאה", f"לא ניתן לשלוף מודלים:\n{e}")
                return
            if not models:
                self.say("לא נמצאו מודלים זמינים למפתח הזה."); return
            self.cb_model["values"] = models
            if not self.model.get():
                self.model.set(models[0])
            self.say(f"נמצאו {len(models)} מודלים. נבחר: {self.model.get()}")
        self.run_bg(job)

    def pick(self):
        d = filedialog.askdirectory(title="בחר תיקיית תוסף (זו שמכילה manifest.json)")
        if not d: return
        if not os.path.exists(os.path.join(d, "manifest.json")):
            messagebox.showerror("שגיאה", "לא נמצא manifest.json בתיקייה שנבחרה."); return
        self.plugin_dir.set(d); self.persist()
        m = json.loads(io.open(os.path.join(d, "manifest.json"), encoding="utf-8-sig").read())
        self.say(f"נבחר: {m.get('name')} v{m.get('version')}")

    def run_bg(self, fn):
        def wrap():
            try: fn()
            except Exception:
                self.say("שגיאה:\n" + traceback.format_exc())
        threading.Thread(target=wrap, daemon=True).start()

    def refresh_table(self, rows):
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end",
                             values=(r["he"], r["en"], r.get("back", ""),
                                     "" if r.get("score") is None else r["score"],
                                     "" if r.get("score_fwd") is None else r["score_fwd"]),
                             tags=("suspect",) if r.get("suspect") else ())

    # ─────────────── פעולות ───────────────
    def do_extract(self):
        d = self.plugin_dir.get()
        if not d: messagebox.showwarning("", "בחר תוסף תחילה."); return
        def job():
            self.say("מחלץ מחרוזות ממשק…")
            self.strings = core.extract_strings(d)
            self.say(f"נמצאו {len(self.strings)} מחרוזות.")
            self.refresh_table([{"he": s, "en": ""} for s in sorted(self.strings)])
        self.run_bg(job)

    def do_translate(self):
        if not self.strings: self.do_extract(); return
        key = self.api_key.get().strip()
        if not key: messagebox.showwarning("", "הזן מפתח API."); return
        self.persist()
        code, target = self.lang()
        def job():
            self.say(f"מתרגם ל{self.lang_name.get()} באמצעות {self.provider.get()}…")
            try:
                self.pairs = core.translate(sorted(self.strings), self.provider.get(),
                                            key, target, progress=self.say,
                                            model=self.model.get().strip() or None)
            except Exception as e:
                self.say(f"התרגום נכשל: {e}")
                messagebox.showerror(
                    "התרגום נכשל",
                    f"{e}\n\nנסה 'רענן רשימת מודלים' ובחר מודל מהרשימה.")
                return
            self.say(f"התקבלו {len(self.pairs)} תרגומים.")
            if not self.pairs:
                messagebox.showerror("", "לא התקבל אף תרגום — לא נוצרה חבילה.")
                return
            rows = [{"he": h, "en": self.pairs.get(h, "")} for h in sorted(self.strings)]
            self.refresh_table(rows)
            if self.do_verify.get():
                self.say("מאמת מול Google Translate…")
                rows = core.verify(self.pairs, progress=self.say)
                self.refresh_table(rows)
                n = sum(1 for r in rows if r.get("suspect"))
                self.say(f"אימות הושלם. {n} מחרוזות חשודות מסומנות באדום.")
        self.run_bg(job)

    def do_build(self, mode):
        d = self.plugin_dir.get()
        if not d: messagebox.showwarning("", "בחר תוסף תחילה."); return
        if not self.pairs: messagebox.showwarning("", "תרגם תחילה."); return
        code, _ = self.lang()
        m = json.loads(io.open(os.path.join(d, "manifest.json"), encoding="utf-8-sig").read())
        entry = m.get("entrypoint", "index.html")
        out = os.path.join(os.path.dirname(d.rstrip("\\/")), "פלט-תרגום")

        beta = None
        if mode == "force":
            # מציעים אוטומטית שם בשפת היעד; המשתמש רשאי לשנות
            self.say("מציע שם בשפת היעד…")
            suggested = core.suggest_name(m.get("name", ""), code, self.pairs)
            beta = tk.simpledialog.askstring(
                "שם החבילה",
                f"אוצריא אינה תומכת בשם תלוי-שפה, ולכן חבילה זו\n"
                f"נושאת שם קבוע בשפת היעד (עד {core.MAX_NAME} תווים):",
                initialvalue=suggested, parent=self)
            if not beta: return
            beta = beta.strip()
            if len(beta) > core.MAX_NAME:
                messagebox.showerror(
                    "שם ארוך מדי",
                    f"'{beta}' באורך {len(beta)} תווים.\n"
                    f"אוצריא מגבילה ל-{core.MAX_NAME} ותדחה את ההתקנה.")
                return

        def job():
            self.say("בונה חבילה…")
            dest, size = core.build_package(
                d, out, self.pairs, code, RUNTIME,
                mode=mode, beta_name=beta, progress=self.say)
            ver = core.MIN_VER_AUTO if mode == "auto" else core.MIN_VER_FORCE
            self.say(f"נוצר: {dest}  ({size//1024} KB, minAppVersion {ver})")
            messagebox.showinfo("הושלם", f"החבילה נוצרה:\n{dest}")
        self.run_bg(job)

    def edit_cell(self, _ev):
        sel = self.tree.selection()
        if not sel: return
        item = sel[0]
        he, en = self.tree.item(item, "values")[0], self.tree.item(item, "values")[1]
        new = tk.simpledialog.askstring("עריכת תרגום", he, initialvalue=en, parent=self)
        if new is None: return
        self.pairs[he] = new
        v = list(self.tree.item(item, "values")); v[1] = new
        self.tree.item(item, values=v, tags=())


if __name__ == "__main__":
    import tkinter.simpledialog
    tk.simpledialog = tkinter.simpledialog
    App().mainloop()
