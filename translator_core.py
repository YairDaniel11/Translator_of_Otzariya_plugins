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
#  תיקיות שאין לחלץ מהן מחרוזות — הן מכילות נתונים, לא כיתובי ממשק.
#  אינן קובעות מה נארז: זו הבחנה נפרדת לגמרי (ראו NO_PACK_DIRS).
SKIP_DIRS = {"__pycache__", "data", "fonts", "i18n", "seder-hadorot", ".git", ".idea"}

#  מה שלא נכנס לחבילה. רק פסולת פיתוח — כל השאר הוא התוסף עצמו.
#  בעבר האריזה השתמשה ב-SKIP_DIRS, וכך data/ ו-seder-hadorot/ נשמטו
#  מהחבילה: התוסף נטען, אבל בלי הנתונים והציג "אין תוכן".
NO_PACK_DIRS = {"__pycache__", ".git", ".idea", "פלט-תרגום"}

#  חבילה ארוזה בתוך חבילה היא תמיד תקלה — קורה כשבוחרים תיקייה
#  שבה שמורות גם גרסאות .otzplugin קודמות.
NO_PACK_FILES = re.compile(r"(\.otzplugin$|\.pyc$)")

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


#  שברי תגיות וביטויים רגולריים. הם נוצרים כשמחרוזת בקוד מכילה
#  מירכאות פנימיות, והסריקה קוטעת אותה באמצע — התוצאה נשלחת
#  לתרגום ומייצרת מפתחות שלא יתאימו לשום טקסט בדף.
#  קוד לכל דבר: ביטוי רגולרי, שרשור מחרוזות, פריט במילון. אין בו
#  כיתוב שאפשר לחלץ, ולכן הוא נזרק כולו.
_CODEY = re.compile(
    r"(\(\?:|\\\\|\$\{|"
    r"[\"']\s*\+|\+\s*[\"']|"      # שרשור מחרוזות בקוד
    r"[\"']\s*:\s*[\"']|"          # פריט במילון קוד
    r"\|)")                        # חלופה בביטוי רגולרי

#  שברי תגיות. כאן דווקא *יש* כיתוב, והוא נפוץ: הקוד מרכיב
#  'הדיווח מתייחס לערך: <b>' + name, וצומת הטקסט שבדף הוא החלק
#  שלפני התגית. לכן מקלפים את התגיות ובודקים כל מקטע בנפרד.
_TAGS = re.compile(r"(<[a-zA-Z/!][^<>]*>?|</?[a-zA-Z]*>|/?>|=\s*[\"']|&[a-z]+;)")

_MARKUP = re.compile("|".join((_CODEY.pattern, _TAGS.pattern)))


def _split_markup(s):
    """מפרק שבר שמכיל תגיות למקטעי הטקסט שביניהן."""
    if _CODEY.search(s):
        return []
    out = []
    for p in _TAGS.split(s):
        if not p or _TAGS.match(p):
            continue
        # המירכאה שסגרה את האטריביוט נשארת דבוקה למקטע
        out.append(p.strip(" \t\"'"))
    return out


def _unescape_js(s):
    """הופך תווי בריחה של JS/JSON לתו עצמו.

    בקבצי נתונים הטקסט שמור מוברח (\\u0027 במקום גרש), אבל בדף הוא
    מוצג כתו רגיל. בלי הפענוח המפתח במילון לא יתאים לשום טקסט.
    """
    if "\\" not in s:
        return s
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    for a, b in (("\\\"", "\""), ("\\'", "'"), ("\\n", " "),
                 ("\\t", " "), ("\\/", "/"), ("\\\\", "\\")):
        s = s.replace(a, b)
    return s


def _clean(s, maxlen=140):
    s = _unescape_js(s).strip()
    if not s or not HEB.search(s):        return None
    if len(s) > maxlen:                   return None      # תוכן, לא כיתוב
    if s.startswith(("//", "/*", "*", "<!--")): return None
    if re.match(r"^[֐-׿]{1,2}$", s):      return None      # אות בודדת
    if _MARKUP.search(s):                 return None

    if _CODE_HINTS.search(s):             return None
    # רצף של תווים בודדים מופרדים בפסיקים — מפת מקלדת וכדומה
    if len(re.findall(r"[\"']?[^,\s]{1,2}[\"']?\s*,", s)) >= 3: return None
    # יחס עברית נמוך — סימן לשבר קוד שבמקרה מכיל מילה עברית
    heb = len(HEB.findall(s))
    if heb / max(len(s), 1) < 0.30:       return None
    return s


#  היקף החילוץ. "content" פותח גם את תיקיות הנתונים ומרים את תקרת
#  האורך, ולכן הוא כולל את הביוגרפיות והטקסטים עצמם — הרבה יותר
#  מחרוזות, ובהתאם יותר זמן ועלות מול המודל.
SCOPE_UI      = "ui"
SCOPE_CONTENT = "content"

_CONTENT_SKIP_DIRS = {"__pycache__", "i18n", "fonts", ".git", ".idea"}
_CONTENT_SKIP_FILES = re.compile(
    r"(\.min\.|pdf\.worker|mammoth|jszip|libzim|otzaria_plugin\.js$)")
CONTENT_MAXLEN = 600


def extract_strings(plugin_dir, scope=SCOPE_UI):
    """מחזיר {מחרוזת: [קבצים שבהם הופיעה]}

    scope='ui'      — כיתובי ממשק בלבד: כפתורים, תוויות, הודעות.
    scope='content' — גם תוכן: תיקיות הנתונים ומשפטים ארוכים.
    """
    content = scope == SCOPE_CONTENT
    skip_dirs = _CONTENT_SKIP_DIRS if content else SKIP_DIRS
    skip_files = _CONTENT_SKIP_FILES if content else SKIP_FILES
    maxlen = CONTENT_MAXLEN if content else 140

    found = {}
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [x for x in dirs if x not in skip_dirs]
        for f in sorted(files):
            if not f.endswith((".html", ".js")) or skip_files.search(f):
                continue
            rel = os.path.relpath(os.path.join(root, f), plugin_dir).replace("\\", "/")
            raw = io.open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            body = re.sub(r"<style\b.*?</style>", "", raw, flags=re.S)
            body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            body = re.sub(r"^\s*//.*$", "", body, flags=re.M)

            L = str(maxlen)
            cand = re.findall(r">([^<>]{2," + L + r"})<", body)

            #  אטריביוטים גלויים, במפורש. הם היו נשמטים: הסריקה
            #  הכללית מזווגת מירכאות לפי הסדר, ו-id="q" בעל התו
            #  הבודד הזיז את הזיווג בשורה — placeholder="חיפוש…"
            #  נקרא כ-" placeholder=" ולא כערך עצמו.
            for a in ("placeholder", "title", "aria-label", "alt", "value",
                      "data-label", "data-title"):
                cand += re.findall(a + r'\s*=\s*"([^"\n]{1,' + L + r'})"', body)
                cand += re.findall(a + r"\s*=\s*'([^'\n]{1," + L + r"})'", body)

            #  מירכאות: {0,} ולא {2,} — מחרוזת קצרה או ריקה חייבת
            #  להיבלע כזוג, אחרת הזיווג נשאר מוסט לכל אורך השורה.
            for q in ("'", '"', "`"):
                cand += re.findall(q + r"([^" + q + r"\n]{0," + L + r"})" + q, body)

            def keep(s):
                found.setdefault(s, [])
                if rel not in found[s]:
                    found[s].append(rel)

            for raw_c in cand:
                got = _clean(raw_c, maxlen)
                if got:
                    keep(got)
                    continue
                # נדחה בגלל תגיות — ננסה את מקטעי הטקסט שביניהן
                for seg in _split_markup(raw_c):
                    got = _clean(seg, maxlen)
                    if got:
                        keep(got)
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


#  עומס אצל הספק הוא זמני, ולכן שווה להמתין ולנסות שוב. בלי זה
#  אצווה שלמה נופלת על 503 חולף, ומחצית הממשק נשארת בעברית.
RETRY_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _post_json(url, payload, headers, timeout=120, retries=4, progress=None):
    import time
    data = json.dumps(payload).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # גוף התשובה מכיל את הסיבה האמיתית — בלעדיו רואים רק "404"
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
                msg = json.loads(body).get("error", {}).get("message", body)
            except Exception:
                msg = body
            last = RuntimeError(f"HTTP {e.code}: {msg[:400]}")
            if e.code not in RETRY_CODES or attempt == retries:
                raise last from None
            # הספק מציין בעצמו מתי לחזור — לכבד את זה עדיף מלנחש,
            # ובחריגת מכסה זה ההבדל בין הצלחה לכשל מיידי נוסף
            wait = _retry_after(e, body) or min(2 ** attempt * 3, 45)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = RuntimeError(f"תקלת רשת: {e}")
            if attempt == retries:
                raise last from None
            wait = min(2 ** attempt * 3, 45)
        if progress:
            progress(f"    הספק עמוס ({last}) — ממתין {wait}ש׳ ומנסה שוב")
        time.sleep(wait)
    raise last


def _retry_after(e, body=""):
    """זמן ההמתנה שהספק ביקש — מהכותרת, ואם אין, מגוף התשובה.

    Gemini אינו שולח retry-after; הוא מחזיר RetryInfo בגוף
    (`retryDelay: "11s"`) ואת המשפט "Please retry in 10.48954".
    בלי הקריאה מהגוף המתנו 3 שניות במקום 11, וכל הניסיונות נשרפו.
    """
    try:
        v = e.headers.get("retry-after")
        if v:
            return min(max(int(float(v)), 1), 120)
    except Exception:
        pass
    for pat in (r'"retryDelay"\s*:\s*"?([\d.]+)s',
                r"retry in ([\d.]+)"):
        m = re.search(pat, body or "")
        if m:
            # תוספת קטנה: המתנה מדויקת-מדי נדחית שוב לעתים
            return min(max(int(float(m.group(1))) + 2, 2), 120)
    return None


def explain_error(msg):
    """הסבר בעברית ומה לעשות. הודעת הספק באנגלית ולא מכוונת למשתמש."""
    s = str(msg)
    if "429" in s or "quota" in s.lower() or "rate" in s.lower():
        return ("חרגת ממכסת הבקשות של הספק (לרוב מכסת ה-Free Tier).\n\n"
                "מה אפשר לעשות:\n"
                "• להמתין ולנסות שוב — המכסה מתאפסת לפי חלון הזמן של הספק.\n"
                "• לבחור מודל אחר ב'רענן רשימת מודלים' — המכסה נמדדת לכל מודל בנפרד.\n"
                "• לעבור למנוע השני (Claude/Gemini) עם מפתח שלו.\n"
                "• לצמצם היקף: 'ממשק בלבד' שולח פחות בקשות מ'ממשק + תוכן'.")
    if "503" in s or "high demand" in s.lower() or "overload" in s.lower():
        return ("השרת של הספק עמוס כרגע. התוכנה כבר ניסתה שוב מספר פעמים.\n\n"
                "נסה שוב בעוד כמה דקות, או בחר מודל אחר.")
    if "404" in s:
        return ("המודל שנבחר אינו זמין למפתח הזה.\n\n"
                "לחץ 'רענן רשימת מודלים' ובחר מהרשימה.")
    if "401" in s or "403" in s or "API key" in s:
        return ("המפתח נדחה. ודא שהוא תקין ושייך למנוע שנבחר "
                "(מפתח של Google לא יעבוד עם Claude ולהיפך).")
    return ""


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
    out = {k: v for k, v in first_out.items() if v}

    for i in range(chunk, len(items), chunk):
        part = items[i:i + chunk]
        try:
            got = fn(part, api_key, target, model=chosen, examples=examples)
            out.update({k: v for k, v in got.items() if v})
        except Exception as e:
            if progress: progress(f"  שגיאה באצווה {i//chunk+1}: {e}")
        if progress:
            progress(f"  תורגמו {min(i+chunk, len(items))}/{len(items)}")

    #  סבבי השלמה. אצווה שנפלה — או תשובה חלקית של המודל — השאירה
    #  עד כאן מחרוזות בלי תרגום, ובלי הסבב הזה הן פשוט נשארות
    #  בעברית בתוסף הסופי, בשקט. באצוות קטנות יותר, כי תשובה ארוכה
    #  היא בעצמה סיבה שכיחה לכשל.
    for rnd, size in enumerate((max(chunk // 3, 10), 8), start=1):
        missing = [s for s in items if not out.get(s)]
        if not missing:
            break
        if progress:
            progress(f"  סבב השלמה {rnd}: {len(missing)} מחרוזות ללא תרגום")
        for i in range(0, len(missing), size):
            part = missing[i:i + size]
            try:
                got = fn(part, api_key, target, model=chosen, examples=examples)
                out.update({k: v for k, v in got.items() if v})
            except Exception as e:
                if progress: progress(f"    לא הושלם: {e}")

    still = [s for s in items if not out.get(s)]
    if progress:
        if still:
            progress(f"  ⚠ {len(still)} מחרוזות נשארו בלי תרגום ויופיעו בעברית:")
            for s in still[:10]:
                progress(f"      {s}")
            if len(still) > 10:
                progress(f"      … ועוד {len(still) - 10}")
        else:
            progress(f"  כל {len(items)} המחרוזות תורגמו")
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
             f"window.TRANSLATIONS[{json.dumps(lang)}] = {{"]
    #  המחרוזות נכתבות דרך json.dumps: המבנה של JSON תקף כמובאה ב-JS,
    #  והוא מבריח נכון גרש, מקף-על, שורה חדשה ותווי בקרה. ציטוט ידני
    #  כאן שבר את כל הקובץ על תרגום אחד שהכיל גרש (Se'if, l'auteur),
    #  ואז window.TRANSLATIONS לא נוצר כלל והממשק נשאר בעברית.
    for he in sorted(pairs):
        en = pairs[he]
        if not en or en == he:
            continue
        lines.append("  %s: %s," % (json.dumps(he, ensure_ascii=False),
                                    json.dumps(str(en), ensure_ascii=False)))
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("};")
    #  U+2028/29 הם סוף-שורה ב-JS אך תווים רגילים ב-JSON
    body = "\n".join(lines).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    io.open(os.path.join(d, f"{lang}.js"), "w", encoding="utf-8",
            newline="\n").write(body + "\n")


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
    #  המילון נטען *לפני* המנוע. בסדר ההפוך המנוע קרא את
    #  window.TRANSLATIONS לפני שהיה קיים, קבע dict=null, והממשק
    #  נשאר בעברית — במיוחד בחבילה הנעולה לשפה, שמחילה מיד.
    tags = (f"{_MARK_START}\n"
            f'<script src="i18n/{lang}.js"></script>\n'
            f'<script src="i18n/i18n.js"></script>\n'
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
    # Google מחזיר לרוב באות קטנה; שם תוסף נראה רשלני כך
    if t[:1].islower():
        t = t[0].upper() + t[1:]
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


def unpack_plugin(pkg_path, dest=None):
    """פורס קובץ .otzplugin ומחזיר את התיקייה שבה יושב manifest.json.

    כך אפשר לבחור את הקובץ שהמשתמש באמת מחזיק ביד, ולא לדרוש ממנו
    לאתר תיקיית מקור. יש חבילות שעוטפות את התוכן בתיקייה אחת
    בתוך ה-ZIP, ולכן מחפשים את ה-manifest ולא מניחים שהוא בשורש.
    """
    import tempfile
    if dest is None:
        dest = tempfile.mkdtemp(prefix="otzaria_src_")
    with zipfile.ZipFile(pkg_path) as z:
        names = z.namelist()
        # הגנה מפני נתיבים שיוצאים מתיקיית היעד (Zip Slip)
        for n in names:
            p = os.path.normpath(os.path.join(dest, n))
            if not p.startswith(os.path.normpath(dest) + os.sep) and p != os.path.normpath(dest):
                raise RuntimeError(f"החבילה מכילה נתיב לא חוקי: {n}")
        z.extractall(dest)

    for root, dirs, files in os.walk(dest):
        if "manifest.json" in files:
            return root
    raise RuntimeError("לא נמצא manifest.json בתוך החבילה — ייתכן שאינה תוסף אוצריא")


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
            dirs[:] = [x for x in dirs if x not in NO_PACK_DIRS]
            for f in files:
                if NO_PACK_FILES.search(f):
                    continue
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
        "     ב-0.9.96, שאינה מדווחת על שפת הממשק.\n"
        "\n"
        "     שני תנאים חייבים להתקיים בהחלה: המילון נטען, ו-body\n"
        "     קיים. בלי ההמתנה עליהם הקריאה מתבצעת פעם אחת בטרם\n"
        f"     זמנה, dict נשאר null, והממשק נשאר בעברית. */\n"
        "  (function applyForced() {\n"
        f"    var LANG = '{lang}', DIR = '{direction}', waited = 0;\n"
        "    function ready() {\n"
        "      var tbl = global.TRANSLATIONS;\n"
        "      return !!(tbl && tbl[LANG] && document.body);\n"
        "    }\n"
        "    function go() {\n"
        "      setLanguage(LANG, DIR);\n"
        "      /* רינדור מאוחר (מסגרות, טעינה א-סינכרונית) — סריקה חוזרת */\n"
        "      [300, 1000, 2500].forEach(function (ms) {\n"
        "        setTimeout(function () { try { apply(); } catch (e) {} }, ms);\n"
        "      });\n"
        "    }\n"
        "    if (ready()) { go(); return; }\n"
        "    var timer = setInterval(function () {\n"
        "      waited += 50;\n"
        "      if (ready()) { clearInterval(timer); go(); }\n"
        "      else if (waited >= 10000) clearInterval(timer);\n"
        "    }, 50);\n"
        "  })();\n")
    return s.replace("\n  autoInit();\n", forced).encode("utf-8")


def _missing_from_package(src_dir, pkg_path):
    """קבצים שקיימים במקור ואינם בחבילה.

    שומר מפני בדיוק התקלה שקרתה: תיקיית נתונים נשמטה מהאריזה,
    התוסף נטען כרגיל והציג "אין תוכן" — בלי שום שגיאה שתסגיר זאת.
    """
    expected = set()
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [x for x in dirs if x not in NO_PACK_DIRS]
        for f in files:
            if NO_PACK_FILES.search(f):
                continue
            expected.add(os.path.relpath(os.path.join(root, f), src_dir)
                         .replace("\\", "/"))
    with zipfile.ZipFile(pkg_path) as z:
        got = set(z.namelist())
    return sorted(expected - got)


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
        dest, size = package(work, out_dir, mode=mode, lang=lang,
                             runtime_js=runtime_js, beta_name=beta_name, pairs=pairs)
        missing = _missing_from_package(src_dir, dest)
        if missing:
            raise RuntimeError(
                "החבילה נבנתה חסרה — הקבצים הבאים לא נארזו:\n  "
                + "\n  ".join(missing[:15])
                + ("\n  …" if len(missing) > 15 else ""))
        if progress:
            progress(f"  נארזו {len(zipfile.ZipFile(dest).namelist())} קבצים")
        return dest, size
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
