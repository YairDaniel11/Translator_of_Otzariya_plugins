# -*- coding: utf-8 -*-
"""
מתרגם תוספי אוצריא — הליבה.

זרימת העבודה:
  1. חילוץ מחרוזות ממשק עבריות מהתוסף (מסנן נתונים, ספריות, הערות)
  2. תרגום באמצעות מודל שפה (Claude / Gemini)
  3. אימות: תרגום חוזר לעברית מול Google Translate והשוואה למקור —
     פער גדול מסמן את המחרוזת כחשודה לבדיקה ידנית
  4. כתיבת i18n/<lang>.js + הזרקת מנוע התרגום ל-HTML
  5. אריזה לאחת משתי חבילות:
       AUTO  — הממשק נקבע לפי שפת אוצריא. דורש 0.9.97 ומעלה
               (השדה app.language קיים רק משם).
       FORCE — הממשק נעול לשפת היעד ואינו תלוי באוצריא, ולכן עובד
               כבר ב-0.9.96. מיועד לבדיקת איכות התרגום עכשיו.
"""
import os, io, re, json, zipfile, shutil, urllib.request, urllib.parse

HEB = re.compile(r"[֐-׿]")

SKIP_FILES = re.compile(
    r"(\.min\.|pdf\.worker|-data\.js$|-enrich\.js$|relations\.js$|"
    r"dictionary\.js$|mammoth|jszip|libzim|otzaria_plugin\.js$)")
SKIP_DIRS = {"__pycache__", "data", "fonts", "i18n", "seder-hadorot", ".git", ".idea"}

MAX_NAME = 14           # אוצריא דוחה שם תוסף ארוך מזה
MIN_VER_AUTO  = "0.9.97"   # app.language זמין רק מכאן
MIN_VER_FORCE = "0.9.96"


# ─────────────────────────── חילוץ ───────────────────────────

#  דפוסים שמסגירים שבר קוד ולא כיתוב ממשק. נחוצים במיוחד בתוספים
#  שנבנו ממסגרת (Vue/React) — שם ה-bundle מכיל מחרוזות ענק שמערבבות
#  קוד ועברית, וללא סינון הן נשלחות לתרגום ומייצרות זבל.
_CODE_HINTS = re.compile(
    r"""(=>|\{|\}|\[|\]|`|\|\||&&|;\s*$|</|/>|
        \bnull\b|\bfunction\b|\breturn\b|\bvar\b|\blet\b|\bconst\b|
        \w+\s*:\s*(?:\d+|null|true|false)|      # מפתח: ערך
        ^[\s,:]|                                 # מתחיל בפיסוק של קוד
        \\[nrt"'\\])""", re.X)


def _clean(s):
    s = s.strip()
    if not s or not HEB.search(s):        return None
    if len(s) > 140:                      return None      # תוכן, לא כיתוב
    if s.startswith(("//", "/*", "*", "<!--")): return None
    if re.match(r"^[֐-׿]{1,2}$", s):      return None      # אות בודדת

    if _CODE_HINTS.search(s):             return None
    # רצף של תווים בודדים מופרדים בפסיקים — מפת מקלדת וכדומה
    if len(re.findall(r"[\"']?[^,\s]{1,2}[\"']?\s*,", s)) >= 3: return None
    # יחס עברית נמוך — סימן לשבר קוד שבמקרה מכיל מילה עברית
    heb = len(HEB.findall(s))
    if heb / max(len(s), 1) < 0.30:       return None
    return s


def extract_strings(plugin_dir):
    """מחזיר {מחרוזת: [קבצים שבהם הופיעה]}"""
    found = {}
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in sorted(files):
            if not f.endswith((".html", ".js")) or SKIP_FILES.search(f):
                continue
            rel = os.path.relpath(os.path.join(root, f), plugin_dir).replace("\\", "/")
            raw = io.open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            body = re.sub(r"<style\b.*?</style>", "", raw, flags=re.S)
            body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            body = re.sub(r"^\s*//.*$", "", body, flags=re.M)

            cand = re.findall(r">([^<>]{2,140})<", body)
            for q in ("'", '"', "`"):
                cand += re.findall(q + r"([^" + q + r"\n]{2,140})" + q, body)

            for c in cand:
                c = _clean(c)
                if c:
                    found.setdefault(c, [])
                    if rel not in found[c]:
                        found[c].append(rel)
    return found


# ─────────────────────────── תרגום ───────────────────────────

SYSTEM_PROMPT = """You translate UI strings for Otzaria, a Jewish-texts study application.

Rules:
- Translate ONLY the user-interface wording. Keep it short — these are buttons,
  labels, tooltips and messages that must fit in a UI.
- Keep Jewish/Torah terms in their accepted English forms: מסכת→Tractate,
  סוגיה→sugya, פרשה→parasha, מראה מקום→reference, תנאים→Tannaim,
  אמוראים→Amoraim, ניקוד→nikud, פסוק→verse, דף→page/folio, סימן→siman,
  סעיף→se'if, הלכה→halakha, שו"ת→responsa.
- Do NOT translate proper names of people, books or tractates.
- Preserve leading/trailing punctuation, emoji, and placeholders such as
  {0}, %s, or trailing colons exactly as they appear.
- Preserve the register: terse Hebrew UI text becomes terse English UI text.

Return ONLY a JSON object mapping each source string to its translation.
No commentary, no markdown fences."""


def _post_json(url, payload, headers, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # גוף התשובה מכיל את הסיבה האמיתית — בלעדיו רואים רק "404"
        try:
            body = e.read().decode("utf-8", "replace")
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = ""
        raise RuntimeError(f"HTTP {e.code}: {msg[:400]}") from None


def _get_json(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = ""
        raise RuntimeError(f"HTTP {e.code}: {msg[:400]}") from None


def list_gemini_models(api_key):
    """מחזיר את שמות המודלים שהמפתח באמת יכול להריץ.

    שמות המודלים של Gemini משתנים בין גרסאות ובין חשבונות, ולכן
    עדיף לשאול מאשר לקבע שם שעלול להחזיר 404."""
    data = _get_json("https://generativelanguage.googleapis.com/v1beta/models?key="
                     + urllib.parse.quote(api_key))
    out = []
    for m in data.get("models", []):
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        n = m["name"].split("/")[-1]
        # רק משפחת gemini לטקסט: מודלים אחרים ברשימה (gemma,
        # deep-research, antigravity) דורשים ממשק אחר ומחזירים 400
        if not n.startswith("gemini-"):
            continue
        if re.search(r"(image|tts|audio|embedding|vision|live|native|thinking)", n):
            continue
        out.append(n)

    def rank(n):
        # הגרסה נלקחת רק מהתבנית gemini-X.Y — אחרת מספרים בשם
        # (כמו preview-12-2025) נקראים בטעות כגרסה
        m = re.match(r"gemini-(\d+(?:\.\d+)?)", n)
        ver = float(m.group(1)) if m else 0.0
        kind = 0 if "flash" in n else 1 if "pro" in n else 2
        return (-ver, 1 if ("preview" in n or "exp" in n) else 0, kind, n)

    out.sort(key=rank)
    return out


def list_claude_models(api_key):
    data = _get_json("https://api.anthropic.com/v1/models?limit=50",
                     {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    return [m["id"] for m in data.get("data", [])]


def list_models(provider, api_key):
    return (list_claude_models if provider == "claude" else list_gemini_models)(api_key)


def _parse_json_blob(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("לא התקבל JSON מהמודל")
    return json.loads(text[i:j + 1])


def translate_claude(strings, api_key, target="English", model=None,
                     examples=None):
    user = ""
    if examples:
        user += ("Follow the style of these approved translations:\n"
                 + json.dumps(examples, ensure_ascii=False, indent=1) + "\n\n")
    user += (f"Translate these Hebrew UI strings to {target}:\n"
             + json.dumps(strings, ensure_ascii=False, indent=1))
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": model, "max_tokens": 8000, "system": SYSTEM_PROMPT,
         "messages": [{"role": "user", "content": user}]},
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    return _parse_json_blob(data["content"][0]["text"])


def translate_gemini(strings, api_key, target="English",
                     model=None, examples=None):
    user = SYSTEM_PROMPT + "\n\n"
    if examples:
        user += ("Follow the style of these approved translations:\n"
                 + json.dumps(examples, ensure_ascii=False, indent=1) + "\n\n")
    user += (f"Translate these Hebrew UI strings to {target}:\n"
             + json.dumps(strings, ensure_ascii=False, indent=1))
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        {"contents": [{"parts": [{"text": user}]}],
         "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8000}}, {})
    return _parse_json_blob(data["candidates"][0]["content"]["parts"][0]["text"])


def translate(strings, provider, api_key, target="English", examples=None, chunk=60,
              progress=None, model=None):
    """מתרגם באצוות, כדי לא לחרוג ממגבלת התשובה של המודל.

    אם לא נמסר model, נבחר אוטומטית מתוך המודלים שהמפתח יכול להריץ.
    כשל באצווה הראשונה עוצר מיד — אין טעם לחזור על אותה שגיאה.
    """
    items = list(strings)
    if not items:
        return {}

    fn = translate_claude if provider == "claude" else translate_gemini

    # מועמדים: המודל שנבחר ידנית, או הרשימה הזמינה לפי סדר עדיפות.
    # הרשימה עשויה לכלול מודלים שהשרת דוחה בפועל ("no longer available"),
    # ולכן מנסים כמה עד שאחד באמת עונה.
    if model:
        candidates = [model]
    else:
        try:
            candidates = list_models(provider, api_key)[:5]
        except Exception as e:
            raise RuntimeError(f"לא ניתן לאתר מודל זמין — {e}")
        if not candidates:
            raise RuntimeError("לא נמצאו מודלים זמינים למפתח הזה")

    chosen, first, last_err = None, items[:chunk], None
    for cand in candidates:
        try:
            first_out = fn(first, api_key, target, model=cand, examples=examples)
            chosen = cand
            break
        except Exception as e:
            last_err = e
            if progress: progress(f"  {cand} לא זמין — מנסה את הבא")
    if not chosen:
        raise RuntimeError(f"התרגום נכשל בכל המודלים שנוסו. אחרון: {last_err}")

    if progress:
        progress(f"  מודל: {chosen}")
        progress(f"  תורגמו {len(first)}/{len(items)}")
    out = dict(first_out)

    for i in range(chunk, len(items), chunk):
        part = items[i:i + chunk]
        try:
            out.update(fn(part, api_key, target, model=chosen, examples=examples))
        except Exception as e:
            if progress: progress(f"  שגיאה באצווה {i//chunk+1}: {e}")
        if progress:
            progress(f"  תורגמו {min(i+chunk, len(items))}/{len(items)}")
    return out


# ─────────────────── אימות: תרגום חוזר ───────────────────

def google_back_translate(text, src="en", dest="he", timeout=20):
    """תרגום חוזר דרך נקודת הקצה הציבורית של Google Translate.

    אינה API רשמית ואינה מובטחת — כשל כאן מדלג על האימות בלבד
    ואינו עוצר את התרגום."""
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=" + src + "&tl=" + dest + "&dt=t&q="
           + urllib.parse.quote(text))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def _norm_he(s):
    s = re.sub(r"[֑-ׇ]", "", s)          # ניקוד וטעמים
    s = re.sub(r"[^\w֐-׿ ]", " ", s)     # פיסוק
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a, b):
    """דמיון לפי מילים משותפות — מספיק לאיתור סטיות גסות."""
    wa, wb = set(_norm_he(a).split()), set(_norm_he(b).split())
    if not wa or not wb: return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def _similarity_en(a, b):
    """דמיון בין שתי מחרוזות אנגליות, ללא תלות ברישיות ובפיסוק."""
    norm = lambda s: set(re.sub(r"[^\w ]", " ", s.lower()).split())
    wa, wb = norm(a), norm(b)
    if not wa or not wb: return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def verify(pairs, threshold=0.34, progress=None, limit=None):
    """מאמת כל תרגום בשתי דרכים בלתי תלויות, וכך מצמצם אזעקות שווא:

      back — התרגום שלנו חזרה לעברית, מול המקור
      fwd  — המקור לאנגלית לפי Google, מול התרגום שלנו

    מחרוזת מסומנת כחשודה רק אם *שתי* הבדיקות נמוכות. די בכך שאחת
    מהן מאשרת: מילה בודדת כמו "שמור" עשויה לחזור כ"להציל" ועדיין
    להיות תרגום נכון לחלוטין.

    מחזיר [{he, en, back, score, score_fwd, suspect}].
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading

    items = list(pairs.items())
    if limit: items = items[:limit]

    def one(pair):
        he, en = pair
        row = {"he": he, "en": en, "back": "", "score": None,
               "score_fwd": None, "suspect": False}
        if not en:
            return row
        # המודל החזיר עברית — כלומר לא תרגם כלל. אין טעם לאמת,
        # וזה תמיד פגם שדורש טיפול.
        if HEB.search(en):
            row["back"] = "(התרגום נשאר בעברית)"
            row["suspect"] = True
            return row
        try:
            back = google_back_translate(en, src="en", dest="he")
            row["back"] = back
            row["score"] = round(_similarity(he, back), 2)
        except Exception as e:
            row["back"] = f"(אימות נכשל: {e})"
        try:
            fwd = google_back_translate(he, src="he", dest="en")
            row["score_fwd"] = round(_similarity_en(en, fwd), 2)
        except Exception:
            pass
        scores = [s for s in (row["score"], row["score_fwd"]) if s is not None]
        row["suspect"] = bool(scores) and max(scores) < threshold
        return row

    # שתי קריאות רשת לכל מחרוזת; בטור זה ארוך מאוד, ולכן מריצים
    # במקביל. 8 עובדים — מספיק כדי לקצר משמעותית בלי להיחסם.
    done = [0]
    lock = threading.Lock()

    def tracked(pair):
        r = one(pair)
        with lock:
            done[0] += 1
            if progress and done[0] % 10 == 0:
                progress(f"  אומתו {done[0]}/{len(items)}")
        return r

    with ThreadPoolExecutor(max_workers=8) as pool:
        res = list(pool.map(tracked, items))
    if progress:
        progress(f"  אומתו {len(items)}/{len(items)}")
    return res


# ─────────────────── הזרקה לתוסף ואריזה ───────────────────

def write_dictionary(plugin_dir, lang, pairs):
    """כותב i18n/<lang>.js — המפתחות הם מחרוזות המקור בעברית."""
    d = os.path.join(plugin_dir, "i18n")
    os.makedirs(d, exist_ok=True)
    lines = ["/* תרגום — ממשק בלבד. נוצר על-ידי מתרגם תוספי אוצריא.",
             "   המפתחות הם מחרוזות המקור בעברית; מחרוזת שאינה כאן",
             "   נשארת בעברית (נפילה טבעית). */",
             "window.TRANSLATIONS = window.TRANSLATIONS || {};",
             f"window.TRANSLATIONS.{lang} = {{"]
    for he in sorted(pairs):
        en = pairs[he]
        if not en or en == he:
            continue
        k = he.replace("\\", "\\\\").replace("'", "\'")
        v = str(en).replace("\\", "\\\\").replace("'", "\'")
        lines.append(f"  '{k}': '{v}',")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("};")
    io.open(os.path.join(d, f"{lang}.js"), "w", encoding="utf-8",
            newline="\n").write("\n".join(lines) + "\n")


#  סימון גבולות ההזרקה, כדי שנוכל להסיר אותה במלואה בריצה חוזרת.
#  בלעדיו הזרקה שנייה משאירה קוד ישן ושבור מהריצה הקודמת.
_MARK_START = "<!-- otzaria-i18n:start -->"
_MARK_END   = "<!-- otzaria-i18n:end -->"


def strip_injection(html):
    """מסיר כל הזרקה קודמת — מסומנת או מגרסאות ישנות של הכלי."""
    html = re.sub(re.escape(_MARK_START) + r".*?" + re.escape(_MARK_END),
                  "", html, flags=re.S)
    # שאריות מגרסאות שקדמו לסימון
    html = re.sub(r'\s*<script src="i18n/[\w-]+\.js"></script>', "", html)
    html = re.sub(r"\s*<script>\s*/\* אתחול התרגום.*?</script>", "", html, flags=re.S)
    html = re.sub(r"\s*<script>\s*if \(window\.Otzaria && window\.I18n\).*?</script>",
                  "", html, flags=re.S)
    return html


def inject_runtime(plugin_dir, runtime_js, lang, entry):
    """מעתיק את מנוע התרגום ומחבר אותו ל-HTML של נקודת הכניסה.

    ההזרקה idempotent: כל הזרקה קודמת מוסרת תחילה, כך שהרצה חוזרת
    אינה משאירה שאריות מגרסה ישנה.
    """
    d = os.path.join(plugin_dir, "i18n")
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(runtime_js, os.path.join(d, "i18n.js"))

    p = os.path.join(plugin_dir, entry)
    s = strip_injection(io.open(p, encoding="utf-8").read())

    # אין קוד אתחול כאן: המנוע מאתחל את עצמו וממתין שגשר אוצריא
    # ייטען. בדיקת "if (window.Otzaria)" בשלב הזה נכשלה תמיד, כי
    # המנוע נטען לפני otzaria_plugin.js.
    tags = (f"{_MARK_START}\n"
            f'<script src="i18n/i18n.js"></script>\n'
            f'<script src="i18n/{lang}.js"></script>\n'
            f"{_MARK_END}\n")
    i = s.find("<script")
    s = (s[:i] + tags + s[i:]) if i != -1 else s.replace("</body>", tags + "</body>")
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


_STOPWORDS = {"the", "a", "an", "of", "for", "to", "and", "in", "on"}

# קיצורים מקובלים — נקראים טבעי, בניגוד לחיתוך שרירותי באמצע מילה
_ABBREV = {
    "automatic": "Auto", "automated": "Auto", "configuration": "Config",
    "settings": "Prefs", "management": "Mgmt", "manager": "Mgr",
    "downloading": "Download", "downloader": "Download",
    "biographies": "Bios", "biography": "Bio", "references": "Refs",
    "reference": "Ref", "documents": "Docs", "document": "Doc",
    "translation": "Translate", "statistics": "Stats", "tracking": "Tracker",
    "generations": "Gens", "generation": "Gen", "vocalization": "Nikud",
}


def suggest_name(he_name, lang="en", pairs=None):
    """מציע שם לחבילה בשפת היעד, בתוך מגבלת 14 התווים של אוצריא.

    אוצריא אינה תומכת בשם תלוי-שפה — `name` הוא מחרוזת אחת —
    ולכן החבילה הנעולה לשפה מקבלת שם בשפה הזו.

    מקור התרגום, לפי סדר: המילון שכבר תורגם (איכותי — הוא הגיע
    מהמודל עם הקשר), ואחריו Google Translate.
    """
    t = ""
    if pairs:
        t = (pairs.get(he_name) or "").strip()
    if not t:
        try:
            t = google_back_translate(he_name, src="he", dest=lang).strip()
        except Exception:
            t = ""
    if not t:
        return f"{lang.upper()} test"[:MAX_NAME]

    t = re.sub(r"\s+", " ", t).strip(" .·-")
    if len(t) <= MAX_NAME:
        return t

    # 1) הסרת מילות עזר — לרוב מספיקה ואינה פוגעת במשמעות
    words = [w for w in t.split() if w.lower() not in _STOPWORDS]
    if words and len(" ".join(words)) <= MAX_NAME:
        return " ".join(words)

    # 2) קיצורים מקובלים — נקראים טבעי, בניגוד לחיתוך באמצע מילה
    short = [_ABBREV.get(w.lower(), w) for w in words]
    if len(" ".join(short)) <= MAX_NAME:
        return " ".join(short)

    # 3) השמטת מילים מההתחלה: המילה האחרונה נושאת את הזהות
    #    ("Automatic Nikud" -> "Nikud", ולא "Automati Nikud")
    for i in range(1, len(short)):
        cand = " ".join(short[i:])
        if len(cand) <= MAX_NAME:
            return cand

    return short[-1][:MAX_NAME] if short else t[:MAX_NAME]


def ensure_permissions(manifest):
    """ההרשאות שמנוע התרגום זקוק להן."""
    perms = manifest.setdefault("permissions", [])
    for p in ("app.info.read", "events.subscribe:settings.changed"):
        if p not in perms:
            perms.append(p)
    return manifest


def _zip_dir(src_dir, dest, overrides=None):
    overrides = overrides or {}
    written = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [x for x in dirs if x in ("i18n",) or x not in SKIP_DIRS]
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src_dir).replace("\\", "/")
                if rel in overrides:
                    z.writestr(rel, overrides[rel])
                else:
                    z.write(full, rel)
                written.add(rel)
        for rel, data in overrides.items():
            if rel not in written:
                z.writestr(rel, data)
    return os.path.getsize(dest)


def _force_runtime(runtime_path, lang):
    """גרסת מנוע שנעולה לשפה — אינה תלויה ב-app.language של אוצריא.

    כאן השפה ידועה מראש, ולכן אין להמתין ל-Otzaria או לאירוע
    plugin.boot: ההמתנה היא בדיוק מה שמנע מהתרגום לחול. מחליפים
    את הקריאה ל-autoInit() בהחלה מיידית.
    """
    s = io.open(runtime_path, encoding="utf-8").read()
    direction = "rtl" if lang == "he" else "ltr"

    if "\n  autoInit();\n" not in s:
        raise RuntimeError("מבנה i18n.js אינו מזוהה — לא נמצאה קריאת autoInit")

    forced = (
        "\n"
        "  /* ── חבילה נעולה לשפה ──\n"
        "     השפה קבועה, ולכן מחילים מיד ואיננו תלויים בטעינת גשר\n"
        "     אוצריא או באירוע plugin.boot. כך החבילה עובדת גם\n"
        "     ב-0.9.96, שאינה מדווחת על שפת הממשק. */\n"
        "  (function applyForced() {\n"
        f"    setLanguage('{lang}', '{direction}');\n"
        "    if (!document.body) {\n"
        "      document.addEventListener('DOMContentLoaded', function () {\n"
        f"        setLanguage('{lang}', '{direction}');\n"
        "      });\n"
        "    }\n"
        "    /* רינדור מאוחר (מסגרות, טעינה א-סינכרונית) — סריקה חוזרת */\n"
        "    [300, 1000, 2500].forEach(function (ms) {\n"
        "      setTimeout(function () { try { apply(); } catch (e) {} }, ms);\n"
        "    });\n"
        "  })();\n")
    return s.replace("\n  autoInit();\n", forced).encode("utf-8")


def build_package(src_dir, out_dir, pairs, lang, runtime_js, mode="auto",
                  beta_name=None, progress=None):
    """בונה חבילה מתוך עותק עבודה — קוד המקור של המשתמש אינו משתנה.

    זהו נתיב הבנייה המומלץ: עריכה במקום עלולה להשאיר הזרקות
    מריצות קודמות, ולזהם את התוסף המקורי.
    """
    import tempfile
    m = json.loads(io.open(os.path.join(src_dir, "manifest.json"),
                           encoding="utf-8-sig").read())
    entry = m.get("entrypoint", "index.html")

    tmp_root = tempfile.mkdtemp(prefix="otzaria_i18n_")
    work = os.path.join(tmp_root, "plugin")
    try:
        shutil.copytree(src_dir, work,
                        ignore=shutil.ignore_patterns("__pycache__", ".git", ".idea"))
        if progress: progress("  נבנה עותק עבודה (המקור לא משתנה)")
        write_dictionary(work, lang, pairs)
        inject_runtime(work, runtime_js, lang, entry)
        return package(work, out_dir, mode=mode, lang=lang,
                       runtime_js=runtime_js, beta_name=beta_name, pairs=pairs)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def package(plugin_dir, out_dir, mode="auto", lang="en", runtime_js=None,
            beta_name=None, pairs=None):
    """אורז .otzplugin.

    mode='auto'  — לפי שפת אוצריא, minAppVersion 0.9.97
    mode='force' — נעול לשפת היעד, minAppVersion 0.9.96, id ושם נפרדים
    """
    os.makedirs(out_dir, exist_ok=True)
    mp = os.path.join(plugin_dir, "manifest.json")
    m = json.loads(io.open(mp, encoding="utf-8-sig").read())

    if mode == "auto":
        m = ensure_permissions(m)
        m["minAppVersion"] = MIN_VER_AUTO
        io.open(mp, "w", encoding="utf-8", newline="\n").write(
            json.dumps(m, ensure_ascii=False, indent=2) + "\n")
        if len(m["name"]) > MAX_NAME:
            raise RuntimeError(f"שם התוסף '{m['name']}' חורג מ-{MAX_NAME} תווים")
        dest = os.path.join(out_dir, f"{m['name']} v{m['version']}.otzplugin")
        return dest, _zip_dir(plugin_dir, dest)

    # ── force ──
    bm = ensure_permissions(dict(m))
    bm["id"] = m["id"] + f".{lang}only"
    name = (beta_name or "").strip() or suggest_name(m["name"], lang, pairs)
    if len(name) > MAX_NAME:
        raise RuntimeError(
            f"שם החבילה '{name}' באורך {len(name)} — אוצריא מגבילה ל-{MAX_NAME} תווים.")
    bm["name"] = name
    bm["minAppVersion"] = MIN_VER_FORCE
    bm["stability"] = "experimental"
    contrib = json.loads(json.dumps(m.get("contributes", {})))
    if "toolTab" in contrib:
        contrib["toolTab"]["title"] = bm["name"]
        contrib["toolTab"]["defaultPinned"] = False
    if contrib:
        bm["contributes"] = contrib

    ov = {"manifest.json": json.dumps(bm, ensure_ascii=False, indent=2).encode("utf-8")}
    if runtime_js:
        ov["i18n/i18n.js"] = _force_runtime(runtime_js, lang)
    dest = os.path.join(out_dir, f"{m['name']} [{lang.upper()}] v{m['version']}.otzplugin")
    return dest, _zip_dir(plugin_dir, dest, ov)
