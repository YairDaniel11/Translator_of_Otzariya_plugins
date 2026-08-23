/* ═══════════════════════════════════════════════════════════════
   i18n — תשתית תרגום משותפת לתוספי אוצריא
   ───────────────────────────────────────────────────────────────
   עברית היא שפת הבסיס: המפתחות הם מחרוזות המקור בעברית, וכל
   מחרוזת שאין לה תרגום נשארת כמות שהיא. לכן מילון חלקי בטוח —
   הוא לעולם לא "משבש", רק מתרגם את מה שהוגדר.

   שני מצבי עבודה, שאפשר לשלב:

   1. אוטומטי (ברירת מחדל) — סורק את צמתי הטקסט של הדף ומחליף כל
      מחרוזת שקיימת במילון. MutationObserver מטפל גם בתוכן שנוצר
      דינמית (כולל מסגרות כמו Vue/React שמרנדרות אחרי הטעינה).
      מכיוון שרק מחרוזות שבמילון מוחלפות, תוכן ונתונים — שמות
      חכמים, מסכתות, טקסט תורני — אינם נוגעים בהם.

   2. מפורש — data-i18n על אלמנט, או I18n.t() בקוד. מדויק יותר,
      ומתאים למחרוזות שמורכבות בזמן ריצה.

   שימוש:
     <script src="i18n/i18n.js"></script>
     <script src="i18n/en.js"></script>
     ...
     Otzaria.on('plugin.boot', p => I18n.init(p));

   אלמנטים שלא לתרגם:  <div data-i18n-skip> ... </div>
   ═══════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  var dict = null;      // המילון הפעיל; null = עברית (ברירת המחדל)
  var lang = 'he';
  var dir  = 'rtl';
  var observer = null;
  var auto = true;

  /* תגיות שאין לגעת בתוכן שלהן */
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };

  function t(s) {
    if (s == null || !dict) return s;
    var k = String(s).trim();
    if (!k) return s;
    var v = dict[k];
    if (v == null) return s;
    var str = String(s);
    return str.match(/^\s*/)[0] + v + str.match(/\s*$/)[0];
  }

  function skipNode(node) {
    for (var el = node.parentElement; el; el = el.parentElement) {
      if (SKIP_TAGS[el.tagName]) return true;
      if (el.hasAttribute && el.hasAttribute('data-i18n-skip')) return true;
    }
    return false;
  }

  /* מספר מוביל שנוצר בזמן ריצה: הקוד מרכיב `n + ' ערכים'`, ולכן
     המחרוזת שבדף היא "0 ערכים" ואינה מפתח במילון. המספר נשמר
     כמות שהוא ורק שאר הכיתוב מתורגם. */
  var NUM_PREFIX = /^([0-9][0-9.,  ]*?)\s+(\S.*)$/;

  function lookup(key) {
    if (!dict || !key) return null;
    if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
    var m = NUM_PREFIX.exec(key);
    if (m && Object.prototype.hasOwnProperty.call(dict, m[2])) {
      return m[1] + ' ' + dict[m[2]];
    }
    return null;
  }

  /* ── מצב אוטומטי: החלפת צמתי טקסט ── */
  function translateTextNodes(root) {
    if (!dict) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n, batch = [];
    while ((n = walker.nextNode())) {
      var raw = n.nodeValue;
      if (!raw || !raw.trim()) continue;
      if (lookup(raw.trim()) === null) continue;
      if (skipNode(n)) continue;
      batch.push(n);
    }
    batch.forEach(function (node) {
      var raw = node.nodeValue;
      node.nodeValue = raw.match(/^\s*/)[0] + lookup(raw.trim()) + raw.match(/\s*$/)[0];
    });
  }

  /* ── מצב אוטומטי: אטריביוטים גלויים ── */
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  function translateAttrs(root) {
    if (!dict) return;
    ATTRS.forEach(function (a) {
      root.querySelectorAll('[' + a + ']').forEach(function (el) {
        if (el.closest('[data-i18n-skip]')) return;
        var v = el.getAttribute(a), tr = v && lookup(v.trim());
        if (tr !== null && tr !== undefined && tr !== false) el.setAttribute(a, tr);
      });
    });
    // value של כפתורים ושדות submit (לא של קלט טקסט רגיל)
    root.querySelectorAll('input[type=button],input[type=submit]').forEach(function (el) {
      var tr = el.value && lookup(el.value.trim());
      if (tr !== null && tr !== undefined && tr !== false) el.value = tr;
    });
  }

  /* ── מצב מפורש: data-i18n ── */
  function applyMarked(root) {
    if (!dict) return;
    root.querySelectorAll('[data-i18n]').forEach(function (el) {
      if (!el.dataset.i18nSrc) el.dataset.i18nSrc = el.textContent;
      el.textContent = t(el.dataset.i18nSrc);
    });
    [['placeholder', 'data-i18n-placeholder'],
     ['title',       'data-i18n-title'],
     ['aria-label',  'data-i18n-aria-label'],
     ['value',       'data-i18n-value']].forEach(function (pair) {
      root.querySelectorAll('[' + pair[1] + ']').forEach(function (el) {
        el.setAttribute(pair[0], t(el.getAttribute(pair[1])));
      });
    });
  }

  function apply(root) {
    if (!dict) return;
    root = root || document.body || document;
    applyMarked(root);
    if (auto) { translateTextNodes(root); translateAttrs(root); }
  }

  /* עוקב אחרי תוכן שנוצר דינמית */
  function startObserver() {
    if (observer || !dict || !auto || !global.MutationObserver) return;
    var pending = false;
    observer = new MutationObserver(function (muts) {
      if (pending) return;
      pending = true;
      // איגוד עדכונים לפריים אחד — כדי לא לרוץ על כל שינוי קטן
      requestAnimationFrame(function () {
        pending = false;
        observer.disconnect();
        muts.forEach(function (m) {
          m.addedNodes && m.addedNodes.forEach(function (n) {
            if (n.nodeType === 1) { applyMarked(n); translateTextNodes(n); translateAttrs(n); }
            else if (n.nodeType === 3 && n.parentElement) translateTextNodes(n.parentElement);
          });
          // אטריביוט שנקבע בזמן ריצה (placeholder, title) — היה
          // נשאר בעברית, כי המעקב הקודם ניטר רק צמתים שנוספו
          if (m.type === 'attributes' && m.target) translateAttrs(m.target.parentElement || m.target);
        });
        connect();
      });
    });
    connect();
    function connect() {
      observer.observe(document.body, {
        childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ATTRS
      });
    }
  }

  function setLanguage(language, textDirection) {
    lang = language || 'he';
    var table = global.TRANSLATIONS || {};
    dict = (lang !== 'he' && table[lang]) ? table[lang] : null;

    // הכיוון נקבע בזמן ריצה בלבד — ה-HTML הסטטי נשאר dir="rtl"
    // כדרישת ולידציית העיצוב של אוצריא
    dir = textDirection || (dict ? 'ltr' : 'rtl');
    document.documentElement.dir = dir;
    document.documentElement.lang = lang;
    if (document.body) document.body.classList.toggle('ltr', dir === 'ltr');

    if (!dict) return;
    if (document.body) { apply(); startObserver(); }
    else document.addEventListener('DOMContentLoaded', function () { apply(); startObserver(); });
  }

  function init(payload, opts) {
    if (opts && opts.auto === false) auto = false;
    var app = (payload && payload.app) || {};
    // language קיים מ-0.9.97; בגרסאות ישנות נגזר מ-locale, ואם אין — עברית
    var language = app.language ||
      (app.locale ? String(app.locale).split('-')[0] : 'he');
    setLanguage(language, app.textDirection);

    // עדכון חי בשינוי שפה (דורש events.subscribe:settings.changed)
    if (global.Otzaria && typeof global.Otzaria.on === 'function') {
      global.Otzaria.on('settings.changed', function (data) {
        if (!data || data.key !== 'key-settings-language') return;
        global.Otzaria.call('app.getLocale').then(function (res) {
          if (res && res.success && res.data) {
            if (observer) { observer.disconnect(); observer = null; }
            setLanguage(res.data.language, res.data.textDirection);
          }
        });
      });
    }
  }

  /* אתחול עצמי — אינו מניח ש-Otzaria כבר נטען.

     המנוע נטען לפני otzaria_plugin.js, ולכן בדיקה חד-פעמית של
     window.Otzaria תיכשל תמיד והתרגום לא יופעל. כאן ממתינים
     שהגשר יופיע, ואם ה-boot כבר חלף — נשלפת השפה ישירות. */
  function autoInit(maxWaitMs) {
    var waited = 0, step = 50;
    var booted = false;

    function attach() {
      if (!global.Otzaria || typeof global.Otzaria.on !== 'function') return false;
      global.Otzaria.on('plugin.boot', function (p) { booted = true; init(p); });

      // רשת ביטחון: אם ה-boot כבר נורה לפני שהספקנו להירשם,
      // שואלים את אוצריא ישירות מה השפה.
      setTimeout(function () {
        if (booted || !global.Otzaria.call) return;
        try {
          global.Otzaria.call('app.getLocale').then(function (res) {
            if (booted) return;
            if (res && res.success && res.data) {
              init({ app: res.data });
            }
          }).catch(function () {});
        } catch (e) {}
      }, 800);
      return true;
    }

    if (attach()) return;
    var timer = setInterval(function () {
      waited += step;
      if (attach() || waited >= (maxWaitMs || 8000)) clearInterval(timer);
    }, step);
  }

  global.I18n = {
    init: init,
    autoInit: autoInit,
    setLanguage: setLanguage,
    apply: apply,
    t: t,
    get lang() { return lang; },
    get dir()  { return dir; },
    get isTranslated() { return !!dict; }
  };

  autoInit();
})(window);
