#![allow(dead_code)]

use std::io::Read;
use std::os::unix::net::UnixStream;

pub struct Request {
    pub sender: u32,
    pub opcode: u16,
    pub args: Vec<u8>,
}

pub fn read_request(s: &mut UnixStream) -> std::io::Result<Request> {
    let mut hdr = [0u8; 8];
    s.read_exact(&mut hdr)?;
    let sender = u32le(&hdr, 0);
    let word2 = u32le(&hdr, 4);
    let size = (word2 >> 16) as usize;
    let opcode = (word2 & 0xffff) as u16;
    if size < 8 {
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "short message"));
    }
    let mut args = vec![0u8; size - 8];
    s.read_exact(&mut args)?;
    Ok(Request { sender, opcode, args })
}

pub fn event(object: u32, opcode: u16, payload: &[u8]) -> Vec<u8> {
    let size = (8 + payload.len()) as u32;
    let mut m = Vec::with_capacity(size as usize);
    m.extend_from_slice(&object.to_le_bytes());
    m.extend_from_slice(&((size << 16) | opcode as u32).to_le_bytes());
    m.extend_from_slice(payload);
    m
}

#[inline]
pub fn u32le(b: &[u8], off: usize) -> u32 {
    if off + 4 > b.len() {
        return 0;
    }
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

#[inline]
pub fn p_u32(v: &mut Vec<u8>, x: u32) {
    v.extend_from_slice(&x.to_le_bytes());
}
#[inline]
pub fn p_i32(v: &mut Vec<u8>, x: i32) {
    v.extend_from_slice(&x.to_le_bytes());
}

pub fn p_str(v: &mut Vec<u8>, s: &str) {
    let bytes = s.as_bytes();
    p_u32(v, (bytes.len() + 1) as u32);
    v.extend_from_slice(bytes);
    v.push(0);
    while v.len() % 4 != 0 {
        v.push(0);
    }
}

pub fn parse_bind(args: &[u8]) -> (String, u32, u32) {
    let slen = u32le(args, 4) as usize;
    let padded = (slen + 3) & !3;

    let s_start = 8.min(args.len());
    let s_end = (8 + slen.saturating_sub(1)).clamp(s_start, args.len());
    let iface = String::from_utf8_lossy(&args[s_start..s_end]).to_string();
    let next = 8 + padded;
    (iface, u32le(args, next), u32le(args, next + 4))
}

pub fn read_str(b: &[u8], off: usize) -> String {
    if off + 4 > b.len() {
        return String::new();
    }
    let slen = u32le(b, off) as usize;
    let start = off + 4;
    let end = (start + slen.saturating_sub(1)).min(b.len());
    String::from_utf8_lossy(&b[start..end]).to_string()
}

pub fn hexpeek(b: &[u8]) -> String {
    let n = b.len().min(16);
    let mut s = String::from("[");
    for (i, byte) in b[..n].iter().enumerate() {
        if i > 0 {
            s.push(' ');
        }
        s.push_str(&format!("{byte:02x}"));
    }
    if b.len() > n {
        s.push_str(" …");
    }
    s.push(']');
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_header_roundtrip() {
        let mut p = Vec::new();
        p_u32(&mut p, 0xAABB_CCDD);
        let m = event(7, 3, &p);
        assert_eq!(u32le(&m, 0), 7);
        assert_eq!(u32le(&m, 4) & 0xffff, 3);
        assert_eq!(u32le(&m, 4) >> 16, 12);
        assert_eq!(u32le(&m, 8), 0xAABB_CCDD);
    }

    #[test]
    fn string_is_nul_terminated_and_padded() {
        let mut v = Vec::new();
        p_str(&mut v, "ab");
        assert_eq!(v.len(), 8);
        assert_eq!(u32le(&v, 0), 3);
    }

    #[test]
    fn u32le_out_of_bounds_is_zero_not_panic() {

        let b = [0u8; 8];
        assert_eq!(u32le(&b, 8), 0);
        assert_eq!(u32le(&b, 6), 0);
        assert_eq!(u32le(&[], 0), 0);
    }

    #[test]
    fn parse_bind_truncated_does_not_panic() {

        let mut a = Vec::new();
        p_u32(&mut a, 1);
        p_u32(&mut a, 999);
        let _ = parse_bind(&a);
    }

    #[test]
    fn bind_parses() {

        let mut a = Vec::new();
        p_u32(&mut a, 1);
        p_str(&mut a, "wl_shm");
        p_u32(&mut a, 1);
        p_u32(&mut a, 6);
        let (iface, ver, id) = parse_bind(&a);
        assert_eq!((iface.as_str(), ver, id), ("wl_shm", 1, 6));
    }
}
