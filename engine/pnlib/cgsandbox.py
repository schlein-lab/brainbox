
from __future__ import annotations
import ctypes
import errno as _errno
import os
import platform

class SandboxError(Exception):
    pass

_libc = ctypes.CDLL(None, use_errno=True)

def _e() -> int:
    return ctypes.get_errno()

_MACH = platform.machine()

if _MACH in ("x86_64", "amd64"):
    NR_seccomp = 317
    NR_landlock_create_ruleset = 444
    NR_landlock_add_rule = 445
    NR_landlock_restrict_self = 446
    AUDIT_ARCH = 0xC000003E
    _X32_BIT = 0x40000000
    _NR = None
    _ARCH_KEY = "x86_64"
elif _MACH in ("aarch64", "arm64"):
    NR_seccomp = 277
    NR_landlock_create_ruleset = 444
    NR_landlock_add_rule = 445
    NR_landlock_restrict_self = 446
    AUDIT_ARCH = 0xC00000B7
    _X32_BIT = 0
    _NR = None
    _ARCH_KEY = "aarch64"
else:
    NR_seccomp = NR_landlock_create_ruleset = NR_landlock_add_rule = NR_landlock_restrict_self = -1
    AUDIT_ARCH = 0
    _X32_BIT = 0
    _NR = {}
    _ARCH_KEY = None

_NR_X86_64 = {
    "read": 0, "write": 1, "open": 2, "close": 3, "stat": 4, "fstat": 5, "lstat": 6, "poll": 7,
    "lseek": 8, "mmap": 9, "mprotect": 10, "munmap": 11, "brk": 12, "rt_sigaction": 13,
    "rt_sigprocmask": 14, "rt_sigreturn": 15, "ioctl": 16, "pread64": 17, "pwrite64": 18,
    "readv": 19, "writev": 20, "access": 21, "pipe": 22, "select": 23, "sched_yield": 24,
    "mremap": 25, "msync": 26, "mincore": 27, "madvise": 28, "shmget": 29, "shmat": 30,
    "shmctl": 31, "dup": 32, "dup2": 33, "pause": 34, "nanosleep": 35, "getitimer": 36,
    "alarm": 37, "setitimer": 38, "getpid": 39, "sendfile": 40, "socket": 41, "connect": 42,
    "accept": 43, "sendto": 44, "recvfrom": 45, "sendmsg": 46, "recvmsg": 47, "shutdown": 48,
    "bind": 49, "listen": 50, "getsockname": 51, "getpeername": 52, "socketpair": 53,
    "setsockopt": 54, "getsockopt": 55, "clone": 56, "fork": 57, "vfork": 58, "execve": 59,
    "exit": 60, "wait4": 61, "kill": 62, "uname": 63, "semget": 64, "semop": 65, "semctl": 66,
    "shmdt": 67, "msgget": 68, "msgsnd": 69, "msgrcv": 70, "msgctl": 71, "fcntl": 72,
    "flock": 73, "fsync": 74, "fdatasync": 75, "truncate": 76, "ftruncate": 77, "getdents": 78,
    "getcwd": 79, "chdir": 80, "fchdir": 81, "rename": 82, "mkdir": 83, "rmdir": 84,
    "creat": 85, "link": 86, "unlink": 87, "symlink": 88, "readlink": 89, "chmod": 90,
    "fchmod": 91, "chown": 92, "fchown": 93, "lchown": 94, "umask": 95, "gettimeofday": 96,
    "getrlimit": 97, "getrusage": 98, "sysinfo": 99, "times": 100, "ptrace": 101,
    "getuid": 102, "syslog": 103, "getgid": 104, "setuid": 105, "setgid": 106, "geteuid": 107,
    "getegid": 108, "setpgid": 109, "getppid": 110, "getpgrp": 111, "setsid": 112,
    "setreuid": 113, "setregid": 114, "getgroups": 115, "setgroups": 116, "setresuid": 117,
    "getresuid": 118, "setresgid": 119, "getresgid": 120, "getpgid": 121, "setfsuid": 122,
    "setfsgid": 123, "getsid": 124, "capget": 125, "capset": 126, "rt_sigpending": 127,
    "rt_sigtimedwait": 128, "rt_sigqueueinfo": 129, "rt_sigsuspend": 130, "sigaltstack": 131,
    "utime": 132, "mknod": 133, "uselib": 134, "personality": 135, "ustat": 136, "statfs": 137,
    "fstatfs": 138, "sysfs": 139, "getpriority": 140, "setpriority": 141,
    "sched_setparam": 142, "sched_getparam": 143, "sched_setscheduler": 144,
    "sched_getscheduler": 145, "sched_get_priority_max": 146, "sched_get_priority_min": 147,
    "sched_rr_get_interval": 148, "mlock": 149, "munlock": 150, "mlockall": 151,
    "munlockall": 152, "vhangup": 153, "modify_ldt": 154, "pivot_root": 155, "_sysctl": 156,
    "prctl": 157, "arch_prctl": 158, "adjtimex": 159, "setrlimit": 160, "chroot": 161,
    "sync": 162, "acct": 163, "settimeofday": 164, "mount": 165, "umount2": 166, "swapon": 167,
    "swapoff": 168, "reboot": 169, "sethostname": 170, "setdomainname": 171, "iopl": 172,
    "ioperm": 173, "create_module": 174, "init_module": 175, "delete_module": 176,
    "get_kernel_syms": 177, "query_module": 178, "quotactl": 179, "nfsservctl": 180,
    "getpmsg": 181, "putpmsg": 182, "afs_syscall": 183, "tuxcall": 184, "security": 185,
    "gettid": 186, "readahead": 187, "setxattr": 188, "lsetxattr": 189, "fsetxattr": 190,
    "getxattr": 191, "lgetxattr": 192, "fgetxattr": 193, "listxattr": 194, "llistxattr": 195,
    "flistxattr": 196, "removexattr": 197, "lremovexattr": 198, "fremovexattr": 199,
    "tkill": 200, "time": 201, "futex": 202, "sched_setaffinity": 203,
    "sched_getaffinity": 204, "set_thread_area": 205, "io_setup": 206, "io_destroy": 207,
    "io_getevents": 208, "io_submit": 209, "io_cancel": 210, "get_thread_area": 211,
    "lookup_dcookie": 212, "epoll_create": 213, "epoll_ctl_old": 214, "epoll_wait_old": 215,
    "remap_file_pages": 216, "getdents64": 217, "set_tid_address": 218, "restart_syscall": 219,
    "semtimedop": 220, "fadvise64": 221, "timer_create": 222, "timer_settime": 223,
    "timer_gettime": 224, "timer_getoverrun": 225, "timer_delete": 226, "clock_settime": 227,
    "clock_gettime": 228, "clock_getres": 229, "clock_nanosleep": 230, "exit_group": 231,
    "epoll_wait": 232, "epoll_ctl": 233, "tgkill": 234, "utimes": 235, "vserver": 236,
    "mbind": 237, "set_mempolicy": 238, "get_mempolicy": 239, "mq_open": 240, "mq_unlink": 241,
    "mq_timedsend": 242, "mq_timedreceive": 243, "mq_notify": 244, "mq_getsetattr": 245,
    "kexec_load": 246, "waitid": 247, "add_key": 248, "request_key": 249, "keyctl": 250,
    "ioprio_set": 251, "ioprio_get": 252, "inotify_init": 253, "inotify_add_watch": 254,
    "inotify_rm_watch": 255, "migrate_pages": 256, "openat": 257, "mkdirat": 258,
    "mknodat": 259, "fchownat": 260, "futimesat": 261, "newfstatat": 262, "unlinkat": 263,
    "renameat": 264, "linkat": 265, "symlinkat": 266, "readlinkat": 267, "fchmodat": 268,
    "faccessat": 269, "pselect6": 270, "ppoll": 271, "unshare": 272, "set_robust_list": 273,
    "get_robust_list": 274, "splice": 275, "tee": 276, "sync_file_range": 277, "vmsplice": 278,
    "move_pages": 279, "utimensat": 280, "epoll_pwait": 281, "signalfd": 282,
    "timerfd_create": 283, "eventfd": 284, "fallocate": 285, "timerfd_settime": 286,
    "timerfd_gettime": 287, "accept4": 288, "signalfd4": 289, "eventfd2": 290,
    "epoll_create1": 291, "dup3": 292, "pipe2": 293, "inotify_init1": 294, "preadv": 295,
    "pwritev": 296, "rt_tgsigqueueinfo": 297, "perf_event_open": 298, "recvmmsg": 299,
    "fanotify_init": 300, "fanotify_mark": 301, "prlimit64": 302, "name_to_handle_at": 303,
    "open_by_handle_at": 304, "clock_adjtime": 305, "syncfs": 306, "sendmmsg": 307,
    "setns": 308, "getcpu": 309, "process_vm_readv": 310, "process_vm_writev": 311,
    "kcmp": 312, "finit_module": 313, "sched_setattr": 314, "sched_getattr": 315,
    "renameat2": 316, "seccomp": 317, "getrandom": 318, "memfd_create": 319,
    "kexec_file_load": 320, "bpf": 321, "execveat": 322, "userfaultfd": 323, "membarrier": 324,
    "mlock2": 325, "copy_file_range": 326, "preadv2": 327, "pwritev2": 328,
    "pkey_mprotect": 329, "pkey_alloc": 330, "pkey_free": 331, "statx": 332,
    "io_pgetevents": 333, "rseq": 334, "pidfd_send_signal": 424, "io_uring_setup": 425,
    "io_uring_enter": 426, "io_uring_register": 427, "open_tree": 428, "move_mount": 429,
    "fsopen": 430, "fsconfig": 431, "fsmount": 432, "fspick": 433, "pidfd_open": 434,
    "clone3": 435, "close_range": 436, "openat2": 437, "pidfd_getfd": 438, "faccessat2": 439,
    "process_madvise": 440, "epoll_pwait2": 441, "mount_setattr": 442, "quotactl_fd": 443,
    "landlock_create_ruleset": 444, "landlock_add_rule": 445, "landlock_restrict_self": 446,
    "memfd_secret": 447, "process_mrelease": 448, "futex_waitv": 449,
    "set_mempolicy_home_node": 450, "cachestat": 451, "fchmodat2": 452,
    "map_shadow_stack": 453, "futex_wake": 454, "futex_wait": 455, "futex_requeue": 456,
    "statmount": 457, "listmount": 458, "lsm_get_self_attr": 459, "lsm_set_self_attr": 460,
    "lsm_list_modules": 461,
}

_NR_AARCH64 = {
    "io_setup": 0, "io_destroy": 1, "io_submit": 2, "io_cancel": 3, "io_getevents": 4,
    "setxattr": 5, "lsetxattr": 6, "fsetxattr": 7, "getxattr": 8, "lgetxattr": 9,
    "fgetxattr": 10, "listxattr": 11, "llistxattr": 12, "flistxattr": 13, "removexattr": 14,
    "lremovexattr": 15, "fremovexattr": 16, "getcwd": 17, "lookup_dcookie": 18, "eventfd2": 19,
    "epoll_create1": 20, "epoll_ctl": 21, "epoll_pwait": 22, "dup": 23, "dup3": 24,
    "inotify_init1": 26, "inotify_add_watch": 27, "inotify_rm_watch": 28, "ioctl": 29,
    "ioprio_set": 30, "ioprio_get": 31, "flock": 32, "mknodat": 33, "mkdirat": 34,
    "unlinkat": 35, "symlinkat": 36, "linkat": 37, "renameat": 38, "umount2": 39, "mount": 40,
    "pivot_root": 41, "nfsservctl": 42, "fallocate": 47, "faccessat": 48, "chdir": 49,
    "fchdir": 50, "chroot": 51, "fchmod": 52, "fchmodat": 53, "fchownat": 54, "fchown": 55,
    "openat": 56, "close": 57, "vhangup": 58, "pipe2": 59, "quotactl": 60, "getdents64": 61,
    "read": 63, "write": 64, "readv": 65, "writev": 66, "pread64": 67, "pwrite64": 68,
    "preadv": 69, "pwritev": 70, "pselect6": 72, "ppoll": 73, "signalfd4": 74, "vmsplice": 75,
    "splice": 76, "tee": 77, "readlinkat": 78, "sync": 81, "fsync": 82, "fdatasync": 83,
    "sync_file_range": 84, "timerfd_create": 85, "timerfd_settime": 86, "timerfd_gettime": 87,
    "utimensat": 88, "acct": 89, "capget": 90, "capset": 91, "personality": 92, "exit": 93,
    "exit_group": 94, "waitid": 95, "set_tid_address": 96, "unshare": 97, "futex": 98,
    "set_robust_list": 99, "get_robust_list": 100, "nanosleep": 101, "getitimer": 102,
    "setitimer": 103, "kexec_load": 104, "init_module": 105, "delete_module": 106,
    "timer_create": 107, "timer_gettime": 108, "timer_getoverrun": 109, "timer_settime": 110,
    "timer_delete": 111, "clock_settime": 112, "clock_gettime": 113, "clock_getres": 114,
    "clock_nanosleep": 115, "syslog": 116, "ptrace": 117, "sched_setparam": 118,
    "sched_setscheduler": 119, "sched_getscheduler": 120, "sched_getparam": 121,
    "sched_setaffinity": 122, "sched_getaffinity": 123, "sched_yield": 124,
    "sched_get_priority_max": 125, "sched_get_priority_min": 126, "sched_rr_get_interval": 127,
    "restart_syscall": 128, "kill": 129, "tkill": 130, "tgkill": 131, "sigaltstack": 132,
    "rt_sigsuspend": 133, "rt_sigaction": 134, "rt_sigprocmask": 135, "rt_sigpending": 136,
    "rt_sigtimedwait": 137, "rt_sigqueueinfo": 138, "rt_sigreturn": 139, "setpriority": 140,
    "getpriority": 141, "reboot": 142, "setregid": 143, "setgid": 144, "setreuid": 145,
    "setuid": 146, "setresuid": 147, "getresuid": 148, "setresgid": 149, "getresgid": 150,
    "setfsuid": 151, "setfsgid": 152, "times": 153, "setpgid": 154, "getpgid": 155,
    "getsid": 156, "setsid": 157, "getgroups": 158, "setgroups": 159, "uname": 160,
    "sethostname": 161, "setdomainname": 162, "getrusage": 165, "umask": 166, "prctl": 167,
    "getcpu": 168, "gettimeofday": 169, "settimeofday": 170, "adjtimex": 171, "getpid": 172,
    "getppid": 173, "getuid": 174, "geteuid": 175, "getgid": 176, "getegid": 177,
    "gettid": 178, "sysinfo": 179, "mq_open": 180, "mq_unlink": 181, "mq_timedsend": 182,
    "mq_timedreceive": 183, "mq_notify": 184, "mq_getsetattr": 185, "msgget": 186,
    "msgctl": 187, "msgrcv": 188, "msgsnd": 189, "semget": 190, "semctl": 191,
    "semtimedop": 192, "semop": 193, "shmget": 194, "shmctl": 195, "shmat": 196, "shmdt": 197,
    "socket": 198, "socketpair": 199, "bind": 200, "listen": 201, "accept": 202,
    "connect": 203, "getsockname": 204, "getpeername": 205, "sendto": 206, "recvfrom": 207,
    "setsockopt": 208, "getsockopt": 209, "shutdown": 210, "sendmsg": 211, "recvmsg": 212,
    "readahead": 213, "brk": 214, "munmap": 215, "mremap": 216, "add_key": 217,
    "request_key": 218, "keyctl": 219, "clone": 220, "execve": 221, "swapon": 224,
    "swapoff": 225, "mprotect": 226, "msync": 227, "mlock": 228, "munlock": 229,
    "mlockall": 230, "munlockall": 231, "mincore": 232, "madvise": 233,
    "remap_file_pages": 234, "mbind": 235, "get_mempolicy": 236, "set_mempolicy": 237,
    "migrate_pages": 238, "move_pages": 239, "rt_tgsigqueueinfo": 240, "perf_event_open": 241,
    "accept4": 242, "recvmmsg": 243, "wait4": 260, "prlimit64": 261, "fanotify_init": 262,
    "fanotify_mark": 263, "name_to_handle_at": 264, "open_by_handle_at": 265,
    "clock_adjtime": 266, "syncfs": 267, "setns": 268, "sendmmsg": 269,
    "process_vm_readv": 270, "process_vm_writev": 271, "kcmp": 272, "finit_module": 273,
    "sched_setattr": 274, "sched_getattr": 275, "renameat2": 276, "seccomp": 277,
    "getrandom": 278, "memfd_create": 279, "bpf": 280, "execveat": 281, "userfaultfd": 282,
    "membarrier": 283, "mlock2": 284, "copy_file_range": 285, "preadv2": 286, "pwritev2": 287,
    "pkey_mprotect": 288, "pkey_alloc": 289, "pkey_free": 290, "statx": 291,
    "io_pgetevents": 292, "rseq": 293, "kexec_file_load": 294, "pidfd_send_signal": 424,
    "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427, "open_tree": 428,
    "move_mount": 429, "fsopen": 430, "fsconfig": 431, "fsmount": 432, "fspick": 433,
    "pidfd_open": 434, "clone3": 435, "close_range": 436, "openat2": 437, "pidfd_getfd": 438,
    "faccessat2": 439, "process_madvise": 440, "epoll_pwait2": 441, "mount_setattr": 442,
    "quotactl_fd": 443, "landlock_create_ruleset": 444, "landlock_add_rule": 445,
    "landlock_restrict_self": 446, "process_mrelease": 448, "futex_waitv": 449,
    "set_mempolicy_home_node": 450, "cachestat": 451, "fchmodat2": 452,
    "map_shadow_stack": 453, "futex_wake": 454, "futex_wait": 455, "futex_requeue": 456,
    "statmount": 457, "listmount": 458, "lsm_get_self_attr": 459, "lsm_set_self_attr": 460,
    "lsm_list_modules": 461, "mseal": 462, "setxattrat": 463, "getxattrat": 464,
    "listxattrat": 465, "removexattrat": 466, "open_tree_attr": 467, "file_getattr": 468,
    "file_setattr": 469,
}

_SYSCALL_ALLOWLIST = (
    "_llseek", "_newselect", "accept", "accept4", "access", "alarm", "arch_prctl",
    "arm_fadvise64_64", "bind", "brk", "cacheflush", "capget", "capset", "chdir", "chmod",
    "chown", "chown32", "clock_getres", "clock_getres_time64", "clock_gettime",
    "clock_gettime64", "clock_nanosleep", "clock_nanosleep_time64", "clone", "clone3", "close",
    "close_range", "connect", "copy_file_range", "creat", "dup", "dup2", "dup3",
    "epoll_create", "epoll_create1", "epoll_ctl", "epoll_ctl_old", "epoll_pwait",
    "epoll_pwait2", "epoll_wait", "epoll_wait_old", "eventfd", "eventfd2", "execve",
    "execveat", "exit", "exit_group", "faccessat", "faccessat2", "fadvise64", "fadvise64_64",
    "fallocate", "fchdir", "fchmod", "fchmodat", "fchmodat2", "fchown", "fchown32", "fchownat",
    "fcntl", "fcntl64", "fdatasync", "fgetxattr", "flistxattr", "flock", "fork",
    "fremovexattr", "fsetxattr", "fstat", "fstat64", "fstatat64", "fstatfs", "fstatfs64",
    "fsync", "ftruncate", "ftruncate64", "futex", "futex_time64", "futex_waitv", "futimesat",
    "get_robust_list", "get_thread_area", "getcpu", "getcwd", "getdents", "getdents64",
    "getegid", "getegid32", "geteuid", "geteuid32", "getgid", "getgid32", "getgroups",
    "getgroups32", "getitimer", "getpeername", "getpgid", "getpgrp", "getpid", "getppid",
    "getpriority", "getrandom", "getresgid", "getresgid32", "getresuid", "getresuid32",
    "getrlimit", "getrusage", "getsid", "getsockname", "getsockopt", "gettid", "gettimeofday",
    "getuid", "getuid32", "getxattr", "inotify_add_watch", "inotify_init", "inotify_init1",
    "inotify_rm_watch", "io_cancel", "io_destroy", "io_getevents", "io_pgetevents",
    "io_pgetevents_time64", "io_setup", "io_submit", "ioctl", "ioprio_get", "kill", "lchown",
    "lchown32", "lgetxattr", "link", "linkat", "listen", "listxattr", "llistxattr",
    "lremovexattr", "lseek", "lsetxattr", "lstat", "lstat64", "madvise", "membarrier", "mkdir",
    "mkdirat", "mlock", "mlock2", "mlockall", "mmap", "mmap2", "mprotect", "mq_getsetattr",
    "mq_notify", "mq_open", "mq_timedreceive", "mq_timedreceive_time64", "mq_timedsend",
    "mq_timedsend_time64", "mq_unlink", "mremap", "msgctl", "msgget", "msgrcv", "msgsnd",
    "msync", "munlock", "munlockall", "munmap", "nanosleep", "newfstatat", "nice", "oldfstat",
    "oldlstat", "oldolduname", "oldstat", "olduname", "open", "openat", "openat2", "pause",
    "pidfd_open", "pidfd_send_signal", "pipe", "pipe2", "poll", "ppoll", "ppoll_time64",
    "prctl", "pread64", "preadv", "preadv2", "prlimit64", "process_madvise", "pselect6",
    "pselect6_time64", "pwrite64", "pwritev", "pwritev2", "read", "readahead", "readdir",
    "readlink", "readlinkat", "readv", "recv", "recvfrom", "recvmmsg", "recvmmsg_time64",
    "recvmsg", "remap_file_pages", "removexattr", "rename", "renameat", "renameat2",
    "restart_syscall", "riscv_flush_icache", "riscv_hwprobe", "rmdir", "rseq", "rt_sigaction",
    "rt_sigpending", "rt_sigprocmask", "rt_sigqueueinfo", "rt_sigreturn", "rt_sigsuspend",
    "rt_sigtimedwait", "rt_sigtimedwait_time64", "rt_tgsigqueueinfo", "sched_get_priority_max",
    "sched_get_priority_min", "sched_getaffinity", "sched_getattr", "sched_getparam",
    "sched_getscheduler", "sched_rr_get_interval", "sched_rr_get_interval_time64",
    "sched_setaffinity", "sched_yield", "select", "semctl", "semget", "semop", "semtimedop",
    "semtimedop_time64", "send", "sendfile", "sendfile64", "sendmmsg", "sendmsg", "sendto",
    "set_robust_list", "set_thread_area", "set_tid_address", "set_tls", "setfsgid",
    "setfsgid32", "setfsuid", "setfsuid32", "setgid", "setgid32", "setgroups", "setgroups32",
    "setitimer", "setpgid", "setpriority", "setregid", "setregid32", "setresgid",
    "setresgid32", "setresuid", "setresuid32", "setreuid", "setreuid32", "setrlimit", "setsid",
    "setsockopt", "setuid", "setuid32", "setxattr", "shmat", "shmctl", "shmdt", "shmget",
    "shutdown", "sigaction", "sigaltstack", "signal", "signalfd", "signalfd4", "sigpending",
    "sigprocmask", "sigreturn", "sigsuspend", "socket", "socketpair", "splice", "stat",
    "stat64", "statfs", "statfs64", "statx", "swapcontext", "symlink", "symlinkat", "sync",
    "sync_file_range", "sync_file_range2", "syncfs", "sysinfo", "tee", "tgkill", "time",
    "timer_create", "timer_delete", "timer_getoverrun", "timer_gettime", "timer_gettime64",
    "timer_settime", "timer_settime64", "timerfd_create", "timerfd_gettime",
    "timerfd_gettime64", "timerfd_settime", "timerfd_settime64", "times", "tkill", "truncate",
    "truncate64", "ugetrlimit", "umask", "uname", "unlink", "unlinkat", "utime", "utimensat",
    "utimensat_time64", "utimes", "vfork", "vmsplice", "wait4", "waitid", "waitpid", "write",
    "writev",
)

if _ARCH_KEY == "x86_64":
    _NR = _NR_X86_64
elif _ARCH_KEY == "aarch64":
    _NR = _NR_AARCH64

PR_SET_NO_NEW_PRIVS = 38
PR_CAPBSET_DROP = 24
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4

def set_no_new_privs() -> None:
    if _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxError(f"prctl(PR_SET_NO_NEW_PRIVS) failed: errno={_e()}")

_LINUX_CAPABILITY_VERSION_3 = 0x20080522
_CAP_LAST_CAP = 40

class _cap_hdr(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

class _cap_data(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                ("inheritable", ctypes.c_uint32)]

def drop_capabilities() -> None:

    hdr = _cap_hdr(_LINUX_CAPABILITY_VERSION_3, 0)
    data = (_cap_data * 2)()
    if _libc.syscall(126, ctypes.byref(hdr), ctypes.byref(data)) != 0:
        raise SandboxError(f"capset(clear) failed: errno={_e()}")
    if _libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
        raise SandboxError(f"prctl(CAP_AMBIENT_CLEAR_ALL) failed: errno={_e()}")
    for cap in range(0, _CAP_LAST_CAP + 1):
        _libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0)

_BPF_LD = 0x00
_BPF_JMP = 0x05
_BPF_RET = 0x06
_BPF_W = 0x00
_BPF_ABS = 0x20
_BPF_JEQ = 0x10
_BPF_JGE = 0x30
_BPF_JGT = 0x20
_BPF_K = 0x00

SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_SET_MODE_FILTER = 1
SECCOMP_FILTER_FLAG_TSYNC = 1

_OFF_NR = 0
_OFF_ARCH = 4
_OFF_ARG0_LO = 16

_AF = {
    "AF_UNIX": 1, "AF_LOCAL": 1, "AF_INET": 2, "AF_INET6": 10,
    "AF_NETLINK": 16, "AF_PACKET": 17, "AF_VSOCK": 40, "AF_UNSPEC": 0,
}

class _sock_filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

class _sock_fprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_sock_filter))]

def _stmt(code, k):
    return (code, 0, 0, k & 0xFFFFFFFF)

def _jump(code, k, jt, jf):
    assert 0 <= jt <= 255 and 0 <= jf <= 255, f"BPF jump offset overflow jt={jt} jf={jf}"
    return (code, jt, jf, k & 0xFFFFFFFF)

_HARDENING_SUBTRACTION = (
    "io_uring_setup", "io_uring_enter", "io_uring_register",
    "unshare", "setns",
    "userfaultfd",
    "personality",
    "mbind", "migrate_pages", "move_pages",
    "set_mempolicy", "set_mempolicy_home_node", "get_mempolicy",
    "add_key", "keyctl", "request_key",
    "mknod", "mknodat",
    "socketcall", "ipc",
    "process_vm_readv", "process_vm_writev", "kcmp",
    "name_to_handle_at", "open_by_handle_at",
    "pidfd_getfd",
    "sched_setscheduler", "sched_setattr", "sched_setparam", "ioprio_set",
    "memfd_create",
)

def _allowed_numbers() -> set:

    if _NR is None or not _NR:
        raise SandboxError(f"no syscall number table for arch {_MACH}")
    return {_NR[n] for n in _SYSCALL_ALLOWLIST if n in _NR}

def build_seccomp_filter(allowed_families) -> list:

    if AUDIT_ARCH == 0 or NR_seccomp < 0:
        raise SandboxError(f"unsupported arch for seccomp: {_MACH}")
    fam = sorted({int(f) for f in allowed_families})
    allowed = _allowed_numbers()
    sock_nr = _NR.get("socket")
    sockpair_nr = _NR.get("socketpair")

    plain = sorted(n for n in allowed if n not in (sock_nr, sockpair_nr))

    prog: list = []

    prog.append(_stmt(_BPF_LD | _BPF_W | _BPF_ABS, _OFF_ARCH))
    prog.append(_jump(_BPF_JMP | _BPF_JEQ | _BPF_K, AUDIT_ARCH, 1, 0))
    prog.append(_stmt(_BPF_RET | _BPF_K, SECCOMP_RET_KILL_PROCESS))

    prog.append(_stmt(_BPF_LD | _BPF_W | _BPF_ABS, _OFF_NR))
    if _X32_BIT:

        prog.append(_jump(_BPF_JMP | _BPF_JGE | _BPF_K, _X32_BIT, 0, 1))
        prog.append(_stmt(_BPF_RET | _BPF_K, SECCOMP_RET_KILL_PROCESS))

    def emit_socket_gate(nr):
        block = []

        fam_tail = [_stmt(_BPF_LD | _BPF_W | _BPF_ABS, _OFF_ARG0_LO)]
        for f in fam:
            fam_tail.append(_jump(_BPF_JMP | _BPF_JEQ | _BPF_K, f, 0, 1))
            fam_tail.append(_stmt(_BPF_RET | _BPF_K, SECCOMP_RET_ALLOW))
        fam_tail.append(_stmt(_BPF_RET | _BPF_K,
                              SECCOMP_RET_ERRNO | (_errno.EAFNOSUPPORT & 0xFFFF)))

        skip = len(fam_tail)
        block.append(_jump(_BPF_JMP | _BPF_JEQ | _BPF_K, nr, 0, skip))
        block.extend(fam_tail)
        return block
    if sock_nr is not None:
        prog.extend(emit_socket_gate(sock_nr))
        prog.append(_stmt(_BPF_LD | _BPF_W | _BPF_ABS, _OFF_NR))
    if sockpair_nr is not None:
        prog.extend(emit_socket_gate(sockpair_nr))
        prog.append(_stmt(_BPF_LD | _BPF_W | _BPF_ABS, _OFF_NR))

    for nr in plain:
        prog.append(_jump(_BPF_JMP | _BPF_JEQ | _BPF_K, nr, 0, 1))
        prog.append(_stmt(_BPF_RET | _BPF_K, SECCOMP_RET_ALLOW))

    prog.append(_stmt(_BPF_RET | _BPF_K, SECCOMP_RET_ERRNO | (_errno.EPERM & 0xFFFF)))
    if len(prog) > 4096:
        raise SandboxError(f"seccomp program too long ({len(prog)} insns)")
    return prog

def install_seccomp(allowed_families) -> None:

    program = build_seccomp_filter(allowed_families)
    arr = (_sock_filter * len(program))(*[_sock_filter(*t) for t in program])
    fprog = _sock_fprog(len(program), arr)
    r = _libc.syscall(NR_seccomp, SECCOMP_SET_MODE_FILTER,
                      SECCOMP_FILTER_FLAG_TSYNC, ctypes.byref(fprog))
    if r != 0:
        PR_SET_SECCOMP = 22
        SECCOMP_MODE_FILTER = 2
        if _libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
            raise SandboxError(f"seccomp install failed: errno={_e()}")

def af_ints(names):

    return sorted({_AF[n] for n in (names or []) if n in _AF})

_LL_ACCESS = {
    "EXECUTE": 1 << 0, "WRITE_FILE": 1 << 1, "READ_FILE": 1 << 2, "READ_DIR": 1 << 3,
    "REMOVE_DIR": 1 << 4, "REMOVE_FILE": 1 << 5, "MAKE_CHAR": 1 << 6, "MAKE_DIR": 1 << 7,
    "MAKE_REG": 1 << 8, "MAKE_SOCK": 1 << 9, "MAKE_FIFO": 1 << 10, "MAKE_BLOCK": 1 << 11,
    "MAKE_SYM": 1 << 12, "REFER": 1 << 13, "TRUNCATE": 1 << 14, "IOCTL_DEV": 1 << 15,
}
_LL_WRITE = (_LL_ACCESS["WRITE_FILE"] | _LL_ACCESS["REMOVE_DIR"] | _LL_ACCESS["REMOVE_FILE"] |
             _LL_ACCESS["MAKE_CHAR"] | _LL_ACCESS["MAKE_DIR"] | _LL_ACCESS["MAKE_REG"] |
             _LL_ACCESS["MAKE_SOCK"] | _LL_ACCESS["MAKE_FIFO"] | _LL_ACCESS["MAKE_BLOCK"] |
             _LL_ACCESS["MAKE_SYM"] | _LL_ACCESS["TRUNCATE"])
_LL_READ = (_LL_ACCESS["READ_FILE"] | _LL_ACCESS["READ_DIR"] | _LL_ACCESS["EXECUTE"])
_LL_ALL = _LL_WRITE | _LL_READ | _LL_ACCESS["REFER"] | _LL_ACCESS["IOCTL_DEV"]

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1

_LL_MIN_ABI = 3

class _ll_attr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]

class _ll_path_beneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

def landlock_available() -> int:

    if NR_landlock_create_ruleset < 0:
        return 0
    v = _libc.syscall(NR_landlock_create_ruleset, None,
                      ctypes.c_size_t(0), ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION))
    return v if v and v > 0 else 0

_READ_ROOTS_FALLBACK = ["/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32",
                        "/etc", "/opt", "/proc", "/sys", "/dev", "/run", "/var", "/home",
                        "/srv", "/tmp"]

def read_roots() -> list:

    roots = []
    try:
        roots = sorted(os.path.join("/", n) for n in os.listdir("/"))
    except OSError:
        roots = []
    for p in _READ_ROOTS_FALLBACK:
        if p not in roots:
            roots.append(p)
    return roots

_READ_ROOTS = _READ_ROOTS_FALLBACK

_SAFE_DEVS = ["/dev/null", "/dev/zero", "/dev/full", "/dev/tty", "/dev/random", "/dev/urandom"]

_DIR_ONLY = (_LL_ACCESS["READ_DIR"] | _LL_ACCESS["REMOVE_DIR"] | _LL_ACCESS["REMOVE_FILE"] |
             _LL_ACCESS["MAKE_CHAR"] | _LL_ACCESS["MAKE_DIR"] | _LL_ACCESS["MAKE_REG"] |
             _LL_ACCESS["MAKE_SOCK"] | _LL_ACCESS["MAKE_FIFO"] | _LL_ACCESS["MAKE_BLOCK"] |
             _LL_ACCESS["MAKE_SYM"] | _LL_ACCESS["REFER"])

def apply_landlock(rw_paths, inaccessible=None) -> None:

    abi = landlock_available()
    if abi < _LL_MIN_ABI:
        raise SandboxError(f"Landlock ABI {abi} < required {_LL_MIN_ABI} — refusing "
                           f"(write-confinement would be incomplete)")

    inacc_real = []
    for p in (inaccessible or []):
        if p:
            try:
                inacc_real.append(os.path.realpath(p))
            except OSError:
                inacc_real.append(os.path.abspath(p))
    strict_read = bool(inacc_real)

    handled = _LL_WRITE | _LL_ACCESS["REFER"] | _LL_ACCESS["TRUNCATE"]
    if strict_read:
        handled |= _LL_READ | _LL_ACCESS["IOCTL_DEV"]
    if abi < 4:
        handled &= ~_LL_ACCESS["IOCTL_DEV"]

    attr = _ll_attr(handled, 0, 0)
    fd = _libc.syscall(NR_landlock_create_ruleset, ctypes.byref(attr),
                       ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0))
    if fd < 0:
        raise SandboxError(f"landlock_create_ruleset failed: errno={_e()}")

    try:
        def add(path, access, required=True):
            try:
                pfd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
            except OSError:
                if required:
                    try:
                        pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                    except OSError:
                        return
                else:
                    return
            try:
                acc = access & handled
                try:
                    st = os.stat(pfd)
                    if (st.st_mode & 0o170000) != 0o040000:
                        acc &= ~(_DIR_ONLY & handled)
                except OSError:
                    pass
                if acc == 0:
                    return
                pb = _ll_path_beneath(acc, pfd)
                r = _libc.syscall(NR_landlock_add_rule, ctypes.c_int(fd),
                                  ctypes.c_uint(LANDLOCK_RULE_PATH_BENEATH),
                                  ctypes.byref(pb), ctypes.c_uint32(0))
                if r != 0 and required:
                    raise SandboxError(f"landlock_add_rule({path}) failed: errno={_e()}")
            finally:
                os.close(pfd)

        def rw_is_safe(p):
            rp = os.path.realpath(p)
            for i in inacc_real:
                if rp == i or rp.startswith(i + os.sep):
                    raise SandboxError(f"rw_path {p} is inside inaccessible {i} — refusing")
                if i.startswith(rp + os.sep):

                    raise SandboxError(f"rw_path {p} contains inaccessible {i} — refusing")
            return rp

        if strict_read:

            add("/", _LL_ACCESS["READ_DIR"], required=False)

            def _carve_read(root):
                rp = os.path.realpath(root)
                if any(rp == i or rp.startswith(i + os.sep) for i in inacc_real):
                    return
                below = [i for i in inacc_real if i.startswith(rp + os.sep)]
                if not below:
                    add(rp, _LL_READ, required=False)
                    return
                add(rp, _LL_ACCESS["READ_DIR"], required=False)
                try:
                    children = [os.path.join(rp, c) for c in os.listdir(rp)]
                except OSError:
                    return
                for child in children:
                    try:
                        crp = os.path.realpath(child)
                    except OSError:
                        continue
                    if any(crp == i or crp.startswith(i + os.sep) for i in inacc_real):
                        continue
                    if os.path.isdir(crp) and any(i.startswith(crp + os.sep) for i in below):
                        _carve_read(crp)
                    else:
                        add(crp, _LL_READ, required=False)
            for root in read_roots():
                if os.path.exists(root):
                    _carve_read(root)
            for p in rw_paths:
                add(rw_is_safe(p), _LL_ALL, required=True)
        else:
            for p in rw_paths:

                add(os.path.realpath(p), _LL_ALL, required=True)

        for _d in _SAFE_DEVS:
            if os.path.exists(_d):
                add(_d, _LL_ALL, required=False)

        for p in rw_paths:
            rp = os.path.realpath(p)
            if rp == "/sys/fs/cgroup" or rp.startswith("/sys/fs/cgroup/"):
                raise SandboxError("refusing: a cgroupfs path was requested writable (leaf escape)")

        if _libc.syscall(NR_landlock_restrict_self, ctypes.c_int(fd), ctypes.c_uint32(0)) != 0:
            raise SandboxError(f"landlock_restrict_self failed: errno={_e()}")
    finally:
        os.close(fd)
