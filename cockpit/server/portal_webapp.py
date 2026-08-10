
import os, time
from portal_legacy_ui import CSS, JS, JOBCSS

PORTAL_FILE = __file__
job_get = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def page_html(cfg):
    caps = cfg.get("caps", {})
    ghost = "👻"
    cockpit_ok = caps.get("cockpit")

    try:
        build = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(PORTAL_FILE)))
    except Exception:
        build = "?"

    return f"""<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Brainarbeit</title>
<link rel=stylesheet href="static/xterm.css">
<style>{CSS}</style>
<style>
.lpad{{padding:16px;max-width:1100px;margin:0 auto}}
.lgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
.lcard{{background:rgba(30,30,52,.5);border:1px solid #23233a;border-radius:14px;padding:14px}}
.lcard h3{{margin:0 0 10px;font-size:15px;color:#cfd0ee}}
.lhint,.lsub{{font-size:12px;color:#8a8ab0;font-weight:400}}
.lsub{{margin:12px 0 6px}}
.lrow{{display:flex;flex-wrap:wrap;gap:8px}}
.lbtn{{background:#242444;color:#e8e8ff;border:1px solid #34345a;border-radius:10px;padding:10px 13px;font:inherit;font-size:14px;cursor:pointer}}
.lbtn:hover{{background:#2c2c54}}
.lbtn.sm{{padding:6px 10px;font-size:13px}}
.lbtn.ghost{{background:transparent;border-color:#3a3a5a}}
.llast,.lpipes{{display:flex;flex-direction:column;gap:6px}}
.ljob{{display:flex;gap:8px;align-items:center;text-decoration:none;color:#c9c9de;background:#1b1b30;border-radius:8px;padding:7px 9px}}
.ljp{{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.pipe{{display:flex;justify-content:space-between;align-items:center;gap:10px;background:#1b1b30;border:1px solid #2a2a44;border-radius:10px;padding:9px 11px}}
.pipe.on{{border-color:#3a6b4a;background:#17251b}}
.pipen{{font-size:14px;color:#e0e0f4;display:flex;align-items:center;gap:2px;min-width:0}}
.pipdot{{color:#7fdca0}}
.piptype{{font-size:11px;color:#8a8ab0}}
.pipa{{display:flex;gap:6px;flex:0 0 auto}}
.lpadd{{margin-top:12px}}
.lpadd summary{{cursor:pointer;color:#9a9ac0;font-size:13px}}
.lpadd input,.lpadd select,.lpadd textarea{{display:block;width:100%;box-sizing:border-box;margin:7px 0;background:#0e0e1a;color:#e0e0f4;border:1px solid #2a2a44;border-radius:8px;padding:9px;font:inherit;font-size:14px}}
.ltoggle{{display:flex;align-items:center;gap:8px;margin:12px 0 4px;font-size:13px;color:#c9c9de}}
</style></head><body>
<header>
  <span class=pill>◉ brainarbeit</span><h1>Portal</h1>
  <button id=summon>⚡ Öffnen ▾</button>
  <span id=curlens class=curlens>🏠 Start</span>
  <div id=palette>
    <button class=palit onclick="summon('landing')">🏠 Start <span class=palhint>weitermachen · Pipelines · Einstellungen</span></button>
    <button class=palit onclick="summon('screen')">🖥 Screen <span class=palhint>alle Programme · Browser · anklickbar</span></button>
    <button class=palit onclick="summon('chat')">💬 Chat mit Claude</button>
    <button class=palit onclick="summon('queue')">📋 Queue <span class=palhint>delegierte Arbeit · annehmen / iterieren</span></button>
  </div>
  <span class=spacer></span>
  <span class=lan>{cfg.get('lan_ip','')}:{cfg.get('port','')} · build {build}</span>
</header>
<nav id=tabs>
  <span class=lenslabel title="Bereiche — die Konversation unten arbeitet in jedem">Ansicht ▸</span>
  <button class="tab active" data-t=landing>🏠 Start</button>
  <button class="tab" data-t=screen>🖥 Screen</button>
  <button class="tab" data-t=cockpit>💬 Chat</button>
  <button class="tab" data-t=queue>📋 Queue</button>
  <a class=policytab href="/policy" title="Session-Berechtigungen — was diese Session darf"
     style="margin-left:auto;text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">🛡 Rechte</a>
  <a href="/sessions" title="Laufende Sessions + Rechte der aktuellen Session live anpassen"
     style="text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">🗂 Sessions</a>
  <a href="/freigaben" title="Freigaben — Off-LAN Fernzugriff scharfschalten (Handy-2FA)"
     style="text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">🔓 Freigaben</a>
  <a href="/netprofile" title="Was sendet die Box? — Netzverhalten & guter-Gast-Status im (Firmen-)Netz"
     style="text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">📡 Netzverhalten</a>
  <a href="/selftest" title="Boot-Check — verifiziert nach jedem Neustart alle Kern-Invarianten"
     style="text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">🩺 Boot-Check</a>
  <a href="/blueprints" title="Geräte-Blueprints — ladbare Integrations-Cards (HACS-Modell)"
     style="text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">🧩 Blueprints</a>
  <a href="/vpn" title="VPN — Netz-Tunnel starten und den 2FA-Link anfordern"
     style="text-decoration:none;padding:6px 11px;border-radius:8px;color:inherit;opacity:.82;font-size:14px">🔒 VPN</a>
</nav>
<main>
  <section id=landing class="panel active"><div class=lpad>
    <div class=lgrid>
      <div class=lcard>
        <h3>▸ Weitermachen</h3>
        <div class=lrow>
          <button class=lbtn onclick="summon('chat')">💬 Chat mit Claude</button>
          <button class=lbtn onclick="summon('screen')">🖥 Screen</button>
          <button class=lbtn onclick="summon('queue')">📋 Queue</button>
        </div>
        <div class=lsub>Letzte Arbeit</div>
        <div id=llast class=llast>…</div>
      </div>
      <div class=lcard>
        <h3>▸ Pipelines <span class=lhint>speichern · pausieren · starten</span></h3>
        <div id=lpipes class=lpipes>…</div>
        <details class=lpadd>
          <summary>＋ Pipeline hinzufügen</summary>
          <input id=pip-name placeholder="Name (z. B. Website-Relaunch)">
          <select id=pip-type><option value=task>Command / Shell</option><option value=commission>Auftrag (LLM-Room)</option></select>
          <textarea id=pip-spec placeholder="Shell-Befehl  ODER  Auftragsbeschreibung" rows=2></textarea>
          <input id=pip-mem placeholder="RAM MiB (optional, nur Command)" inputmode=numeric>
          <button class=lbtn id=pip-save>Speichern</button>
        </details>
      </div>
      <div class=lcard>
        <h3>▸ Einstellungen</h3>
        <div class=lrow>
          <button class=lbtn id=set-claude>🔑 Claude-Login</button>
          <button class=lbtn id=set-vpn>🔒 VPN</button>
        </div>
        <label class=ltoggle><input type=checkbox id=set-llm> eigenes LLM statt zentralem</label>
        <div class=lsub id=set-whoami></div>
        <div class=lrow><button class="lbtn ghost" id=set-logout>↪ Abmelden</button></div>
        <div class=lsub id=set-status></div>
      </div>
    </div>
  </div></section>
  <section id=cockpit class=panel>
    {"<div id=term></div>" if cockpit_ok else "<p class=stub>Chat/Konsole nicht verfügbar (tmux fehlt auf dieser Box).</p>"}
  </section>
  <section id=apps class=panel>
    <iframe id=fframe title=app src="about:blank" style="width:100%;height:100%;border:0;background:#fff"></iframe>
  </section>
  <section id=queue class=panel><div class=qpad>
    <div class=qhead id=qhead>…</div>
    <div class=qadd>
      <textarea id=qprompt placeholder="Aufgabe beschreiben — wird governt in der Queue abgearbeitet und in einem Room gebaut…" rows=2></textarea>
      <div class=qrow>
        <select id=qtype><option value=commission>Auftrag (Room)</option><option value=task>Command</option></select>
        <input id=qroom placeholder="Room (optional)">
        <button class=abtn id=qsubmit>＋ In die Queue</button>
      </div>
    </div>
    <div class=qlanes>
      <div class=qlane><h3>Wartet <span class=qn id=qqn></span></h3><div id=qq class=qcol></div></div>
      <div class=qlane><h3>Läuft <span class=qn id=qrn></span></h3><div id=qr class=qcol></div></div>
      <div class=qlane><h3>Fertig <span class=qn id=qdn></span></h3><div id=qd class=qcol></div></div>
    </div>
  </div></section>
  <section id=commission class=panel><div class=cpad>
    <div class=cclar id=cclar></div>
    <textarea id=cprompt placeholder="Was soll gebaut werden? Beschreib dein Ziel — die Agenten bauen, frische Reviewer verifizieren + optimieren, dann bekommst du das Artefakt." rows=4></textarea>
    <div class=crow><input id=cemail type=email placeholder="deine@email (optional — Mail wenn fertig)"></div>
    <div class=crow>
      <button class=abtn id=cclarify>↳ Erst Rückfragen klären</button>
      <button class=abtn id=csubmit>🚀 Auftrag starten</button>
    </div>
    <div id=cjobs class=cjobs></div>
  </div></section>
  <section id=rooms class=panel><div class=rpad>
    <div id=rlist class=rlist></div>
    <div class=rtitle id=rtitle>Room wählen, um live zuzuschauen…</div>
    <pre id=rfeed class=rfeed></pre>
  </div></section>
  <section id=attach class=panel><div class=apad>
    <div class=arow>
      <label class=abtn>📁 Datei<input id=afile type=file multiple hidden></label>
      <button class=abtn id=acam>📷 Kamera</button>
      <button class=abtn id=amic>🎤 Audio</button>
      <button class=abtn id=avid>🎥 Video</button>
    </div>
    <div id=acapture class=acapture></div>
    <div id=alist class=agrid></div>
  </div></section>
  <section id=screen class=panel><div class=scpad>
    <div class=scbar>
      <button class=abtn id=scapps-btn>⊞ Apps</button>
      <button class=abtn data-app=BROWSER>🦊 Browser</button>
      <button class=abtn data-app=TERMINAL>▟ Terminal</button>
      <button class=abtn data-app=FILES>🗂 Files</button>
      <span class=scspacer></span>
      <a class=abtn id=scvnc href="/vnc3" target="_blank" rel="noopener"
         title="Vollbild in neuem Tab öffnen"
         style="text-decoration:none;background:#243; border-color:#3a6b4a">🖥 Direkt steuern (VNC) ✨</a>
      <button class=abtn id=scstop>⏻ Bildschirm beenden</button>
    </div>
    <div class=scwrap id=scwrap>
      <img id=scrimg class=scrimg alt="Bildschirm" tabindex=0>
      <canvas id=scrcanvas class=scrimg tabindex=0 hidden></canvas>
      <iframe id=scrframe class=scrframe title="Bildschirm — direkt steuern (VNC)" allowfullscreen allow="fullscreen" hidden></iframe>
      <div class=schint id=schint>Öffne den Screen-Tab: der Bildschirm startet, dann eine App über die Leiste starten. Klicken + tippen steuert sie.</div>
      <div class=scapps id=scapps hidden>
        <div class=scapps-head>
          <input id=scapps-q class=scapps-q type=search placeholder="App suchen … (alles Installierte)" autocomplete=off>
          <button class=abtn id=scapps-close>✕</button>
        </div>
        <div class=scapps-grid id=scapps-grid></div>
      </div>
    </div>
  </div></section>
</main>
<!-- LINK CAPTURE = the terminal↔browser CONNECTION made visible. Any http(s) URL printed in a
     terminal is scanned NAS-side and stored per-principal; this strip renders that store, so a link
     survives scrollback loss / tab switch / reload, and one tap opens it in the Screen (no select+copy
     on mobile). This is the first cross-part handoff surface. -->
<div id=linkcap></div>
<!-- VOICE = the ambient META-LAYER over ALL lenses (design §1/§13: control is NOT per-tab). The
     one cross-context conversation is always present here, above whatever lens is shown. -->
<div id=voicebar>
  <div class=tlog id=tlog></div>
  <div class=vbrow>
    <button id=ptt class=ptt>🎤 Sprechen</button>
    <input id=ttext placeholder="…sag oder tippe — ich arbeite in JEDEM Reiter (die Konversation liegt über allem)">
    <button id=vbtog class=vbtog title="Verlauf ein/aus">⌃</button>
    <label class=tspeak><input type=checkbox id=tautospeak checked> vorlesen</label>
  </div>
</div>
<script src="static/xterm.js"></script>
<script src="static/addon-fit.js"></script>
<script>window.PP_VOICE={"true" if caps.get("voice") else "false"};</script>
<script>{JS}</script>
</body></html>"""

def job_page_html(jid, cfg, principal=None, is_admin=False):
    j = job_get(jid, principal, is_admin)
    if not j:
        return "<body style='background:#11111c;color:#fff;font-family:monospace;padding:24px'>Auftrag nicht gefunden</body>"
    arts = "".join(f'<a class=jart href="api/jobs/{jid}/art/{urllib.parse.quote(a)}">{html.escape(a)}</a>'
                   for a in j["artifacts"]) or "<span class=jmut>noch keine</span>"
    refresh = "" if j["status"] in ("done", "error") else '<meta http-equiv=refresh content=5>'
    return (f'<!doctype html><html><head><meta charset=utf-8>'
            f'<meta name=viewport content="width=device-width,initial-scale=1">{refresh}'
            f'<title>Auftrag {jid[:6]}</title><style>{CSS}{JOBCSS}</style></head><body class=jbody>'
            f'<header><span class=pill>◉ brainarbeit</span><h1>Auftrag {jid[:6]}</h1><span class=spacer></span>'
            f'<span class="jbadge s-{html.escape(j["status"])}">{html.escape(j["status"])}</span></header>'
            f'<div class=jwrap>'
            f'<h2 class=jh>Ziel (1:1)</h2><div class=jgoal>{html.escape(j["prompt"])}</div>'
            f'<h2 class=jh>Artefakte</h2><div class=jarts>{arts}</div>'
            f'<h2 class=jh>Verlauf (live)</h2><pre class=jlog>{html.escape(j.get("log") or "")}</pre>'
            f'</div></body></html>')
