

JOBCSS = """
.jbody{display:block;height:auto;min-height:100vh}
.jwrap{max-width:900px;margin:0 auto;padding:18px}
.jh{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:20px 0 8px}
.jgoal{background:var(--bg2);border:1px solid #23233a;border-radius:12px;padding:14px;white-space:pre-wrap}
.jbadge{font-size:12px;font-weight:700;padding:3px 11px;border-radius:8px;background:#2a2a40;color:var(--fg)}
.s-done{background:#8fd0a0;color:#0b0b14}.s-error{background:#e58a93;color:#0b0b14}
.s-building,.s-reviewing,.s-starting{background:var(--acc);color:#0b0b14}
.jarts{display:flex;flex-wrap:wrap;gap:8px}
.jart{background:var(--acc);color:#0b0b14;text-decoration:none;font-weight:700;border-radius:8px;padding:8px 12px;font-size:13px}
.jmut{color:var(--mut)}
.jlog{background:#0a0a12;border:1px solid #23233a;border-radius:10px;padding:12px;white-space:pre-wrap;font-size:12px;max-height:60vh;overflow:auto}
"""

CSS = """
:root{--bg:#11111c;--bg2:#171724;--fg:#d4d4e2;--mut:#8a8aac;--acc:#8a7fff}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:14px/1.5 'JetBrains Mono',ui-monospace,monospace;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:10px;padding:10px 14px;background:linear-gradient(90deg,#15151f,#11111c);border-bottom:1px solid #23233a}
header .pill{background:var(--acc);color:#0b0b14;font-weight:700;border-radius:8px;padding:2px 9px}
header h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.04em}
header .spacer{flex:1}header .lan{font-size:12px;color:var(--mut)}
nav#tabs{display:none}
header{position:relative}
#summon{background:var(--acc);color:#0b0b14;border:0;border-radius:10px;padding:7px 13px;font:inherit;font-weight:700;cursor:pointer;margin-left:14px}
.curlens{color:var(--fg);font-size:13px;margin-left:10px;opacity:.85}
#palette{display:none;position:absolute;top:46px;left:120px;z-index:600;background:#141422;border:1px solid #2a2a44;border-radius:12px;padding:6px;min-width:240px;box-shadow:0 12px 40px rgba(0,0,0,.5)}
#palette.open{display:block}
.palit{display:block;width:100%;text-align:left;background:transparent;color:var(--fg);border:0;border-radius:8px;padding:10px 12px;font:inherit;cursor:pointer}
.palit:hover{background:#22223a}
.palhint{display:block;color:var(--mut);font-size:11px;font-weight:400;margin-top:2px}
.tab{flex:0 0 auto;background:#171724;border:1px solid #23233a;color:var(--mut);border-radius:9px;padding:8px 14px;font:inherit;cursor:pointer}
.tab.active{background:var(--acc);color:#0b0b14;font-weight:700;border-color:var(--acc)}
main{flex:1;position:relative;overflow:hidden}
.panel{display:none;position:absolute;inset:0;padding:0}
.panel.active{display:block}
#term{position:absolute;inset:0;padding:6px}
.stub{padding:28px;color:var(--mut);font-size:15px}
.xterm{height:100%}
.panel{overflow:auto}
.apad{padding:14px}
.arow{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.abtn{background:#171724;border:1px solid #2a2a40;color:var(--fg);border-radius:10px;padding:11px 15px;font:inherit;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.abtn:hover{border-color:var(--acc)}
.acapture{margin-bottom:12px}.acapture video{max-width:100%;border-radius:10px;border:1px solid #2a2a40;display:block;margin-bottom:8px}
.agrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.scpad{position:absolute;inset:0;display:flex;flex-direction:column}
.scbar{display:flex;gap:8px;align-items:center;padding:8px 10px;border-bottom:1px solid #23233a;flex:0 0 auto}
.sctype{display:flex;gap:8px;align-items:center;padding:8px 10px;border-bottom:1px solid #23233a;flex:0 0 auto;background:#12121f}
.sctypelbl{font-size:16px;opacity:.75;flex:0 0 auto}
.sclocal{flex:1 1 auto;min-width:120px;background:#0b0b14;border:1px solid #3a3a5a;border-radius:9px;color:#e8e8f0;font:inherit;font-size:15px;padding:9px 12px;outline:none}
.sclocal:focus{border-color:#6b7cff;box-shadow:0 0 0 2px rgba(107,124,255,.25)}
.scstatus{flex:0 0 auto;font-size:13px;color:#9a9ab0;min-width:0;max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scspacer{flex:1}
.scwrap{flex:1;position:relative;background:#0a0a12;overflow:auto;display:flex;align-items:center;justify-content:center}
.scrimg{max-width:100%;max-height:100%;display:block;outline:none;cursor:crosshair}
.scrimg[hidden]{display:none}
canvas.scrimg{image-rendering:pixelated}
.scrframe{position:absolute;inset:0;width:100%;height:100%;border:0;display:block;background:#0a0a12}
.scrframe[hidden]{display:none}
/* SPINAL REFLEX: an instant client-side click acknowledgment drawn at the pointer the moment
   you click — zero round-trip to the NAS. The NAS still processes the real click (the brain),
   this just removes the "did it land?" latency (the reflex arc). */
.clickreflex{position:fixed;width:16px;height:16px;margin:-8px 0 0 -8px;border:2px solid var(--acc);border-radius:50%;pointer-events:none;z-index:9998;animation:clickpulse .45s ease-out forwards}
@keyframes clickpulse{from{transform:scale(.35);opacity:.95}to{transform:scale(2.4);opacity:0}}
.schint{position:absolute;color:var(--mut);font-size:13px;padding:20px;text-align:center;max-width:520px}
/* App store drawer (overlays the stream) */
.scapps{position:absolute;inset:0;background:rgba(10,10,18,.97);display:flex;flex-direction:column;z-index:5}
.scapps[hidden]{display:none}
.scapps-head{display:flex;gap:8px;padding:12px;border-bottom:1px solid #23233a;flex:0 0 auto}
.scapps-q{flex:1;background:#12121e;border:1px solid #2a2a40;color:var(--fg);border-radius:10px;padding:11px 14px;font:inherit;outline:none}
.scapps-q:focus{border-color:var(--acc)}
.scapps-grid{flex:1;overflow:auto;padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;align-content:start}
.acard{background:#141422;border:1px solid #262640;border-radius:12px;padding:14px 12px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;transition:border-color .1s,transform .05s}
.acard:hover{border-color:var(--acc)}
.acard:active{transform:scale(.97)}
.acard .ai{width:44px;height:44px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:#0b0b14}
.acard .an{font-size:12.5px;color:var(--fg);line-height:1.25;word-break:break-word}
.acard .as{font-size:10px;color:#8a7fe0;border:1px solid #34305a;border-radius:6px;padding:0 5px;margin-top:2px}
.scapps-empty{color:var(--mut);padding:20px;grid-column:1/-1;text-align:center}
.acard{background:var(--bg2);border:1px solid #23233a;border-radius:12px;padding:8px;font-size:11px;overflow:hidden}
.acard img,.acard video{width:100%;border-radius:7px;display:block}
.acard audio{width:100%}
.acard .an{color:var(--mut);margin:6px 0;word-break:break-all;font-size:10px}
.acard .aa{display:flex;gap:6px;flex-wrap:wrap}
.acard .aa button,.acard .aa a{background:var(--acc);color:#0b0b14;border:0;border-radius:7px;padding:5px 8px;font:inherit;font-size:11px;font-weight:700;cursor:pointer;text-decoration:none}
.acard .afile{padding:18px 6px;text-align:center;color:var(--fg)}
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--acc);color:#0b0b14;font-weight:700;padding:9px 16px;border-radius:10px;z-index:99}
.tpad{display:flex;flex-direction:column;height:100%;padding:12px}
.tlog{flex:1;overflow:auto;display:flex;flex-direction:column;gap:8px;padding-bottom:8px}
.tmsg{max-width:85%;padding:9px 13px;border-radius:13px;white-space:pre-wrap;word-break:break-word;font-size:14px}
.tmsg.me{align-self:flex-end;background:var(--acc);color:#0b0b14}
.tmsg.bot{align-self:flex-start;background:var(--bg2);border:1px solid #23233a}
.trow{display:flex;gap:8px;align-items:center}
.ptt{flex:0 0 auto;background:var(--acc);color:#0b0b14;border:0;border-radius:12px;padding:13px 16px;font:inherit;font-weight:700;cursor:pointer}
.ptt.rec{background:#e58a93;animation:pulse 1s infinite}
@keyframes pulse{50%{opacity:.55}}
.trow input{flex:1;background:#0e0e18;border:1px solid #2a2a40;color:var(--fg);border-radius:11px;padding:11px;font:inherit}
.tspeak{color:var(--mut);font-size:12px;margin-top:8px;display:flex;gap:6px;align-items:center}
/* LINK CAPTURE strip — the terminal↔browser handoff surface, just above the voice bar.
   Collapsed by default (thin header only) so it never eats the mobile screen; ✕ minimizes it to a
   tiny floating pill, tapping the pill (or a new link) brings it back. */
#linkcap{flex:0 0 auto;display:none;background:#0e1a12;border-top:1px solid #2a4a34;max-height:38vh;overflow:auto}
#linkcap.on{display:block}
.lchead{display:flex;align-items:center;gap:6px;padding:0 6px 0 2px}
.lchtitle{flex:1;min-width:0;cursor:pointer;color:#7fbf97;font-size:12px;font-weight:700;padding:9px 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lcclear{flex:0 0 auto;background:transparent;border:0;color:#6a8a76;font-size:12px;cursor:pointer;padding:9px 6px}
.lcrow{display:flex;align-items:center;gap:8px;padding:8px 10px;border-top:1px solid #16281c}
.lcurl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#bfe6cf;font-size:12.5px;font-family:'JetBrains Mono',monospace}
.lcbtn{flex:0 0 auto;background:#1e5a34;color:#eafff0;border:0;border-radius:9px;padding:9px 12px;font:inherit;font-weight:700;font-size:13px;cursor:pointer}
.lcbtn:hover{background:#247040}
.lcbtn:disabled{opacity:.6;cursor:default}
.lcx{flex:0 0 auto;background:transparent;border:0;color:#9aa;font-size:16px;cursor:pointer;padding:8px 10px;line-height:1}
#linkpill{position:fixed;right:12px;bottom:74px;z-index:500;background:#1e5a34;color:#eafff0;border:0;border-radius:20px;padding:9px 13px;font:inherit;font-weight:700;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.45);cursor:pointer}
/* VOICE META-LAYER (design §1/§13: the one conversation over ALL lenses, always present) */
.lenslabel{flex:0 0 auto;color:var(--mut);font-size:11px;align-self:center;opacity:.6;padding-right:2px}
#voicebar{flex:0 0 auto;background:#12121e;border-top:2px solid var(--acc);box-shadow:0 -6px 20px rgba(0,0,0,.4);z-index:400}
#voicebar .vbrow{display:flex;gap:8px;align-items:center;padding:9px 10px}
#voicebar #ttext{flex:1;background:#0b0b14;color:#eee;border:1px solid #2a2a44;border-radius:11px;padding:11px 14px;font:inherit}
#voicebar #tlog{max-height:0;overflow:auto;transition:max-height .25s ease;padding:0 12px;display:flex;flex-direction:column;gap:8px}
#voicebar.open #tlog{max-height:40vh;padding:12px}
.vbtog{flex:0 0 auto;background:#171724;border:1px solid #2a2a44;color:var(--mut);border-radius:10px;padding:11px 12px;cursor:pointer;font:inherit;transition:transform .2s}
#voicebar.open .vbtog{transform:rotate(180deg)}
.cpad{padding:14px;display:flex;flex-direction:column;gap:10px}
.cclar{display:flex;flex-direction:column;gap:8px}
#cprompt{background:#0e0e18;border:1px solid #2a2a40;color:var(--fg);border-radius:11px;padding:12px;font:inherit;resize:vertical}
.crow{display:flex;gap:8px;flex-wrap:wrap}.crow input{flex:1;min-width:180px;background:#0e0e18;border:1px solid #2a2a40;color:var(--fg);border-radius:11px;padding:11px;font:inherit}
#csubmit{background:var(--acc);color:#0b0b14;font-weight:700}
.cjobs{display:flex;flex-direction:column;gap:7px;margin-top:6px}
.cjob{display:flex;gap:9px;align-items:center;background:var(--bg2);border:1px solid #23233a;border-radius:10px;padding:9px 11px;text-decoration:none;color:var(--fg);font-size:13px}
.cjob:hover{border-color:var(--acc)}.cjp{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rpad{display:flex;flex-direction:column;height:100%;padding:12px;gap:8px}
.rlist{display:flex;gap:7px;flex-wrap:wrap}
.rchip{background:#171724;border:1px solid #2a2a40;color:var(--mut);border-radius:9px;padding:8px 12px;font:inherit;font-size:12px;cursor:pointer}
.rchip.up{color:var(--fg);border-color:var(--acc)}
.rtitle{font-size:12px;color:var(--mut)}
.rfeed{flex:1;overflow:auto;background:#0a0a12;border:1px solid #23233a;border-radius:10px;padding:11px;white-space:pre-wrap;font-size:12px;margin:0}
.qpad{display:flex;flex-direction:column;height:100%;padding:12px;gap:10px}
.qhead{background:#0e0e18;border:1px solid #23233a;border-radius:10px;padding:9px 12px;font-size:12px;color:var(--mut)}
.qhead b{color:var(--fg);font-weight:600}.qwarn{color:#e58a93;font-weight:700}
.qadd{display:flex;flex-direction:column;gap:8px}
#qprompt{background:#0e0e18;border:1px solid #2a2a40;color:var(--fg);border-radius:11px;padding:11px;font:inherit;resize:vertical}
.qrow{display:flex;gap:8px;flex-wrap:wrap}
.qrow select,.qrow input{background:#0e0e18;border:1px solid #2a2a40;color:var(--fg);border-radius:10px;padding:10px;font:inherit}
.qrow input{flex:1;min-width:120px}#qsubmit{background:var(--acc);color:#0b0b14;font-weight:700}
.qlanes{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;min-height:0}
.qlane{display:flex;flex-direction:column;min-height:0;background:#0d0d16;border:1px solid #1d1d2e;border-radius:12px;padding:8px}
.qlane h3{margin:2px 4px 8px;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--mut)}
.qn{display:inline-block;min-width:18px;text-align:center;background:#23233a;color:var(--fg);border-radius:6px;padding:0 5px;font-size:11px}
.qcol{overflow:auto;display:flex;flex-direction:column;gap:7px;flex:1}
.qcard{background:var(--bg2);border:1px solid #23233a;border-radius:10px;padding:8px 10px;font-size:12px}
.qcard.s-running{border-color:var(--acc)}.qcard.s-failed,.qcard.s-timeout{border-color:#e58a93}
.qcard.s-done{opacity:.78}
.qtop{display:flex;align-items:center;gap:7px}
.qid{color:var(--acc);font-weight:700}.qtag{color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.qx{margin-left:auto;background:#2a2a40;color:var(--mut);border:0;border-radius:6px;padding:2px 7px;cursor:pointer;font:inherit}
.qx:hover{background:#e58a93;color:#0b0b14}
.qbar{height:6px;background:#23233a;border-radius:4px;overflow:hidden;margin:6px 0 3px}
.qbar i{display:block;height:100%;background:var(--acc);border-radius:4px;transition:width .4s}
.qbar i.ind{width:35%;animation:qslide 1.2s infinite ease-in-out}
@keyframes qslide{0%{margin-left:-35%}100%{margin-left:100%}}
.qpct{font-size:10px;color:var(--mut)}.qmsg{color:var(--fg);margin:3px 0;font-size:11px;word-break:break-word}
.qroom{background:var(--acc);color:#0b0b14;border:0;border-radius:7px;padding:3px 8px;font:inherit;font-size:11px;font-weight:700;cursor:pointer;margin-top:4px}
.qmeta{color:var(--mut);font-size:10px;margin-top:4px}
@media(max-width:760px){.qlanes{grid-template-columns:1fr;overflow:auto}}
"""

JS = """
const tabs=document.querySelectorAll('.tab'), panels=document.querySelectorAll('.panel');
// ── PHANTOM SCREEN TAB (Stage C) — on-demand streamed, operable seat ──
(function(){
  var img=document.getElementById('scrimg'), hint=document.getElementById('schint'),
      canvas=document.getElementById('scrcanvas');
  if(!img) return;
  var started=false;
  // Client-composite (WASM) lane state. `mode` is decided per seat-start by the box's
  // /placement endpoint. The MJPEG <img> lane is the DEFAULT + fallback and is never removed.
  var mode='server-composite', compositor=null, sceneAbort=null, wasmMod=null;
  var SCREEN_W=960, SCREEN_H=600;   // seat resolution (overwritten by placement.screen)
  var _authLost=false;
  function sessionLost(){
    if(_authLost) return; _authLost=true;
    var b=document.createElement('div');
    b.style.cssText='position:fixed;left:0;right:0;top:0;z-index:99999;background:#c0392b;color:#fff;padding:12px 16px;font:600 15px system-ui;text-align:center;cursor:pointer';
    b.textContent='⚠ Sitzung abgelaufen (Portal wurde neu gestartet) — hier klicken zum neu Anmelden';
    b.onclick=function(){location.href='/';};
    document.body.appendChild(b);
  }
  // POST helper: on 401/403 the session is gone (portal restarted) — SURFACE it, never swallow.
  function post(p,b){return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(function(r){
    if(r.status===401||r.status===403){ sessionLost(); return {ok:false,_auth:false}; }
    return r.json().catch(function(){return {ok:false};});
  }).catch(function(){return {ok:false};});}

  // The element the user is currently pointing/typing at: the canvas in client-composite mode,
  // else the MJPEG <img>. coords()/keydown are wired to BOTH and read this to stay generic.
  function activeEl(){ return (mode==='client-composite' && canvas && !canvas.hidden) ? canvas : img; }

  // Gather the client's rendering capabilities → the box decides where compositing happens.
  function gatherCaps(){
    var gl=false; try{ gl=!!document.createElement('canvas').getContext('webgl2'); }catch(e){}
    return {
      webgl2: gl?1:0,
      webgpu: (navigator.gpu?1:0),
      cores: (navigator.hardwareConcurrency||4),
      mem_mb: ((navigator.deviceMemory||4)*1024),
      hw_decode: (window.VideoDecoder?1:0),
      native: 0,
      throttled: (document.hidden?1:0)
    };
  }
  function capsQuery(){
    var c=gatherCaps(), q=[];
    for(var k in c){ q.push(k+'='+encodeURIComponent(c[k])); }
    return q.join('&');
  }

  // ── MJPEG lane (server-composite / display-only fallback) — UNCHANGED behaviour ──
  var frame=document.getElementById('scrframe');
  // ── VNC lane (our own RFB client, embedded) — the DEFAULT view + input path ──
  function startVnc(){
    mode='vnc'; stopScene();
    if(canvas){ canvas.hidden=true; } img.hidden=true; img.removeAttribute('src');
    if(hint)hint.style.display='none';
    if(frame){ if(!frame.getAttribute('src')) frame.src='/vnc3'; frame.hidden=false; }
  }
  function startMjpeg(){
    mode='server-composite';
    if(canvas){ canvas.hidden=true; }
    img.hidden=false;
    img.src='screen/stream?token='+encodeURIComponent(window.PP_WSTOKEN||'')+'&t='+Date.now();
    if(hint)hint.style.display='none';
  }

  // ── Client-composite lane: WASM compositor + length-prefixed SceneFrame stream ──
  // WASM glue import path is a PLACEHOLDER (Track A owns the real path — see blockers). The
  // module must export a `Compositor` class + a default init(). Copy Track A's pkg/ to the
  // portal static dir as /static/phantom-wasm/ so this resolves.
  var WASM_JS='/static/phantom-wasm/phantom_wasm.js';
  function loadWasm(){
    if(wasmMod) return Promise.resolve(wasmMod);
    return import(WASM_JS).then(function(m){
      // wasm-pack --target web exports a default init() that fetches the .wasm; call it first.
      var initP = (typeof m.default==='function') ? m.default() : Promise.resolve();
      return initP.then(function(){ wasmMod=m; return m; });
    });
  }
  function startClientComposite(){
    mode='client-composite';
    img.hidden=true; img.removeAttribute('src');
    canvas.width=SCREEN_W; canvas.height=SCREEN_H;
    canvas.hidden=false;
    if(hint)hint.style.display='none';
    loadWasm().then(function(m){
      canvas.id='scrcanvas';
      compositor = new m.Compositor('scrcanvas');
      pumpScene();
    }).catch(function(err){
      // WASM unavailable/failed → gracefully fall back to the always-working MJPEG lane.
      if(window.console)console.warn('client-composite init failed, falling back to MJPEG:',err);
      startMjpeg();
    });
  }
  // Read the chunked /api/screen/scene octet-stream, reassemble [u32 LE len][frame] frames,
  // and hand each frame to the WASM compositor. Stops when the tab is hidden (throttled) or
  // the seat stops.
  function pumpScene(){
    if(sceneAbort){ try{sceneAbort.abort();}catch(e){} }
    sceneAbort = (typeof AbortController!=='undefined') ? new AbortController() : null;
    var opts = sceneAbort ? {signal:sceneAbort.signal} : {};
    fetch('api/screen/scene', opts).then(function(resp){
      if(!resp.ok || !resp.body){ throw new Error('scene stream not ok'); }
      var reader=resp.body.getReader();
      var buf=new Uint8Array(0);
      function concat(a,b){ var c=new Uint8Array(a.length+b.length); c.set(a,0); c.set(b,a.length); return c; }
      function drain(){
        // Extract every complete [u32 len][payload] frame currently buffered.
        while(buf.length>=4){
          var dv=new DataView(buf.buffer, buf.byteOffset, 4);
          var len=dv.getUint32(0, true);      // little-endian frame length prefix
          if(buf.length < 4+len) break;        // wait for the rest of this frame
          var frame=buf.subarray(4, 4+len);
          if(compositor){ try{ compositor.on_frame(frame); }catch(e){ if(window.console)console.warn('on_frame',e); } }
          buf=buf.subarray(4+len);
        }
      }
      function step(){
        return reader.read().then(function(res){
          if(res.done) return;
          if(res.value && res.value.length){ buf=concat(buf, res.value); drain(); }
          if(document.hidden){ // tab hidden → stop pulling (throttled); reader will be released
            try{reader.cancel();}catch(e){}
            return;
          }
          return step();
        });
      }
      return step();
    }).catch(function(e){
      if(e && e.name==='AbortError') return;   // deliberate stop
      if(window.console)console.warn('scene stream ended:',e);
    });
  }
  function stopScene(){ if(sceneAbort){ try{sceneAbort.abort();}catch(e){} sceneAbort=null; } }

  // Entry point: start the seat, ask the box where to composite, then open the right lane.
  function startSeat(){
    return post('api/screen/start',{}).then(function(r){
      if(!(r&&r.ok)){ if(hint){ hint.textContent='Seat-Start fehlgeschlagen: '+((r&&r.error)||'?'); } return r; }
      started=true;
      // Decide placement. If the box can't answer (old build / no scene lane), the MJPEG
      // fallback keeps the tab fully working — client-composite is a pure enhancement.
      return fetch('api/screen/placement?'+capsQuery()).then(function(pr){return pr.json();}).then(function(pl){
        if(pl && pl.screen){ SCREEN_W=pl.screen.w||SCREEN_W; SCREEN_H=pl.screen.h||SCREEN_H; }
        // LANE CHOICE. The client-composite (WASM) lane currently ships RAW uncompressed
        // surface buffers (~2.3 MB/frame; a keyframe ~4.6 MB) — and full-repainting apps like
        // Firefox damage their whole surface every frame, so every "delta" is a full 2.3 MB.
        // That is ~75x MORE bytes than the JPEG MJPEG lane (~30 KB/frame), so over LAN the WASM
        // lane LAGS badly despite compositing on the client GPU. Until the scene transport is
        // compressed (JPEG-per-surface, decoded in WASM), DEFAULT to the MJPEG lane, which is
        // both usable and already client-side-DECODED. Opt into the raw WASM lane with
        // ?lane=wasm for testing the compressed path once it lands.
        var wantWasm = /[?&]lane=wasm(\b|=1)/.test(location.search);
        var wantMjpeg = /[?&]lane=mjpeg(\b|=1)/.test(location.search);
        if(wantWasm && pl && pl.mode==='client-composite' && canvas && ('WebAssembly' in window)){
          startClientComposite();
        } else if(wantMjpeg){
          startMjpeg();
        } else {
          startVnc();
        }
        return r;
      }).catch(function(){ startVnc(); return r; });
    });
  }
  function coords(e){
    var el=activeEl();
    var rc=el.getBoundingClientRect();
    // Native (source) size: <img> exposes naturalWidth/Height; the canvas is sized to the seat.
    var nw=(el===img ? (img.naturalWidth||SCREEN_W) : (canvas.width||SCREEN_W));
    var nh=(el===img ? (img.naturalHeight||SCREEN_H) : (canvas.height||SCREEN_H));
    var x=Math.round((e.clientX-rc.left)*nw/(rc.width||1));
    var y=Math.round((e.clientY-rc.top)*nh/(rc.height||1));
    return {x:Math.max(0,Math.min(nw-1,x)), y:Math.max(0,Math.min(nh-1,y))};
  }
  // TAP/CLICK-ONLY — the mobile model (deliberate, load-bearing). The cursor lives CLIENT-SIDE
  // (the browser's own cursor over this element, instant); we NEVER stream mousemove/hover. Hover
  // is a desktop gimmick that (a) wastes seat resources — every move makes the encode-bound box
  // re-render+re-encode, so real actions queue behind the backlog and the user FEELS latency —
  // and (b) doesn't exist on touch devices, which this must also serve: mobile users just tap.
  // A click carries its own position (the box does enter+motion+button there), so no prior move
  // is needed. Only clicks/scroll/keys go to the box. No mousemove handler on purpose.
  // KEYBOARD PASSTHROUGH → /api/screen/key → box key-forge. Typing is a tap, NOT a hover, so it
  // is allowed under the tap-only rule. We normalize the browser key event and let the portal
  // resolve it to the box's evdev wire format (printable text vs named keycode vs Enter).
  // KEYBOARD BATCHING — fast typing must not stall at ~1 key/sec. We coalesce rapid
  // PRINTABLE keystrokes with a ~40ms debounce into ONE `screen text "<string>"` (the box
  // maps the whole string via layout in a single do_inject batch), and flush that pending
  // batch IMMEDIATELY when a NAMED key (Enter/Backspace/Tab/… /space) arrives or a >~40ms
  // pause elapses. Ordering is preserved two ways: (1) chars append to `pendBuf` in event
  // order and always flush BEFORE the named key that interrupted them; (2) every send goes
  // through `keyQ`, a single serialized promise chain — the next fetch only starts after the
  // previous one resolves — so the box receives commands in exactly the order typed even
  // though the portal is a threaded server. Per-key sends remain the fallback path.
  var KEY_DEBOUNCE_MS = 40;
  var pendBuf = "";           // accumulated printable chars not yet sent
  var pendTimer = null;       // debounce timer for the current batch
  var keyQ = Promise.resolve();  // serialized send chain (guarantees box-side ordering)
  function enqueue(fn){ keyQ = keyQ.then(fn, fn); return keyQ; }
  function sendText(s){ if(!s) return; enqueue(function(){ return post('api/screen/key',{text:s}); }); }
  function sendNamed(k){ enqueue(function(){ return post('api/screen/key',{key:k}); }); }
  function clearPendTimer(){ if(pendTimer){ clearTimeout(pendTimer); pendTimer=null; } }
  function flushPend(){ clearPendTimer(); if(pendBuf){ var s=pendBuf; pendBuf=""; sendText(s); } }
  function onKey(e){
    // NO MORE raw per-keystroke forging. That path forged each key through the ~2fps stream, and a
    // laggy input stack re-emits keydown so Firefox (binds wl_seat twice) produced DOUBLE + wrong
    // letters (the "type h, get a second c" bug). Typing now ALWAYS goes through the native local
    // field below — which cannot double. A keystroke while the stream is focused simply REDIRECTS
    // you into that field, carrying the first character; the rest is ordinary native input.
    var k=e.key; if(k==null || e.repeat) return;
    var f=document.getElementById('sclocal'); if(!f) return;
    if(k.length===1 && !e.ctrlKey && !e.metaKey && !e.altKey){
      e.preventDefault(); f.focus(); f.value += k;      // carry the first char into the field
    } else if(k==='Enter'||k==='Backspace'||k==='Tab'||k===' '){
      e.preventDefault(); f.focus();                    // land in the field; commit from there
    }
  }
  // Any focus loss / tab switch should not strand a half-typed batch.
  window.addEventListener('blur', flushPend);
  document.addEventListener('visibilitychange', function(){ if(document.hidden) flushPend(); });
  // SPINAL REFLEX (client-side): draw an instant click acknowledgment at the pointer, the
  // moment the click happens — synchronously, BEFORE the fetch to the NAS. Pure local feedback
  // (the reflex arc); the NAS still gets the real click and the app's response streams back
  // (the brain, reconciling). Removes the "did my click register?" round-trip latency.
  function clickReflex(x,y){
    try{
      var d=document.createElement('div'); d.className='clickreflex';
      d.style.left=x+'px'; d.style.top=y+'px';
      document.body.appendChild(d);
      setTimeout(function(){ if(d&&d.parentNode) d.parentNode.removeChild(d); }, 460);
    }catch(e){}
  }
  function bindEl(el){
    el.addEventListener('click',function(e){clickReflex(e.clientX,e.clientY);var c=coords(e);post('api/screen/input',{action:'click',x:c.x,y:c.y});var f=document.getElementById('sclocal');if(f)f.focus();});
    el.addEventListener('contextmenu',function(e){e.preventDefault();var c=coords(e);post('api/screen/input',{action:'click',x:c.x,y:c.y,btn:'right'});});
    el.addEventListener('wheel',function(e){e.preventDefault();var c=coords(e);post('api/screen/input',{action:'scroll',x:c.x,y:c.y,n:(e.deltaY<0?1:-1)});},{passive:false});
    el.addEventListener('keydown',onKey);
  }
  bindEl(img);
  if(canvas) bindEl(canvas);
  // Throttle: when the tab is hidden stop pulling the scene stream; resume on re-show.
  document.addEventListener('visibilitychange',function(){
    if(mode!=='client-composite') return;
    if(document.hidden){ stopScene(); }
    else if(started){ pumpScene(); }
  });
  var stop=document.getElementById('scstop');
  if(stop)stop.addEventListener('click',function(){post('api/screen/stop',{});stopScene();if(frame){frame.hidden=true;frame.removeAttribute('src');}img.removeAttribute('src');img.hidden=false;if(canvas)canvas.hidden=true;started=false;compositor=null;mode='server-composite';if(hint){hint.style.display='';hint.textContent='Seat gestoppt. Screen-Tab erneut öffnen zum Starten.';}});
  document.querySelectorAll('#screen .scbar [data-app]').forEach(function(b){
    b.addEventListener('click',function(){ if(!started){startSeat().then(function(){setTimeout(function(){post('api/screen/launch',{prog:b.getAttribute('data-app')});},600);});} else {post('api/screen/launch',{prog:b.getAttribute('data-app')});} });
  });
  var tab=document.querySelector('.tab[data-t=screen]');
  if(tab)tab.addEventListener('click',function(){ if(!started) startSeat();
    setTimeout(function(){ var f=document.getElementById('sclocal'); if(f) f.focus(); }, 120); });

  // LOCAL TYPE-AND-COMMIT (the reliable typing path). The per-keystroke passthrough forges each
  // key through the ~2fps stream — laggy, and on some input stacks the browser re-emits keydown so
  // Firefox (which binds wl_seat twice) shows doubles. Instead: type into this LOCAL field at full
  // native speed (instant, no lag, no doubling, you SEE every char), then commit the whole string
  // in ONE atomic `screen text` injection — which is proven to land exactly, no doubles. This is
  // the execution-client/data-NAS idea for text: the keystrokes live on the client; only the
  // finished string crosses to the seat.
  (function localType(){
    var f=document.getElementById('sclocal'); if(!f) return;
    function inp(o){ return post('api/screen/input', o); }
    function setStatus(msg, ok){
      var s=document.getElementById('scstatus'); if(!s) return;
      s.textContent=msg||'';
      s.style.color = ok===true ? '#6be675' : (ok===false ? '#ff7a7a' : '#c7c7d8');
    }
    function commit(withEnter){
      var v=f.value.trim();
      if(withEnter){
        // Address / search → DETERMINISTIC navigation via Firefox's own protocol (Marionette).
        // No forging, no focus race, no lag dependence — the reliable path. A bare host gets
        // https://, free text becomes a web search (handled box-side). Every outcome is shown in
        // the status line so it can NEVER fail silently again.
        if(!v){ setStatus('leer — nichts zu öffnen', false); return; }
        setStatus('→ öffne '+v+' …', null);
        post('api/browser/navigate',{url:v}).then(function(r){
          if(r && r.ok){ setStatus('✓ geöffnet: '+(r.url||v), true); f.value=''; f.focus(); }
          else if(r && r._auth===false){ setStatus('✗ Sitzung abgelaufen — neu anmelden', false); }
          else { setStatus('✗ Fehler: '+((r&&r.error)||'keine Antwort vom Server'), false); }
        });
        return;
      }
      // Plain "In Feld tippen" → type the whole string into whatever field is focused IN the page
      // (one atomic injection, no doubling). Use after clicking a search box / form field.
      if(!v){ setStatus('leer — nichts zu tippen', false); return; }
      setStatus('→ tippe in Feld …', null);
      inp({action:'text',text:v}).then(function(r){
        if(r && r._auth===false){ setStatus('✗ Sitzung abgelaufen — neu anmelden', false); }
        else { setStatus('✓ getippt: '+v, true); f.value=''; f.focus(); }
      });
    }
    f.addEventListener('keydown',function(e){
      e.stopPropagation();                 // never let the seat's per-key forge see local typing
      if(e.key==='Enter'){ e.preventDefault(); commit(true); }
    });
    var b1=document.getElementById('scsend'); if(b1) b1.addEventListener('click',function(){ commit(false); });
    var b2=document.getElementById('scsendret'); if(b2) b2.addEventListener('click',function(){ commit(true); });
    var bb=document.getElementById('scback'); if(bb) bb.addEventListener('click',function(){ inp({action:'key',code:14}); f.focus(); }); // Backspace=14
    var ad=document.getElementById('scaddr');
    if(ad) ad.addEventListener('click',function(){ inp({action:'keycode',code:38,mod:4}).then(function(){ f.focus(); }); }); // Ctrl+L
  })();

  // ── App store drawer: launch ANY installed app, not just the 3 favourites ──
  var appsBtn=document.getElementById('scapps-btn'),
      drawer=document.getElementById('scapps'),
      grid=document.getElementById('scapps-grid'),
      q=document.getElementById('scapps-q'),
      closeBtn=document.getElementById('scapps-close');
  var APPS=null, appsLoading=false;
  function ensureSeat(){ return started ? Promise.resolve({ok:true}) : startSeat(); }
  function spawnExec(exec){
    var wasStarted=started;
    ensureSeat().then(function(r){
      if(!r||!r.ok) return;
      setTimeout(function(){ post('api/screen/spawn',{exec:exec}); }, wasStarted?0:400);
    });
  }
  function initial(n){ return ((n||'?').trim().charAt(0)||'?').toUpperCase(); }
  function hue(s){ var h=0,i; for(i=0;i<s.length;i++){ h=(h*31+s.charCodeAt(i))>>>0; } return h%360; }
  function renderApps(filter){
    if(!grid) return;
    var f=(filter||'').toLowerCase().trim();
    var list=(APPS||[]).filter(function(a){ return !f || a.name.toLowerCase().indexOf(f)>=0 || (a.comment||'').toLowerCase().indexOf(f)>=0; });
    if(!list.length){ grid.innerHTML='<div class=scapps-empty>'+(APPS?'Keine Treffer.':'Lade Apps …')+'</div>'; return; }
    grid.textContent='';
    list.forEach(function(a){
      var card=document.createElement('div'); card.className='acard'; card.title=a.comment||a.name;
      var ic=document.createElement('div'); ic.className='ai'; ic.textContent=initial(a.name);
      ic.style.background='hsl('+hue(a.name)+',55%,62%)';
      var nm=document.createElement('div'); nm.className='an'; nm.textContent=a.name;
      card.appendChild(ic); card.appendChild(nm);
      if(a.snap){ var sp=document.createElement('div'); sp.className='as'; sp.textContent='snap'; card.appendChild(sp); }
      card.addEventListener('click',function(){ spawnExec(a.exec); closeDrawer(); });
      grid.appendChild(card);
    });
  }
  function loadApps(){
    if(APPS||appsLoading) return;
    appsLoading=true;
    fetch('api/screen/apps').then(function(r){return r.json();}).then(function(d){
      APPS=(d&&d.apps)||[]; appsLoading=false; renderApps(q?q.value:'');
    }).catch(function(){ appsLoading=false; if(grid)grid.innerHTML='<div class=scapps-empty>Fehler beim Laden.</div>'; });
  }
  function openDrawer(){ if(!drawer)return; drawer.hidden=false; loadApps(); renderApps(q?q.value:''); if(q){q.focus();} }
  function closeDrawer(){ if(drawer)drawer.hidden=true; }
  if(appsBtn)appsBtn.addEventListener('click',function(){ (drawer&&drawer.hidden)?openDrawer():closeDrawer(); });
  if(closeBtn)closeBtn.addEventListener('click',closeDrawer);
  if(q)q.addEventListener('input',function(){ renderApps(q.value); });
  if(q)q.addEventListener('keydown',function(e){ if(e.key==='Escape')closeDrawer(); });
})();

// Host-Shell RETIRED: the second xterm (term2/ws2 -> /ws/term?target=shell) opened a plain login
// shell on the HOST. That lane is gone server-side (4004), so its client half is gone with it.
let fitAddon, term, ws;
tabs.forEach(b=>b.onclick=()=>{
  tabs.forEach(x=>x.classList.remove('active'));b.classList.add('active');
  panels.forEach(p=>p.classList.toggle('active',p.id===b.dataset.t));
  if(b.dataset.t==='cockpit' && fitAddon){setTimeout(()=>{fitAddon.fit();sendResize()},50)}
  if(b.dataset.t==='landing'){ loadLast(); loadPipes(); }
});
function sendResize(){ if(ws&&ws.readyState===1&&term){ws.send(JSON.stringify({t:'r',rows:term.rows,cols:term.cols}))} }
function initTerm(){
  const el=document.getElementById('term'); if(!el||typeof Terminal==='undefined')return;
  term=new Terminal({fontSize:13,fontFamily:"'JetBrains Mono',monospace",cursorBlink:true,
    theme:{background:'#11111c',foreground:'#d4d4e2',cursor:'#f0d890',selectionBackground:'#2b2b50'}});
  fitAddon=new FitAddon.FitAddon(); term.loadAddon(fitAddon); term.open(el); fitAddon.fit();
  term.onData(d=>{ if(ws&&ws.readyState===1)ws.send(d) });
  window.addEventListener('resize',()=>{fitAddon.fit();sendResize()});
  var CRLF=String.fromCharCode(13,10);
  // Auto-reconnect: the session PERSISTS on the NAS (tmux), so a dropped socket (mobile backgrounding)
  // is not lost work — we just re-attach and tmux redraws. The client is a thin view.
  (function connect(){
    const proto=location.protocol==='https:'?'wss':'ws';
    ws=new WebSocket(proto+'://'+location.host+'/ws/term?token='+encodeURIComponent(window.PP_WSTOKEN||'')); ws.binaryType='arraybuffer';
    ws.onopen=()=>{sendResize();term.focus()};
    ws.onmessage=e=>{ if(typeof e.data==='string'){term.write(e.data)} else {term.write(new Uint8Array(e.data))} };
    ws.onclose=()=>{ term.write(CRLF+'[getrennt — verbinde neu…]'+CRLF); setTimeout(connect,1500); };
  })();
}
if(document.getElementById('term')) initTerm();
// (initTerm2 — the host-shell xterm — removed with the shell lane.)
// ---- NAS-backed link-capture strip (the terminal↔browser handoff surface) ----
// The NAS scans terminal output for URLs server-side and stores them per-principal; the client just
// renders that store. Persists across reload / tab switch / scrollback loss; one tap opens the URL in
// the Screen (Firefox navigates AND is brought to the front) — mobile never has to select+copy.
(function linkcap(){
  var strip=document.getElementById('linkcap'); if(!strip) return;
  var state='collapsed';   // 'collapsed' (thin header only) | 'expanded' (rows) | 'hidden' (tiny pill)
  var links=[];
  function openInScreen(url,btn){
    if(btn){ btn.disabled=true; btn.textContent='… öffne'; }
    fetch('api/screen/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url})})
      .then(function(r){return r.json();}).then(function(d){
        if(d&&d.ok){ if(btn)btn.textContent='✓ im Screen'; if(window.summon)summon('screen'); }
        else { if(btn){btn.disabled=false;btn.textContent='🌐 Öffnen';} if(window.toast)toast('Öffnen fehlgeschlagen'); }
      }).catch(function(){ if(btn){btn.disabled=false;btn.textContent='🌐 Öffnen';} });
  }
  function pill(){ return document.getElementById('linkpill'); }
  function render(){
    strip.textContent=''; strip.classList.remove('on');
    var p=pill();
    if(!links.length){ if(p)p.remove(); return; }
    if(state==='hidden'){        // minimized to a small floating pill; tap re-opens (weg & wieder hin)
      if(!p){ p=document.createElement('button'); p.id='linkpill'; document.body.appendChild(p);
              p.onclick=function(){ state='collapsed'; render(); }; }
      p.textContent='🔗 '+links.length; return;
    }
    if(p)p.remove();
    strip.classList.add('on');
    var head=document.createElement('div'); head.className='lchead';
    var ttl=document.createElement('span'); ttl.className='lchtitle';
    ttl.textContent=(state==='expanded'?'▾ ':'▸ ')+'🌐 '+links.length+' Link'+(links.length>1?'s':'')+' aus dem Terminal';
    ttl.onclick=function(){ state=(state==='expanded'?'collapsed':'expanded'); render(); };
    head.appendChild(ttl);
    var clr=document.createElement('button'); clr.className='lcclear'; clr.textContent='leeren';
    clr.onclick=function(e){ e.stopPropagation(); fetch('api/links/clear',{method:'POST'}).then(function(){ links=[]; _sig=''; render(); }).catch(function(){}); };
    head.appendChild(clr);
    var x=document.createElement('button'); x.className='lcx'; x.textContent='✕'; x.title='ausblenden';
    x.onclick=function(e){ e.stopPropagation(); state='hidden'; render(); };
    head.appendChild(x); strip.appendChild(head);
    if(state==='expanded'){
      links.slice(0,8).forEach(function(l){
        var row=document.createElement('div'); row.className='lcrow';
        var u=document.createElement('span'); u.className='lcurl'; u.textContent=l.url; u.title=l.url;
        var b=document.createElement('button'); b.className='lcbtn'; b.textContent='🌐 Öffnen';
        b.onclick=function(){ openInScreen(l.url,b); };
        row.appendChild(u); row.appendChild(b); strip.appendChild(row);
      });
    }
  }
  var _sig='';
  function refresh(){
    if(document.hidden) return;
    fetch('api/links').then(function(r){return r.json();}).then(function(d){
      var nl=(d&&d.links)||[], sig=nl.map(function(l){return l.url;}).join('|');
      if(sig===_sig) return;
      var grew=nl.length>links.length; _sig=sig; links=nl;
      if(grew && state==='hidden') state='collapsed';   // a NEW link brings it back (but only to thin bar)
      render();
    }).catch(function(){});
  }
  window.refreshLinks=refresh;
  refresh(); setInterval(refresh, 3000);
})();

// ---- Landing: Weitermachen + Pipelines + Einstellungen ----
function _le(tag,cls,txt){ var e=document.createElement(tag); if(cls)e.className=cls; if(txt!=null)e.textContent=txt; return e; }
async function loadLast(){
  var box=document.getElementById('llast'); if(!box) return;
  try{
    var jobs=await (await fetch('api/jobs')).json();
    box.textContent='';
    if(!jobs.length){ box.appendChild(_le('div','jmut','noch keine Aufträge')); return; }
    jobs.slice(0,6).forEach(function(j){
      var a=_le('a','ljob'); a.href='job/'+j.id; a.target='_blank';
      a.appendChild(_le('span','jbadge s-'+j.status,j.status));
      a.appendChild(_le('span','ljp',(j.prompt||'').slice(0,70)));
      box.appendChild(a);
    });
  }catch(e){ box.textContent='Fehler.'; }
}
async function loadPipes(){
  var box=document.getElementById('lpipes'); if(!box) return;
  try{
    var d=await (await fetch('api/pipelines')).json();
    var ps=(d&&d.pipelines)||[];
    box.textContent='';
    if(!ps.length){ box.appendChild(_le('div','jmut','keine Pipelines — unten hinzufügen')); return; }
    ps.forEach(function(p){
      var row=_le('div','pipe'+(p.active?' on':''));
      var nm=_le('div','pipen');
      nm.appendChild(_le('span','pipdot',p.active?'●':'○'));
      nm.appendChild(_le('span',null,' '+p.name));
      nm.appendChild(_le('span','piptype',' '+(p.type==='commission'?'Auftrag':'Command')));
      row.appendChild(nm);
      var acts=_le('div','pipa');
      var tog=_le('button','lbtn sm',p.active?'⏸ Pause':'▶ Start');
      tog.onclick=function(){ pipeToggle(p.id,p.active); };
      var del=_le('button','lbtn sm ghost','🗑');
      del.onclick=function(){ if(confirm('Pipeline löschen?')) pipeDelete(p.id); };
      acts.appendChild(tog); acts.appendChild(del); row.appendChild(acts);
      box.appendChild(row);
    });
  }catch(e){ box.textContent='Fehler.'; }
}
async function pipeToggle(id,active){
  try{ await fetch('api/pipelines/'+id+'/'+(active?'pause':'start'),{method:'POST'}); }catch(e){}
  setTimeout(loadPipes,300);
}
async function pipeDelete(id){ try{ await fetch('api/pipelines/'+id+'/delete',{method:'POST'}); }catch(e){} loadPipes(); }
(function initLanding(){
  var save=document.getElementById('pip-save');
  if(save) save.onclick=async function(){
    var name=(document.getElementById('pip-name').value||'').trim();
    var type=document.getElementById('pip-type').value;
    var spec=(document.getElementById('pip-spec').value||'').trim();
    var mem=(document.getElementById('pip-mem').value||'').trim();
    if(!spec){ if(window.toast)toast('Befehl/Beschreibung fehlt'); return; }
    try{ await fetch('api/pipelines',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,type:type,spec:spec,mem:mem})});
      document.getElementById('pip-name').value=''; document.getElementById('pip-spec').value=''; document.getElementById('pip-mem').value='';
      loadPipes();
    }catch(e){}
  };
  // Both shortcuts used to fire a POST and then summon('terminal') so the user could watch the
  // command run in the HOST shell. That shell is gone, and both endpoints are retired (410) —
  // they typed into a host tmux. So: report what the server actually said and offer the lane it
  // names in `next`, instead of promising a Terminal that no longer exists.
  async function shortcut(url, statusEl, pending){
    if(statusEl) statusEl.textContent=pending;
    var d=null;
    try{ var r=await fetch(url,{method:'POST'}); d=await r.json(); }catch(e){}
    if(!d){ if(statusEl) statusEl.textContent='Keine Antwort von der Box.'; return; }
    if(d.ok){ if(statusEl) statusEl.textContent='Läuft.'; return; }
    var msg=d.detail||d.error||'Nicht verfügbar.';
    if(statusEl){
      statusEl.textContent='';
      statusEl.appendChild(document.createTextNode(msg+' '));
      if(d.next){ var a=document.createElement('a'); a.href=d.next; a.textContent='→ dort öffnen';
                  a.style.color='#9ab6ff'; statusEl.appendChild(a); }
    }
  }
  var sc=document.getElementById('set-claude');
  if(sc) sc.onclick=function(){ shortcut('api/shortcut/claude-login',document.getElementById('set-status'),
    'Prüfe Claude-Anmeldung …'); };
  var sv=document.getElementById('set-vpn');
  if(sv) sv.onclick=function(){ shortcut('api/shortcut/vpn',document.getElementById('set-status'),
    'Prüfe VPN-Lane …'); };
  var ll=document.getElementById('set-llm');
  if(ll) ll.onchange=function(){ var s=document.getElementById('set-status'); if(s)s.textContent=ll.checked?'LLM-Modus: eigenes LLM (wird bei Multi-User pro User geroutet).':'LLM-Modus: zentrales LLM.'; };
  var who=document.getElementById('set-whoami');
  if(who) who.textContent='angemeldet als: '+(window.PP_USER||'—');
  var lo=document.getElementById('set-logout');
  if(lo) lo.onclick=function(){ fetch('api/logout',{method:'POST'}).finally(function(){ location.href='/login'; }); };
  loadLast(); loadPipes();
})();

// ---- M2 attachments ----
function toast(m){const t=document.createElement('div');t.className='toast';t.textContent=m;document.body.appendChild(t);setTimeout(()=>t.remove(),2200);}
async function aUpload(blob,name,type){
  await fetch('api/upload',{method:'POST',headers:{'X-Filename':encodeURIComponent(name),'Content-Type':type||blob.type||'application/octet-stream'},body:blob});
  toast('hochgeladen: '+name); aRefresh();
}
async function aRefresh(){
  const el=document.getElementById('alist'); if(!el)return;
  try{const items=await (await fetch('api/attachments')).json(); el.innerHTML=items.map(aCard).join('');}catch(e){}
}
function aCard(it){
  const u=it.url; let media;
  if(/^image\\//.test(it.type)) media='<img src="'+u+'">';
  else if(/^audio\\//.test(it.type)) media='<audio src="'+u+'" controls></audio>';
  else if(/^video\\//.test(it.type)) media='<video src="'+u+'" controls></video>';
  else media='<div class=afile>📄</div>';
  const p=(it.path||'').replace(/'/g,"\\\\'");
  return '<div class=acard>'+media+'<div class=an>'+it.name+'</div><div class=aa>'+
    '<button onclick="aPaste(\\''+p+'\\')">→ Cockpit</button>'+
    '<a href="'+u+'" target=_blank>Ansehen</a></div></div>';
}
async function aPaste(path){
  await fetch('api/cockpit/paste',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:path+' '})});
  toast('Pfad ins Cockpit gesendet');
}
// DROP-ANYWHERE (design §6.3 / §13): a file dropped ANYWHERE in the portal is uploaded to the
// NAS attachment store and toasted — the Anhang tab is no longer the only way in. Reuses aUpload.
(function dropAnywhere(){
  var veil=null, depth=0;
  function show(){ if(veil)return; veil=document.createElement('div');
    veil.textContent='📎 Dateien hier ablegen — landen im NAS-Anhang';
    veil.style.cssText='position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(11,11,20,.82);color:#e7e7f2;font:600 18px system-ui,sans-serif;border:3px dashed #6b7cff;box-sizing:border-box;pointer-events:none';
    document.body.appendChild(veil); }
  function hide(){ if(veil){veil.remove();veil=null;} depth=0; }
  window.addEventListener('dragenter',function(e){ if(e.dataTransfer&&Array.prototype.indexOf.call(e.dataTransfer.types||[],'Files')>=0){ e.preventDefault(); depth++; show(); } });
  window.addEventListener('dragover',function(e){ if(veil){ e.preventDefault(); e.dataTransfer.dropEffect='copy'; } });
  window.addEventListener('dragleave',function(e){ if(veil){ depth--; if(depth<=0) hide(); } });
  window.addEventListener('drop',function(e){ if(!veil) return; e.preventDefault(); hide();
    var fs=e.dataTransfer&&e.dataTransfer.files; if(!fs||!fs.length) return;
    for(var i=0;i<fs.length;i++){ (function(f){ if(typeof aUpload==='function') aUpload(f,f.name,f.type); })(fs[i]); } });
})();
function noMedia(){ return !(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia); }
(function initAttach(){
  const fi=document.getElementById('afile'); if(!fi)return;
  fi.onchange=e=>{ for(const f of e.target.files) aUpload(f,f.name,f.type); fi.value=''; };
  const cap=document.getElementById('acapture');
  document.getElementById('acam').onclick=async()=>{
    if(noMedia()){toast('Kamera braucht HTTPS — Zertifikat akzeptieren');return;}
    let stream; try{stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});}catch(e){toast('Kamera: '+e.message);return;}
    cap.innerHTML='<video autoplay playsinline></video><div><button class=abtn id=shot>Auslösen</button> <button class=abtn id=acanc>×</button></div>';
    const v=cap.querySelector('video'); v.srcObject=stream;
    document.getElementById('shot').onclick=()=>{const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;c.getContext('2d').drawImage(v,0,0);c.toBlob(b=>aUpload(b,'foto-'+Date.now()+'.png','image/png'),'image/png');};
    document.getElementById('acanc').onclick=()=>{stream.getTracks().forEach(t=>t.stop());cap.innerHTML='';};
  };
  function rec(constraints,base){return async()=>{
    if(noMedia()){toast('Aufnahme braucht HTTPS — Zertifikat akzeptieren');return;}
    let stream; try{stream=await navigator.mediaDevices.getUserMedia(constraints);}catch(e){toast(e.message);return;}
    const mr=new MediaRecorder(stream),chunks=[]; mr.ondataavailable=e=>chunks.push(e.data);
    mr.onstop=()=>{const b=new Blob(chunks,{type:chunks[0]?chunks[0].type:'application/octet-stream'});aUpload(b,base+'-'+Date.now()+'.webm',b.type);stream.getTracks().forEach(t=>t.stop());cap.innerHTML='';};
    cap.innerHTML=(constraints.video?'<video autoplay muted playsinline></video>':'<div class=stub>🎤 Aufnahme läuft…</div>')+'<div><button class=abtn id=astop>⏹ Stop</button></div>';
    if(constraints.video)cap.querySelector('video').srcObject=stream;
    mr.start(); document.getElementById('astop').onclick=()=>mr.stop();
  };}
  document.getElementById('amic').onclick=rec({audio:true},'audio');
  document.getElementById('avid').onclick=rec({video:true,audio:true},'video');
  aRefresh();
})();

// ---- M3 voice / talk ----
function tAdd(who,text){const l=document.getElementById('tlog');const d=document.createElement('div');d.className='tmsg '+who;d.textContent=text;l.appendChild(d);l.scrollTop=l.scrollHeight;return d;}
async function tSpeak(text){
  try{const r=await fetch('api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    const ct=r.headers.get('Content-Type')||''; if(ct.indexOf('audio')>=0){const b=await r.blob();new Audio(URL.createObjectURL(b)).play();}}catch(e){}
}
window.__cer=null;  // armed ceremony: {re,state:'prompted'|'holding',verb,hold_ms}
function _cerSay(say){ var t=tAdd('bot',say); if(document.getElementById('tautospeak').checked&&say) tSpeak(say); return t; }
async function tTalk(text){
  // The voice META-LAYER: every utterance goes through the cross-context dispatcher (/api/voice).
  // A lens-summon verb switches the lens INSTANTLY (client-side reflex); an irreversible verb ARMS a
  // Phase-4 ceremony (read-back + nonce + hold); everything else is answered by the agent.
  if(!text)return; tAdd('me',text);
  // While a ceremony is armed, this utterance is the confirmation channel (nonce / stopp), not a new command.
  if(window.__cer){ return _cerTurn(text); }
  const t=tAdd('bot','…');
  try{const r=await fetch('api/voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    const d=await r.json();
    if(d.action==='summon' && d.lens && window.summon){ summon(d.lens); }
    if(d.action==='ceremony'){ return _cerArm(d,t); }
    var say=d.speak||d.reply||'(keine Antwort)'; t.textContent=say;
    if(document.getElementById('tautospeak').checked && (d.speak||d.reply)) tSpeak(say);
  }catch(e){t.textContent='Fehler: '+e.message;}
}
function _cerArm(d,t){
  window.__cer={re:d.re,state:'prompted',verb:d.verb,hold_ms:d.hold_ms||10000};
  var rb=d.readback||{}; var facts='⚠ '+(d.verb||'irreversibel');
  if(rb.recipient) facts+=' · '+rb.recipient;
  if(rb.subject) facts+=' · Betreff: '+rb.subject;
  if(rb.digest&&rb.digest.length) facts+=' · '+rb.digest.join(' · ');
  t.textContent=(d.speak||'Bestätigung nötig.')+'   ['+facts+']';
  t.style.borderLeft='3px solid #ffcc55'; t.style.paddingLeft='8px';
  if(document.getElementById('tautospeak').checked && d.speak) tSpeak(d.speak);
}
async function _cerTurn(text){
  var c=window.__cer, low=text.trim().toLowerCase();
  var isStop=/(^|\b)(stopp|stop|abbrechen|abbruch|cancel|halt|nein)(\b|$)/.test(low);
  try{
    if(c.state==='holding'){
      if(isStop){
        const r=await fetch('api/ceremony/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({re:c.re})});
        const d=await r.json(); window.__cer=null; _cerSay(d.speak||'Gestoppt.'); return;
      }
      _cerSay('Sende läuft — sag stopp zum Abbrechen.'); return;
    }
    // state 'prompted': the utterance IS the nonce (or a non-match that aborts).
    const r=await fetch('api/ceremony/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({re:c.re,nonce_response:text})});
    const d=await r.json();
    if(d.accepted){ c.state='holding'; _cerSay(d.speak||'Bestätigt.'); _cerHoldWatch(c); }
    else { window.__cer=null; _cerSay(d.speak||'Abgebrochen.'); }
  }catch(e){ window.__cer=null; tAdd('bot','Ceremony-Fehler: '+e.message); }
}
function _cerHoldWatch(c){
  // client countdown mirrors the server-side hold; when it elapses, query the AUTHORITATIVE state.
  setTimeout(async function(){
    if(!window.__cer||window.__cer.re!==c.re) return;  // cancelled meanwhile
    try{ const r=await fetch('api/ceremony/status?re='+encodeURIComponent(c.re)); const d=await r.json();
      window.__cer=null; if(d.speak) _cerSay(d.speak); }catch(e){ window.__cer=null; }
  }, (c.hold_ms||10000)+700);
}
async function tStt(blob){
  const t=tAdd('me','🎤 …');
  try{const r=await fetch('api/stt',{method:'POST',headers:{'Content-Type':blob.type||'audio/webm'},body:blob});
    const d=await r.json(); t.remove();
    if(d.text){tTalk(d.text);} else {tAdd('bot','(nichts verstanden'+(d.error?': '+d.error:'')+')');}
  }catch(e){t.textContent='STT-Fehler: '+e.message;}
}
// SUMMON PALETTE (chosen nav paradigm): one "⚡ Öffnen" button opens a palette; picking a lens
// shows that ONE lens full-screen (single-layer). Reuses the (now hidden) panel/tab wiring so the
// seat-start / queue / fit logic stays intact — the palette just drives the right panel.
window.summon=function(id){
  var M={
    landing:{t:'landing',label:'🏠 Start'},
    // Browser IS the real Firefox on the seat, streamed in the Screen — no fake proxy (/go is gone).
    browser:{t:'screen',label:'🌐 Browser'},
    chat:{t:'cockpit',label:'💬 Chat'},           // the working Claude/Mode-A tmux terminal
    // Host-Shell RETIRED: „Terminal"/„Shell" used to summon the ▟ lens = a login shell on the HOST.
    // mapLens()/the voice lane still route the words „terminal"/„konsole"/„shell" here, so the lens
    // must DEGRADE HONESTLY — say what happened and where the work lives — never silently no-op
    // and never throw on the missing tab.
    terminal:{gone:1},
    shell:{gone:1},
    notes:{t:'apps',url:'/apps/notes',label:'📝 Notes'},
    screen:{t:'screen',label:'🖥 Screen'},
    queue:{t:'queue',label:'📋 Queue'},
    attach:{t:'attach',label:'📎 Anhänge'}
  }[id];
  if(!M)return;
  if(M.gone){
    var pg=document.getElementById('palette'); if(pg)pg.classList.remove('open');
    var msg='Host-Shell entfernt — Arbeit läuft in einer Session-Zelle (Reiter „Sessions“), '
           +'Box-Verwaltung per SSH.';
    if(window.toast)toast(msg); else alert(msg);
    return;
  }
  if(M.url){var f=document.getElementById('fframe'); if(f)f.setAttribute('src',M.url);}
  var tab=document.querySelector('.tab[data-t='+M.t+']'); if(tab)tab.click();
  var cl=document.getElementById('curlens'); if(cl)cl.textContent=M.label;
  var pal=document.getElementById('palette'); if(pal)pal.classList.remove('open');
};
(function summonUX(){
  var btn=document.getElementById('summon'), pal=document.getElementById('palette');
  if(!btn||!pal)return;
  btn.onclick=function(e){e.stopPropagation();pal.classList.toggle('open');};
  document.addEventListener('click',function(e){ if(!pal.contains(e.target)&&e.target!==btn)pal.classList.remove('open'); });
})();
// Voice meta-layer: expand the conversation log on focus / new message; toggle with the chevron.
(function voicebarUX(){
  var vb=document.getElementById('voicebar'); if(!vb)return;
  var tog=document.getElementById('vbtog'), ti=document.getElementById('ttext'), log=document.getElementById('tlog');
  function open(){vb.classList.add('open');}
  if(tog)tog.onclick=function(){vb.classList.toggle('open');};
  if(ti)ti.addEventListener('focus',open);
  if(log&&window.MutationObserver){ new MutationObserver(open).observe(log,{childList:true}); }
})();
(function initTalk(){
  const ptt=document.getElementById('ptt'); if(!ptt)return;
  const ti=document.getElementById('ttext');
  ti.addEventListener('keydown',e=>{if(e.key==='Enter'&&ti.value.trim()){tTalk(ti.value.trim());ti.value='';}});
  let mr,chunks,recording=false;
  ptt.onclick=async()=>{
    if(recording){mr.stop();return;}
    if(!window.PP_VOICE){tAdd('bot','Sprache ist auf dieser Box nicht installiert (brainbox-portal install-voice). Tippen geht trotzdem.');return;}
    if(noMedia()){tAdd('bot','Mikrofon braucht HTTPS — Zertifikat akzeptieren.');return;}
    let stream; try{stream=await navigator.mediaDevices.getUserMedia({audio:true});}catch(e){tAdd('bot','Mic: '+e.message);return;}
    mr=new MediaRecorder(stream); chunks=[];
    mr.ondataavailable=e=>chunks.push(e.data);
    mr.onstop=()=>{recording=false;ptt.classList.remove('rec');ptt.textContent='🎤 Sprechen';stream.getTracks().forEach(x=>x.stop());tStt(new Blob(chunks,{type:chunks[0]?chunks[0].type:'audio/webm'}));};
    mr.start();recording=true;ptt.classList.add('rec');ptt.textContent='⏹ Stop';
  };
  tAdd('bot','Sag mir, was ich auf dem Rechner tun soll — oder unterhalte dich einfach. Ich kann jedes Programm bedienen, Pipelines bauen und Artefakte erzeugen.');
})();

// ---- M4/M5/M6 commission ----
let cHist=[];
function cClar(who,text){const el=document.getElementById('cclar');const d=document.createElement('div');d.className='tmsg '+who;d.textContent=text;el.appendChild(d);}
async function cClarify(){
  const ta=document.getElementById('cprompt'),p=ta.value.trim(); if(!p)return;
  cHist.push({role:'user',text:p}); cClar('me',p); ta.value='';
  try{const d=await (await fetch('api/clarify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({history:cHist})})).json();
    if(d.ready){cClar('bot','✓ Verstanden: '+d.spec); ta.value=d.spec;}
    else{cHist.push({role:'assistant',text:d.question}); cClar('bot',d.question);}
  }catch(e){cClar('bot','Fehler: '+e.message);}
}
async function cSubmit(){
  const ta=document.getElementById('cprompt'),p=ta.value.trim(); if(!p){toast('Ziel fehlt');return;}
  const email=document.getElementById('cemail').value.trim();
  try{const d=await (await fetch('api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:p,email})})).json();
    if(d.id){toast('Auftrag gestartet'); ta.value=''; cHist=[]; document.getElementById('cclar').innerHTML=''; cJobs();}
  }catch(e){toast('Fehler: '+e.message);}
}
async function cJobs(){
  const el=document.getElementById('cjobs'); if(!el)return;
  try{const jobs=await (await fetch('api/jobs')).json();
    el.innerHTML=jobs.map(j=>'<a class=cjob href="job/'+j.id+'" target=_blank><span class="jbadge s-'+j.status+'">'+j.status+'</span><span class=cjp>'+(j.prompt||'').replace(/[<>&]/g,' ')+'</span></a>').join('')||'<div class=jmut>noch keine Aufträge</div>';
  }catch(e){}
}
(function initCommission(){
  if(!document.getElementById('csubmit'))return;
  document.getElementById('cclarify').onclick=cClarify;
  document.getElementById('csubmit').onclick=cSubmit;
  cJobs(); setInterval(()=>{if(document.querySelector('#commission.active'))cJobs();},5000);
})();

// ---- M7 rooms dashboard ----
let rWs;
async function rList(){
  const el=document.getElementById('rlist'); if(!el)return;
  try{const rooms=await (await fetch('api/rooms')).json();
    el.innerHTML=rooms.map(r=>'<button class="rchip '+(r.broker?'up':'')+'" onclick="rOpen(\\''+r.name+'\\')">'+r.name+(r.broker?' ●':'')+'</button>').join('')||'<div class=jmut>keine Rooms</div>';
  }catch(e){}
}
function rOpen(name){
  document.getElementById('rtitle').textContent='live: '+name;
  const feed=document.getElementById('rfeed'); feed.textContent='';
  if(rWs){try{rWs.close()}catch(e){}}
  const proto=location.protocol==='https:'?'wss':'ws';
  rWs=new WebSocket(proto+'://'+location.host+'/ws/feed?room='+encodeURIComponent(name)+'&token='+encodeURIComponent(window.PP_WSTOKEN||''));
  rWs.binaryType='arraybuffer';
  rWs.onmessage=e=>{if(typeof e.data==='string'){feed.textContent+=e.data;feed.scrollTop=feed.scrollHeight;}};
}
(function initRooms(){
  if(!document.getElementById('rlist'))return;
  rList(); setInterval(()=>{if(document.querySelector('#rooms.active'))rList();},5000);
})();

// ---- Queue dashboard (portioneer-governed unified queue) ----
function esc(s){return (s==null?'':String(s)).replace(/[<>&]/g,function(c){return c==='<'?'&lt;':c==='>'?'&gt;':'&amp;';});}
// Rooms are no longer a top-level tab (design/vision: the watchdog feeds them centrally; the user
// doesn't work IN them — only accepts/iterates at the end). Opening a room = an on-demand DETAIL of
// a queue item: reveal the rooms panel over the current lens, with a back link to the Queue.
function qGoRoom(n){
  panels.forEach(function(p){p.classList.toggle('active',p.id==='rooms');});
  tabs.forEach(function(x){x.classList.remove('active');});
  var qt=document.querySelector('.tab[data-t=queue]'); if(qt)qt.classList.add('active');
  var rp=document.getElementById('rooms');
  if(rp && !document.getElementById('roomback')){
    var b=document.createElement('button'); b.id='roomback'; b.className='abtn'; b.textContent='← Queue';
    b.style.cssText='position:absolute;top:8px;right:8px;z-index:5';
    b.onclick=function(){ panels.forEach(function(p){p.classList.toggle('active',p.id==='queue');}); };
    rp.appendChild(b);
  }
  rOpen(n);
}
async function qCancel(id){ try{await fetch('api/queue/'+id+'/cancel',{method:'POST'});}catch(e){} qRefresh(); }
function qCard(j){
  var pct=(j.prog_done!=null&&j.prog_total)?Math.floor(100*j.prog_done/j.prog_total):null;
  var bar='';
  if(pct!=null) bar='<div class=qbar><i style="width:'+pct+'%"></i></div><span class=qpct>'+pct+'%</span>';
  else if(j.state==='running') bar='<div class=qbar><i class=ind></i></div>';
  var room=j.room?'<button class=qroom onclick="qGoRoom(\\''+j.room+'\\')">▶ '+esc(j.room)+'</button>':'';
  var msg=j.prog_msg?'<div class=qmsg>'+esc(j.prog_msg)+'</div>':'';
  var ex=(j.exit_code!=null)?' · exit '+j.exit_code:'';
  var x=(j.state==='queued'||j.state==='running')?'<button class=qx onclick="qCancel('+j.id+')">✕</button>':'';
  return '<div class="qcard s-'+j.state+'"><div class=qtop><span class=qid>#'+j.id+'</span><span class=qtag>'+esc(j.client_tag||'')+'</span>'+x+'</div>'+bar+msg+room+'<div class=qmeta>'+j.state+ex+' · '+(j.mem_estimate||0)+'M</div></div>';
}
async function qRefresh(){
  var head=document.getElementById('qhead'); if(!head)return;
  try{
    var d=await (await fetch('api/queue?limit=120')).json();
    if(d.status&&d.status.snap){var s=d.status.snap,c=d.status.cfg;
      head.innerHTML='<b>RAM</b> '+s.mem_available+'/'+s.mem_total+' MiB frei · <b>batch</b> '+s.batch_current+' MiB · <b>PSI</b> '+(s.psi_avg10||0).toFixed(0)+' · <b>load</b> '+(s.load1||0).toFixed(1)+'/'+s.cpu_count+' · floor '+c.mem_floor+(d.status.pressure_blocked?' · <span class=qwarn>PRESSURE</span>':'');
    } else head.innerHTML='<span class=qwarn>pnd nicht erreichbar'+(d.error?': '+esc(d.error):'')+'</span>';
    var jobs=d.jobs||[], term={done:1,failed:1,cancelled:1,timeout:1};
    var q=jobs.filter(function(j){return j.state==='queued';});
    var r=jobs.filter(function(j){return j.state==='running';});
    var done=jobs.filter(function(j){return term[j.state];});
    document.getElementById('qq').innerHTML=q.map(qCard).join('')||'<div class=jmut>—</div>';
    document.getElementById('qr').innerHTML=r.map(qCard).join('')||'<div class=jmut>—</div>';
    document.getElementById('qd').innerHTML=done.slice(0,40).map(qCard).join('')||'<div class=jmut>—</div>';
    document.getElementById('qqn').textContent=q.length;
    document.getElementById('qrn').textContent=r.length;
    document.getElementById('qdn').textContent=done.length;
  }catch(e){ head.innerHTML='<span class=qwarn>Queue-Fehler: '+e.message+'</span>'; }
}
async function qSubmit(){
  var ta=document.getElementById('qprompt'),p=ta.value.trim();
  var type=document.getElementById('qtype').value, room=document.getElementById('qroom').value.trim();
  if(!p){toast('Aufgabe fehlt');return;}
  var body=(type==='commission')?{type:'commission',prompt:p}:{type:'task',cmd:p};
  if(room) body.room=room;
  try{var d=await (await fetch('api/queue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(d.ok||d.id||d.portal_job){toast('in die Queue gelegt'); ta.value=''; qRefresh();} else toast('Fehler: '+(d.error||'?'));
  }catch(e){toast('Fehler: '+e.message);}
}
(function initQueue(){
  if(!document.getElementById('qsubmit'))return;
  document.getElementById('qsubmit').onclick=qSubmit;
  qRefresh(); setInterval(function(){if(document.querySelector('#queue.active'))qRefresh();},2000);
})();
"""

VNC3_HTML = r"""<!doctype html><html><head><meta charset=utf-8>
<title>phantom · Bildschirm (eigener Client)</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
/* Our OWN minimal RFB client. rfbd sends ONE full-frame Raw rect per FramebufferUpdate; we own
   every byte, so there is no noVNC black box to fight. Canvas is the real 960x600 bitmap, scaled
   aspect-correct to the viewport; input maps 1:1 back to framebuffer pixels. */
html,body{margin:0;padding:0;width:100%;height:100%;background:#0a0a12;overflow:hidden}
#wrap{position:fixed;inset:0;display:flex;align-items:center;justify-content:center}
#fb{background:#000;cursor:default;outline:none;display:block}
#hud{position:fixed;left:0;right:0;bottom:0;font:600 12px/1.35 ui-monospace,monospace;color:#bfe;background:rgba(4,10,4,.86);padding:5px 10px;z-index:10;white-space:pre;pointer-events:none}
/* VISIBLE keyboard bar (toggled by ⌨). Programmatic focus of an INVISIBLE input does not reliably
   summon the mobile soft keyboard (esp. iOS) — a real, visible input the user taps directly does.
   16px font = no iOS auto-zoom. Text typed here is forwarded to the stream as RFB keys, then cleared. */
#kbbar{position:fixed;left:0;right:0;bottom:0;z-index:15;display:none;padding:6px;box-sizing:border-box;background:rgba(6,12,8,.94);border-top:1px solid rgba(120,180,140,.5)}
#kbbar.on{display:flex;gap:6px;align-items:center}
#kbd{flex:1;min-width:0;box-sizing:border-box;font-size:16px;padding:11px 12px;border-radius:10px;border:1px solid rgba(120,180,140,.5);background:#0d1a12;color:#dfe;caret-color:#7fdca0;outline:none}
#kbd::placeholder{color:#6a8a76}
#kbclose{flex:0 0 auto;width:42px;height:42px;border-radius:10px;border:1px solid rgba(120,180,140,.5);background:#0d1a12;color:#cfe;font-size:16px;cursor:pointer}
/* Floating touch controls: fullscreen (all) + summon-keyboard (touch only). Big tap targets. */
.vbtn{position:fixed;z-index:20;width:46px;height:46px;border-radius:12px;border:1px solid rgba(120,180,140,.5);
  background:rgba(8,16,10,.72);color:#cfe;font-size:22px;line-height:44px;text-align:center;cursor:pointer;
  -webkit-tap-highlight-color:transparent;user-select:none;padding:0}
.vbtn:active{background:rgba(20,40,26,.9)}
#fsbtn{top:10px;right:10px}
#rcbtn{top:64px;right:10px}
#ffbtn{top:118px;right:10px}
#kbbtn{bottom:12px;right:10px;display:none}
:fullscreen #fsbtn{opacity:.55}
</style></head><body>
<div id=wrap><canvas id=fb width=16 height=16 tabindex=0></canvas></div>
<div id=kbbar><input id=kbd placeholder="⌨ Hier tippen → landet im Screen  (Enter / ⌫ gehen)" autocomplete=off autocapitalize=off autocorrect=off spellcheck=false inputmode=text><button id=kbclose title="Tastatur schließen">✕</button></div>
<button id=fsbtn class=vbtn title="Vollbild ein/aus">⛶</button>
<button id=rcbtn class=vbtn title="Ansicht neu verbinden (bei eingefrorenem Bild)">🔄</button>
<button id=ffbtn class=vbtn title="Firefox neu starten (bei kaputtem/schwarzem Fenster)">🦊</button>
<button id=kbbtn class=vbtn title="Handy-Tastatur">⌨</button>
<div id=hud>verbinde …</div>
<script>
"use strict";
var TOKEN = %TOKEN%;
var hud = document.getElementById("hud");
var cv  = document.getElementById("fb");
var ctx = cv.getContext("2d", { alpha: false });
var proto = location.protocol === "https:" ? "wss" : "ws";
// Admin OAuth provisioning: view/drive a specific isolated cell. The cell is taken from THIS page's
// own ?cell= (only llmoauth_* is honoured); the server /ws/vnc route enforces admin + prefix.
var _cellm = /[?&]cell=([A-Za-z0-9_-]+)/.exec(location.search || "");  // vmcells + llmoauth; Server gated beides
var URL_ = proto + "://" + location.host + "/ws/vnc?token=" + encodeURIComponent(TOKEN)
  + (_cellm ? ("&cell=" + encodeURIComponent(_cellm[1])) : "");

var fbW = 0, fbH = 0, frames = 0, kbytes = 0, connected = false, lastMsg = "", lastFrame = 0, wsOpenAt = 0;
var _lastKeys = [];
function setHud(extra){
  hud.textContent = "eigener RFB-Client  ·  " + (connected ? (fbW + "x" + fbH) : "verbinde")
    + "  ·  Frames: " + frames + "  ·  " + Math.round(kbytes) + " KB"
    + (extra ? ("  ·  " + extra) : "") + (lastMsg ? ("  ·  " + lastMsg) : "")
    + (_lastKeys.length ? ("  ·  keys " + _lastKeys.join(" ")) : "");
}

/* ---- async byte-stream reader over the binary WebSocket (RFB arrives split across messages) ---- */
var chunks = [], chunksLen = 0, head = 0, waiter = null;
function onData(u8){
  chunks.push(u8); chunksLen += u8.length; kbytes += u8.length / 1024;
  if (waiter && chunksLen >= waiter.n){ var w = waiter; waiter = null; w.resolve(pull(w.n)); }
}
function pull(n){
  var out = new Uint8Array(n), got = 0;
  while (got < n){
    var c = chunks[0], avail = c.length - head, take = Math.min(avail, n - got);
    out.set(c.subarray(head, head + take), got);
    got += take; head += take; chunksLen -= take;
    if (head >= c.length){ chunks.shift(); head = 0; }
  }
  return out;
}
function readN(n){
  return new Promise(function(resolve){
    if (chunksLen >= n) resolve(pull(n)); else waiter = { n: n, resolve: resolve };
  });
}

var ws = new WebSocket(URL_);
ws.binaryType = "arraybuffer";
var sendQ = [];
function wsend(arr){ ws.send(new Uint8Array(arr)); }
function u16(n){ return [(n >> 8) & 255, n & 255]; }

ws.onmessage = function(ev){ onData(new Uint8Array(ev.data)); };
function _vncNoScreen(){
  try {
    var w = document.getElementById("wrap"); if (w) w.innerHTML = "";
    var d = document.createElement("div");
    d.style.cssText = "position:fixed;inset:0;display:flex;flex-direction:column;gap:14px;align-items:center;justify-content:center;text-align:center;padding:24px;color:#cfe;font:600 15px/1.5 ui-monospace,monospace";
    d.innerHTML = "<div style=\"font-size:34px\">🖥∅</div>"
      + "<div>Kein Bildschirm f\u00fcr dieses Ziel.</div>"
      + "<div style=\"font-weight:400;max-width:34em;opacity:.85\">Dieser Agent l\u00e4uft vermutlich <b>headless</b> (Terminal-Agent) \u2014 sein Bildschirm ist das <b>Terminal</b> (Reiter \u201eSessions\u201c). Grafische Ziele sind GUI-Zellen und der Box-Bildschirm.</div>";
    var b = document.createElement("button");
    b.textContent = "\ud83d\udd04 Erneut versuchen";
    b.style.cssText = "padding:9px 18px;border-radius:10px;border:1px solid rgba(120,180,140,.5);background:#0d1a12;color:#cfe;font:600 14px ui-monospace,monospace;cursor:pointer";
    b.onclick = function(){ try { sessionStorage.removeItem("vnc3fails:" + (location.search||"")); } catch(e){} location.reload(); };
    d.appendChild(b); document.body.appendChild(d);
  } catch(e){}
  lastMsg = "kein Bildschirm"; setHud();
}
ws.onclose   = function(){
  connected = false; lastMsg = "getrennt"; setHud();
  if (window.__vncReloading) return;                    // manual reload already in flight
  var FAILKEY = "vnc3fails:" + (location.search || "");
  if (frames > 0) {                                     // had a live screen this load -> transient drop, reconnect
    try { sessionStorage.setItem(FAILKEY, "0"); } catch(e){}
    setTimeout(function(){ location.reload(); }, 1200); return;
  }
  var n = 0; try { n = (parseInt(sessionStorage.getItem(FAILKEY) || "0", 10) || 0) + 1; sessionStorage.setItem(FAILKEY, String(n)); } catch(e){}
  if (n >= 4) { try { clearInterval(_vncWatch); } catch(e){} _vncNoScreen(); return; }  // never a frame after several tries -> no screen here
  setTimeout(function(){ location.reload(); }, Math.min(8000, 800 * Math.pow(2, n - 1)));
};
ws.onerror   = function(){ lastMsg = "ws-fehler"; setHud(); };
ws.onopen    = function(){ wsOpenAt = Date.now(); run().catch(function(e){ lastMsg = "err " + e; setHud(); }); };

function fbur(incremental){
  wsend([3, incremental ? 1 : 0].concat(u16(0), u16(0), u16(fbW), u16(fbH)));
}

async function run(){
  /* ---- RFB 3.8 handshake ---- */
  await readN(12);                                    // ProtocolVersion "RFB 003.008\n"
  wsend([82,70,66,32,48,48,51,46,48,48,56,10]);       // send it back
  var nsec = (await readN(1))[0];
  await readN(nsec);
  wsend([1]);                                         // choose security type None(1)
  await readN(4);                                     // SecurityResult (0=OK)
  wsend([1]);                                         // ClientInit (shared)
  var si = await readN(24);
  fbW = (si[0] << 8) | si[1];
  fbH = (si[2] << 8) | si[3];
  var nameLen = (si[20] << 24) | (si[21] << 16) | (si[22] << 8) | si[23];
  if (nameLen > 0) await readN(nameLen);
  cv.width = fbW; cv.height = fbH;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, fbW, fbH);
  connected = true; lastFrame = Date.now(); layout(); setHud();
  try { sessionStorage.setItem("vnc3fails:" + (location.search||""), "0"); } catch(e){}

  /* ---- SetPixelFormat: 32bpp, R@0 G@8 B@16 little-endian => wire bytes are [R,G,B,X] per pixel,
         i.e. already canvas RGBA order (we just force alpha=255). No per-pixel channel swap. ---- */
  wsend([0, 0,0,0,  32, 24, 0, 1,  0,255, 0,255, 0,255,  0, 8, 16,  0,0,0]);
  /* SetEncodings: only Raw(0). No DesktopSize pseudo-encoding => rfbd never renegotiates size. */
  wsend([2, 0].concat(u16(1), [0,0,0,0]));
  fbur(0);                                            // first full frame

  /* ---- render loop ---- */
  while (true){
    var t = (await readN(1))[0];
    if (t === 0){                                     // FramebufferUpdate
      await readN(1);                                 // padding
      var nr2 = await readN(2); var nrects = (nr2[0] << 8) | nr2[1];
      for (var i = 0; i < nrects; i++){
        var rh = await readN(12);
        var rx = (rh[0] << 8) | rh[1], ry = (rh[2] << 8) | rh[3];
        var rw = (rh[4] << 8) | rh[5], rhh = (rh[6] << 8) | rh[7];
        var enc = (rh[8] << 24) | (rh[9] << 16) | (rh[10] << 8) | rh[11];
        if (enc === 0){
          var px = await readN(rw * rhh * 4);
          for (var p = 3; p < px.length; p += 4) px[p] = 255;   // opaque
          var img = new ImageData(new Uint8ClampedArray(px.buffer, px.byteOffset, rw * rhh * 4), rw, rhh);
          ctx.putImageData(img, rx, ry);
        } else {
          lastMsg = "enc " + enc + " (unerwartet)"; setHud(); return;
        }
      }
      frames++; lastFrame = Date.now(); if ((frames & 7) === 0) setHud();
      fbur(1);                                         // ask for the next change
    } else if (t === 1){                               // SetColorMapEntries — skip
      var a = await readN(5); var nc = (a[3] << 8) | a[4]; await readN(nc * 6);
    } else if (t === 2){                               // Bell — nothing
    } else if (t === 3){                               // ServerCutText — skip
      var b = await readN(7); var ln = (b[3]<<24)|(b[4]<<16)|(b[5]<<8)|b[6]; if (ln>0) await readN(ln);
    } else {
      lastMsg = "server msg " + t + " (unbekannt)"; setHud(); return;
    }
  }
}

/* ---- scale canvas aspect-correct to the viewport; keep input mapping 1:1 ---- */
function layout(){
  if (!fbW) return;
  // Die HUD-Statuszeile liegt blickdicht am unteren Rand (position:fixed;bottom:0). Ihre Hoehe
  // von der verfuegbaren Flaeche abziehen, sonst rutscht die unterste Framebuffer-Zeile (tint2-
  // Panel mit Uhr) dahinter und ist auch im Vollbild abgeschnitten. #wrap wird ebenso oben
  // gehalten, damit das Canvas in der Restflaeche zentriert bleibt (Eingabe bleibt 1:1, weil
  // fbCoords ueber getBoundingClientRect rechnet).
  var reserve = (hud && getComputedStyle(hud).display !== "none")
    ? Math.ceil(hud.getBoundingClientRect().height) : 0;
  var wrap = document.getElementById("wrap");
  if (wrap) wrap.style.bottom = reserve + "px";
  var vw = window.innerWidth, vh = Math.max(1, window.innerHeight - reserve);
  var s = Math.min(vw / fbW, vh / fbH);
  cv.style.width  = Math.round(fbW * s) + "px";
  cv.style.height = Math.round(fbH * s) + "px";
}
window.addEventListener("resize", layout);

/* ---- input: PointerEvent(5) + KeyEvent(4), coords mapped display->framebuffer ---- */
var btnMask = 0;
function fbCoords(e){
  var r = cv.getBoundingClientRect();
  var x = Math.round((e.clientX - r.left) / r.width  * fbW);
  var y = Math.round((e.clientY - r.top)  / r.height * fbH);
  x = x < 0 ? 0 : (x >= fbW ? fbW - 1 : x);
  y = y < 0 ? 0 : (y >= fbH ? fbH - 1 : y);
  return [x, y];
}
function pointer(x, y, mask){ if (connected) wsend([5, mask & 255].concat(u16(x), u16(y))); }
function bitFor(button){ return button === 0 ? 1 : (button === 1 ? 2 : (button === 2 ? 4 : 0)); }

cv.addEventListener("mousemove", function(e){ var c = fbCoords(e); pointer(c[0], c[1], btnMask); });
cv.addEventListener("mousedown", function(e){ e.preventDefault(); focusKeys(); btnMask |= bitFor(e.button); var c = fbCoords(e); pointer(c[0], c[1], btnMask); });
cv.addEventListener("mouseup",   function(e){ e.preventDefault(); btnMask &= ~bitFor(e.button); var c = fbCoords(e); pointer(c[0], c[1], btnMask); });
cv.addEventListener("contextmenu", function(e){ e.preventDefault(); });

/* ==== Relacon Geraete-Umschalter: Maus-Seitentasten (zurueck/vor) = vorheriges/naechstes Geraet ==== */
(function(){
  var curCell = (typeof _cellm !== "undefined" && _cellm) ? _cellm[1] : "";
  if (curCell.indexOf("llmoauth_") === 0) return;            // Admin-OAuth-Zellen unangetastet
  var ring = null, curIdx = 0;
  var bn = document.createElement("div");
  bn.style.cssText = "position:fixed;left:50%;top:11%;transform:translateX(-50%);z-index:99999;"
    + "background:rgba(10,14,22,.93);color:#eef;border:2px solid #4dd;border-radius:16px;"
    + "padding:18px 30px;font:600 26px system-ui,-apple-system,sans-serif;letter-spacing:.4px;"
    + "box-shadow:0 10px 44px rgba(0,0,0,.65);opacity:0;transition:opacity .22s;pointer-events:none;text-align:center;line-height:1.35";
  var tag = document.createElement("div");
  tag.style.cssText = "position:fixed;left:10px;bottom:10px;z-index:99998;background:rgba(10,14,22,.82);"
    + "color:#bdf;border:1px solid #366;border-radius:10px;padding:6px 12px;"
    + "font:600 13px system-ui,sans-serif;pointer-events:none;max-width:74vw";
  function mount(){ if(!bn.parentNode){ document.body.appendChild(bn); document.body.appendChild(tag); } }
  var bnT = null;
  function label(t){ return t.name || t.cell || "Box-Bildschirm"; }
  function setTag(t){ tag.textContent = "Gerät: " + label(t) + "  (" + (curIdx+1) + "/" + (ring?ring.length:1) + ")   —   Relacon-Seitentasten wechseln  ◀ ▶"; }
  function banner(t){
    mount();
    bn.innerHTML = "Durchgeschleift zu<br><span style='color:#8ef;font-size:30px'>" + label(t) + "</span>";
    bn.style.opacity = "1";
    if (bnT) clearTimeout(bnT);
    bnT = setTimeout(function(){ bn.style.opacity = "0"; }, 2600);
    setTag(t);
  }
  function go(dir){
    if (!ring || ring.length < 2) return;
    curIdx = (curIdx + dir + ring.length) % ring.length;
    var t = ring[curIdx];
    banner(t);
    setTimeout(function(){ location.search = t.cell ? ("?cell=" + encodeURIComponent(t.cell)) : ""; }, 260);
  }
  fetch("/api/vmcells", {credentials:"same-origin"}).then(function(r){ return r.json(); }).then(function(d){
    ring = [{cell:"", name:"Box-Bildschirm"}];
    ((d && d.cells) || []).forEach(function(c){ ring.push({cell:c.id, name:c.name||c.id}); });
    curIdx = 0;
    for (var i=0;i<ring.length;i++){ if (ring[i].cell === curCell) curIdx = i; }
    mount(); banner(ring[curIdx]);                           // Bestaetigung beim Laden
  }).catch(function(){ mount(); tag.textContent = "Geräteliste nicht ladbar (eingeloggt?)"; });
  window.addEventListener("mousedown", function(e){
    if (e.button === 3){ e.preventDefault(); e.stopPropagation(); go(-1); }
    else if (e.button === 4){ e.preventDefault(); e.stopPropagation(); go(1); }
    else if (e.button > 2){ e.preventDefault(); mount(); tag.textContent = "Seitentaste erkannt: button " + e.button + " — sag mir: vor oder zurück?"; }
  }, true);
  window.addEventListener("auxclick", function(e){ if (e.button >= 3){ e.preventDefault(); } }, true);
  window.addEventListener("keydown", function(e){
    if (e.ctrlKey && e.altKey && (e.key === "ArrowRight" || e.key === "l")){ e.preventDefault(); go(1); }
    else if (e.ctrlKey && e.altKey && (e.key === "ArrowLeft" || e.key === "h")){ e.preventDefault(); go(-1); }
  }, true);
})();

cv.addEventListener("wheel", function(e){
  e.preventDefault(); var c = fbCoords(e); var bit = e.deltaY < 0 ? 8 : 16;
  pointer(c[0], c[1], btnMask | bit); pointer(c[0], c[1], btnMask);
}, { passive: false });

/* ---- TOUCH (mobile): the mouse listeners above NEVER fire from a finger, so without this a phone
   can VIEW the screen but not click it (the reported bug). A tap sends a real button down+up at the
   touch point; a vertical swipe past a threshold emits wheel notches so pages scroll. We do NOT focus
   the soft keyboard on tap (the ⌨ button owns that) — tapping is pure pointing. ---- */
cv.style.touchAction = "none";      // let US handle the gesture (no browser scroll/zoom stealing it)
function fbXY(cx, cy){
  var r = cv.getBoundingClientRect();
  var x = Math.round((cx - r.left) / r.width  * fbW);
  var y = Math.round((cy - r.top)  / r.height * fbH);
  x = x < 0 ? 0 : (x >= fbW ? fbW - 1 : x);
  y = y < 0 ? 0 : (y >= fbH ? fbH - 1 : y);
  return [x, y];
}
var _tt = null;
cv.addEventListener("touchstart", function(e){
  if (!e.touches.length) return;
  e.preventDefault();
  var t = e.touches[0];
  _tt = { cx:t.clientX, cy:t.clientY, ly:t.clientY, moved:false };
}, { passive:false });
cv.addEventListener("touchmove", function(e){
  if (!_tt || !e.touches.length) return;
  e.preventDefault();
  var t = e.touches[0];
  if (Math.abs(t.clientX - _tt.cx) > 8 || Math.abs(t.clientY - _tt.cy) > 8) _tt.moved = true;
  var ddy = t.clientY - _tt.ly, PX = 22;                 // finger travel per wheel notch
  if (Math.abs(ddy) >= PX){
    var bit = ddy < 0 ? 16 : 8;                          // finger up => scroll page DOWN
    var steps = Math.min(6, Math.floor(Math.abs(ddy) / PX));
    var c = fbXY(t.clientX, t.clientY);
    for (var i = 0; i < steps; i++){ pointer(c[0], c[1], btnMask | bit); pointer(c[0], c[1], btnMask); }
    _tt.ly = t.clientY;
  }
}, { passive:false });
cv.addEventListener("touchend", function(e){
  if (!_tt) return;
  e.preventDefault();
  if (!_tt.moved){                                       // a TAP => a real click at the down point
    var c = fbXY(_tt.cx, _tt.cy);
    pointer(c[0], c[1], btnMask | 1);
    pointer(c[0], c[1], btnMask & ~1);
  }
  _tt = null;
}, { passive:false });
cv.addEventListener("touchcancel", function(){ _tt = null; }, { passive:false });

/* keysym mapping: printable -> its code point (ASCII/Latin-1 == X keysym); named keys via table. */
var KEYMAP = {
  "Enter":0xff0d, "Backspace":0xff08, "Tab":0xff09, "Escape":0xff1b, "Delete":0xffff,
  "Home":0xff50, "End":0xff57, "PageUp":0xff55, "PageDown":0xff56, "Insert":0xff63,
  "ArrowLeft":0xff51, "ArrowUp":0xff52, "ArrowRight":0xff53, "ArrowDown":0xff54,
  "Shift":0xffe1, "Control":0xffe3, "Alt":0xffe9, "AltGraph":0xfe03, "Meta":0xffe7, "OS":0xffe7,
  "F1":0xffbe,"F2":0xffbf,"F3":0xffc0,"F4":0xffc1,"F5":0xffc2,"F6":0xffc3,
  "F7":0xffc4,"F8":0xffc5,"F9":0xffc6,"F10":0xffc7,"F11":0xffc8,"F12":0xffc9,
  " ":0x20
};
function charKeysym(ch){ var cp = ch.codePointAt(0); return cp <= 0xff ? cp : 0x01000000 + cp; }
function keysym(e){
  if (KEYMAP[e.key] !== undefined) return KEYMAP[e.key];
  if (e.key && Array.from(e.key).length === 1) return charKeysym(e.key);
  return 0;
}
function key(k, down){
  if (connected && k){
    wsend([4, down ? 1 : 0, 0, 0, (k>>24)&255, (k>>16)&255, (k>>8)&255, k&255]);
    if (down){ _lastKeys.push("0x" + k.toString(16)); if (_lastKeys.length > 8) _lastKeys.shift(); setHud(); }
  }
}
function isPureMod(e){ var k = e.key; return k === "Shift" || k === "Control" || k === "Alt" || k === "AltGraph" || k === "Meta" || k === "OS"; }
// Keyboard listeners live on WINDOW (not the canvas) so focus survives clicking the page chrome and
// coming back — the canvas silently losing focus was why typing sometimes stopped. Touch keeps the
// #kbd path. We do NOT preventDefault pure modifier keys (can disrupt the browser's AltGr resolution).
window.addEventListener("keydown", function(e){ if (isTouch) return; var k = keysym(e); if (k){ if (!isPureMod(e)) e.preventDefault(); key(k, true); } });
window.addEventListener("keyup",   function(e){ if (isTouch) return; var k = keysym(e); if (k){ if (!isPureMod(e)) e.preventDefault(); key(k, false); } });
/* ---- mobile soft keyboard: a focusable hidden input summons it; its text becomes RFB keys ---- */
var kbd = document.getElementById("kbd");
var isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0) || (window.matchMedia && matchMedia('(pointer: coarse)').matches);
function tapKey(k){ if (k){ key(k, true); key(k, false); } }
function focusKeys(){ if (isTouch){ try { kbd.focus(); } catch(e){} } else { cv.focus(); } }
if (isTouch){
  // Android Gboard/iOS: capture printable text via the INPUT event (NOT preventDefault'd beforeinput —
  // that fights predictive/composition input and can drop everything). We read whatever landed in the
  // field, forward each char as an RFB key, then clear. Enter/Backspace come via beforeinput (soft
  // keyboards fire insertLineBreak / deleteContentBackward reliably); nav/function keys via keydown.
  var _flush = function(){ var v = kbd.value; if (v){ Array.from(v).forEach(function(ch){ tapKey(charKeysym(ch)); }); kbd.value = ""; } };
  kbd.addEventListener("input", function(e){ if (e.isComposing) return; _flush(); });   // wait for IME/prediction to commit
  kbd.addEventListener("compositionend", function(){ _flush(); });
  kbd.addEventListener("beforeinput", function(e){
    var t = e.inputType;
    if (t === "insertLineBreak" || t === "insertParagraph"){ e.preventDefault(); kbd.value = ""; tapKey(0xff0d); }
    else if (t === "deleteContentBackward" || t === "deleteContentForward"){ e.preventDefault(); tapKey(0xff08); }
  });
  kbd.addEventListener("keydown", function(e){
    if (e.key === "Enter" || e.key === "Backspace") return;   // handled via beforeinput
    var k = KEYMAP[e.key]; if (k && Array.from(e.key || "").length !== 1){ e.preventDefault(); tapKey(k); }
  });
  kbd.value = "";
} else {
  cv.addEventListener("mousedown", function(){ cv.focus(); });
}
/* ---- fullscreen (all devices) + explicit keyboard summon (touch) ---- */
var fsbtn = document.getElementById("fsbtn"), kbbtn = document.getElementById("kbbtn");
function _fsEl(){ return document.fullscreenElement || document.webkitFullscreenElement || null; }
function toggleFs(){
  try{
    if (_fsEl()){ (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
    else { var el = document.documentElement; (el.requestFullscreen || el.webkitRequestFullscreen).call(el); }
  }catch(e){ lastMsg = "Vollbild n/a"; setHud(); }
}
if (fsbtn) fsbtn.addEventListener("click", function(e){ e.preventDefault(); toggleFs(); setTimeout(layout, 250); });
document.addEventListener("fullscreenchange", layout);
document.addEventListener("webkitfullscreenchange", layout);
// On touch: show the ⌨ button and let it summon the soft keyboard from WITHIN the tap gesture
// (the reliable path — auto-focus on canvas tap is flaky across mobile browsers).
// ---- recovery the USER can do (no SSH) ----
// 🔄 reconnect the view: the frozen-frame fix. Mobile backgrounding kills the WS socket silently
// (no onclose fires) so the render loop hangs on a stale frame — a reload gets a fresh connection.
function _reconnect(manual){
  if (window.__vncReloading) return; window.__vncReloading = true;
  var FK = "vnc3fails:" + (location.search || "");
  if (manual) { try { sessionStorage.removeItem(FK); } catch(e){} }        // user retry -> reset the cap
  else if (frames === 0) {                                                 // auto-reconnect on a never-shown screen counts toward the cap
    var n = 0; try { n = (parseInt(sessionStorage.getItem(FK) || "0", 10) || 0) + 1; sessionStorage.setItem(FK, String(n)); } catch(e){}
    if (n >= 4) { window.__vncReloading = false; try { clearInterval(_vncWatch); } catch(e){} _vncNoScreen(); return; }
  }
  try { ws.close(); } catch(e){} location.reload();
}
function _reload(){ _reconnect(true); }   // the manual button always resets the cap
var rcbtn = document.getElementById("rcbtn");
if (rcbtn) rcbtn.addEventListener("click", function(e){ e.preventDefault(); _reload(); });
// 🦊 restart Firefox on the seat: for a genuinely broken/black window (e.g. closed the last tab).
var ffbtn = document.getElementById("ffbtn");
if (ffbtn) ffbtn.addEventListener("click", function(e){ e.preventDefault();
  lastMsg = "starte Firefox neu …"; setHud();
  fetch("/api/screen/firefox-restart", {method:"POST"}).then(function(){ setTimeout(function(){ try { fbur(0); } catch(_){} }, 4000); }).catch(function(){});
});
// „Firefox neu starten" (🦊) ergibt NUR auf dem Firefox-Seat Sinn. Bei einer Session-/Office-Zelle
// (sc-*) gibt es kein Firefox — der Knopf träfe den falschen Seat. Dort ausblenden (Owner-Feedback:
// im Office-Desktop sind Browser/Reload Relikte; nur Vollbild ⛶ + Reconnect 🔄 bleiben sinnvoll).
if (ffbtn && _cellm && /^sc-/.test(_cellm[1])) ffbtn.style.display = "none";
// AUTO self-heal: watch for a stale frame. Nudge with a full-frame request; hard-reconnect if dead.
// Runs silently so most freezes recover without the user doing anything.
var _vncWatch = setInterval(function(){
  if (document.hidden) return;
  if (!connected){
    // HANDSHAKE STALL: the RFB handshake never reached `connected` (rfbd starved at the handshake/
    // first frame — the root cause of a blank "verbinde …" /vnc3). If the socket has been open too
    // long without connecting, drop it and reconnect fresh rather than sit forever. The compositor
    // CPUWeight fix makes this rare; this is the belt-and-braces the earlier watchdog missed (it
    // early-returned on !connected and so never healed a stuck handshake).
    if (wsOpenAt && Date.now() - wsOpenAt > 9000){ _reconnect(false); }
    return;
  }
  var age = Date.now() - lastFrame;
  if (age > 4000){ try { fbur(0); } catch(e){} }     // nudge the server for a full frame
  if (age > 12000){ _reconnect(false); }              // socket dead -> reconnect (live screen: not capped)
}, 3000);
document.addEventListener("visibilitychange", function(){
  if (document.hidden) return;                          // returning to the tab (very common on mobile)
  if (!ws || ws.readyState !== 1){ _reconnect(false); return; } // socket died while backgrounded -> reconnect
  lastFrame = Date.now(); try { fbur(0); } catch(e){}   // otherwise pull a fresh frame immediately
});
// Keyboard bar: ⌨ opens it AND hides itself (no doubling); ✕ closes it and ⌨ returns. Focus is
// synchronous inside the tap so Android opens Gboard.
if (isTouch && kbbtn){
  kbbtn.style.display = "block";
  var _bar = document.getElementById("kbbar"), _kbclose = document.getElementById("kbclose");
  kbbtn.addEventListener("click", function(e){ e.preventDefault();
    _bar.classList.add("on"); kbbtn.style.display = "none"; try { kbd.focus(); } catch(_){} });
  if (_kbclose) _kbclose.addEventListener("click", function(e){ e.preventDefault();
    _bar.classList.remove("on"); kbbtn.style.display = "block"; try { kbd.blur(); } catch(_){} });
}
</script>
</body></html>"""

LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>brainbox-portal — Login</title><style>
body{background:#11111c;color:#d4d4e2;font:15px/1.5 ui-monospace,monospace;display:grid;place-items:center;height:100vh;margin:0}
form{background:#171724;border:1px solid #23233a;border-radius:16px;padding:28px;text-align:center;min-width:260px}
.g{font-size:34px}h1{font-size:16px;margin:6px 0 16px;color:#8a7fff}
input{width:100%;box-sizing:border-box;margin:5px 0;background:#0e0e18;border:1px solid #2a2a40;color:#d4d4e2;border-radius:10px;padding:11px;font:inherit}
button{margin-top:12px;width:100%;background:#8a7fff;color:#0b0b14;border:0;border-radius:10px;padding:11px;font-weight:700;cursor:pointer}
.err{color:#e58a93;font-size:13px;min-height:18px;margin-top:8px}
.hint{color:#6a6a86;font-size:11px;margin-top:10px}
</style></head><body>
<form method=POST action="api/login"><div class=g>👻</div><h1>brainbox-portal</h1>
<input name=user type=text autocapitalize=none autocorrect=off autocomplete=username placeholder="Benutzer" autofocus>
<input name=password type=password autocomplete=current-password placeholder="Passwort">
<button>Eintreten</button><div class=err>%ERR%</div>
<div class=hint>owner: Benutzer „owner" + PIN</div></form></body></html>"""
