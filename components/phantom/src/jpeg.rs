#![allow(clippy::needless_range_loop)]

#[rustfmt::skip]
const STD_LUMA_Q: [u8; 64] = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68,109,103, 77,
    24, 35, 55, 64, 81,104,113, 92,
    49, 64, 78, 87,103,121,120,101,
    72, 92, 95, 98,112,100,103, 99,
];
#[rustfmt::skip]
const STD_CHROMA_Q: [u8; 64] = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
];

#[rustfmt::skip]
const ZIGZAG: [usize; 64] = [
     0, 1, 8,16, 9, 2, 3,10,
    17,24,32,25,18,11, 4, 5,
    12,19,26,33,40,48,41,34,
    27,20,13, 6, 7,14,21,28,
    35,42,49,56,57,50,43,36,
    29,22,15,23,30,37,44,51,
    58,59,52,45,38,31,39,46,
    53,60,61,54,47,55,62,63,
];

const DC_LUMA_BITS:   [u8; 16] = [0,1,5,1,1,1,1,1,1,0,0,0,0,0,0,0];
const DC_LUMA_VALS:   [u8; 12] = [0,1,2,3,4,5,6,7,8,9,10,11];
const DC_CHROMA_BITS: [u8; 16] = [0,3,1,1,1,1,1,1,1,1,1,0,0,0,0,0];
const DC_CHROMA_VALS: [u8; 12] = [0,1,2,3,4,5,6,7,8,9,10,11];

const AC_LUMA_BITS: [u8; 16] = [0,2,1,3,3,2,4,3,5,5,4,4,0,0,1,0x7d];
#[rustfmt::skip]
const AC_LUMA_VALS: [u8; 162] = [
    0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0x06,0x13,0x51,0x61,0x07,
    0x22,0x71,0x14,0x32,0x81,0x91,0xa1,0x08,0x23,0x42,0xb1,0xc1,0x15,0x52,0xd1,0xf0,
    0x24,0x33,0x62,0x72,0x82,0x09,0x0a,0x16,0x17,0x18,0x19,0x1a,0x25,0x26,0x27,0x28,
    0x29,0x2a,0x34,0x35,0x36,0x37,0x38,0x39,0x3a,0x43,0x44,0x45,0x46,0x47,0x48,0x49,
    0x4a,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x5a,0x63,0x64,0x65,0x66,0x67,0x68,0x69,
    0x6a,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7a,0x83,0x84,0x85,0x86,0x87,0x88,0x89,
    0x8a,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9a,0xa2,0xa3,0xa4,0xa5,0xa6,0xa7,
    0xa8,0xa9,0xaa,0xb2,0xb3,0xb4,0xb5,0xb6,0xb7,0xb8,0xb9,0xba,0xc2,0xc3,0xc4,0xc5,
    0xc6,0xc7,0xc8,0xc9,0xca,0xd2,0xd3,0xd4,0xd5,0xd6,0xd7,0xd8,0xd9,0xda,0xe1,0xe2,
    0xe3,0xe4,0xe5,0xe6,0xe7,0xe8,0xe9,0xea,0xf1,0xf2,0xf3,0xf4,0xf5,0xf6,0xf7,0xf8,
    0xf9,0xfa,
];
const AC_CHROMA_BITS: [u8; 16] = [0,2,1,2,4,4,3,4,7,5,4,4,0,1,2,0x77];
#[rustfmt::skip]
const AC_CHROMA_VALS: [u8; 162] = [
    0x00,0x01,0x02,0x03,0x11,0x04,0x05,0x21,0x31,0x06,0x12,0x41,0x51,0x07,0x61,0x71,
    0x13,0x22,0x32,0x81,0x08,0x14,0x42,0x91,0xa1,0xb1,0xc1,0x09,0x23,0x33,0x52,0xf0,
    0x15,0x62,0x72,0xd1,0x0a,0x16,0x24,0x34,0xe1,0x25,0xf1,0x17,0x18,0x19,0x1a,0x26,
    0x27,0x28,0x29,0x2a,0x35,0x36,0x37,0x38,0x39,0x3a,0x43,0x44,0x45,0x46,0x47,0x48,
    0x49,0x4a,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x5a,0x63,0x64,0x65,0x66,0x67,0x68,
    0x69,0x6a,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7a,0x82,0x83,0x84,0x85,0x86,0x87,
    0x88,0x89,0x8a,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9a,0xa2,0xa3,0xa4,0xa5,
    0xa6,0xa7,0xa8,0xa9,0xaa,0xb2,0xb3,0xb4,0xb5,0xb6,0xb7,0xb8,0xb9,0xba,0xc2,0xc3,
    0xc4,0xc5,0xc6,0xc7,0xc8,0xc9,0xca,0xd2,0xd3,0xd4,0xd5,0xd6,0xd7,0xd8,0xd9,0xda,
    0xe2,0xe3,0xe4,0xe5,0xe6,0xe7,0xe8,0xe9,0xea,0xf2,0xf3,0xf4,0xf5,0xf6,0xf7,0xf8,
    0xf9,0xfa,
];

type HuffTable = [(u16, u8); 256];

fn build_huff(bits: &[u8; 16], vals: &[u8]) -> HuffTable {
    let mut table: HuffTable = [(0, 0); 256];
    let mut code: u16 = 0;
    let mut k = 0usize;
    for len in 1..=16u8 {
        for _ in 0..bits[(len - 1) as usize] {
            let sym = vals[k] as usize;
            table[sym] = (code, len);
            code += 1;
            k += 1;
        }
        code <<= 1;
    }
    table
}

struct BitWriter {
    out: Vec<u8>,
    acc: u32,
    nbits: u32,
}
impl BitWriter {
    fn new(out: Vec<u8>) -> BitWriter {
        BitWriter { out, acc: 0, nbits: 0 }
    }
    fn put_bits(&mut self, code: u16, len: u8) {
        if len == 0 {
            return;
        }
        self.acc |= (code as u32) << (32 - self.nbits - len as u32);
        self.nbits += len as u32;
        while self.nbits >= 8 {
            let byte = (self.acc >> 24) as u8;
            self.out.push(byte);
            if byte == 0xFF {
                self.out.push(0x00);
            }
            self.acc <<= 8;
            self.nbits -= 8;
        }
    }
    fn flush(&mut self) {
        if self.nbits > 0 {

            self.put_bits(0x7F, (8 - self.nbits) as u8);
        }
    }
    fn into_inner(self) -> Vec<u8> {
        self.out
    }
}

fn cos_table() -> &'static [f32; 64] {
    use std::sync::OnceLock;
    static T: OnceLock<[f32; 64]> = OnceLock::new();
    T.get_or_init(|| {
        use std::f32::consts::PI;
        let mut t = [0f32; 64];
        for u in 0..8 {
            for x in 0..8 {
                t[u * 8 + x] = ((2.0 * x as f32 + 1.0) * u as f32 * PI / 16.0).cos();
            }
        }
        t
    })
}

fn fdct_8x8(block: &mut [f32; 64]) {
    let t = cos_table();
    let mut tmp = [0f32; 64];
    for i in 0..8 {
        dct_1d(&block[i * 8..i * 8 + 8], &mut tmp[i * 8..i * 8 + 8], t);
    }

    let mut col_in = [0f32; 8];
    let mut col_out = [0f32; 8];
    for j in 0..8 {
        for i in 0..8 {
            col_in[i] = tmp[i * 8 + j];
        }
        dct_1d(&col_in, &mut col_out, t);
        for i in 0..8 {
            block[i * 8 + j] = col_out[i];
        }
    }
}

fn dct_1d(input: &[f32], output: &mut [f32], t: &[f32; 64]) {
    for u in 0..8 {
        let cu = if u == 0 { 1.0 / (2.0f32).sqrt() } else { 1.0 };
        let mut sum = 0f32;
        for x in 0..8 {
            sum += input[x] * t[u * 8 + x];
        }
        output[u] = 0.5 * cu * sum;
    }
}

fn scaled_quant(base: &[u8; 64], quality: u32) -> [u16; 64] {
    let q = quality.clamp(1, 100);
    let scale = if q < 50 { 5000 / q } else { 200 - q * 2 };
    let mut out = [0u16; 64];
    for i in 0..64 {
        let v = (base[i] as u32 * scale + 50) / 100;
        out[i] = v.clamp(1, 255) as u16;
    }
    out
}

fn encode_value(v: i32) -> (u8, u16) {
    let mut abs = v.unsigned_abs();
    let mut cat = 0u8;
    while abs > 0 {
        cat += 1;
        abs >>= 1;
    }
    let bits = if v < 0 { (v - 1) as u16 & ((1 << cat) - 1) } else { v as u16 & ((1 << cat) - 1) };
    (cat, bits)
}

struct Component<'a> {
    dc_table: &'a HuffTable,
    ac_table: &'a HuffTable,
    quant: &'a [u16; 64],
    prev_dc: i32,
}

impl<'a> Component<'a> {

    fn encode_block(&mut self, bw: &mut BitWriter, samples: &[f32; 64]) {
        let mut block = *samples;
        fdct_8x8(&mut block);

        let mut zz = [0i32; 64];
        for k in 0..64 {
            let nat = ZIGZAG[k];
            zz[k] = (block[nat] / self.quant[nat] as f32).round() as i32;
        }

        let diff = zz[0] - self.prev_dc;
        self.prev_dc = zz[0];
        let (cat, bits) = encode_value(diff);
        let (code, len) = self.dc_table[cat as usize];
        bw.put_bits(code, len);
        if cat > 0 {
            bw.put_bits(bits, cat);
        }

        let mut run = 0;
        for k in 1..64 {
            let v = zz[k];
            if v == 0 {
                run += 1;
            } else {
                while run > 15 {

                    let (c, l) = self.ac_table[0xF0];
                    bw.put_bits(c, l);
                    run -= 16;
                }
                let (size, vbits) = encode_value(v);
                let sym = ((run as u8) << 4) | size;
                let (c, l) = self.ac_table[sym as usize];
                bw.put_bits(c, l);
                bw.put_bits(vbits, size);
                run = 0;
            }
        }
        if run > 0 {

            let (c, l) = self.ac_table[0x00];
            bw.put_bits(c, l);
        }
    }
}

fn emit_dqt(out: &mut Vec<u8>, id: u8, q: &[u16; 64]) {
    out.extend_from_slice(&[0xFF, 0xDB]);
    out.extend_from_slice(&[0x00, 0x43]);
    out.push(id);
    for k in 0..64 {
        out.push(q[ZIGZAG[k]] as u8);
    }
}

fn emit_dht(out: &mut Vec<u8>, class_id: u8, bits: &[u8; 16], vals: &[u8]) {
    let len = 2 + 1 + 16 + vals.len();
    out.extend_from_slice(&[0xFF, 0xC4]);
    out.extend_from_slice(&(len as u16).to_be_bytes());
    out.push(class_id);
    out.extend_from_slice(bits);
    out.extend_from_slice(vals);
}

pub fn encode_rgba(width: u32, height: u32, rgba: &[u8], quality: u32) -> Vec<u8> {
    let w = width as usize;
    let h = height as usize;
    let luma_q = scaled_quant(&STD_LUMA_Q, quality);
    let chroma_q = scaled_quant(&STD_CHROMA_Q, quality);

    let dc_l = build_huff(&DC_LUMA_BITS, &DC_LUMA_VALS);
    let ac_l = build_huff(&AC_LUMA_BITS, &AC_LUMA_VALS);
    let dc_c = build_huff(&DC_CHROMA_BITS, &DC_CHROMA_VALS);
    let ac_c = build_huff(&AC_CHROMA_BITS, &AC_CHROMA_VALS);

    let mut out: Vec<u8> = Vec::with_capacity(w * h / 4 + 1024);

    out.extend_from_slice(&[0xFF, 0xD8]);

    out.extend_from_slice(&[0xFF, 0xE0, 0x00, 0x10]);
    out.extend_from_slice(b"JFIF\0");
    out.extend_from_slice(&[0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00]);

    emit_dqt(&mut out, 0, &luma_q);
    emit_dqt(&mut out, 1, &chroma_q);

    out.extend_from_slice(&[0xFF, 0xC0, 0x00, 0x11, 0x08]);
    out.extend_from_slice(&(height as u16).to_be_bytes());
    out.extend_from_slice(&(width as u16).to_be_bytes());
    out.push(3);
    out.extend_from_slice(&[0x01, 0x22, 0x00]);
    out.extend_from_slice(&[0x02, 0x11, 0x01]);
    out.extend_from_slice(&[0x03, 0x11, 0x01]);

    emit_dht(&mut out, 0x00, &DC_LUMA_BITS, &DC_LUMA_VALS);
    emit_dht(&mut out, 0x10, &AC_LUMA_BITS, &AC_LUMA_VALS);
    emit_dht(&mut out, 0x01, &DC_CHROMA_BITS, &DC_CHROMA_VALS);
    emit_dht(&mut out, 0x11, &AC_CHROMA_BITS, &AC_CHROMA_VALS);

    out.extend_from_slice(&[0xFF, 0xDA, 0x00, 0x0C, 0x03]);
    out.extend_from_slice(&[0x01, 0x00]);
    out.extend_from_slice(&[0x02, 0x11]);
    out.extend_from_slice(&[0x03, 0x11]);
    out.extend_from_slice(&[0x00, 0x3F, 0x00]);

    let mut bw = BitWriter::new(out);
    let mut y_comp = Component { dc_table: &dc_l, ac_table: &ac_l, quant: &luma_q, prev_dc: 0 };
    let mut cb_comp = Component { dc_table: &dc_c, ac_table: &ac_c, quant: &chroma_q, prev_dc: 0 };
    let mut cr_comp = Component { dc_table: &dc_c, ac_table: &ac_c, quant: &chroma_q, prev_dc: 0 };

    let mcus_x = w.div_ceil(16);
    let mcus_y = h.div_ceil(16);

    let sample = |x: usize, y: usize| -> (f32, f32, f32) {
        let cx = x.min(w.saturating_sub(1));
        let cy = y.min(h.saturating_sub(1));
        let i = (cy * w + cx) * 4;
        let r = rgba[i] as f32;
        let g = rgba[i + 1] as f32;
        let b = rgba[i + 2] as f32;

        let yy = 0.299 * r + 0.587 * g + 0.114 * b;
        let cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0;
        let cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0;
        (yy, cb, cr)
    };

    let mut yblk = [0f32; 64];
    let mut cbblk = [0f32; 64];
    let mut crblk = [0f32; 64];

    for my in 0..mcus_y {
        for mx in 0..mcus_x {
            let bx = mx * 16;
            let by = my * 16;

            for (dby, dbx) in [(0, 0), (0, 8), (8, 0), (8, 8)] {
                for yy in 0..8 {
                    for xx in 0..8 {
                        let (l, _, _) = sample(bx + dbx + xx, by + dby + yy);
                        yblk[yy * 8 + xx] = l - 128.0;
                    }
                }
                y_comp.encode_block(&mut bw, &yblk);
            }

            for yy in 0..8 {
                for xx in 0..8 {
                    let px = bx + xx * 2;
                    let py = by + yy * 2;
                    let mut sb = 0f32;
                    let mut sr = 0f32;
                    for (ox, oy) in [(0, 0), (1, 0), (0, 1), (1, 1)] {
                        let (_, cb, cr) = sample(px + ox, py + oy);
                        sb += cb;
                        sr += cr;
                    }
                    cbblk[yy * 8 + xx] = sb / 4.0 - 128.0;
                    crblk[yy * 8 + xx] = sr / 4.0 - 128.0;
                }
            }
            cb_comp.encode_block(&mut bw, &cbblk);
            cr_comp.encode_block(&mut bw, &crblk);
        }
    }

    bw.flush();
    let mut out = bw.into_inner();

    out.extend_from_slice(&[0xFF, 0xD9]);
    out
}
