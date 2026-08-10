fn main() {
    let out = std::env::args().nth(1).unwrap_or_else(|| "/tmp/phantom-jpeg-selftest.jpg".into());
    let (w, h) = (640u32, 360u32);
    let mut rgba = vec![0u8; (w * h * 4) as usize];
    for y in 0..h {
        for x in 0..w {
            let i = ((y * w + x) * 4) as usize;
            rgba[i] = (x * 255 / w) as u8;
            rgba[i + 1] = (y * 255 / h) as u8;
            rgba[i + 2] = 64;
            rgba[i + 3] = 255;
        }
    }

    for y in 80..160 {
        for x in 200..440 {
            let i = ((y * w + x) * 4) as usize;
            rgba[i] = 255;
            rgba[i + 1] = 0;
            rgba[i + 2] = 255;
        }
    }
    let jpg = phantom::jpeg::encode_rgba(w, h, &rgba, 70);
    std::fs::write(&out, &jpg).unwrap();
    println!("wrote {} bytes to {out} ({}x{})", jpg.len(), w, h);

    assert_eq!(&jpg[0..2], &[0xFF, 0xD8], "missing SOI");
    assert_eq!(&jpg[jpg.len() - 2..], &[0xFF, 0xD9], "missing EOI");
    println!("SOI/EOI ok");
}
