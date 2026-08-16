use std::collections::{HashMap, HashSet};
use std::os::raw::c_int;
use std::sync::atomic::AtomicU64;
use std::sync::{Arc, Mutex, OnceLock};

use phantom::sys;

pub(crate) static CLIENT_SEQ: AtomicU64 = AtomicU64::new(0);

pub(crate) static GATE: OnceLock<phantom::cap::Gate> = OnceLock::new();

#[derive(Clone, Copy)]
pub(crate) struct BufferInfo {
    pub(crate) pool: u32,
    pub(crate) offset: i32,
    pub(crate) width: i32,
    pub(crate) height: i32,
    pub(crate) stride: i32,
    pub(crate) format: u32,
}

pub(crate) struct ClientState {
    pub(crate) client_fd: c_int,

    pub(crate) writer: Arc<Mutex<()>>,
    pub(crate) objects: HashMap<u32, String>,

    pub(crate) pid: Option<i32>,
    pub(crate) seat: Option<u32>,
    pub(crate) keyboard: Option<u32>,

    pub(crate) pointer: Option<u32>,

    pub(crate) pointers: Vec<u32>,

    pub(crate) keyboards: Vec<u32>,
    pub(crate) surface: Option<u32>,
    pub(crate) title: Option<String>,
    pub(crate) app_id: Option<String>,
    pub(crate) serial: u32,
    pub(crate) time: u32,

    pub(crate) pools: HashMap<u32, Arc<sys::Mmap>>,
    pub(crate) buffers: HashMap<u32, BufferInfo>,
    pub(crate) pending_attach: HashMap<u32, u32>,
    pub(crate) surf_buffer: HashMap<u32, u32>,

    
    
    
    
    
    pub(crate) surf_damage: HashMap<u32, Vec<phantom::sehwerk::Schaden>>,
    pub(crate) pend_damage: HashMap<u32, Vec<(phantom::sehwerk::Raum, (i32, i32, i32, i32))>>,

    pub(crate) xdg_surf_wl: HashMap<u32, u32>,

    pub(crate) popup_surf: HashSet<u32>,

    
    
    
    pub(crate) popup_objs: HashMap<u32, u32>,

    pub(crate) subsurface_obj: HashMap<u32, u32>,

    pub(crate) subsurf_parent: HashMap<u32, u32>,

    pub(crate) subsurf_pos: HashMap<u32, (i32, i32)>,

    pub(crate) keylog: String,

    pub(crate) xparent: Option<u64>,

    
    
    
    
    
    
    pub(crate) commits: u64,
}

pub(crate) type Shared = Arc<Mutex<HashMap<u64, ClientState>>>;
