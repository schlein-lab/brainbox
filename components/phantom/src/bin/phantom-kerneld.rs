use phantom::kernel::{self, KernelEvent, Opts};
use phantom::kernelbus::{Bus, DEFAULT_RING_BYTES};
use phantom::sys;

use std::thread;
use std::time::Duration;

const DEFAULT_SOCK: &str = "/run/phantom/events.sock";

const DEFAULT_RING: &str = "/run/phantom/kerneld.ring";

const DEFAULT_GROUP: &str = "phantom";

struct Args {
    opts: Opts,
    socket: String,

    ring: Option<String>,

    ring_bytes: usize,

    group: String,
}

fn parse_args() -> Option<Args> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut opts = Opts::default();
    let mut socket = DEFAULT_SOCK.to_string();
    let mut ring: Option<String> = None;
    let mut ring_bytes = DEFAULT_RING_BYTES;
    let mut group = DEFAULT_GROUP.to_string();
    let mut i = 0;
    while i < argv.len() {
        match argv[i].as_str() {
            "--files" => opts.files = true,
            "--io" => opts.io = true,

            "--connect" => opts.syscalls.push("connect".into()),
            "--syscall" => {
                i += 1;
                if let Some(s) = argv.get(i) {
                    opts.syscalls.push(s.clone());
                }
            }
            "--pid" => {
                i += 1;
                opts.only_pid = argv.get(i).and_then(|s| s.parse().ok());
            }
            "--socket" => {
                i += 1;
                if let Some(s) = argv.get(i) {
                    socket = s.clone();
                }
            }

            "--ring" => {
                let path = match argv.get(i + 1) {
                    Some(s) if !s.starts_with("--") => {
                        i += 1;
                        s.clone()
                    }
                    _ => DEFAULT_RING.to_string(),
                };
                ring = Some(path);
            }
            "--ring-bytes" => {
                i += 1;
                if let Some(n) = argv.get(i).and_then(|s| s.parse::<usize>().ok()) {
                    ring_bytes = n;
                }
            }

            "--group" => {
                i += 1;
                if let Some(s) = argv.get(i) {
                    group = s.clone();
                }
            }
            "-h" | "--help" => return None,
            other => eprintln!("phantom-kerneld: ignoring unknown argument {other}"),
        }
        i += 1;
    }
    Some(Args { opts, socket, ring, ring_bytes, group })
}

fn help() {
    eprintln!(
        "phantom-kerneld — persistent system-wide syscall tap → a pub/sub socket (needs root)\n\
         \n\
         phantom-kerneld                       lifecycle (exec/fork/exit) → {DEFAULT_SOCK}\n\
         phantom-kerneld --files               + every file open, with the real path\n\
         phantom-kerneld --connect             + sys_enter_connect (who the machine talks to)\n\
         phantom-kerneld --io                  + raw read/write (firehose; off by default)\n\
         phantom-kerneld --syscall NAME        + syscalls/sys_enter_NAME (repeatable)\n\
         phantom-kerneld --pid N               only the task with pid N\n\
         phantom-kerneld --socket PATH         publish here (default {DEFAULT_SOCK})\n\
         phantom-kerneld --ring [PATH]         on-disk replay ring (default {DEFAULT_RING})\n\
         phantom-kerneld --ring-bytes N        replay-ring byte budget (default {DEFAULT_RING_BYTES})\n\
         phantom-kerneld --group NAME          socket group gate (default {DEFAULT_GROUP})\n\
         \n\
         Subscribe with:  socat - UNIX-CONNECT:{DEFAULT_SOCK}\n\
         One JSON object per line (== phantom-kernel --json), plus seq + ts.\n\
         \n\
         Optional first-line handshake (JSON), else you get the live stream of everything:\n\
           {{\"hello\":1,\"filter\":[\"execve\",\"open*\",\"connect\"],\"replay\":200}}\n\
           {{\"hello\":1,\"mode\":\"human\"}}           KRN-style lines for cat/socat\n\
           {{\"hello\":1,\"pid\":4711}}                only that pid\n\
         (A \"token\":\"...\" field is reserved for a future capability check; accepted, not\n\
          yet validated — the bus is gated by the {DEFAULT_GROUP}-group socket, see the unit.)"
    );
}

fn main() {
    let Some(args) = parse_args() else {
        help();
        return;
    };

    let bus = Bus::new(args.ring.clone(), args.ring_bytes);

    {
        let bus = bus.clone();
        let path = args.socket.clone();
        let group = args.group.clone();
        thread::spawn(move || bus.serve(&path, &group));
    }

    if let Some(half) = sys::watchdog_interval() {
        thread::spawn(move || loop {
            thread::sleep(half.max(Duration::from_millis(500)));

            sys::sd_notify("WATCHDOG=1");
        });
    }

    let ready = std::sync::Once::new();
    let banner_log = |line: &str| {
        eprintln!("{line}");
        ready.call_once(|| sys::sd_notify("READY=1"));
    };

    eprintln!("phantom-kerneld: publishing to {}", args.socket);
    if let Some(ring) = &args.ring {
        eprintln!("phantom-kerneld: replay ring at {ring} ({} bytes)", args.ring_bytes);
    }
    eprintln!("phantom-kerneld: socket group gate = {:?}", args.group);

    let bus_for_sink = bus.clone();
    let res = kernel::run(
        &args.opts,
        banner_log,
        |ev: &KernelEvent| {

            bus_for_sink.publish(ev);
            true
        },
    );

    sys::sd_notify("STOPPING=1");
    if let Err(e) = res {
        eprintln!("phantom-kerneld: kernel tap: {e}");
        std::process::exit(1);
    }

}
