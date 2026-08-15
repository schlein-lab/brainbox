use phantom::kernel::{self, KernelEvent, Opts};

use std::io::{self, Write};

fn parse_args() -> Option<(Opts, bool)> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut o = Opts::default();
    let mut json = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--json" => json = true,
            "--files" => o.files = true,
            "--io" => o.io = true,
            "--pid" => {
                i += 1;
                o.only_pid = args.get(i).and_then(|s| s.parse().ok());
            }
            "--syscall" => {
                i += 1;
                if let Some(s) = args.get(i) {
                    o.syscalls.push(s.clone());
                }
            }
            "-h" | "--help" => return None,
            other => eprintln!("phantom-kernel: ignoring unknown argument {other}"),
        }
        i += 1;
    }
    Some((o, json))
}

fn help() {
    eprintln!(
        "phantom-kernel — system-wide syscall/kernel-activity tap (needs root)\n\
         \n\
         phantom-kernel                 exec / fork / exit of every process\n\
         phantom-kernel --files         + every file open, with the real path\n\
         phantom-kernel --io            + raw read/write/connect (noisy)\n\
         phantom-kernel --syscall NAME  + syscalls/sys_enter_NAME (repeatable)\n\
         phantom-kernel --pid N         only the task with pid N\n\
         phantom-kernel --json          JSON lines for machine parsing"
    );
}

fn main() {
    let Some((opts, json)) = parse_args() else {
        help();
        return;
    };
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let res = kernel::run(
        &opts,
        |line| eprintln!("{line}"),
        |ev| emit(ev, json, &mut out).is_ok(),
    );
    if let Err(e) = res {
        eprintln!("phantom-kernel: {e}");
        std::process::exit(1);
    }
}

fn emit(ev: &KernelEvent, json: bool, out: &mut impl Write) -> io::Result<()> {
    let KernelEvent { pid, comm, verb, event, info } = *ev;
    if json {
        writeln!(
            out,
            "{{\"pid\":{pid},\"comm\":{comm:?},\"verb\":{verb:?},\"event\":{event:?},\"info\":{info:?}}}"
        )?;
    } else {
        writeln!(out, "{verb:<5} {pid:>7} {comm:<16} {info}")?;
    }
    out.flush()
}
