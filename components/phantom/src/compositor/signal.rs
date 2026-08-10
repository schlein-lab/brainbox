use phantom::sys;
use phantom::shell::Shell;
use std::sync::{Arc, Mutex};

pub type SharedShell = Arc<Mutex<Shell>>;

pub struct Damage {
    pub wake: sys::Wakeup,
    pending: std::sync::atomic::AtomicBool,
}

impl Damage {
    pub fn new() -> Damage {
        Damage { wake: sys::Wakeup::new(), pending: std::sync::atomic::AtomicBool::new(false) }
    }

    pub fn mark(&self) {
        self.pending.store(true, std::sync::atomic::Ordering::Release);
        self.wake.signal();
    }

    pub fn take(&self) -> bool {
        self.pending.swap(false, std::sync::atomic::Ordering::AcqRel)
    }
}

pub type SharedDamage = Arc<Damage>;
