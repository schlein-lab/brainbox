
import fs from 'node:fs';

const pkgDir = process.argv[2];
const wasmPath = pkgDir + '/phantom_wasm_bg.wasm';

function makeFrame() {
  const screenW = 320, screenH = 200;
  const sw = 160, sh = 120, sx = 40, sy = 30;
  const pix = sw * sh * 4;
  const header = 28;
  const surfHdr = 4+2+2+2+2+2+1+1+2 + (1*(2+2+2+2)) + 4;
  const total = header + surfHdr + pix;
  const buf = new ArrayBuffer(total);
  const dv = new DataView(buf);
  const u8 = new Uint8Array(buf);
  let o = 0;
  u8[0]=0x50; u8[1]=0x53; u8[2]=0x4E; u8[3]=0x31; o=4;
  dv.setUint16(o,1,true); o+=2;
  dv.setUint16(o,0x0001,true); o+=2;
  dv.setUint32(o,7,true); o+=4;
  dv.setUint32(o,1,true); o+=4;
  dv.setUint16(o,screenW,true); o+=2;
  dv.setUint16(o,screenH,true); o+=2;
  dv.setUint16(o,1,true); o+=2;
  dv.setUint16(o,0,true); o+=2;
  dv.setUint32(o,pix,true); o+=4;
  if (o !== header) throw new Error('header size mismatch: '+o);
  dv.setUint32(o,42,true); o+=4;
  dv.setInt16(o,sx,true); o+=2;
  dv.setInt16(o,sy,true); o+=2;
  dv.setUint16(o,sw,true); o+=2;
  dv.setUint16(o,sh,true); o+=2;
  dv.setUint16(o,sw,true); o+=2;
  u8[o]=0; o+=1;
  u8[o]=0x01; o+=1;
  dv.setUint16(o,1,true); o+=2;
  dv.setUint16(o,0,true); o+=2;
  dv.setUint16(o,0,true); o+=2;
  dv.setUint16(o,sw,true); o+=2;
  dv.setUint16(o,sh,true); o+=2;
  dv.setUint32(o,pix,true); o+=4;
  const pixelsOff = o;
  o += pix;
  if (o !== total) throw new Error('total size mismatch: '+o+' vs '+total);
  return { buf: new Uint8Array(buf), pixelsOff, pix, total };
}

const f = makeFrame();
console.log('FRAME OK: total='+f.total+' pixelsOff='+f.pixelsOff+' pix='+f.pix);

const m = f.buf.slice(0,4);
console.log('magic bytes:', [...m], '(expect 80,83,78,49 = P,S,N,1)');

const bytes = fs.readFileSync(wasmPath);
const mod = await WebAssembly.compile(bytes);
const needed = WebAssembly.Module.imports(mod);

const imports = {};
for (const imp of needed) {
  imports[imp.module] = imports[imp.module] || {};
  if (imp.kind === 'function') {
    imports[imp.module][imp.name] = (...a) => 0;
  } else if (imp.kind === 'memory') {
    imports[imp.module][imp.name] = new WebAssembly.Memory({ initial: 17 });
  } else if (imp.kind === 'table') {
    imports[imp.module][imp.name] = new WebAssembly.Table({ initial: 1, element: 'anyfunc' });
  } else if (imp.kind === 'global') {
    imports[imp.module][imp.name] = new WebAssembly.Global({ value: 'i32', mutable: true }, 0);
  }
}
const inst = await WebAssembly.instantiate(mod, imports);
const exp = inst.exports;
const wantExports = ['compositor_new','compositor_on_frame','compositor_resize','compositor_frame_count','memory'];
const have = Object.keys(exp);
const missing = wantExports.filter(x => !have.includes(x));
console.log('WASM instantiated. exports present:', wantExports.filter(x=>have.includes(x)).join(','));
if (missing.length) { console.log('MISSING EXPORTS:', missing.join(',')); process.exit(1); }
console.log('IMPORTS count:', needed.length, '(all stubbed OK)');
console.log('VERIFY PASS');
