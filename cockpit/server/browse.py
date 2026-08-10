#!/usr/bin/env python3

import io
import re
import ssl
import sys
import html
import socket
import ipaddress
import urllib.parse
import urllib.request
import urllib.error
import http.client
import http.cookiejar
from html.parser import HTMLParser

PAGE_EP = "/api/browse?url="
RAW_EP = "/api/browse?raw=1&url="

NAV = {"a": "href", "area": "href"}
RESOURCE_ATTRS = {"src", "poster", "data"}
RESOURCE_HREF_TAGS = {"link", "use", "image"}
SRCSET_TAGS = {"img", "source"}
DROP_TAGS = {"base"}

FORM_EP = "/api/browse"
PXURL = "__pxurl"

_UA = "Mozilla/5.0 (X11; Linux x86_64; phantom-browse) Gecko/20100101 Firefox/128.0"

def _ctx():
    c = ssl.create_default_context()
    return c

def _absolute(base, u):

    if u is None:
        return None
    s = u.strip()
    if s == "" or s.startswith(("data:", "javascript:", "mailto:", "tel:", "about:", "blob:", "#", "vbscript:")):
        return None
    return urllib.parse.urljoin(base, s)

def _px(url, raw):
    return (RAW_EP if raw else PAGE_EP) + urllib.parse.quote(url, safe="")

def _rewrite_css(css, base):
    def sub(m):
        raw = m.group(2)
        ab = _absolute(base, raw)
        return "url(" + m.group(1) + (_px(ab, True) if ab else raw) + m.group(1) + ")"
    css = re.sub(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", sub, css)

    css = re.sub(r'@import\s+(["\'])([^"\']+)\1',
                 lambda m: '@import "' + (_px(_absolute(base, m.group(2)), True) or m.group(2)) + '"', css)
    return css

SHIM = """<script>(function(){
try{
 var P=%r;
 function px(u){try{return P+encodeURIComponent(new URL(u,location.href).href);}catch(e){return u;}}
 // intercept clicks that JS may turn into navigations / new tabs
 document.addEventListener('click',function(e){
   var a=e.target&&e.target.closest?e.target.closest('a[href]'):null;
   if(a&&a.getAttribute('href')&&/^https?:/i.test(a.href)){ e.preventDefault(); location.href=px(a.href); }
 },true);
 // window.open -> same-tab proxied nav
 window.open=function(u){ if(u) location.href=px(u); return null; };
}catch(e){}
})();</script>"""

class _Rewriter(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=False)
        self.base = base
        self.out = io.StringIO()
        self._in = None
        self._injected = False

    def _w(self, s):
        self.out.write(s)

    def handle_decl(self, d): self._w("<!%s>" % d)
    def unknown_decl(self, d): self._w("<![%s]>" % d)
    def handle_comment(self, c): self._w("<!--%s-->" % c)
    def handle_pi(self, d): self._w("<?%s>" % d)
    def handle_entityref(self, n): self._w("&%s;" % n)
    def handle_charref(self, n): self._w("&#%s;" % n)

    def handle_data(self, d):
        if self._in == "style":
            self._w(_rewrite_css(d, self.base))
        else:
            self._w(d)

    def handle_endtag(self, t):
        if self._in == t:
            self._in = None
        self._w("</%s>" % t)

    def handle_startendtag(self, t, a): self._start(t, a, True)
    def handle_starttag(self, t, a): self._start(t, a, False)

    def _start(self, tag, attrs, selfclose):
        if tag in DROP_TAGS:
            return
        if tag == "form":
            return self._form(attrs, selfclose)
        if tag in ("style",):
            self._in = "style"
        parts = ["<" + tag]
        for (k, v) in attrs:
            if v is None:
                parts.append(" " + k)
                continue
            nk = k.lower()
            nv = v
            try:
                if tag in NAV and nk == NAV[tag]:
                    ab = _absolute(self.base, v)
                    if ab:
                        nv = _px(ab, False)
                elif nk == "srcset" and tag in SRCSET_TAGS:
                    nv = self._srcset(v)
                elif nk == "href" and tag in RESOURCE_HREF_TAGS:
                    ab = _absolute(self.base, v)
                    if ab:
                        nv = _px(ab, True)
                elif nk in RESOURCE_ATTRS:
                    ab = _absolute(self.base, v)
                    if ab:
                        nv = _px(ab, True)
                elif nk == "style":
                    nv = _rewrite_css(v, self.base)
                elif nk == "integrity":
                    continue
            except Exception:
                nv = v
            q = '"' if '"' not in str(nv) else "'"
            parts.append(" %s=%s%s%s" % (k, q, nv, q))
        parts.append("/>" if selfclose else ">")
        self._w("".join(parts))

        if not self._injected and tag in ("head", "html", "body"):
            self._w(SHIM % PAGE_EP)
            self._injected = True

    def _form(self, attrs, selfclose):

        action = None
        has_method = False
        parts = ["<form"]
        for (k, v) in attrs:
            nk = k.lower()
            if nk == "action":
                action = v
                continue
            if nk == "method":
                has_method = True
            if v is None:
                parts.append(" " + k)
            else:
                q = '"' if '"' not in str(v) else "'"
                parts.append(" %s=%s%s%s" % (k, q, v, q))
        ab = (_absolute(self.base, action) if action else None) or self.base
        parts.append(' action="%s"' % FORM_EP)
        if not has_method:
            parts.append(' method="get"')
        parts.append("/>" if selfclose else ">")
        self._w("".join(parts))
        self._w('<input type="hidden" name="%s" value="%s">' % (PXURL, html.escape(ab, quote=True)))
        if not self._injected:
            self._w(SHIM % PAGE_EP)
            self._injected = True

    def _srcset(self, v):
        out = []
        for part in v.split(","):
            seg = part.strip().split(None, 1)
            if not seg or not seg[0]:
                continue
            ab = _absolute(self.base, seg[0])
            url = _px(ab, True) if ab else seg[0]
            out.append(url + ((" " + seg[1]) if len(seg) > 1 else ""))
        return ", ".join(out)

def rewrite_html(html, base):
    r = _Rewriter(base)
    try:
        r.feed(html)
        r.close()
    except Exception:
        pass
    body = r.out.getvalue()
    if not r._injected:
        body = (SHIM % PAGE_EP) + body
    return body

_ALLOWED_SCHEMES = ("http", "https")

def _ssrf_check(url):
    p = urllib.parse.urlsplit(url)
    if (p.scheme or "").lower() not in _ALLOWED_SCHEMES:
        raise ValueError("scheme not allowed: %r" % p.scheme)
    host = p.hostname
    if not host:
        raise ValueError("no host in url")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError("dns resolution failed: %s" % e)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ValueError("blocked internal address %s for host %r" % (ip, host))
    return True

class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _ssrf_check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def _vet_and_pick(host, port):

    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    vetted = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ValueError("blocked internal address %s for host %r" % (ip, host))
        if vetted is None:
            vetted = info[4][0]
    if vetted is None:
        raise ValueError("no address for host %r" % host)
    return vetted

class _PinnedHTTPConnection(http.client.HTTPConnection):

    def connect(self):
        ip = _vet_and_pick(self.host, self.port or 80)
        self.sock = socket.create_connection((ip, self.port or 80), self.timeout, self.source_address)

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        ip = _vet_and_pick(self.host, self.port or 443)
        sock = socket.create_connection((ip, self.port or 443), self.timeout, self.source_address)

        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)

class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):

        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)

def fetch(url, cj=None, timeout=15, method="GET", data=None):

    _ssrf_check(url)
    handlers = [_PinnedHTTPHandler(), _PinnedHTTPSHandler(context=_ctx()), _GuardedRedirect()]
    if cj is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cj))
    op = urllib.request.build_opener(*handlers)
    hdrs = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "de,en;q=0.7",
    }
    if data is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=(method or "GET").upper())
    r = op.open(req, timeout=timeout)
    ct = r.headers.get_content_type()
    charset = r.headers.get_content_charset() or "utf-8"
    return r.geturl(), ct, r.read(), charset

def form_target(base, pairs, method):

    enc = urllib.parse.urlencode(pairs)
    if (method or "GET").upper() == "POST":
        return base, enc.encode("utf-8")
    p = urllib.parse.urlsplit(base)
    q = (p.query + "&" + enc) if (p.query and enc) else (p.query or enc)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, q, "")), None

def render(url, cj=None, method="GET", data=None):

    final, ct, raw, charset = fetch(url, cj, method=method, data=data)
    if ct in ("text/html", "application/xhtml+xml"):
        htmltext = raw.decode(charset, "replace")
        return "text/html; charset=utf-8", rewrite_html(htmltext, final).encode("utf-8")
    if ct == "text/css":
        css = raw.decode(charset, "replace")
        return "text/css; charset=utf-8", _rewrite_css(css, final).encode("utf-8")
    return ct, raw

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.org"
    cj = http.cookiejar.CookieJar()
    ct, out = render(url, cj)
    sys.stderr.write("content-type: %s  bytes: %d\n" % (ct, len(out)))
    sys.stdout.buffer.write(out[:4000])
