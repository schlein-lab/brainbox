use vm_memory::{Bytes, GuestAddress, GuestMemoryMmap};

const MP_FP_BASE: u64 = 0x000f_0000;
const MP_CONFIG_BASE: u64 = 0x000f_0010;
pub const LAPIC_ADDR: u32 = 0xfee0_0000;
pub const IOAPIC_ADDR: u32 = 0xfec0_0000;

fn checksum(bytes: &[u8]) -> u8 {
    (0u8).wrapping_sub(bytes.iter().fold(0u8, |a, &b| a.wrapping_add(b)))
}

pub fn setup(gm: &GuestMemoryMmap, ncpus: u8) -> Result<(), String> {
    let ncpus = ncpus.max(1);

    let ioapic_id: u8 = ncpus;
    let mut entries: Vec<u8> = Vec::new();

    for id in 0..ncpus {
        let mut p = [0u8; 20];
        p[0] = 0;
        p[1] = id;
        p[2] = 0x14;
        p[3] = if id == 0 { 0x03 } else { 0x01 };
        p[4..8].copy_from_slice(&0x0000_0600u32.to_le_bytes());
        p[8..12].copy_from_slice(&0x0000_0201u32.to_le_bytes());
        entries.extend_from_slice(&p);
    }

    let mut b = [0u8; 8];
    b[0] = 1;
    b[1] = 0;
    b[2..8].copy_from_slice(b"ISA   ");
    entries.extend_from_slice(&b);

    let mut io = [0u8; 8];
    io[0] = 2;
    io[1] = ioapic_id;
    io[2] = 0x11;
    io[3] = 0x01;
    io[4..8].copy_from_slice(&IOAPIC_ADDR.to_le_bytes());
    entries.extend_from_slice(&io);

    let mut n_entries: u16 = ncpus as u16 + 2;

    for irq in 0u8..16 {
        let mut e = [0u8; 8];
        e[0] = 3;
        e[1] = 0;
        e[2] = 0;
        e[3] = 0;
        e[4] = 0;
        e[5] = irq;
        e[6] = ioapic_id;
        e[7] = irq;
        entries.extend_from_slice(&e);
        n_entries += 1;
    }

    for &(itype, lint) in &[(3u8, 0u8), (1u8, 1u8)] {
        let mut e = [0u8; 8];
        e[0] = 4;
        e[1] = itype;
        e[2] = 0;
        e[3] = 0;
        e[4] = 0;
        e[5] = 0;
        e[6] = 0;
        e[7] = lint;
        entries.extend_from_slice(&e);
        n_entries += 1;
    }

    let base_len = 44 + entries.len();
    let mut cfg = [0u8; 44];
    cfg[0..4].copy_from_slice(b"PCMP");
    cfg[4..6].copy_from_slice(&(base_len as u16).to_le_bytes());
    cfg[6] = 4;
    cfg[8..16].copy_from_slice(b"PN-VMM  ");
    cfg[16..28].copy_from_slice(b"cell        ");
    cfg[34..36].copy_from_slice(&n_entries.to_le_bytes());
    cfg[36..40].copy_from_slice(&LAPIC_ADDR.to_le_bytes());

    let mut table = Vec::with_capacity(base_len);
    table.extend_from_slice(&cfg);
    table.extend_from_slice(&entries);
    let ck = checksum(&table);
    table[7] = ck;

    let mut fp = [0u8; 16];
    fp[0..4].copy_from_slice(b"_MP_");
    fp[4..8].copy_from_slice(&(MP_CONFIG_BASE as u32).to_le_bytes());
    fp[8] = 1;
    fp[9] = 4;

    let fck = checksum(&fp);
    fp[10] = fck;

    gm.write_slice(&table, GuestAddress(MP_CONFIG_BASE))
        .map_err(|e| format!("write mptable cfg: {e:?}"))?;
    gm.write_slice(&fp, GuestAddress(MP_FP_BASE))
        .map_err(|e| format!("write mptable fp: {e:?}"))?;
    Ok(())
}
