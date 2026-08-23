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

VERSION = "1.4"

LANGS = {"אנגלית": ("en", "English"), "צרפתית": ("fr", "French"),
         "ספרדית": ("es", "Spanish"), "רוסית": ("ru", "Russian")}


def L(text):
    """תווית עברית עם נקודתיים.

    tkinter על Windows אינו מיישם את אלגוריתם ה-BiDi של יוניקוד:
    סימן ניטרלי בקצה מחרוזת עברית — נקודתיים, שלוש נקודות, סוגריים
    — נדחף לצד הלא נכון, ו"מנוע:" מוצג כ-":מנוע". אין הגדרה שמתקנת
    זאת, ולכן הנקודתיים נכתבות בתחילת המחרוזת הלוגית וכך נראות
    בסופה הוויזואלי. באותו נימוק הוסרו "…" וסוגריים משאר התוויות.
    """
    return ":" + text


def load_cfg():
    try:    return json.load(io.open(CFG, encoding="utf-8"))
    except Exception: return {}


def save_cfg(d):
    try:    io.open(CFG, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
    except Exception: pass


class BuildDialog(tk.Toplevel):
    """בחירת גרסת המינימום — ההחלטה היחידה שהמשתמש צריך לקבל.

    השם בשפת היעד נשאל כאן ולא בדיאלוג נפרד: הוא נדרש רק לחבילה
    הנעולה לשפה, ואוצריא מגבילה אותו ל-14 תווים.
    """

    def __init__(self, parent, n_pairs, suspect, lang, he_name, pairs):
        super().__init__(parent)
        self.title("יצירת החבילה")
        self.resizable(False, False)
        self.transient(parent)
        self.modes, self.name = None, None
        self.mode_var = tk.StringVar(value="force")

        head = f"התרגום הושלם — {n_pairs} מחרוזות"
        if suspect:
            head += f", {suspect} חשודות ומסומנות באדום"
        ttk.Label(self, text=head, font=("", 10, "bold")).pack(
            anchor="e", padx=14, pady=(12, 2))
        ttk.Label(self, text="?לאיזו גרסת אוצריא לבנות",
                  foreground="#555").pack(anchor="e", padx=14, pady=(0, 8))

        opts = [
            ("force", f"נעול ל{lang.upper()} · עובד מגרסה {core.MIN_VER_FORCE}",
             "הממשק תמיד בשפת היעד, ללא תלות באוצריא · מומלץ לבדיקה"),
            ("auto", f"לפי שפת אוצריא · דורש {core.MIN_VER_AUTO} ומעלה",
             "הממשק נקבע לפי שפת אוצריא · שדה השפה קיים רק מגרסה זו"),
            ("both", "שתי החבילות", "נוצרות שתיהן, זו לצד זו"),
        ]
        for val, title, why in opts:
            f = ttk.Frame(self); f.pack(fill="x", padx=14, pady=2)
            ttk.Radiobutton(f, text=title, value=val, variable=self.mode_var,
                            command=self._sync).pack(anchor="e")
            ttk.Label(f, text=why, foreground="#777").pack(anchor="e", padx=(0, 22))

        self.nf = ttk.Frame(self); self.nf.pack(fill="x", padx=14, pady=(10, 2))
        ttk.Label(self.nf,
                  text=f"שם החבילה הנעולה — עד {core.MAX_NAME} תווים"
                  ).pack(anchor="e")
        self.name_var = tk.StringVar(value=core.suggest_name(he_name, lang, pairs))
        ttk.Entry(self.nf, textvariable=self.name_var, width=24,
                  justify="right").pack(anchor="e", pady=2)
        ttk.Label(self.nf, text="אוצריא אינה תומכת בשם תלוי-שפה, ולכן הוא קבוע",
                  foreground="#777").pack(anchor="e")

        bar = ttk.Frame(self); bar.pack(fill="x", padx=14, pady=12)
        ttk.Button(bar, text="צור חבילה", command=self.ok).pack(side="right")
        ttk.Button(bar, text="עצור לעריכה", command=self.destroy).pack(side="right", padx=6)

        self._sync()
        self.grab_set()
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + 120
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _sync(self):
        # שם החבילה נחוץ רק כשנבנית חבילה נעולה לשפה
        needs = self.mode_var.get() in ("force", "both")
        for w in self.nf.winfo_children():
            try: w.configure(state="normal" if needs else "disabled")
            except tk.TclError: pass

    def ok(self):
        mode = self.mode_var.get()
        name = self.name_var.get().strip()
        if mode in ("force", "both"):
            if not name:
                messagebox.showerror("", "הזן שם לחבילה.", parent=self); return
            if len(name) > core.MAX_NAME:
                messagebox.showerror(
                    "שם ארוך מדי",
                    f"'{name}' באורך {len(name)} תווים.\n"
                    f"אוצריא מגבילה ל-{core.MAX_NAME} ותדחה את ההתקנה.",
                    parent=self)
                return
        self.modes = ["force", "auto"] if mode == "both" else [mode]
        self.name = name
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"מתרגם תוספי אוצריא — גרסה {VERSION}")
        self.geometry("1080x720")
        self.minsize(940, 600)
        cfg = load_cfg()

        # נתיב שנשמר מריצה קודמת עשוי להיות תיקיית פריסה זמנית שנמחקה
        prev = cfg.get("plugin_dir", "")
        if prev and not os.path.exists(os.path.join(prev, "manifest.json")):
            prev = ""
        self.plugin_dir = tk.StringVar(value=prev)
        self.out_root   = os.path.dirname(prev.rstrip("\\/")) if prev else ""
        self.provider   = tk.StringVar(value=cfg.get("provider", "claude"))
        self.api_key    = tk.StringVar(value=cfg.get("api_key", ""))
        self.lang_name  = tk.StringVar(value=cfg.get("lang", "אנגלית"))
        self.do_verify  = tk.BooleanVar(value=cfg.get("verify", True))
        self.model      = tk.StringVar(value=cfg.get("model", ""))
        self.scope      = tk.StringVar(value=cfg.get("scope", core.SCOPE_UI))
        self.strings    = {}
        self.pairs      = {}
        self.closing    = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.say(f"מתרגם תוספי אוצריא, גרסה {VERSION}")

    def on_close(self):
        """סגירה מלאה. עובדי הרקע עשויים להיות תקועים בהמתנה לרשת
        (תרגום או אימות), ולכן חלון שנסגר לא מבטיח שהתהליך מסתיים.
        לאחר פירוק החלון יוצאים מיד, בלי להמתין להם."""
        self.closing = True
        try:
            self.destroy()
        except Exception:
            pass
        _flush_std()
        os._exit(0)

    # ─────────────── פריסה ───────────────
    def _build(self):
        pad = dict(padx=8, pady=4)

        #  סדר האריזה הוא סדר הקריאה: הכפתורים נארזים ראשונים ולכן
        #  יושבים בקצה הימני, ושדה הנתיב נמשך משם שמאלה
        top = ttk.LabelFrame(self, text=" תוסף ")
        top.pack(fill="x", **pad)
        ttk.Button(top, text="בחר קובץ תוסף", command=self.pick_file).pack(
            side="right", padx=(6, 8), pady=6)
        ttk.Button(top, text="או תיקיית מקור", command=self.pick).pack(
            side="right", padx=0, pady=6)
        ttk.Entry(top, textvariable=self.plugin_dir).pack(
            side="right", fill="x", expand=True, padx=8, pady=6)

        cfgf = ttk.LabelFrame(self, text=" תרגום ")
        cfgf.pack(fill="x", **pad)
        r = ttk.Frame(cfgf); r.pack(fill="x", padx=6, pady=6)
        ttk.Label(r, text=L("מנוע")).pack(side="right")
        ttk.Combobox(r, textvariable=self.provider, values=["claude", "gemini"],
                     width=10, state="readonly").pack(side="right", padx=6)
        ttk.Label(r, text=L("שפה")).pack(side="right", padx=(14, 0))
        ttk.Combobox(r, textvariable=self.lang_name, values=list(LANGS),
                     width=10, state="readonly").pack(side="right", padx=6)
        ttk.Label(r, text=L("מפתח API")).pack(side="right", padx=(14, 0))
        ttk.Entry(r, textvariable=self.api_key, show="•", width=42,
                  justify="left").pack(side="right", padx=6)
        ttk.Checkbutton(r, text="אמת מול Google Translate",
                        variable=self.do_verify).pack(side="right", padx=14)

        #  שורת ההיקף נשמרת קצרה, וההסבר יורד לשורה נפרדת: עם
        #  תוויות ארוכות השורה נחתכה בקצה השמאלי של החלון
        r3 = ttk.Frame(cfgf); r3.pack(fill="x", padx=6, pady=(0, 0))
        ttk.Label(r3, text=L("מה לתרגם")).pack(side="right")
        ttk.Radiobutton(r3, text="ממשק בלבד", value=core.SCOPE_UI,
                        variable=self.scope).pack(side="right", padx=6)
        ttk.Radiobutton(r3, text="ממשק ותוכן", value=core.SCOPE_CONTENT,
                        variable=self.scope).pack(side="right", padx=6)
        ttk.Label(cfgf, text="ממשק — כפתורים, תוויות והודעות · "
                             "תוכן — גם הביוגרפיות והטקסטים, פי עשרות מחרוזות",
                  foreground="#666").pack(anchor="e", padx=12, pady=(0, 4))

        r2 = ttk.Frame(cfgf); r2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(r2, text=L("מודל")).pack(side="right")
        self.cb_model = ttk.Combobox(r2, textvariable=self.model, width=34,
                                     justify="left")
        self.cb_model.pack(side="right", padx=6)
        ttk.Button(r2, text="רענן רשימת מודלים",
                   command=self.load_models).pack(side="right", padx=6)
        ttk.Label(r2, text="ריק — בחירה אוטומטית",
                  foreground="#666").pack(side="right", padx=6)

        #  כפתור אחד לכל התהליך. השלבים (חילוץ, תרגום, אימות) הם
        #  פירוט פנימי ולא החלטה של המשתמש; ההחלטה היחידה שנותרה —
        #  לאיזו גרסת אוצריא לבנות — נשאלת בסוף, כשהיא רלוונטית.
        btns = ttk.Frame(self); btns.pack(fill="x", **pad)
        self.b_go = ttk.Button(btns, text="תרגם תוסף", command=self.go)
        self.b_go.pack(side="right", padx=4)
        self.status = ttk.Label(btns, text="", foreground="#666")
        self.status.pack(side="right", padx=10)

        #  סדר העמודות הפוך: Tk מסדר עמודות משמאל לימין, ולכן כדי
        #  ש"מקור" יהיה העמודה הימנית — הראשונה בקריאה עברית —
        #  היא צריכה להיות האחרונה ברשימה
        cols = self.cols = ("fwd", "score", "back", "en", "he")
        heads = {"he": "מקור", "en": "תרגום", "back": "תרגום חוזר",
                 "score": "חוזר", "fwd": "ישיר"}
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for c in cols:
            self.tree.heading(c, text=heads[c], anchor="e")
            self.tree.column(c, width=260, anchor="e")
        self.tree.column("score", width=60, anchor="center")
        self.tree.column("fwd", width=60, anchor="center")
        self.tree.tag_configure("suspect", background="#ffe8e8")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", self.edit_cell)

        ttk.Label(self, text="לחיצה כפולה על שורה פותחת עריכה · "
                             "שורה אדומה — שתי בדיקות האימות נכשלו, כדאי לבדוק",
                  foreground="#666").pack(anchor="e", padx=10)

        logf = ttk.Frame(self); logf.pack(fill="x", padx=8, pady=(4, 8))
        bar = ttk.Frame(logf); bar.pack(fill="x")
        ttk.Button(bar, text="העתק לוג", command=self.copy_log).pack(side="right", padx=2)
        ttk.Button(bar, text="נקה", command=self.clear_log).pack(side="right", padx=2)
        self.log = tk.Text(logf, height=7, wrap="word")
        #  Text אינו יודע RTL מעצמו; יישור לימין דרך tag הוא הדרך
        #  היחידה, והפס נשאר בצד שמאל כמו בטקסט עברי
        self.log.tag_configure("rtl", justify="right")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="left", fill="y")
        self.log.pack(side="right", fill="x", expand=True)

    # ─────────────── עזר ───────────────
    def say(self, msg):
        # עובד רקע עלול לדווח אחרי שהחלון נסגר — כתיבה ל-widget מפורק
        # זורקת TclError ומזהמת את הפלט
        if self.closing: return
        try:
            self.log.insert("end", msg + "\n", "rtl"); self.log.see("end")
            self.update_idletasks()
        except (tk.TclError, RuntimeError):
            # RuntimeError: הודעה מעובד רקע אחרי שלולאת האירועים נעצרה
            pass

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
                  "verify": self.do_verify.get(), "model": self.model.get(),
                  "scope": self.scope.get()})

    def load_models(self):
        key = self.api_key.get().strip()
        if not key:
            messagebox.showwarning("", "הזן מפתח API תחילה."); return
        def job():
            self.say(f"שולף מודלים זמינים מ-{self.provider.get()}")
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

    def pick_file(self):
        """בחירת קובץ .otzplugin — הצורה שבה תוסף מופץ בפועל."""
        p = filedialog.askopenfilename(
            title="בחר קובץ תוסף",
            filetypes=[("תוסף אוצריא", "*.otzplugin"), ("ZIP", "*.zip"),
                       ("כל הקבצים", "*.*")])
        if not p: return
        try:
            d = core.unpack_plugin(p)
        except Exception as e:
            messagebox.showerror("שגיאה", f"לא ניתן לפרוס את החבילה:\n{e}"); return
        # הפלט נשמר ליד הקובץ שהמשתמש בחר, לא ליד תיקיית הפריסה הזמנית
        self.out_root = os.path.dirname(os.path.abspath(p))
        self.say(f"נפרס: {os.path.basename(p)}")
        self._accept(d)

    def pick(self):
        d = filedialog.askdirectory(title="בחר תיקיית תוסף (זו שמכילה manifest.json)")
        if not d: return
        if not os.path.exists(os.path.join(d, "manifest.json")):
            messagebox.showerror(
                "שגיאה",
                "לא נמצא manifest.json בתיקייה שנבחרה.\n\n"
                "אם יש לך קובץ .otzplugin — השתמש בכפתור 'בחר קובץ תוסף…'.")
            return
        self.out_root = os.path.dirname(d.rstrip("\\/"))
        self._accept(d)

    def _accept(self, d):
        self.plugin_dir.set(d)
        self.strings, self.pairs = {}, {}     # מקור חדש — נתוני התרגום הקודמים אינם רלוונטיים
        self.tree.delete(*self.tree.get_children())
        self.persist()
        m = json.loads(io.open(os.path.join(d, "manifest.json"), encoding="utf-8-sig").read())
        self.say(f"נבחר: {m.get('name')} v{m.get('version')}")

    def run_bg(self, fn):
        def wrap():
            try: fn()
            except Exception:
                self.say("שגיאה:\n" + traceback.format_exc())
        threading.Thread(target=wrap, daemon=True).start()

    def refresh_table(self, rows):
        if self.closing: return
        try:
            self._fill_table(rows)
        except (tk.TclError, RuntimeError):
            pass

    def _fill_table(self, rows):
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            #  הערכים נבנים לפי self.cols ולא בסדר קבוע: סדר העמודות
            #  הפוך לצורך RTL, וטופל קבוע היה משבץ את המקור העברי
            #  בעמודת הציונים
            cell = {"he": r["he"], "en": r["en"], "back": r.get("back", ""),
                    "score": "" if r.get("score") is None else r["score"],
                    "fwd": "" if r.get("score_fwd") is None else r["score_fwd"]}
            self.tree.insert("", "end",
                             values=tuple(cell[c] for c in self.cols),
                             tags=("suspect",) if r.get("suspect") else ())

    # ─────────────── התהליך ───────────────
    def busy(self, on, text=""):
        """נועל את הכפתור בזמן עבודה — הרצה כפולה במקביל תשבש את
        המצב המשותף (strings/pairs) ותייצר חבילה חלקית."""
        if self.closing: return
        try:
            self.b_go.configure(state=("disabled" if on else "normal"))
            self.status.configure(text=text)
        except (tk.TclError, RuntimeError):
            # RuntimeError: הודעה מעובד רקע אחרי שלולאת האירועים נעצרה
            pass

    def go(self):
        """כפתור אחד: חילוץ ← תרגום ← אימות ← בחירת גרסה ← בנייה."""
        d = self.plugin_dir.get()
        if not d:
            messagebox.showwarning("", "בחר תוסף תחילה."); return
        key = self.api_key.get().strip()
        if not key:
            messagebox.showwarning("", "הזן מפתח API."); return
        self.persist()
        code, target = self.lang()
        self.busy(True, "עובד")

        def job():
            try:
                scope = self.scope.get()
                self.say("מחלץ מחרוזות ממשק" if scope == core.SCOPE_UI
                         else "מחלץ מחרוזות ממשק ותוכן")
                self.strings = core.extract_strings(d, scope)
                self.say(f"נמצאו {len(self.strings)} מחרוזות.")
                if not self.strings:
                    self.say("לא נמצאו מחרוזות לתרגום.")
                    messagebox.showwarning("", "לא נמצאו מחרוזות בתוסף הזה.")
                    return
                #  תרגום תוכן הוא בקנה מידה אחר לגמרי — עשרות אלפי
                #  מחרוזות עלולות לקחת שעות ולעלות בהתאם. לא מתחילים
                #  בלי שהמשתמש רואה את המספר ומאשר.
                if len(self.strings) > 600:
                    if not messagebox.askokcancel(
                            "היקף גדול",
                            f"נמצאו {len(self.strings):,} מחרוזות.\n\n"
                            f"תרגום בהיקף כזה עשוי לקחת זמן רב ולצרוך "
                            f"מכסת API משמעותית.\n\n?להמשיך"):
                        self.say("בוטל לפי בקשת המשתמש.")
                        return
                self.refresh_table([{"he": s, "en": ""} for s in sorted(self.strings)])

                self.say(f"מתרגם ל{self.lang_name.get()} באמצעות {self.provider.get()}")
                try:
                    self.pairs = core.translate(
                        sorted(self.strings), self.provider.get(), key, target,
                        progress=self.say, model=self.model.get().strip() or None)
                except Exception as e:
                    self.say(f"התרגום נכשל: {e}")
                    hint = core.explain_error(e)
                    messagebox.showerror(
                        "התרגום נכשל",
                        (hint + "\n\n— — —\nההודעה מהספק:\n" + str(e)[:500])
                        if hint else str(e))
                    return
                self.say(f"התקבלו {len(self.pairs)} תרגומים.")
                if not self.pairs:
                    messagebox.showerror("", "לא התקבל אף תרגום — לא נוצרה חבילה.")
                    return

                rows = [{"he": h, "en": self.pairs.get(h, "")} for h in sorted(self.strings)]
                self.refresh_table(rows)
                suspect = 0
                if self.do_verify.get():
                    self.say("מאמת מול Google Translate")
                    rows = core.verify(self.pairs, progress=self.say)
                    self.refresh_table(rows)
                    suspect = sum(1 for r in rows if r.get("suspect"))
                    self.say(f"אימות הושלם. {suspect} מחרוזות חשודות מסומנות באדום.")

                # הדיאלוג חייב לרוץ על התהליכון הראשי של tkinter
                self.after(0, lambda: self.choose_and_build(suspect))
            finally:
                self.busy(False)

        self.run_bg(job)

    def choose_and_build(self, suspect):
        """השאלה היחידה שנשארה למשתמש, ורק אחרי שהתרגום בידו."""
        dlg = BuildDialog(self, len(self.pairs), suspect, self.lang()[0],
                          self.plugin_name(), self.pairs)
        self.wait_window(dlg)
        if not dlg.modes:
            self.say("הבנייה בוטלה. אפשר לערוך תרגומים בטבלה ולבנות שוב.")
            self.b_go.configure(text="בנה חבילה", command=self.rebuild)
            return
        self.build(dlg.modes, dlg.name)

    def rebuild(self):
        """בנייה חוזרת אחרי עריכה ידנית בטבלה — בלי לתרגם מחדש."""
        if not self.pairs:
            messagebox.showwarning("", "אין תרגום. לחץ 'תרגם תוסף'."); return
        self.choose_and_build(0)

    def plugin_name(self):
        m = json.loads(io.open(os.path.join(self.plugin_dir.get(), "manifest.json"),
                               encoding="utf-8-sig").read())
        return m.get("name", "")

    def build(self, modes, beta):
        d = self.plugin_dir.get()
        code, _ = self.lang()
        base = self.out_root or os.path.dirname(d.rstrip("\\/"))
        out = os.path.join(base, "פלט-תרגום")
        self.busy(True, "בונה")

        def job():
            try:
                made = []
                for mode in modes:
                    self.say(f"בונה חבילה — {mode}")
                    try:
                        dest, size = core.build_package(
                            d, out, self.pairs, code, RUNTIME,
                            mode=mode, beta_name=beta, progress=self.say)
                    except Exception as e:
                        self.say(f"הבנייה נכשלה: {e}")
                        messagebox.showerror("הבנייה נכשלה", str(e))
                        continue
                    ver = core.MIN_VER_AUTO if mode == "auto" else core.MIN_VER_FORCE
                    self.say(f"נוצר: {dest}  ({size//1024} KB, minAppVersion {ver})")
                    made.append(dest)
                if made:
                    messagebox.showinfo("הושלם", "נוצר:\n" + "\n".join(made))
            finally:
                self.busy(False)

        self.run_bg(job)

    def edit_cell(self, _ev):
        sel = self.tree.selection()
        if not sel: return
        item = sel[0]
        vals = list(self.tree.item(item, "values"))
        i_he, i_en = self.cols.index("he"), self.cols.index("en")
        he, en = vals[i_he], vals[i_en]
        new = tk.simpledialog.askstring("עריכת תרגום", he, initialvalue=en, parent=self)
        if new is None: return
        self.pairs[he] = new
        vals[i_en] = new
        self.tree.item(item, values=vals, tags=())


def _flush_std():
    """ב-EXE ‎--windowed אין stdout/stderr, והם None.

    flush עליהם זורק, ובמצב windowed חריגה לא מטופלת פותחת חלון
    traceback מודאלי — שממתין ללחיצה לנצח. זה בדיוק מה שתקע את
    הבדיקה ב-CI.
    """
    for s in (sys.stdout, sys.stderr):
        try:
            if s is not None:
                s.flush()
        except Exception:
            pass


def selftest(report="selftest.log"):
    """בדיקה ללא מסך, לשימוש ה-CI: ה-EXE הארוז באמת עובד ויוצא.

    בודקת את מה שאריזה שוברת בפועל — שהנתונים נארזו בתוך ה-EXE,
    שהליבה נטענת, ושהחבילה שנבנית תקינה ומלאה.

    התוצאה נכתבת לקובץ ולא ל-stdout: ב-EXE ‎--windowed אין קונסולה,
    והדפסת עברית לשם נפלה על codec cp1252 והשאירה את התהליך תקוע.
    """
    lines = []

    def out(msg):
        lines.append(msg)
        try:
            print(msg)
        except Exception:
            pass

    try:
        code = _selftest_body(out)
    except Exception:
        out("FAIL: חריגה לא צפויה\n" + traceback.format_exc())
        code = 1
    try:
        # context manager: os._exit אינו מריק חוצצים, ובלי סגירה
        # מפורשת הדוח עלול להישאר ריק
        with io.open(report, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
            fh.flush()
    except Exception:
        pass
    return code


def _selftest_body(out):
    import tempfile, zipfile, shutil
    if not os.path.exists(RUNTIME):
        out("FAIL: i18n_runtime.js אינו ארוז ב-EXE: " + RUNTIME); return 1

    src = os.path.join(tempfile.mkdtemp(), "plug")
    os.makedirs(os.path.join(src, "data"))
    io.open(os.path.join(src, "manifest.json"), "w", encoding="utf-8").write(json.dumps(
        {"id": "t.p", "name": "בדיקה", "version": "1.0",
         "entrypoint": "index.html"}, ensure_ascii=False))
    io.open(os.path.join(src, "index.html"), "w", encoding="utf-8").write(
        '<html dir="rtl"><body><h1>סעיף</h1>'
        '<input id="q" placeholder="חיפוש שם או תוכן…">'
        '<script>var h = \'הדיווח מתייחס לערך: <b>\' + n;'
        'var re = /^(?:רבי|רב)/;</script></body></html>')
    io.open(os.path.join(src, "data", "big-data.js"), "w", encoding="utf-8").write(
        "window.D=[1,2,3];\n")

    found = core.extract_strings(src)
    for need in ("חיפוש שם או תוכן…", "הדיווח מתייחס לערך:"):
        if need not in found:
            out("FAIL: לא חולץ: " + need); return 1
    if any("<" in s or "(?:" in s for s in found):
        out("FAIL: שבר קוד חולץ בטעות"); return 1

    pairs = {"סעיף": "Se'if", "חיפוש שם או תוכן…": "Search…"}
    dst = tempfile.mkdtemp()
    dest, size = core.build_package(src, dst, pairs, "en", RUNTIME,
                                    mode="force", beta_name="Test EN")
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
        dic = z.read("i18n/en.js").decode("utf-8")
    for need in ("data/big-data.js", "index.html", "i18n/en.js", "i18n/i18n.js"):
        if need not in names:
            out("FAIL: חסר בחבילה: " + need); return 1
    if '"Se\'if"' not in dic:
        out("FAIL: המילון אינו מוברח נכון"); return 1
    shutil.rmtree(dst, ignore_errors=True)
    out("SELFTEST OK — %d מחרוזות, חבילה %d KB, %d קבצים"
        % (len(found), size // 1024, len(names)))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        rep = sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest.log"
        # כל חריגה חייבת להגיע לדוח ולא לחלון: ב-EXE ‎--windowed
        # חריגה לא מטופלת פותחת חלון traceback מודאלי, והתהליך ממתין
        # ללחיצה. ב-CI זה נראה כמו תקיעה בלי שום הסבר.
        try:
            rc = selftest(rep)
        except BaseException:
            try:
                with io.open(rep, "a", encoding="utf-8") as fh:
                    fh.write("FAIL: חריגה\n" + traceback.format_exc() + "\n")
            except Exception:
                pass
            rc = 1
        # יציאה מיידית: שריד של תהליכון או של tk עלול להשאיר את
        # התהליך תלוי
        _flush_std()
        os._exit(rc)
    import tkinter.simpledialog
    tk.simpledialog = tkinter.simpledialog
    App().mainloop()
