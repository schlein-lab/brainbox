
import json, os, re, threading

_HERE = os.path.dirname(os.path.realpath(__file__))
_SHIPPED_DIR = os.path.join(_HERE, "webapp", "i18n")
_USER_DIR = os.path.expanduser("~/.local/share/brainbox-portal/i18n")

_LANG_CODE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")
_lock = threading.Lock()
_compiled = {}

def _pack_path(base, lang):
    return os.path.join(base, lang + ".json")

def _read_pack(lang):

    cat = {}
    for base in (_SHIPPED_DIR, _USER_DIR):
        p = _pack_path(base, lang)
        try:
            if os.path.isfile(p):
                raw = json.load(open(p, encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if (isinstance(k, str) and isinstance(v, str) and k and v and k != v
                                and '\n' not in k and '\r' not in k):
                            cat[k] = v
        except Exception:
            pass
    return cat

def _matcher(lang):
    if lang in _compiled:
        return _compiled[lang]
    with _lock:
        if lang in _compiled:
            return _compiled[lang]
        cat = _read_pack(lang)
        pat = None
        if cat:
            keys = sorted(cat.keys(), key=len, reverse=True)
            alt = "|".join(re.escape(k) for k in keys)
            pat = re.compile(r'(["\'`>])(\s*)(' + alt + r')(\s*)(["\'`<])')
        _compiled[lang] = (pat, cat)
        return _compiled[lang]

def available_langs():

    langs = set()
    for base in (_SHIPPED_DIR, _USER_DIR):
        try:
            for fn in os.listdir(base):
                if fn.endswith(".json") and _LANG_CODE.match(fn[:-5]):
                    langs.add(fn[:-5])
        except Exception:
            pass
    langs.discard("de")
    return ["de"] + sorted(langs)

def translate(text, lang):

    if not text or not lang or lang == "de":
        return text
    pat, cat = _matcher(lang)
    if pat is None:
        return text

    def _sub(m):
        o, w1, key, w2, c = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        v = cat.get(key)
        if v is None:
            return m.group(0)
        q = c if c in ('"', "'", "`") else (o if o in ('"', "'", "`") else "")
        if q:
            v = v.replace("\\", "\\\\").replace(q, "\\" + q)
            if q == "`":
                v = v.replace("${", "\\${")
        return o + w1 + v + w2 + c

    try:
        return pat.sub(_sub, text)
    except Exception:
        return text

_COOKIE_RE = re.compile(r'(?:^|;\s*)bbxlang=([a-z]{2}(?:-[a-z]{2})?)\b')
_SITE_CONF = "/etc/brainbox/site.conf"

def _site_lang():

    try:
        for ln in open(_SITE_CONF, encoding="utf-8"):
            ln = ln.strip()
            if ln.startswith("LANG_UI"):
                return ln.split("=", 1)[1].strip().strip('"').strip("'").lower()
    except Exception:
        pass
    return ""

def ui_lang(cfg, cookie_header=""):

    try:
        m = _COOKIE_RE.search(cookie_header or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        v = str((cfg or {}).get("LANG_UI") or _site_lang() or "de").lower()
    except Exception:
        v = "de"
    return v if _LANG_CODE.match(v) else "de"

def language_name(lang):

    return {"de": "German", "en": "English", "fr": "French", "es": "Spanish",
            "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
            "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "ar": "Arabic",
            "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}.get(lang, lang)

def save_pack_request(body):

    try:
        lang = str((body or {}).get("lang") or "").strip().lower()
        cat = (body or {}).get("catalog")
        if not _LANG_CODE.match(lang):
            return {"ok": False, "error": "invalid language code (use e.g. 'fr', 'pt-br')"}
        if lang == "de":
            return {"ok": False, "error": "'de' is the source language and cannot be overridden"}
        if not isinstance(cat, dict) or not cat:
            return {"ok": False, "error": "catalog must be a non-empty JSON object"}
        clean = {}
        for k, v in cat.items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                clean[k[:400]] = v[:800]
        if not clean:
            return {"ok": False, "error": "no usable {german: translation} pairs"}
        if len(clean) > 20000:
            return {"ok": False, "error": "catalog too large"}
        os.makedirs(_USER_DIR, exist_ok=True)
        tmp = _pack_path(_USER_DIR, lang) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False)
        os.replace(tmp, _pack_path(_USER_DIR, lang))
        with _lock:
            _compiled.pop(lang, None)
        return {"ok": True, "lang": lang, "entries": len(clean), "langs": available_langs()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

_DOM_TRANSLATOR_JS = r"""(function(){
var C=%%CAT%%;
var SKIP={SCRIPT:1,STYLE:1,NOSCRIPT:1,TEXTAREA:1,CODE:1,PRE:1,KBD:1,SAMP:1};
function skip(n){if(SKIP[n.nodeName])return true;
if(n.getAttribute&&n.getAttribute("data-no-i18n")!==null)return true;
if(n.classList&&(n.classList.contains("xterm")||n.classList.contains("xterm-screen")||n.classList.contains("xterm-rows")))return true;
if(n.isContentEditable)return true;return false;}
function tr(s){if(s==null)return null;var m=/^(\s*)([\s\S]*?)(\s*)$/.exec(s);var c=m[2].replace(/\s+/g,' ');
if(!c)return null;
if(Object.prototype.hasOwnProperty.call(C,c))return m[1]+C[c]+m[3];
if(c.indexOf(' \u00b7 ')>=0){var ps=c.split(' \u00b7 '),ch=false,i,o=[];for(i=0;i<ps.length;i++){var seg=ps[i];if(Object.prototype.hasOwnProperty.call(C,seg)){o.push(C[seg]);ch=true;}else{o.push(seg);}}if(ch)return m[1]+o.join(' \u00b7 ')+m[3];}
return null;}
function attrs(n){var A=["title","placeholder","aria-label","alt"],i,v,r;
for(i=0;i<A.length;i++){if(n.hasAttribute&&n.hasAttribute(A[i])){v=n.getAttribute(A[i]);r=tr(v);if(r!==null&&r!==v)n.setAttribute(A[i],r);}}
if(n.nodeName==="INPUT"&&(n.type==="button"||n.type==="submit")&&n.value){r=tr(n.value);if(r!==null&&r!==n.value)n.value=r;}}
function walk(n){if(n.nodeType===3){var r=tr(n.nodeValue);if(r!==null&&r!==n.nodeValue)n.nodeValue=r;return;}
if(n.nodeType!==1||skip(n))return;attrs(n);for(var c=n.firstChild;c;c=c.nextSibling)walk(c);}
function start(){try{walk(document.body);}catch(e){}
try{new MutationObserver(function(ms){for(var i=0;i<ms.length;i++){var mu=ms[i];
if(mu.type==="characterData"){var t=mu.target;if(t.nodeType===3){var r=tr(t.nodeValue);if(r!==null&&r!==t.nodeValue)t.nodeValue=r;}}
else if(mu.type==="attributes"){if(mu.target.nodeType===1&&!skip(mu.target))attrs(mu.target);}
else{for(var j=0;j<mu.addedNodes.length;j++)walk(mu.addedNodes[j]);}}}).observe(document.body,
{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:["title","placeholder","aria-label","alt","value"]});}catch(e){}}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",start);else start();
})();"""

def inject_switcher(html, lang, selector=True):

    try:
        langs = available_langs()
        cur = lang if lang in langs else "de"
        opts = "".join(
            '<option value="' + l + '"' + (' selected' if l == cur else '') + '>' + l.upper() + '</option>'
            for l in langs)
        opts += '<option value="__import">+ JSON…</option>'
        ctrl = (
            '<select class="icon-btn" id="bbxLang" title="Sprache / Language" aria-label="Sprache" '
            'style="width:auto;min-width:44px;background:transparent;color:inherit;border:0;'
            'font:inherit;cursor:pointer">' + opts + '</select>'
            '<button class="icon-btn" id="paletteBtn" hidden aria-hidden="true" tabindex="-1">⌘K</button>'
            '<input id="bbxLangFile" type="file" accept="application/json,.json" hidden>'
            '<script>(function(){'
            'var sel=document.getElementById("bbxLang");if(!sel)return;var prev=sel.value;'
            'function pick(l){document.cookie="bbxlang="+l+";path=/;max-age=31536000;samesite=Lax";location.reload();}'
            'sel.addEventListener("change",function(){var v=sel.value;'
            'if(v==="__import"){sel.value=prev;document.getElementById("bbxLangFile").click();return;}pick(v);});'
            'var f=document.getElementById("bbxLangFile");f.addEventListener("change",function(){'
            'var file=f.files&&f.files[0];if(!file)return;var code=(file.name||"").replace(/\\.json$/i,"").toLowerCase();'
            'code=(prompt("Language code for this pack (e.g. fr, es, pt-br):",code)||"").trim().toLowerCase();'
            'if(!/^[a-z]{2}(-[a-z]{2})?$/.test(code)){alert("Please enter a valid code like fr or pt-br.");return;}'
            'var r=new FileReader();r.onload=function(){var cat;try{cat=JSON.parse(r.result);}catch(e){alert("Not valid JSON.");return;}'
            'fetch("/api/i18n/import",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},'
            'body:JSON.stringify({lang:code,catalog:cat})}).then(function(x){return x.json();}).then(function(d){'
            'if(d&&d.ok){document.cookie="bbxlang="+code+";path=/;max-age=31536000;samesite=Lax";location.reload();}'
            'else{alert("Import failed: "+((d&&d.error)||"unknown"));}}).catch(function(){alert("Import failed.");});};'
            'r.readAsText(file);});})();</script>'
        )
        if selector:
            out = re.sub(r'<button\b[^>]*id="paletteBtn"[^>]*>.*?</button>',
                         lambda m: ctrl, html, count=1, flags=re.S)
            if out == html:
                out = (out.replace("</body>", ctrl + "</body>", 1) if "</body>" in out else out + ctrl)
        else:
            out = html
        cat = _read_pack(cur) if cur != "de" else {}
        if cat:
            blob = (json.dumps(cat, ensure_ascii=False)
                    .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
            script = "<script>" + _DOM_TRANSLATOR_JS.replace("%%CAT%%", blob) + "</script>"
            out = (out.replace("</body>", script + "</body>", 1) if "</body>" in out else out + script)
        return out
    except Exception:
        return html
