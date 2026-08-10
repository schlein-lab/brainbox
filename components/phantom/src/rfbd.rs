#![allow(dead_code)]

use crate::headless_fb::FrameTap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::time::Duration;

pub trait InputSink: Send + Sync {

    fn key(&self, keysym: u32, down: bool);

    fn pointer(&self, x: u16, y: u16, button_mask: u8);
}

#[derive(Clone, Copy)]
struct PixelFormat {
    bpp: u8,
    depth: u8,
    big_endian: bool,
    true_color: bool,
    red_max: u16,
    green_max: u16,
    blue_max: u16,
    red_shift: u8,
    green_shift: u8,
    blue_shift: u8,
}

impl PixelFormat {

    fn default_bgrx() -> PixelFormat {
        PixelFormat {
            bpp: 32, depth: 24, big_endian: false, true_color: true,
            red_max: 255, green_max: 255, blue_max: 255,
            red_shift: 16, green_shift: 8, blue_shift: 0,
        }
    }

    fn write(&self, out: &mut Vec<u8>) {
        out.push(self.bpp);
        out.push(self.depth);
        out.push(if self.big_endian { 1 } else { 0 });
        out.push(if self.true_color { 1 } else { 0 });
        out.extend_from_slice(&self.red_max.to_be_bytes());
        out.extend_from_slice(&self.green_max.to_be_bytes());
        out.extend_from_slice(&self.blue_max.to_be_bytes());
        out.push(self.red_shift);
        out.push(self.green_shift);
        out.push(self.blue_shift);
        out.extend_from_slice(&[0u8, 0, 0]);
    }

    fn parse(b: &[u8]) -> Option<PixelFormat> {
        if b.len() < 16 {
            return None;
        }
        Some(PixelFormat {
            bpp: b[0],
            depth: b[1],
            big_endian: b[2] != 0,
            true_color: b[3] != 0,
            red_max: u16::from_be_bytes([b[4], b[5]]),
            green_max: u16::from_be_bytes([b[6], b[7]]),
            blue_max: u16::from_be_bytes([b[8], b[9]]),
            red_shift: b[10],
            green_shift: b[11],
            blue_shift: b[12],
        })
    }

    #[inline]
    fn encode_pixel(&self, r: u8, g: u8, b: u8, out: &mut Vec<u8>) {

        let rv = (r as u32 * self.red_max as u32 / 255) << self.red_shift;
        let gv = (g as u32 * self.green_max as u32 / 255) << self.green_shift;
        let bv = (b as u32 * self.blue_max as u32 / 255) << self.blue_shift;
        let px = rv | gv | bv;
        let nbytes = (self.bpp / 8).clamp(1, 4) as usize;
        if self.big_endian {
            for i in (0..nbytes).rev() {
                out.push((px >> (i * 8)) as u8);
            }
        } else {
            for i in 0..nbytes {
                out.push((px >> (i * 8)) as u8);
            }
        }
    }
}

pub fn spawn(tap: FrameTap, addr: String, input: Option<Arc<dyn InputSink>>) -> std::io::Result<()> {
    let listener = TcpListener::bind(&addr)?;
    eprintln!("rfbd: VNC/RFB on {addr}  (view {}, input {})",
        "FrameTap", if input.is_some() { "live" } else { "view-only" });
    std::thread::spawn(move || {
        for conn in listener.incoming() {
            let stream = match conn {
                Ok(s) => s,
                Err(_) => continue,
            };
            let tap = tap.clone();
            let input = input.clone();
            std::thread::spawn(move || {
                if let Err(e) = handle_client(stream, tap, input) {
                    eprintln!("rfbd: client closed: {e}");
                }
            });
        }
    });
    Ok(())
}

fn read_exact(stream: &mut TcpStream, buf: &mut [u8]) -> std::io::Result<()> {
    stream.read_exact(buf)
}

fn wait_first_frame(tap: &FrameTap) -> Option<(Vec<u8>, u32, u32, u64)> {
    for _ in 0..200 {
        if let Some(f) = tap.snapshot() {
            return Some(f);
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    tap.snapshot()
}

fn handle_client(mut stream: TcpStream, tap: FrameTap, input: Option<Arc<dyn InputSink>>) -> std::io::Result<()> {
    stream.set_nodelay(true).ok();

    stream.write_all(b"RFB 003.008\n")?;
    let mut ver = [0u8; 12];
    read_exact(&mut stream, &mut ver)?;

    let minor = ver.get(10).copied().unwrap_or(b'8');

    if minor >= b'7' {
        stream.write_all(&[1u8, 1u8])?;
        let mut chosen = [0u8; 1];
        read_exact(&mut stream, &mut chosen)?;

        stream.write_all(&[0u8, 0, 0, 0])?;
    } else {

        stream.write_all(&[0u8, 0, 0, 1])?;
    }

    let mut client_init = [0u8; 1];
    read_exact(&mut stream, &mut client_init)?;

    let first = wait_first_frame(&tap);
    let (mut fb_w, mut fb_h) = match &first {
        Some((_, w, h, _)) => (*w as u16, *h as u16),
        None => (1280, 800),
    };
    let mut client_fmt = PixelFormat::default_bgrx();
    {
        let name = b"phantom";
        let mut si = Vec::with_capacity(24 + name.len());
        si.extend_from_slice(&fb_w.to_be_bytes());
        si.extend_from_slice(&fb_h.to_be_bytes());
        client_fmt.write(&mut si);
        si.extend_from_slice(&(name.len() as u32).to_be_bytes());
        si.extend_from_slice(name);
        stream.write_all(&si)?;
    }

    stream.set_read_timeout(Some(Duration::from_millis(30)))?;
    let mut pending_update = false;
    let mut pending_incremental = true;
    let mut last_sent_gen: u64 = 0;

    let mut hdr = [0u8; 1];
    loop {

        match stream.read(&mut hdr) {
            Ok(0) => return Ok(()),
            Ok(_) => {
                match hdr[0] {
                    0 => {

                        let mut rest = [0u8; 19];
                        read_full(&mut stream, &mut rest)?;
                        if let Some(pf) = PixelFormat::parse(&rest[3..19]) {
                            client_fmt = pf;
                        }
                    }
                    2 => {

                        let mut h2 = [0u8; 3];
                        read_full(&mut stream, &mut h2)?;
                        let count = u16::from_be_bytes([h2[1], h2[2]]) as usize;
                        let mut enc = vec![0u8; count * 4];
                        read_full(&mut stream, &mut enc)?;
                    }
                    3 => {

                        let mut r = [0u8; 9];
                        read_full(&mut stream, &mut r)?;
                        pending_incremental = r[0] != 0;
                        pending_update = true;
                    }
                    4 => {

                        let mut r = [0u8; 7];
                        read_full(&mut stream, &mut r)?;
                        let down = r[0] != 0;
                        let keysym = u32::from_be_bytes([r[3], r[4], r[5], r[6]]);
                        if let Some(sink) = &input {
                            sink.key(keysym, down);
                        }
                    }
                    5 => {

                        let mut r = [0u8; 5];
                        read_full(&mut stream, &mut r)?;
                        let mask = r[0];
                        let x = u16::from_be_bytes([r[1], r[2]]);
                        let y = u16::from_be_bytes([r[3], r[4]]);
                        if let Some(sink) = &input {
                            sink.pointer(x, y, mask);
                        }
                    }
                    6 => {

                        let mut r = [0u8; 7];
                        read_full(&mut stream, &mut r)?;
                        let len = u32::from_be_bytes([r[3], r[4], r[5], r[6]]) as usize;
                        let mut text = vec![0u8; len.min(1 << 20)];
                        read_full(&mut stream, &mut text)?;
                    }
                    _ => {

                        return Ok(());
                    }
                }
            }
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => {

            }
            Err(e) => return Err(e),
        }

        if pending_update {
            let cur_gen = tap.generation();
            let want = !pending_incremental || cur_gen != last_sent_gen;
            if want {
                if let Some((rgba, w, h, gen)) = tap.snapshot() {

                    fb_w = w as u16;
                    fb_h = h as u16;
                    send_full_frame(&mut stream, &client_fmt, &rgba, w, h)?;
                    last_sent_gen = gen;
                    pending_update = false;
                }
            }
        }
    }
}

fn read_full(stream: &mut TcpStream, buf: &mut [u8]) -> std::io::Result<()> {
    let mut off = 0;
    while off < buf.len() {
        match stream.read(&mut buf[off..]) {
            Ok(0) => return Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "eof mid-message")),
            Ok(n) => off += n,
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(())
}

fn send_full_frame(stream: &mut TcpStream, fmt: &PixelFormat, rgba: &[u8], w: u32, h: u32) -> std::io::Result<()> {

    let mut msg = Vec::with_capacity(16 + (w * h * (fmt.bpp as u32 / 8)) as usize);
    msg.push(0u8);
    msg.push(0u8);
    msg.extend_from_slice(&1u16.to_be_bytes());

    msg.extend_from_slice(&0u16.to_be_bytes());
    msg.extend_from_slice(&0u16.to_be_bytes());
    msg.extend_from_slice(&(w as u16).to_be_bytes());
    msg.extend_from_slice(&(h as u16).to_be_bytes());
    msg.extend_from_slice(&0i32.to_be_bytes());

    let px = (w * h) as usize;
    for i in 0..px {
        let s = i * 4;
        if s + 3 <= rgba.len() {
            fmt.encode_pixel(rgba[s], rgba[s + 1], rgba[s + 2], &mut msg);
        } else {
            fmt.encode_pixel(0, 0, 0, &mut msg);
        }
    }
    stream.write_all(&msg)?;
    stream.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pixel_bgrx_default_matches_novnc() {

        let f = PixelFormat::default_bgrx();
        let mut out = Vec::new();
        f.encode_pixel(0x11, 0x22, 0x33, &mut out);
        assert_eq!(out, vec![0x33, 0x22, 0x11, 0x00]);
    }

    #[test]
    fn pixel_big_endian_rgbx() {
        let f = PixelFormat {
            bpp: 32, depth: 24, big_endian: true, true_color: true,
            red_max: 255, green_max: 255, blue_max: 255,
            red_shift: 24, green_shift: 16, blue_shift: 8,
        };
        let mut out = Vec::new();
        f.encode_pixel(0x11, 0x22, 0x33, &mut out);
        assert_eq!(out, vec![0x11, 0x22, 0x33, 0x00]);
    }

    #[test]
    fn pixelformat_roundtrips_through_wire() {
        let f = PixelFormat::default_bgrx();
        let mut w = Vec::new();
        f.write(&mut w);
        let p = PixelFormat::parse(&w).unwrap();
        assert_eq!(p.bpp, 32);
        assert_eq!(p.red_shift, 16);
        assert_eq!(p.blue_shift, 0);
        assert!(!p.big_endian && p.true_color);
    }
}
