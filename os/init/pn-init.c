#define _GNU_SOURCE
#include <unistd.h>
#include <signal.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <pwd.h>
#include <grp.h>
#include <dirent.h>
#include <sched.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#include <sys/mount.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/sysinfo.h>
#include <sys/reboot.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <linux/reboot.h>
#include <linux/watchdog.h>

#define WD_TIMEOUT_S      10
#define TICK_S             1
#define BACKOFF_MIN_S      1

#define SVC_STOP_TIMEOUT_S  8
#define BACKOFF_MAX_S     30
#define STABLE_S          30
#define CANARY_EVERY_S     5

#define BOOT_GRACE_S      45
#define GIVEUP_WIN_S     120
#define CLEAN_AFTER_S     30
#define CRASHLOOP_DEFAULT  3
#define STATE_DEV         "/dev/vda"
#define PND_SOCK_DEFAULT  "/run/pnd.sock"

static char g_pnd_sock[108] = PND_SOCK_DEFAULT;

#define CG_ROOT           "/sys/fs/cgroup"

#define CG_CRITICAL       CG_ROOT "/pn-critical.slice"
#define CG_BATCH          CG_ROOT "/pn-batch.slice"
#define CG_MISC           CG_ROOT "/pn-misc.slice"
enum { TIER_CRITICAL = 0, TIER_BATCH = 1, TIER_MISC = 2 };
static const char *TIER_DIR[3]  = { CG_CRITICAL, CG_BATCH, CG_MISC };
static const char *TIER_NAME[3] = { "pn-critical.slice", "pn-batch.slice", "pn-misc.slice" };
#ifndef CONF_PATH
#define CONF_PATH         "/etc/pn-init.conf"
#endif

#define PCT_CRIT_MIN       15
#define PCT_CRIT_MIN_CEIL  40
#define PCT_CRIT_MAX       40

#define PCT_CRIT_HIGH      38
#define PCT_BATCH_HIGH     26
#define PCT_BATCH_MAX      28
#define PCT_MISC_HIGH      17
#define PCT_MISC_MAX       18

#define PCT_RESERVE        10
#define RESERVE_MIN_MIB   128
#define FLOOR_MIN_MIB     256
#define PCT_SWAP_BATCH     25
#define PCT_SWAP_MISC      10

#define W_CPU_CRIT      10000
#define W_CPU_BATCH      1000
#define W_CPU_MISC        100

#define PCT_CPU_BATCH      70
#define PCT_CPU_MISC       40
#define CPU_PERIOD_US  100000UL

#define W_IO_CRIT       10000
#define W_IO_BATCH       1000
#define W_IO_MISC         100
#define IO_BATCH_RBPS  (209715200UL)
#define IO_BATCH_WBPS  (209715200UL)
#define IO_MISC_RBPS   ( 83886080UL)
#define IO_MISC_WBPS   ( 83886080UL)

#define PIDS_CRIT        4096
#define PIDS_BATCH       2048
#define PIDS_MISC        1024

#define LEAF_MAX_NUM        8

#define LEAF_HIGH_NUM      15
#define LEAF_HIGH_DEN      16
#define LEAF_MAX_DEN       10
#define TIER_MAX_MIN_MIB   32
#define MIB              (1024UL*1024UL)

#define CONF_MAX_SVC     128
#define CONF_MAX_ARGV     16
#define CONF_MAX_ENV       8
#define CONF_BUF        131072

#define FSTAB_PATH        "/etc/fstab"
#define RESOLV_PATH       "/etc/resolv.conf"
#define DEFAULT_DNS       "1.1.1.1"

static const char *EUDEV_CANDIDATES[] = {
    "/sbin/udevd", "/usr/sbin/udevd", "/lib/eudev/udevd", "/usr/lib/eudev/udevd", (const char*)0 };
static const char *SYSTEMD_UDEVD_CANDIDATES[] = {
    "/usr/lib/systemd/systemd-udevd", "/lib/systemd/systemd-udevd", (const char*)0 };
#define UDEVADM           "/usr/bin/udevadm"
#define BUSYBOX           "/bin/busybox"

#define PLOG_RING (16*1024)
#define PLOG_MAX  (256*1024)
static char   g_plog_ring[PLOG_RING];
static size_t g_plog_len = 0;
static int    g_plog_fd  = -1;
static char   g_plog_path[128];
static void plog_ring_append(const char *m, size_t n){
    if (n >= PLOG_RING){ m += (n - (PLOG_RING - 1)); n = PLOG_RING - 1; g_plog_len = 0; }
    if (g_plog_len + n > PLOG_RING - 1){
        size_t drop = g_plog_len + n - (PLOG_RING - 1);
        memmove(g_plog_ring, g_plog_ring + drop, g_plog_len - drop); g_plog_len -= drop;
    }
    memcpy(g_plog_ring + g_plog_len, m, n); g_plog_len += n;
}
static void say(const char *m){ size_t n = strlen(m);
    (void)!write(1, m, n); plog_ring_append(m, n);
    if (g_plog_fd >= 0) (void)!write(g_plog_fd, m, n); }
static void sayn(const char *m){ say(m); say("\n"); }
static void sayd(unsigned v){ char b[12]; int i=11; b[i]=0; if(!v)b[--i]='0'; while(v){b[--i]='0'+v%10; v/=10;} say(b+i); }

static size_t plog_u(char *buf, unsigned v){
    char t[12]; int i=11; t[i]=0; if(!v) t[--i]='0'; while(v){ t[--i]='0'+v%10; v/=10; }
    size_t l=0; for (char *q=t+i; *q; q++) buf[l++]=*q; return l;
}

static void plog_open(unsigned boot_id){
    struct stat st; mkdir("/var/log", 0755); mkdir("/var/log/pn", 0755);
    const char *p = (stat("/var/log/pn", &st) == 0 && S_ISDIR(st.st_mode)) ? "/var/log/pn/pn-init.log"
                  : (stat("/var/lib/brainarbeit", &st) == 0 && S_ISDIR(st.st_mode)) ? "/var/lib/brainarbeit/pn-init.log"
                  : "/pn-init.log";
    strncpy(g_plog_path, p, sizeof g_plog_path - 1);
    if (stat(g_plog_path, &st) == 0 && st.st_size > PLOG_MAX){
        char r[160]; size_t l = 0;
        for (const char *q = g_plog_path; *q && l < sizeof r - 3; q++) r[l++] = *q;
        r[l++] = '.'; r[l++] = '1'; r[l] = 0; rename(g_plog_path, r);
    }
    int fd = open(g_plog_path, O_WRONLY|O_CREAT|O_APPEND, 0644); if (fd < 0) return;
    char hdr[80]; size_t h = 0;
    for (const char *q = "\n===== pn-init boot #"; *q; q++) hdr[h++] = *q;
    h += plog_u(hdr + h, boot_id);
    for (const char *q = " t="; *q; q++) hdr[h++] = *q;
    h += plog_u(hdr + h, (unsigned)time(0));
    for (const char *q = " =====\n"; *q; q++) hdr[h++] = *q;
    (void)!write(fd, hdr, h);
    (void)!write(fd, g_plog_ring, g_plog_len);
    fsync(fd);
    g_plog_fd = fd;
}

static void plog_reason(const char *why){ sayn(why); if (g_plog_fd >= 0) fsync(g_plog_fd); else sync(); }

static void child_drop_rt(void){
    struct sched_param zero; memset(&zero, 0, sizeof zero);
    (void)syscall(SYS_sched_setscheduler, 0, SCHED_OTHER, &zero);
    (void)setpriority(PRIO_PROCESS, 0, 0);
}

static int resolve_uid(const char *s){
    if (!s || !*s) return -1;
    int allnum = 1;
    for (const char *p = s; *p; p++) if (*p < '0' || *p > '9') { allnum = 0; break; }
    if (allnum) return (int)strtol(s, (char**)0, 10);
    struct passwd *pw = getpwnam(s);
    return pw ? (int)pw->pw_uid : -1;
}

static volatile sig_atomic_t g_reboot = 0, g_poweroff = 0, g_reload = 0;
static void on_sig(int s){
    if (s == SIGUSR1) g_poweroff = 1;
    else if (s == SIGCHLD) {   }

    else if (s == SIGHUP) g_reload = 1;
    else g_reboot = 1;
}

typedef struct {
    const char  *name;
    char *const *argv;
    int          sacred;
    int          batch;
    int          is_pnd;
    int          enabled;
    int          oneshot;
    int          uid;
    char *const *envp;
    const char  *envfile;
    int          done;

    pid_t        pid;
    int          restarts;
    time_t       start_at;
    time_t       next_try;
    int          backoff;
} svc_t;

static char *const A_SSHD[]       = { "/bin/busybox","sh","-c",
    "echo \"[sshd] sacred up (pid $$)\"; while true; do sleep 5; done", (char*)0 };

static char *const A_PND[]        = { "/bin/pndstub","--crash-once", (char*)0 };

static char *const A_PND_POISON[] = { "/bin/pndstub","--poison", (char*)0 };

static char *const A_PORTAL[]     = { "/bin/busybox","sh","-c",
    "echo \"[portal] up (pid $$)\"; sleep 6; echo '[portal] flap exit'; exit 3", (char*)0 };

static char *const A_CHURN[]      = { "/bin/busybox","sh","-c",
    "while true; do i=0; while [ $i -lt 20 ]; do ( (sleep 1)& ); i=$((i+1)); done; sleep 1; done", (char*)0 };

static char *const A_ZCHECK[]     = { "/bin/busybox","sh","-c",
    "while true; do z=0; for d in /proc/[0-9]*; do grep -q '^State:.*Z' \"$d/status\" 2>/dev/null && z=$((z+1)); done; echo \"[zcheck] zombies=$z\"; sleep 5; done", (char*)0 };

static char *const A_MEMHOG[]     = { "/bin/memhog", (char*)0 };

static char *const A_CPUHOG[]     = { "/bin/busybox","sh","-c",
    "echo \"[cpuhog] start (pid $$) spawning busy-loops on all cores for ~25s\"; "
    "n=$(nproc 2>/dev/null || echo 4); i=0; while [ $i -lt \"$n\" ]; do "
    "  ( end=$(( $(cut -d. -f1 /proc/uptime) + 25 )); while [ $(cut -d. -f1 /proc/uptime) -lt $end ]; do :; done ) & i=$((i+1)); done; "
    "echo \"[cpuhog] $n busy-loops running (will self-stop ~25s)\"; wait; echo \"[cpuhog] storm ended\"", (char*)0 };

static svc_t COMPILED_SVCS[] = {
    { .name="sshd",   .argv=A_SSHD,   .sacred=1, .enabled=1, .backoff=BACKOFF_MIN_S },
    { .name="pnd",    .argv=A_PND,    .is_pnd=1, .enabled=1, .backoff=BACKOFF_MIN_S },
    { .name="portal", .argv=A_PORTAL, .batch=1,  .enabled=1, .backoff=BACKOFF_MIN_S },
    { .name="churn",  .argv=A_CHURN,  .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="zcheck", .argv=A_ZCHECK, .enabled=0, .backoff=BACKOFF_MIN_S },

    { .name="miscstorm",  .argv=A_MEMHOG,             .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="batchstorm", .argv=A_MEMHOG, .batch=1,   .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="critstorm",  .argv=A_MEMHOG, .sacred=1,  .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="cpustorm",   .argv=A_CPUHOG, .batch=1,   .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="critcpustorm",.argv=A_CPUHOG, .sacred=1, .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="miscwitness",.argv=A_ZCHECK,             .enabled=0, .backoff=BACKOFF_MIN_S },
    { .name="critwitness",.argv=A_ZCHECK, .sacred=1,  .enabled=0, .backoff=BACKOFF_MIN_S },
};
#define COMPILED_NSVC ((int)(sizeof(COMPILED_SVCS)/sizeof(COMPILED_SVCS[0])))

static svc_t *SVCS = COMPILED_SVCS;
static int    NSVC = COMPILED_NSVC;

#define CONF_SLOTS 2
static svc_t      CONF_SVCS_S[CONF_SLOTS][CONF_MAX_SVC];
static char      *CONF_ARGV_S[CONF_SLOTS][CONF_MAX_SVC][CONF_MAX_ARGV];
static char      *CONF_ENV_S[CONF_SLOTS][CONF_MAX_SVC][CONF_MAX_ENV];
static char       CONF_STORE_S[CONF_SLOTS][CONF_BUF];
static int        g_conf_slot = 0;

static int g_conf_ignored_pend = 0, g_conf_trunc_pend = 0, g_conf_bufused_pend = 0;
static int g_conf_ignored = 0, g_conf_trunc = 0, g_conf_bufused = 0;
static int g_state_dirty = 1;

#ifndef CMDLINE_EXTRA_PATH
#define CMDLINE_EXTRA_PATH "/etc/pn-init.cmdline"
#endif
static size_t read_cmdline(char *b, size_t bsz){
    size_t used = 0;
    if (bsz < 4){ if (bsz) b[0] = 0; return 0; }
    int fd = open("/proc/cmdline", O_RDONLY);
    if (fd >= 0){
        ssize_t n = read(fd, b, bsz - 2);
        close(fd);
        if (n > 0) used = (size_t)n;
    }
    fd = open(CMDLINE_EXTRA_PATH, O_RDONLY);
    if (fd >= 0){
        if (used > 0 && used < bsz - 2) b[used++] = ' ';
        if (used < bsz - 1){
            ssize_t n = read(fd, b + used, bsz - used - 1);
            if (n > 0) used += (size_t)n;
        }
        close(fd);
    }
    b[used] = 0;
    for (size_t i = 0; i < used; i++) if (b[i] == '\n' || b[i] == '\r') b[i] = ' ';
    return used;
}

static int cmdline_has(const char *flag){
    char b[2048];
    if (read_cmdline(b, sizeof b) == 0) return 0;
    return strstr(b, flag) != (char*)0;
}

static int cmdline_int(const char *key, int dflt){
    char b[2048];
    if (read_cmdline(b, sizeof b) == 0) return dflt;
    char *p = strstr(b, key);
    if (!p) return dflt;
    p += strlen(key);
    int v = 0, any = 0;
    while (*p >= '0' && *p <= '9'){ v = v*10 + (*p - '0'); p++; any = 1; }
    return any ? v : dflt;
}

static int cmdline_str(const char *key, char *out, size_t outsz){
    char b[2048];
    if (read_cmdline(b, sizeof b) == 0) return 0;
    char *p = strstr(b, key);
    if (!p) return 0;
    p += strlen(key);
    size_t l = 0; while (*p && *p != ' ' && *p != '\n' && l < outsz - 1) out[l++] = *p++;
    out[l] = 0;
    return l > 0;
}

static int parse_conf(int slot){

    svc_t *CONF_SVCS            = CONF_SVCS_S[slot];
    char *(*CONF_ARGV)[CONF_MAX_ARGV] = CONF_ARGV_S[slot];
    char *(*CONF_ENV)[CONF_MAX_ENV]   = CONF_ENV_S[slot];
    char  *CONF_STORE           = CONF_STORE_S[slot];

    g_conf_ignored_pend = g_conf_trunc_pend = g_conf_bufused_pend = 0;

    int fd = open(CONF_PATH, O_RDONLY);
    if (fd < 0) return 0;
    ssize_t n = read(fd, CONF_STORE, (size_t)CONF_BUF - 1);
    close(fd);
    if (n <= 0) return 0;
    CONF_STORE[n] = 0;
    g_conf_bufused_pend = (int)n;
    if ((size_t)n == (size_t)CONF_BUF - 1) g_conf_trunc_pend = 1;
    if ((size_t)n == (size_t)CONF_BUF - 1)
        sayn("[pn-init] config: WARNING conf filled CONF_BUF -> tail may be TRUNCATED "
             "(services at the end will NOT spawn; raise CONF_BUF + rebuild PID1)");

    int nsvc = 0, svc_overflow = 0;
    char *save_line = (char*)0;
    for (char *line = strtok_r(CONF_STORE, "\n", &save_line);
         line;
         line = strtok_r((char*)0, "\n", &save_line)){
        while (*line == ' ' || *line == '\t') line++;
        if (*line == 0 || *line == '#') continue;

        char *name  = line;
        char *flags = strchr(name, '|');
        if (!flags) continue;
        *flags++ = 0;
        char *args = strchr(flags, '|');
        if (!args) continue;
        *args++ = 0;

        if (nsvc >= CONF_MAX_SVC){
            svc_overflow++;
            say("[pn-init] config: WARNING service #"); sayd((unsigned)(CONF_MAX_SVC + svc_overflow));
            say(" '"); say(name);
            sayn("' EXCEEDS CONF_MAX_SVC -> IGNORED (raise CONF_MAX_SVC + rebuild PID1)");
            continue;
        }
        svc_t *s = &CONF_SVCS[nsvc];
        memset(s, 0, sizeof *s);
        s->name    = name;
        s->backoff = BACKOFF_MIN_S;
        s->enabled = 1;
        s->uid     = 0;
        s->envp    = (char *const*)0;

        int ec = 0;
        char *fp = flags;
        while (*fp){
            while (*fp == ' ' || *fp == '\t' || *fp == ',') fp++;
            if (*fp == 0) break;
            if (!strncmp(fp, "env=", 4)){

                char *val = fp + 4;
                char *scan = val, *end = val + strlen(val);
                for (; *scan; scan++){
                    if ((*scan == ' ' || *scan == '\t' || *scan == ',')
                        && !strncmp(scan + 1, "env=", 4)){ end = scan; break; }
                }
                fp = (*end) ? end + 1 : end;
                while (end > val && (end[-1] == ' ' || end[-1] == '\t' || end[-1] == ',')) end--;
                *end = 0;
                if (ec < CONF_MAX_ENV - 1) CONF_ENV[nsvc][ec++] = val;
                else { say("[pn-init] config: WARNING service '"); say(name);
                       say("' env= exceeds "); sayd((unsigned)(CONF_MAX_ENV - 1));
                       sayn(" entries -> extra env DROPPED (use envfile=, or raise CONF_MAX_ENV + rebuild)"); }
                continue;
            }

            char *fl = fp;
            while (*fp && *fp != ' ' && *fp != '\t' && *fp != ',') fp++;
            if (*fp) *fp++ = 0;
            if      (!strcmp(fl, "sacred"))    s->sacred  = 1;
            else if (!strcmp(fl, "batch"))     s->batch   = 1;
            else if (!strcmp(fl, "pnd"))       s->is_pnd  = 1;
            else if (!strcmp(fl, "disabled"))  s->enabled = 0;
            else if (!strcmp(fl, "oneshot"))   s->oneshot = 1;
            else if (!strncmp(fl, "user=", 5)){
                int u = resolve_uid(fl + 5);
                if (u >= 0) s->uid = u;
                else { say("[pn-init] config: bad user= for "); sayn(name); }
            }
            else if (!strncmp(fl, "envfile=", 8)){
                s->envfile = fl + 8;
            }
            else if (!strncmp(fl, "pndsock=", 8)){

                strncpy(g_pnd_sock, fl + 8, sizeof g_pnd_sock - 1);
                g_pnd_sock[sizeof g_pnd_sock - 1] = 0;
            }
        }
        if (ec){ CONF_ENV[nsvc][ec] = (char*)0; s->envp = CONF_ENV[nsvc]; }

        int ac = 0; char *save_arg = (char*)0;
        char *tok = strtok_r(args, " \t", &save_arg);
        for (; tok && ac < CONF_MAX_ARGV - 1; tok = strtok_r((char*)0, " \t", &save_arg))
            CONF_ARGV[nsvc][ac++] = tok;
        CONF_ARGV[nsvc][ac] = (char*)0;
        if (tok){ say("[pn-init] config: WARNING service '"); say(name);
                  say("' argv exceeds "); sayd((unsigned)(CONF_MAX_ARGV - 1));
                  sayn(" tokens -> trailing args DROPPED (raise CONF_MAX_ARGV + rebuild)"); }
        if (ac == 0) continue;
        s->argv = CONF_ARGV[nsvc];
        nsvc++;
    }
    if (nsvc == 0) return 0;
    g_conf_ignored_pend = svc_overflow;
    say("[pn-init] config: parsed "); sayd((unsigned)nsvc); say(" services from " CONF_PATH);
    if (svc_overflow){ say(" ("); sayd((unsigned)svc_overflow); say(" IGNORED over cap "); sayd((unsigned)CONF_MAX_SVC); say(")"); }
    sayn("");
    return nsvc;
}

static int load_conf(void){
    int n = parse_conf(0);
    if (n <= 0) return 0;
    SVCS = CONF_SVCS_S[0]; NSVC = n; g_conf_slot = 0;
    g_conf_ignored = g_conf_ignored_pend;
    g_conf_trunc   = g_conf_trunc_pend;
    g_conf_bufused = g_conf_bufused_pend;
    return n;
}

static int svc_argv_same(char *const *a, char *const *b){
    if (!a || !b) return a == b;
    for (int i = 0;; i++){
        if (!a[i] || !b[i]) return a[i] == b[i];
        if (strcmp(a[i], b[i])) return 0;
    }
}

static int svc_str_same(const char *a, const char *b){
    if (!a || !b) return a == b;
    return !strcmp(a, b);
}

static int svc_def_same(const svc_t *a, const svc_t *b){
    return a->sacred == b->sacred && a->batch == b->batch && a->is_pnd == b->is_pnd
        && a->enabled == b->enabled && a->oneshot == b->oneshot && a->uid == b->uid
        && svc_str_same(a->envfile, b->envfile)
        && svc_argv_same(a->argv, b->argv)
        && svc_argv_same(a->envp, b->envp);
}

static svc_t *svc_by_name(svc_t *tab, int n, const char *name){
    for (int i = 0; i < n; i++) if (!strcmp(tab[i].name, name)) return &tab[i];
    return (svc_t*)0;
}

static void stop_one_svc(svc_t *s, const char *why){
    if (s->pid <= 0) return;
    say("[pn-init] reload: stopping "); say(s->name);
    say(" (pid "); sayd((unsigned)s->pid); say(") -- "); say(why); say(" ... ");
    kill(s->pid, SIGTERM);
    for (int t = 0; t < SVC_STOP_TIMEOUT_S * 10; t++){
        int st; pid_t r = waitpid(s->pid, &st, WNOHANG);
        if (r == s->pid || (r < 0 && errno == ECHILD)){ sayn("stopped"); s->pid = 0; return; }
        struct timespec ts = {0, 100*1000*1000}; nanosleep(&ts, (void*)0);
    }
    say("no exit in "); sayd(SVC_STOP_TIMEOUT_S); sayn("s -> SIGKILL");
    kill(s->pid, SIGKILL);
    { int st; (void)waitpid(s->pid, &st, 0); }
    s->pid = 0;
}

static int do_reload(void){
    int slot = g_conf_slot ^ 1;
    int n = parse_conf(slot);
    if (n <= 0){
        sayn("[pn-init] reload: config unreadable/empty -> KEEPING the running set (nothing changed)");
        return 0;
    }
    svc_t *nsv = CONF_SVCS_S[slot];
    int added = 0, changed = 0, removed = 0, kept = 0;

    for (int i = 0; i < n; i++){
        svc_t *old = svc_by_name(SVCS, NSVC, nsv[i].name);
        if (!old){
            nsv[i].next_try = 0;
            added++;
            say("[pn-init] reload: + "); sayn(nsv[i].name);
            continue;
        }

        nsv[i].pid      = old->pid;
        nsv[i].restarts = old->restarts;
        nsv[i].start_at = old->start_at;
        nsv[i].backoff  = old->backoff;
        nsv[i].next_try = old->next_try;
        nsv[i].done     = old->done;
        if (svc_def_same(old, &nsv[i])){ kept++; continue; }
        changed++;
        say("[pn-init] reload: ~ "); sayn(nsv[i].name);
        if (nsv[i].pid > 0){

            kill(nsv[i].pid, SIGTERM);
            nsv[i].next_try = 0;
            nsv[i].backoff  = BACKOFF_MIN_S;
        } else if (nsv[i].oneshot){
            nsv[i].done = 0; nsv[i].next_try = 0;
        } else {

            nsv[i].next_try = 0;
            nsv[i].backoff  = BACKOFF_MIN_S;
        }
    }

    for (int i = 0; i < NSVC; i++){
        if (svc_by_name(nsv, n, SVCS[i].name)) continue;
        removed++;
        say("[pn-init] reload: - "); sayn(SVCS[i].name);
        stop_one_svc(&SVCS[i], "removed from config");
    }
    SVCS = nsv; NSVC = n; g_conf_slot = slot;
    g_conf_ignored = g_conf_ignored_pend;
    g_conf_trunc   = g_conf_trunc_pend;
    g_conf_bufused = g_conf_bufused_pend;
    g_state_dirty  = 1;
    say("[pn-init] reload: done -- "); sayd((unsigned)kept); say(" unchanged, ");
    sayd((unsigned)added); say(" added, "); sayd((unsigned)changed); say(" changed, ");
    sayd((unsigned)removed); sayn(" removed");
    return added + changed + removed;
}

#ifndef PNRUN_DIR
#define PNRUN_DIR "/run/pn-init"
#endif

static void wr_atomic(const char *final, const char *tmp, const char *buf, size_t len){
    int fd = open(tmp, O_WRONLY|O_CREAT|O_TRUNC, 0644);
    if (fd < 0) return;
    size_t off = 0;
    while (off < len){
        ssize_t w = write(fd, buf + off, len - off);
        if (w <= 0){ if (errno == EINTR) continue; close(fd); unlink(tmp); return; }
        off += (size_t)w;
    }
    close(fd);
    if (rename(tmp, final) != 0) unlink(tmp);
}

static void publish_state(void){
    static char buf[64 * 1024];
    size_t o = 0;
    time_t now = time(0);

    for (int i = 0; i < NSVC && o < sizeof buf - 256; i++){
        const svc_t *s = &SVCS[i];
        const char *zu;
        if (!s->enabled)                 zu = "disabled";
        else if (s->pid > 0)             zu = "running";
        else if (s->oneshot && s->done)  zu = "oneshot-done";
        else if (s->next_try > now)      zu = "backoff";
        else                             zu = "pending";
        int k = snprintf(buf + o, sizeof buf - o, "%s %ld %ld %d %s\n",
                         s->name, (long)s->pid, (long)s->start_at, s->restarts, zu);
        if (k < 0) break;
        o += (size_t)k;
    }
    wr_atomic(PNRUN_DIR "/services", PNRUN_DIR "/.services.tmp", buf, o);

    int k = snprintf(buf, sizeof buf,
                     "cap %d\nactive %d\nignored %d\nbufcap %d\nbufused %d\ntruncated %d\n"
                     "maxargv %d\nmaxenv %d\n",
                     CONF_MAX_SVC, NSVC, g_conf_ignored, CONF_BUF, g_conf_bufused,
                     g_conf_trunc, CONF_MAX_ARGV, CONF_MAX_ENV);
    if (k > 0) wr_atomic(PNRUN_DIR "/config", PNRUN_DIR "/.config.tmp", buf, (size_t)k);
    g_state_dirty = 0;
}

static int detect_fullsystem(void);
static int state_fd = -1;
static int g_state_mode = 0;
#define BOOTCOUNT_FILE "/var/lib/brainarbeit/.pn-bootcount"
#define BOOTCOUNT_FILE_FALLBACK "/.pn-bootcount"
static char g_bootcount_path[128];

static unsigned bump_bootcount_file(void){

    struct stat st;
    const char *p = (stat("/var/lib/brainarbeit", &st) == 0 && S_ISDIR(st.st_mode))
                    ? BOOTCOUNT_FILE : BOOTCOUNT_FILE_FALLBACK;
    strncpy(g_bootcount_path, p, sizeof g_bootcount_path - 1);
    unsigned bc = 0;
    int fd = open(g_bootcount_path, O_RDONLY);
    if (fd >= 0){ char buf[16]; ssize_t n = read(fd, buf, sizeof buf - 1); close(fd);
        if (n > 0){ buf[n] = 0; bc = (unsigned)strtoul(buf, (char**)0, 10); } }
    bc++;
    int wf = open(g_bootcount_path, O_WRONLY|O_CREAT|O_TRUNC, 0644);
    if (wf >= 0){
        char out[16]; int l = 0; unsigned v = bc; char tmp[12]; int ti = 0;
        if (!v) tmp[ti++] = '0';
        while (v){ tmp[ti++] = '0' + v % 10; v /= 10; }
        while (ti) out[l++] = tmp[--ti];
        out[l++] = '\n';
        (void)!write(wf, out, l); fsync(wf); close(wf);
    } else {
        sayn("[pn-init] crash-loop: bootcount file not writable -> counter disabled"); return 0;
    }
    return bc;
}
static unsigned bump_bootcount(void){

    char dev[64];
    if (detect_fullsystem() && !cmdline_str("pn.statedev=", dev, sizeof dev)){
        g_state_mode = 1;
        return bump_bootcount_file();
    }

    const char *sdev = cmdline_str("pn.statedev=", dev, sizeof dev) ? dev : STATE_DEV;
    state_fd = open(sdev, O_RDWR);
    if (state_fd < 0){ sayn("[pn-init] no state disk -> crash-loop counter disabled"); return 0; }
    g_state_mode = 2;
    unsigned char b[512];
    if (pread(state_fd, b, 512, 0) != 512) memset(b, 0, 512);
    unsigned bc;
    if (memcmp(b, "PNST", 4) != 0){ memset(b, 0, 512); memcpy(b, "PNST", 4); bc = 0; }
    else memcpy(&bc, b + 4, 4);
    bc++;
    memcpy(b + 4, &bc, 4);
    if (pwrite(state_fd, b, 512, 0) == 512) fsync(state_fd);
    return bc;
}
static void clear_bootcount(void){
    if (g_state_mode == 1){
        int wf = open(g_bootcount_path, O_WRONLY|O_CREAT|O_TRUNC, 0644);
        if (wf >= 0){ (void)!write(wf, "0\n", 2); fsync(wf); close(wf); }
        return;
    }
    if (g_state_mode != 2 || state_fd < 0) return;
    unsigned char b[512];
    if (pread(state_fd, b, 512, 0) != 512) memset(b, 0, 512);
    unsigned z = 0; memcpy(b, "PNST", 4); memcpy(b + 4, &z, 4);
    if (pwrite(state_fd, b, 512, 0) == 512) fsync(state_fd);
}

static void mount_one(const char *src,const char *tgt,const char *fs,unsigned long fl){
    mkdir(tgt,0755);
    if (mount(src,tgt,fs,fl,(void*)0)!=0 && errno!=EBUSY){ say("[pn-init] mount FAILED: "); sayn(tgt); }
}

static void run_sync(char *const argv[]);

static int detect_fullsystem(void){
    if (cmdline_has("pn.nofullsystem")) return 0;
    if (cmdline_has("pn.fullsystem"))   return 1;
    struct stat st;
    int has_fstab = (stat(FSTAB_PATH, &st) == 0 && st.st_size > 0);
    int has_usr   = (stat("/usr/sbin", &st) == 0 && S_ISDIR(st.st_mode));
    return has_fstab && has_usr;
}

static void remount_root_rw(void){
    if (mount((void*)0, "/", (void*)0, MS_REMOUNT, (void*)0) == 0)
        sayn("[pn-init] full-system: remounted / read-write");
    else
        sayn("[pn-init] full-system: remount / rw FAILED (continuing; / may stay ro)");
}

static unsigned long fstab_flags(const char *opts){
    unsigned long fl = 0;
    char buf[256]; size_t n = 0;
    while (opts[n] && n < sizeof buf - 1){ buf[n] = opts[n]; n++; } buf[n] = 0;
    char *save = (char*)0;
    for (char *t = strtok_r(buf, ",", &save); t; t = strtok_r((char*)0, ",", &save)){
        if      (!strcmp(t,"ro"))       fl |= MS_RDONLY;
        else if (!strcmp(t,"nosuid"))   fl |= MS_NOSUID;
        else if (!strcmp(t,"nodev"))    fl |= MS_NODEV;
        else if (!strcmp(t,"noexec"))   fl |= MS_NOEXEC;
        else if (!strcmp(t,"noatime"))  fl |= MS_NOATIME;
        else if (!strcmp(t,"relatime")) fl |= MS_RELATIME;
        else if (!strcmp(t,"nodiratime")) fl |= MS_NODIRATIME;
        else if (!strcmp(t,"sync"))     fl |= MS_SYNCHRONOUS;

    }
    return fl;
}

static const char *resolve_spec(const char *spec, char *out, size_t outsz){
    const char *pfx = (char*)0, *dir = (char*)0;
    if      (!strncmp(spec, "UUID=",  5)){ pfx = spec + 5; dir = "/dev/disk/by-uuid/"; }
    else if (!strncmp(spec, "LABEL=", 6)){ pfx = spec + 6; dir = "/dev/disk/by-label/"; }
    if (!dir) return spec;
    size_t l = 0; const char *p;
    for (p = dir;  *p && l < outsz - 1; p++) out[l++] = *p;
    for (p = pfx;  *p && l < outsz - 1; p++) out[l++] = *p;
    out[l] = 0;
    return out;
}

static void try_swapon(const char *path){
    struct stat st;
    if (stat(path, &st) != 0) return;
    if (syscall(SYS_swapon, path, 0) == 0){ say("[pn-init] full-system: swapon "); sayn(path); }
    else if (errno != EBUSY)              { say("[pn-init] full-system: swapon FAILED "); sayn(path); }
}

static void mount_fstab(void){
    int fd = open(FSTAB_PATH, O_RDONLY);
    if (fd < 0){ sayn("[pn-init] full-system: no /etc/fstab (skipping extra mounts)"); return; }
    static char fb[8192];
    ssize_t n = read(fd, fb, sizeof fb - 1); close(fd);
    if (n <= 0) return;
    fb[n] = 0;

    int mounted = 0;
    char *save_line = (char*)0;
    for (char *line = strtok_r(fb, "\n", &save_line); line; line = strtok_r((char*)0, "\n", &save_line)){
        while (*line == ' ' || *line == '\t') line++;
        if (*line == 0 || *line == '#') continue;

        char *save_col = (char*)0;
        char *spec = strtok_r(line, " \t", &save_col);
        char *mp   = spec ? strtok_r((char*)0, " \t", &save_col) : (char*)0;
        char *fs   = mp   ? strtok_r((char*)0, " \t", &save_col) : (char*)0;
        char *opts = fs   ? strtok_r((char*)0, " \t", &save_col) : (char*)0;
        if (!spec || !mp || !fs) continue;
        if (!strcmp(fs, "swap")){ char rb[128]; try_swapon(resolve_spec(spec, rb, sizeof rb)); continue; }
        if (!strcmp(mp, "/"))    continue;
        if (!strcmp(mp, "none")) continue;
        if (opts && strstr(opts, "noauto")) continue;
        char rb[128]; const char *src = resolve_spec(spec, rb, sizeof rb);
        unsigned long fl = opts ? fstab_flags(opts) : 0;
        mkdir(mp, 0755);
        if (mount(src, mp, fs, fl, (void*)0) == 0 || errno == EBUSY){
            say("[pn-init] full-system: mounted "); say(mp); sayn(""); mounted++;
        } else { say("[pn-init] full-system: mount FAILED "); say(mp); say(" ("); say(strerror(errno)); sayn(")"); }
    }
    say("[pn-init] full-system: fstab mounts done ("); sayd((unsigned)mounted); sayn(" extra)");
}

static void mount_runtime_extras(void){
    mount_one("devpts",  "/dev/pts",  "devpts", MS_NOSUID|MS_NOEXEC);
    mount_one("tmpfs",   "/dev/shm",  "tmpfs",  MS_NOSUID|MS_NODEV);
    mkdir("/run/lock", 01777);
    mount_one("tmpfs",   "/run/lock", "tmpfs",  MS_NOSUID|MS_NODEV|MS_NOEXEC);

    mkdir("/run/sshd",  0755);
    mkdir("/run/dbus",  0755);

    if (cmdline_has("pn.sdmarker")) {
        mkdir("/run/systemd",        0755);
        mkdir("/run/systemd/system", 0755);
        sayn("[pn-init] full-system: runtime extras mounted (devpts, shm, run/lock, sshd, dbus, systemd/system marker [pn.sdmarker])");
    } else {
        sayn("[pn-init] full-system: runtime extras mounted (devpts, shm, run/lock, sshd, dbus; NO sd_booted marker -- no-systemd posture)");
    }
}

static void fixup_dev_perms(void){
    static const char *nodes0666[] = { "/dev/null","/dev/zero","/dev/full","/dev/random",
                                       "/dev/urandom","/dev/tty","/dev/ptmx", (char*)0 };
    for (int i = 0; nodes0666[i]; i++) chmod(nodes0666[i], 0666);
    chmod("/dev/console", 0600);
    sayn("[pn-init] F2 dev: re-asserted canonical /dev node perms (null/zero/random/tty 0666)");
}

static pid_t udevd_pid = 0;
static int   used_systemd_udevd = 0;

static void udev_coldplug(void){
    struct stat st;
    if (stat(UDEVADM, &st) == 0){
        char *const t[] = { UDEVADM, "trigger", "--action=add", (char*)0 };
        char *const s[] = { UDEVADM, "settle", "--timeout=15", (char*)0 };
        run_sync(t); run_sync(s);
        sayn("[pn-init] F2 dev: coldplug triggered + settled");
    } else sayn("[pn-init] F2 dev: no udevadm -> skipping coldplug trigger/settle");
}

static void cg_place_daemon(pid_t pid, int tier, const char *name);

static void udevd_spawn(const char *path){
    pid_t p = fork();
    if (p == 0){
        child_drop_rt();
        signal(SIGCHLD, SIG_DFL);
        char *const a[] = { (char*)path, (char*)0 };
        execv(path, a); _exit(127);
    }
    if (p > 0){
        udevd_pid = p; say("[pn-init] F2 dev: started udevd (foreground) "); sayn(path);
        cg_place_daemon(p, TIER_CRITICAL, "udevd");
    }
}

static int mdev_start(void){
    struct stat st;
    if (stat(BUSYBOX, &st) != 0) return 0;
    int hp = open("/proc/sys/kernel/hotplug", O_WRONLY);
    if (hp >= 0){ (void)!write(hp, "/sbin/mdev\n", 11); close(hp); }
    char *const scan[] = { BUSYBOX, "mdev", "-s", (char*)0 };
    run_sync(scan);
    sayn("[pn-init] F2 dev: busybox mdev registered + coldplug-scanned (non-systemd)");
    return 1;
}

static void udev_start(void){
    char want[16]; cmdline_str("pn.devmgr=", want, sizeof want);
    if (!strcmp(want, "none")){ sayn("[pn-init] F2 dev: pn.devmgr=none -> bare devtmpfs only"); return; }

    struct stat st;
    const char *eudevd = (char*)0;
    for (int i = 0; EUDEV_CANDIDATES[i]; i++)
        if (stat(EUDEV_CANDIDATES[i], &st) == 0){ eudevd = EUDEV_CANDIDATES[i]; break; }

    if (!strcmp(want, "mdev")){
        if (mdev_start()) return;
        sayn("[pn-init] F2 dev: pn.devmgr=mdev requested but busybox absent -> trying udevd");
    }

    if (eudevd && strcmp(want, "udev") != 0){
        udevd_spawn(eudevd); udev_coldplug();
        sayn("[pn-init] F2 dev: using eudev (independent, non-systemd)");
        return;
    }

    if (strcmp(want, "udev") != 0 && mdev_start()) return;

    const char *sysudevd = (char*)0;
    for (int i = 0; SYSTEMD_UDEVD_CANDIDATES[i]; i++)
        if (stat(SYSTEMD_UDEVD_CANDIDATES[i], &st) == 0){ sysudevd = SYSTEMD_UDEVD_CANDIDATES[i]; break; }
    if (sysudevd){
        used_systemd_udevd = 1;
        udevd_spawn(sysudevd); udev_coldplug();
        sayn("[pn-init] F2 dev: WARNING fell back to systemd-udevd (no eudev/mdev present) — install eudev to stay fully systemd-free");
        return;
    }
    sayn("[pn-init] F2 dev: no device manager found -> bare devtmpfs only (kernel ifnames)");
}

static int run_sync_rc(char *const argv[]);

static void pick_netif(char *out, size_t outsz){
    out[0] = 0;

    int fd = open("/proc/cmdline", O_RDONLY);
    if (fd >= 0){
        char b[1024]; ssize_t n = read(fd, b, sizeof b - 1); close(fd);
        if (n > 0){ b[n] = 0; char *p = strstr(b, "pn.netif=");
            if (p){ p += 9; size_t l = 0; while (*p && *p != ' ' && l < outsz-1) out[l++] = *p++; out[l] = 0; } }
    }
    if (out[0]){

        char chk[64]; snprintf(chk, sizeof chk, "/sys/class/net/%s", out);
        struct stat st; if (stat(chk, &st) == 0) return;
        say("[pn-init] F3 net: pn.netif="); say(out); sayn(" not present -> autodetecting");
        out[0] = 0;
    }

    DIR *d = opendir("/sys/class/net");
    if (d){
        struct dirent *e;
        while ((e = readdir(d))){
            if (e->d_name[0] == '.' || !strcmp(e->d_name, "lo")) continue;
            size_t l = 0; while (e->d_name[l] && l < outsz-1){ out[l] = e->d_name[l]; l++; } out[l] = 0;
            break;
        }
        closedir(d);
    }
}

static void apply_hostname(void){
    char hn[65] = {0};
    int fd = open("/etc/hostname", O_RDONLY);
    if (fd >= 0){
        ssize_t n = read(fd, hn, sizeof hn - 1); close(fd);
        if (n > 0){ hn[n] = 0; for (char *p = hn; *p; p++){ if (*p=='\n'||*p=='\r'||*p==' '||*p=='\t'){ *p=0; break; } } }
    }
    if (!hn[0]){
        const char *d = "brainbox"; size_t i=0; for (; d[i] && i<sizeof hn-1; i++) hn[i]=d[i]; hn[i]=0;
        sayn("[pn-init] hostname: /etc/hostname empty/missing -> default 'brainbox'");
    }
    if (sethostname(hn, strlen(hn)) == 0){ say("[pn-init] hostname set: "); sayn(hn); }
    else { say("[pn-init] hostname: sethostname FAILED ("); say(strerror(errno)); sayn(")"); }
}

static void net_up(void){

    if (cmdline_has("pn.nonet")){
        sayn("[pn-init] F3 net: pn.nonet -> Netz gehoert der aeusseren Laufzeit, nichts zu tun");
        return;
    }
    char ifn[32]; pick_netif(ifn, sizeof ifn);
    if (!ifn[0]){ sayn("[pn-init] F3 net: no interface found -> network skipped"); return; }
    say("[pn-init] F3 net: bringing up "); sayn(ifn);

    struct stat st;
    const char *IP = (stat("/usr/bin/ip", &st)==0) ? "/usr/bin/ip"
                   : (stat("/sbin/ip", &st)==0)    ? "/sbin/ip"
                   : (stat("/bin/ip", &st)==0)     ? "/bin/ip" : (char*)0;
    if (IP){
        char *const lo_up[]  = { (char*)IP, "link", "set", "lo", "up", (char*)0 };
        char *const if_up[]  = { (char*)IP, "link", "set", ifn, "up", (char*)0 };
        run_sync(lo_up); run_sync(if_up);
    } else {

        char *const a[] = { "/bin/busybox", "ifconfig", ifn, "up", (char*)0 };
        run_sync(a);
    }

    { struct stat ls; if (lstat(RESOLV_PATH, &ls) == 0 && S_ISLNK(ls.st_mode) && stat(RESOLV_PATH, &st) != 0){
          unlink(RESOLV_PATH); sayn("[pn-init] F3 net: removed dangling /etc/resolv.conf symlink"); } }

    int got = 0;
    if (stat("/sbin/dhclient", &st) == 0 || stat("/usr/sbin/dhclient", &st) == 0){
        const char *dh = (stat("/sbin/dhclient",&st)==0) ? "/sbin/dhclient" : "/usr/sbin/dhclient";
        char *const a[] = { (char*)dh, "-1", "-v", ifn, (char*)0 };
        if (run_sync_rc(a) == 0) got = 1;
    }
    if (!got && (stat("/sbin/dhcpcd", &st) == 0 || stat("/usr/sbin/dhcpcd", &st) == 0)){

        const char *dc = (stat("/sbin/dhcpcd",&st)==0) ? "/sbin/dhcpcd" : "/usr/sbin/dhcpcd";
        char *const a[] = { (char*)dc, "-q", "-t", "30", ifn, (char*)0 };
        if (run_sync_rc(a) == 0) got = 1;
    }
    if (!got && (stat("/sbin/udhcpc", &st) == 0 || stat("/bin/busybox", &st) == 0 || stat("/usr/bin/busybox", &st) == 0)){

        const char *script = "/usr/share/udhcpc/default.script";
        const char *uh = (stat("/sbin/udhcpc",&st)==0) ? "/sbin/udhcpc" : (char*)0;
        if (uh){ char *const a[] = { (char*)uh, "-i", ifn, "-n", "-q", "-s", (char*)script, (char*)0 }; if (run_sync_rc(a)==0) got = 1; }
        else   { char *const a[] = { "/bin/busybox","udhcpc","-i",ifn,"-n","-q","-s",(char*)script,(char*)0 }; if (run_sync_rc(a)==0) got = 1; }
    }
    if (got) say("[pn-init] F3 net: DHCP lease acquired on ");
    else     say("[pn-init] F3 net: DHCP failed (link up, no lease) on ");
    sayn(ifn);

    if (stat(RESOLV_PATH, &st) != 0 || st.st_size == 0){
        int rf = open(RESOLV_PATH, O_WRONLY|O_CREAT|O_TRUNC, 0644);
        if (rf >= 0){ (void)!write(rf, "nameserver " DEFAULT_DNS "\n", strlen("nameserver " DEFAULT_DNS "\n")); close(rf);
            sayn("[pn-init] F3 net: wrote fallback /etc/resolv.conf (" DEFAULT_DNS ")"); }
    }
}

static int cg_write(const char *path, const char *data, size_t len){
    int fd = open(path, O_WRONLY);
    if (fd < 0) return -1;
    ssize_t w = write(fd, data, len);
    close(fd);
    return (w == (ssize_t)len) ? 0 : -1;
}

static int u2s(unsigned long v, char *buf, size_t bufsz){
    char tmp[24]; int t = 0;
    if (!v) tmp[t++] = '0';
    while (v && t < (int)sizeof tmp){ tmp[t++] = (char)('0' + v % 10); v /= 10; }
    int l = 0; while (t && l < (int)bufsz - 1) buf[l++] = tmp[--t];
    buf[l] = 0; return l;
}
static void sayMiB(unsigned long bytes){ sayd((unsigned)(bytes / MIB)); say("MiB"); }

static int cg_join(const char *dir, pid_t pid){
    char path[200]; size_t l = 0;
    for (const char *p = dir; *p && l < sizeof path - 1; p++) path[l++] = *p;
    for (const char *p = "/cgroup.procs"; *p && l < sizeof path - 1; p++) path[l++] = *p;
    path[l] = 0;
    char num[24]; int nl = u2s((unsigned long)pid, num, sizeof num);
    return cg_write(path, num, (size_t)nl);
}

static int cg_set_u(const char *dir, const char *leaf, unsigned long val){
    char path[200]; size_t l = 0;
    for (const char *p = dir; *p && l < sizeof path - 1; p++) path[l++] = *p;
    if (l < sizeof path - 1 && leaf[0] != '/') path[l++] = '/';
    for (const char *p = leaf; *p && l < sizeof path - 1; p++) path[l++] = *p;
    path[l] = 0;
    char num[24]; int nl = u2s(val, num, sizeof num);
    if (cg_write(path, num, (size_t)nl) != 0){
        say("[pn-init] cgroup: set "); say(leaf); say(" on "); say(dir); sayn(" FAILED (controller not delegated?)");
        return -1;
    }
    return 0;
}

static int cg_set_s(const char *dir, const char *leaf, const char *val){
    char path[200]; size_t l = 0;
    for (const char *p = dir; *p && l < sizeof path - 1; p++) path[l++] = *p;
    if (l < sizeof path - 1 && leaf[0] != '/') path[l++] = '/';
    for (const char *p = leaf; *p && l < sizeof path - 1; p++) path[l++] = *p;
    path[l] = 0;
    if (cg_write(path, val, strlen(val)) != 0){
        say("[pn-init] cgroup: set "); say(leaf); say(" on "); say(dir); sayn(" FAILED (controller not delegated?)");
        return -1;
    }
    return 0;
}

static unsigned long g_memtotal = 0;
static unsigned long g_swaptotal = 0;
static int           g_ncpu = 1;
static unsigned long meminfo_bytes(const char *key){
    int fd = open("/proc/meminfo", O_RDONLY);
    if (fd < 0) return 0;
    char b[4096]; ssize_t n = read(fd, b, sizeof b - 1); close(fd);
    if (n <= 0) return 0;
    b[n] = 0;
    char *p = strstr(b, key);
    if (!p) return 0;
    p += strlen(key);
    while (*p == ' ' || *p == '\t') p++;
    unsigned long kb = 0; while (*p >= '0' && *p <= '9'){ kb = kb*10 + (unsigned long)(*p - '0'); p++; }
    return kb * 1024UL;
}
static int count_cpus(void){
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    if (n >= 1) return (int)n;
    return 1;
}
static void detect_capacity(void){
    g_memtotal  = meminfo_bytes("MemTotal:");
    g_swaptotal = meminfo_bytes("SwapTotal:");

    if (!g_memtotal){
        struct sysinfo si;
        if (sysinfo(&si) == 0 && si.totalram){
            unsigned long unit = si.mem_unit ? si.mem_unit : 1;
            g_memtotal  = (unsigned long)si.totalram  * unit;
            g_swaptotal = (unsigned long)si.totalswap * unit;
            sayn("[pn-init] capacity: /proc/meminfo unreadable -> using sysinfo() totals");
        }
    }
    g_ncpu      = count_cpus();
    say("[pn-init] capacity: MemTotal="); sayMiB(g_memtotal);
    say(" SwapTotal=");                    sayMiB(g_swaptotal);
    say(" CPUs=");                         sayd((unsigned)g_ncpu); sayn("");
}

static unsigned long pct_of(unsigned long total, int pct){
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    return (total/100UL) * (unsigned long)pct;
}

static int clamp_pct(int v){ return v < 0 ? 0 : (v > 100 ? 100 : v); }

static int ctrl_has(const char *s, const char *want){
    size_t wl = strlen(want);
    for (const char *p = s; *p; ){
        while (*p==' '||*p=='\n'||*p=='\t') p++;
        const char *tok = p;
        while (*p && *p!=' ' && *p!='\n' && *p!='\t') p++;
        if ((size_t)(p-tok)==wl && memcmp(tok, want, wl)==0) return 1;
    }
    return 0;
}

static int root_dev_majmin(char *out, size_t outsz){
    struct stat st;
    if (stat("/", &st) != 0) return 0;
    unsigned maj = major(st.st_dev), min = minor(st.st_dev);
    if (maj == 0) return 0;
    int l = u2s(maj, out, outsz);
    if (l < 1 || (size_t)l + 2 >= outsz) return 0;
    out[l++] = ':';
    int l2 = u2s(min, out + l, outsz - (size_t)l);
    return (l2 >= 1);
}

static void io_max_str(char *out, size_t outsz, const char *majmin,
                       unsigned long rbps, unsigned long wbps){
    size_t o = 0;
    for (const char *p = majmin; *p && o < outsz - 1; p++) out[o++] = *p;
    char t[24]; int tl;
    for (const char *p = " rbps="; *p && o < outsz - 1; p++) out[o++] = *p;
    tl = u2s(rbps, t, sizeof t);
    for (int i = 0; i < tl && o < outsz - 1; i++) out[o++] = t[i];
    for (const char *p = " wbps="; *p && o < outsz - 1; p++) out[o++] = *p;
    tl = u2s(wbps, t, sizeof t);
    for (int i = 0; i < tl && o < outsz - 1; i++) out[o++] = t[i];
    out[o] = 0;
}

static int valid_svc_name(const char *n){
    if (!n || !*n || n[0] == '.') return 0;
    for (const char *p = n; *p; p++){
        char c = *p;
        if (!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='.'||c=='_'||c=='-'))
            return 0;
    }
    return 1;
}

static int g_have_mem = 0, g_have_cpu = 0, g_have_io = 0, g_have_pids = 0;
static void cg_enable_subtree(const char *dir, int quiet, int record){
    char cpath[200]; size_t cl = 0;
    for (const char *p = dir; *p && cl < sizeof cpath - 1; p++) cpath[cl++] = *p;
    for (const char *p = "/cgroup.controllers"; *p && cl < sizeof cpath - 1; p++) cpath[cl++] = *p;
    cpath[cl] = 0;
    int fd = open(cpath, O_RDONLY);
    if (fd < 0){ if(!quiet){ say("[pn-init] cgroup: cannot read controllers in "); sayn(dir);} return; }
    char ctrl[256]; ssize_t n = read(fd, ctrl, sizeof ctrl - 1); close(fd);
    if (n <= 0){ if(!quiet) sayn("[pn-init] cgroup: no controllers advertised"); return; }
    ctrl[n] = 0;
    if (record){
        g_have_mem  = ctrl_has(ctrl, "memory");
        g_have_cpu  = ctrl_has(ctrl, "cpu");
        g_have_io   = ctrl_has(ctrl, "io");
        g_have_pids = ctrl_has(ctrl, "pids");

        if (cmdline_has("pn.nomemctl")){ g_have_mem = 0; sayn("[pn-init] TEST: pn.nomemctl -> forcing g_have_mem=0 (simulate memory controller not delegated)"); }
        if (cmdline_has("pn.nocpuctl")){ g_have_cpu = 0; sayn("[pn-init] TEST: pn.nocpuctl -> forcing g_have_cpu=0"); }
        if (cmdline_has("pn.noioctl")){  g_have_io  = 0; sayn("[pn-init] TEST: pn.noioctl -> forcing g_have_io=0"); }
    }
    char en[300]; size_t e = 0; int any = 0;
    for (char *p = ctrl; *p; ){
        while (*p == ' ' || *p == '\n' || *p == '\t') p++;
        if (!*p) break;
        char *tok = p;
        while (*p && *p != ' ' && *p != '\n' && *p != '\t') p++;
        size_t tl = (size_t)(p - tok);
        if (e + tl + 2 >= sizeof en) break;
        if (any) en[e++] = ' ';
        en[e++] = '+';
        memcpy(en + e, tok, tl); e += tl;
        any = 1;
    }
    en[e] = 0;
    if (!any){ if(!quiet) sayn("[pn-init] cgroup: no controllers to enable"); return; }
    char spath[200]; size_t sl = 0;
    for (const char *p = dir; *p && sl < sizeof spath - 1; p++) spath[sl++] = *p;
    for (const char *p = "/cgroup.subtree_control"; *p && sl < sizeof spath - 1; p++) spath[sl++] = *p;
    spath[sl] = 0;
    if (cg_write(spath, en, e) == 0){
        if(!quiet){ say("[pn-init] cgroup: enabled controllers ["); say(en); say("] in "); sayn(dir); }
    } else {
        say("[pn-init] cgroup: subtree_control write FAILED in "); say(dir); sayn(" (controllers degraded, continuing)");
    }
}

static unsigned long g_crit_min = 0, g_crit_max = 0, g_batch_max = 0, g_misc_max = 0, g_reserve = 0;

static int g_caps_enforced  = 0;
static int g_caps_incomplete = 0;
static int g_memtotal_unknown = 0;

static int cg_apply_caps(void){

    g_caps_incomplete = 0;

    if (!g_memtotal){
        sayn("[pn-init] cgroup: MemTotal unknown -> FAIL-CLOSED conservative fallback (assume 512MiB; caps stay small, never overcommit)");
        g_memtotal = 512UL * MIB;
        g_memtotal_unknown = 1;
    }
    if (g_memtotal_unknown) g_caps_incomplete = 1;
    if (cmdline_has("pn.nomemtotal")){
        sayn("[pn-init] TEST: pn.nomemtotal -> forcing the MemTotal-unknown DEGRADED path");
        g_caps_incomplete = 1;
    }

    int p_cmin  = clamp_pct(cmdline_int("pn.crit_min=",  PCT_CRIT_MIN));
    int p_cmax  = clamp_pct(cmdline_int("pn.crit_max=",  PCT_CRIT_MAX));
    int p_chigh = clamp_pct(cmdline_int("pn.crit_high=",  PCT_CRIT_HIGH));
    int p_bhigh = clamp_pct(cmdline_int("pn.batch_high=", PCT_BATCH_HIGH));
    int p_bmax  = clamp_pct(cmdline_int("pn.batch_max=",  PCT_BATCH_MAX));
    int p_mhigh = clamp_pct(cmdline_int("pn.misc_high=",  PCT_MISC_HIGH));
    int p_mmax  = clamp_pct(cmdline_int("pn.misc_max=",   PCT_MISC_MAX));
    int p_res   = clamp_pct(cmdline_int("pn.reserve=",    PCT_RESERVE));

    unsigned long batch_swap = pct_of(g_swaptotal, PCT_SWAP_BATCH);
    unsigned long misc_swap  = pct_of(g_swaptotal, PCT_SWAP_MISC);
    unsigned long usable_swap = batch_swap + misc_swap;

    unsigned long crit_min = pct_of(g_memtotal, p_cmin);
    if (crit_min < FLOOR_MIN_MIB * MIB) crit_min = FLOOR_MIN_MIB * MIB;
    unsigned long floor_ceil = pct_of(g_memtotal, PCT_CRIT_MIN_CEIL);
    if (crit_min > floor_ceil) crit_min = floor_ceil;

    unsigned long reserve = pct_of(g_memtotal, p_res);
    if (reserve < RESERVE_MIN_MIB * MIB) reserve = RESERVE_MIN_MIB * MIB;
    if (reserve > g_memtotal) reserve = g_memtotal;

    unsigned long crit_budget = g_memtotal - reserve;
    unsigned long crit_high = pct_of(g_memtotal, p_chigh);
    unsigned long crit_max = pct_of(g_memtotal, p_cmax);
    if (crit_max < crit_min) crit_max = crit_min;
    if (crit_max > crit_budget) crit_max = crit_budget;

    if (crit_min > crit_max){
        say("[pn-init] cgroup: WARN crit.min ("); sayMiB(crit_min);
        say(") > crit.max ("); sayMiB(crit_max);
        sayn(") on a tiny box -> clamping floor to ceiling (no contradictory protection)");
        crit_min = crit_max;
    }

    unsigned long ram_left = (g_memtotal > crit_max + reserve) ? (g_memtotal - crit_max - reserve) : 0;
    unsigned long bm_backing = ram_left + usable_swap;
    unsigned long batch_max = pct_of(g_memtotal, p_bmax);
    unsigned long misc_max  = pct_of(g_memtotal, p_mmax);
    unsigned long batch_high = pct_of(g_memtotal, p_bhigh);
    unsigned long misc_high  = pct_of(g_memtotal, p_mhigh);

    int clamped = 0;
    if (batch_max + misc_max > bm_backing){
        clamped = 1;
        batch_max = bm_backing * 60UL / 100UL;
        misc_max  = bm_backing * 40UL / 100UL;
    }

    int batch_uncapped = 0, misc_uncapped = 0;
    unsigned long floor_b = TIER_MAX_MIN_MIB * MIB;
    unsigned long fb = (batch_max && batch_max < floor_b) ? floor_b : batch_max;
    unsigned long fm = (misc_max  && misc_max  < floor_b) ? floor_b : misc_max;
    if (fb + fm <= bm_backing){
        batch_max = fb; misc_max = fm;
    } else {

        if (batch_max && batch_max >= floor_b && batch_max <= bm_backing){   }
        else { batch_max = 0; batch_uncapped = 1; }
        unsigned long left = (bm_backing > batch_max) ? bm_backing - batch_max : 0;
        if (misc_max && misc_max >= floor_b && misc_max <= left){   }
        else { misc_max = 0; misc_uncapped = 1; }
    }
    if (batch_max == 0) batch_uncapped = 1;
    if (misc_max  == 0) misc_uncapped  = 1;
    if (batch_uncapped || misc_uncapped){
        g_caps_incomplete = 1;
        sayn("[pn-init] cgroup: WARN a tier cap could not fit above its minimum (box too small / overrides too tight) -> leaving that tier UNCAPPED to avoid OOM-on-start; DEGRADED, lower pn.crit_max/pn.reserve");
    }

    if (crit_max){  if (crit_high  == 0 || crit_high  >= crit_max)  crit_high  = crit_max  * 15UL / 16UL; if (crit_high  == 0) crit_high  = crit_max;  } else crit_high = 0;
    if (batch_max){ if (batch_high == 0 || batch_high >= batch_max) batch_high = batch_max * 15UL / 16UL; if (batch_high == 0) batch_high = batch_max; } else batch_high = 0;
    if (misc_max){  if (misc_high  == 0 || misc_high  >= misc_max)  misc_high  = misc_max  * 15UL / 16UL; if (misc_high  == 0) misc_high  = misc_max;  } else misc_high = 0;

    g_crit_min = crit_min; g_crit_max = crit_max; g_batch_max = batch_max; g_misc_max = misc_max; g_reserve = reserve;

    int mem_missing = 0, io_missing = 0, cpu_missing = 0;

    mem_missing |= cg_set_u(CG_CRITICAL, "memory.min", crit_min);
    mem_missing |= cg_set_u(CG_CRITICAL, "memory.low", crit_min);

    if (crit_high) mem_missing |= cg_set_u(CG_CRITICAL, "memory.high", crit_high);
    else           cg_set_s(CG_CRITICAL, "memory.high", "max");
    if (crit_max) mem_missing |= cg_set_u(CG_CRITICAL, "memory.max", crit_max);
    else        { cg_set_s(CG_CRITICAL, "memory.max", "max"); g_caps_incomplete = 1; }
    cpu_missing |= cg_set_u(CG_CRITICAL, "cpu.weight", W_CPU_CRIT);
    cg_set_s(CG_CRITICAL, "cpu.max", "max default");
    io_missing  |= cg_set_u(CG_CRITICAL, "io.weight",  W_IO_CRIT);
    cg_set_u(CG_CRITICAL, "pids.max", PIDS_CRIT);

    cg_set_u(CG_CRITICAL, "memory.swap.max", 0);

    char cpu_b[48], cpu_m[48];
    unsigned long cpu_total = (unsigned long)g_ncpu * CPU_PERIOD_US;
    { char q[24]; int ql = u2s(cpu_total * PCT_CPU_BATCH / 100UL, q, sizeof q);
      int o=0; for (int i=0;i<ql;i++) cpu_b[o++]=q[i]; cpu_b[o++]=' ';
      char per[24]; int pl=u2s(CPU_PERIOD_US, per, sizeof per); for(int i=0;i<pl;i++) cpu_b[o++]=per[i]; cpu_b[o]=0; }
    { char q[24]; int ql = u2s(cpu_total * PCT_CPU_MISC / 100UL, q, sizeof q);
      int o=0; for (int i=0;i<ql;i++) cpu_m[o++]=q[i]; cpu_m[o++]=' ';
      char per[24]; int pl=u2s(CPU_PERIOD_US, per, sizeof per); for(int i=0;i<pl;i++) cpu_m[o++]=per[i]; cpu_m[o]=0; }

    char majmin[32]; int have_dev = root_dev_majmin(majmin, sizeof majmin);
    char io_b[96], io_m[96];
    if (have_dev){
        io_max_str(io_b, sizeof io_b, majmin, IO_BATCH_RBPS, IO_BATCH_WBPS);
        io_max_str(io_m, sizeof io_m, majmin, IO_MISC_RBPS,  IO_MISC_WBPS);
    }

    if (batch_high) mem_missing |= cg_set_u(CG_BATCH, "memory.high", batch_high); else cg_set_s(CG_BATCH, "memory.high", "max");
    if (batch_max)  mem_missing |= cg_set_u(CG_BATCH, "memory.max",  batch_max);  else cg_set_s(CG_BATCH, "memory.max",  "max");
    cg_set_u(CG_BATCH, "memory.swap.max", batch_swap);
    cpu_missing |= cg_set_u(CG_BATCH, "cpu.weight",      W_CPU_BATCH);
    cpu_missing |= cg_set_s(CG_BATCH, "cpu.max", cpu_b);
    io_missing  |= cg_set_u(CG_BATCH, "io.weight",       W_IO_BATCH);
    if (have_dev) io_missing |= cg_set_s(CG_BATCH, "io.max", io_b);
    cg_set_u(CG_BATCH, "pids.max", PIDS_BATCH);
    cg_set_s(CG_BATCH, "memory.oom.group", "1");

    if (misc_high) mem_missing |= cg_set_u(CG_MISC, "memory.high", misc_high); else cg_set_s(CG_MISC, "memory.high", "max");
    if (misc_max)  mem_missing |= cg_set_u(CG_MISC, "memory.max",  misc_max);   else cg_set_s(CG_MISC, "memory.max",  "max");
    cg_set_u(CG_MISC, "memory.swap.max", misc_swap);
    cpu_missing |= cg_set_u(CG_MISC, "cpu.weight",       W_CPU_MISC);
    cpu_missing |= cg_set_s(CG_MISC, "cpu.max", cpu_m);
    io_missing  |= cg_set_u(CG_MISC, "io.weight",        W_IO_MISC);
    if (have_dev) io_missing |= cg_set_s(CG_MISC, "io.max", io_m);
    cg_set_u(CG_MISC, "pids.max", PIDS_MISC);
    cg_set_s(CG_MISC, "memory.oom.group", "1");

    if (!have_dev) sayn("[pn-init] cgroup: NOTE io.max absolute backstop skipped (no MAJ:MIN backing device; io.weight fair-share still applied)");

    say("[pn-init] cgroup: caps "); say(clamped ? "CLAMPED to fit budget — " : "written — ");
    say("crit.min=");  sayMiB(crit_min);
    say(" crit.max="); sayMiB(crit_max);
    say(" reserve=");  sayMiB(reserve);
    say(" batch.max="); if (batch_max) sayMiB(batch_max); else say("UNCAPPED");
    say(" misc.max=");  if (misc_max)  sayMiB(misc_max);  else say("UNCAPPED");
    say(" usable_swap="); sayMiB(usable_swap);
    sayn("");

    int enforced = g_have_mem && !mem_missing && !g_caps_incomplete;
    if (!enforced) g_caps_incomplete = 1;
    g_caps_enforced = enforced;

    unsigned long demand_ram   = crit_max + reserve + batch_max + misc_max;
    unsigned long backing_full = g_memtotal + usable_swap;
    int fits = (crit_max + reserve <= g_memtotal) && (batch_max + misc_max <= bm_backing);
    if (enforced && fits){
        say("[pn-init] cgroup: INVARIANT OK — crit.max+reserve<=RAM AND batch.max+misc.max<=RAM_left+usable_swap; demand=");
        sayMiB(demand_ram); say(" backing="); sayMiB(backing_full);
        sayn(" -> NO system-wide OOM possible (every hard claim is physically backed; swap credited to batch/misc only)");
    } else if (!fits){

        sayn("[pn-init] cgroup: INVARIANT WARN — caps could not be clamped to fit (lower pn.crit_max/pn.reserve) — DEGRADED, no never-OOM guarantee");
        g_caps_incomplete = 1; g_caps_enforced = 0;
    } else {
        sayn("[pn-init] cgroup: INVARIANT NOT ENFORCED — caps not fully written (no never-OOM guarantee; DEGRADED)");
    }
    if (clamped) sayn("[pn-init] cgroup: NOTE batch/misc ceilings were clamped DOWN to honor the never-OOM invariant");

    if (!g_have_mem || mem_missing) sayn("[pn-init] cgroup: WARN memory caps NOT fully enforced (memory controller not delegated on this rootfs)");
    if (!g_have_cpu || cpu_missing) sayn("[pn-init] cgroup: WARN cpu caps NOT fully enforced (cpu controller absent OR a cpu.max write failed)");
    if (!g_have_io  || io_missing)  sayn("[pn-init] cgroup: WARN io weights/caps NOT fully enforced (io controller not delegated; check /sys/fs/cgroup/cgroup.controllers)");
    if (g_have_mem && g_have_cpu && !mem_missing && !cpu_missing && !g_caps_incomplete)
        sayn("[pn-init] cgroup: memory+cpu enforcement CONFIRMED active");

    return enforced ? 0 : -1;
}

static void cg_set_leaf_caps(const char *leaf, int tier){
    unsigned long tier_max = (tier == TIER_CRITICAL) ? g_crit_max
                           : (tier == TIER_BATCH)    ? g_batch_max : g_misc_max;
    if (tier_max){
        unsigned long leaf_max = tier_max * (unsigned long)LEAF_MAX_NUM / (unsigned long)LEAF_MAX_DEN;
        if (leaf_max < TIER_MAX_MIN_MIB * MIB) leaf_max = TIER_MAX_MIN_MIB * MIB;
        if (leaf_max > tier_max) leaf_max = tier_max;

        if (tier == TIER_CRITICAL){
            cg_set_s(leaf, "memory.high", "max");
        } else {
            unsigned long leaf_high = leaf_max * (unsigned long)LEAF_HIGH_NUM / (unsigned long)LEAF_HIGH_DEN;
            if (leaf_high == 0 || leaf_high >= leaf_max) leaf_high = leaf_max;
            cg_set_u(leaf, "memory.high", leaf_high);
        }
        cg_set_u(leaf, "memory.max", leaf_max);
    }
    cg_set_s(leaf, "memory.oom.group", "1");
}

static int cg_ready = 0;
static void cg_setup_slices(void){
    detect_capacity();

    if (cmdline_has("pn.nocg")){
        sayn("[pn-init] TEST: pn.nocg -> simulating cgroup tier setup FAILURE (services would run ungoverned)");
        g_caps_incomplete = 1; g_caps_enforced = 0;
        return;
    }

    cg_enable_subtree(CG_ROOT, 0, 1  );
    int ok = 1;
    if (mkdir(CG_CRITICAL, 0755) != 0 && errno != EEXIST){ sayn("[pn-init] cgroup: mkdir pn-critical.slice FAILED"); ok = 0; }
    if (mkdir(CG_BATCH,    0755) != 0 && errno != EEXIST){ sayn("[pn-init] cgroup: mkdir pn-batch.slice FAILED");    ok = 0; }
    if (mkdir(CG_MISC,     0755) != 0 && errno != EEXIST){ sayn("[pn-init] cgroup: mkdir pn-misc.slice FAILED");     ok = 0; }
    if (ok){
        cg_ready = 1;
        sayn("[pn-init] cgroup: tiers ready (pn-critical.slice, pn-batch.slice, pn-misc.slice)");

        cg_enable_subtree(CG_CRITICAL, 1  , 0);
        cg_enable_subtree(CG_BATCH,    1, 0);
        cg_enable_subtree(CG_MISC,     1, 0);
        cg_apply_caps();
    } else {
        g_caps_incomplete = 1; g_caps_enforced = 0;
        sayn("[pn-init] cgroup: tier setup degraded (services run in root cgroup; NO caps)");
    }
}

static int tier_of(const svc_t *s){ return s->sacred ? TIER_CRITICAL : (s->batch ? TIER_BATCH : TIER_MISC); }

static void cg_delegate(const char *leaf, int uid){
    if (uid <= 0) return;
    (void)chown(leaf, (uid_t)uid, (gid_t)uid);
    char f[224];
    static const char *knobs[] = { "/cgroup.procs", "/cgroup.subtree_control", "/cgroup.threads", 0 };
    for (int i = 0; knobs[i]; i++){
        size_t l = 0;
        for (const char *p = leaf;     *p && l < sizeof f - 1; p++) f[l++] = *p;
        for (const char *p = knobs[i]; *p && l < sizeof f - 1; p++) f[l++] = *p;
        f[l] = 0;
        (void)chown(f, (uid_t)uid, (gid_t)uid);
    }
    say("[pn-init] cgroup: delegated "); say(leaf); say(" to uid "); sayd((unsigned)uid); sayn(" (systemd --user can subdivide)");
}

static void cg_place(pid_t pid, const svc_t *s){
    if (!cg_ready){ say("[pn-init] cgroup: "); say(s->name); sayn(" -> root cgroup (UNGOVERNED; tiers degraded)"); return; }
    int tier = tier_of(s);
    const char *tdir = TIER_DIR[tier];

    if (!valid_svc_name(s->name)){
        say("[pn-init] cgroup: REFUSING to place '"); say(s->name);
        say("' (unsafe name) -> "); sayn(TIER_NAME[tier]);

        if (cg_join(tdir, pid) == 0){ say("[pn-init] cgroup: "); say(s->name); say(" -> "); say(TIER_NAME[tier]); sayn(" (tier root)"); }
        return;
    }
    char leaf[200]; size_t l = 0;
    for (const char *p = tdir; *p && l < sizeof leaf - 1; p++) leaf[l++] = *p;
    if (l < sizeof leaf - 1) leaf[l++] = '/';
    for (const char *p = s->name; *p && l < sizeof leaf - 1; p++) leaf[l++] = *p;
    leaf[l] = 0;
    mkdir(leaf, 0755);
    if (cg_join(leaf, pid) == 0){
        cg_set_leaf_caps(leaf, tier);
        if (s->uid > 0) cg_delegate(leaf, s->uid);
        say("[pn-init] cgroup: "); say(s->name); say(" -> "); say(TIER_NAME[tier]); say("/"); sayn(s->name);
    } else if (cg_join(tdir, pid) == 0){
        say("[pn-init] cgroup: "); say(s->name); say(" -> "); say(TIER_NAME[tier]); sayn(" (tier root; leaf join failed)");
    } else {
        say("[pn-init] cgroup: place "); say(s->name); sayn(" FAILED (left in current cgroup)");
    }
}

static void cg_place_daemon(pid_t pid, int tier, const char *name){
    if (!cg_ready){ say("[pn-init] cgroup: "); say(name); sayn(" -> root cgroup (UNGOVERNED; tiers degraded)"); return; }
    const char *tdir = TIER_DIR[tier];
    char leaf[200]; size_t l = 0;
    int safe = valid_svc_name(name);
    for (const char *p = tdir; *p && l < sizeof leaf - 1; p++) leaf[l++] = *p;
    if (safe){
        if (l < sizeof leaf - 1) leaf[l++] = '/';
        for (const char *p = name; *p && l < sizeof leaf - 1; p++) leaf[l++] = *p;
    }
    leaf[l] = 0;
    if (safe) mkdir(leaf, 0755);
    if (cg_join(leaf, pid) == 0){
        if (safe) cg_set_leaf_caps(leaf, tier);
        say("[pn-init] cgroup: "); say(name); say(" -> "); say(TIER_NAME[tier]);
        if (safe){ say("/"); sayn(name); } else sayn(" (tier root)"); }
    else { say("[pn-init] cgroup: place daemon "); say(name); sayn(" FAILED (left in current cgroup)"); }
}

static void run_sync(char *const argv[]){
    pid_t p = fork();
    if (p == 0){ child_drop_rt(); execv(argv[0], argv); _exit(127); }
    if (p > 0){ int st; waitpid(p, &st, 0); }
}

static int run_sync_rc(char *const argv[]){
    pid_t p = fork();
    if (p == 0){ child_drop_rt(); execv(argv[0], argv); _exit(127); }
    if (p > 0){ int st; if (waitpid(p, &st, 0) == p && WIFEXITED(st)) return WEXITSTATUS(st); }
    return -1;
}

static void spawn_async(char *const argv[]){
    pid_t p = fork();
    if (p == 0){ child_drop_rt(); execv(argv[0], argv); _exit(127); }
}

static void ensure_user_runtime(int uid){
    char dir[64]; size_t l = 0; const char *base = "/run/user/";
    for (const char *p = base; *p; p++) dir[l++] = *p;
    char num[12]; int i = 11; num[i] = 0; unsigned v = (unsigned)uid;
    if (!v) num[--i] = '0';
    while (v){ num[--i] = '0' + v % 10; v /= 10; }
    for (char *q = num + i; *q; q++) dir[l++] = *q;
    dir[l] = 0;
    mkdir("/run/user", 0755);
    if (mkdir(dir, 0700) != 0 && errno != EEXIST){ say("[pn-init] F4: mkdir "); say(dir); sayn(" FAILED"); return; }

    struct stat _sd, _sp;
    int _mounted = (stat(dir, &_sd) == 0 && stat("/run/user", &_sp) == 0 && _sd.st_dev != _sp.st_dev);
    if (!_mounted && mount("tmpfs", dir, "tmpfs", MS_NOSUID|MS_NODEV, "mode=0700,size=64m") != 0 && errno != EBUSY){

    }
    if (chown(dir, uid, uid) != 0){   }
    chmod(dir, 0700);
}

static char  ENVBUF[4096];
static char *ENVP[24];
static char *const *build_envp(const svc_t *s){
    size_t bo = 0; int eo = 0;

    #define ADDENV(str) do{ const char *_p=(str); if(eo<23 && bo<sizeof ENVBUF){ \
        ENVP[eo++]=ENVBUF+bo; while(*_p && bo<sizeof ENVBUF-1) ENVBUF[bo++]=*_p++; ENVBUF[bo++]=0; } }while(0)

    struct passwd *pw = s->uid ? getpwuid(s->uid) : (struct passwd*)0;
    ADDENV("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin");
    if (pw){
        char tmp[256];
        snprintf(tmp, sizeof tmp, "HOME=%s",    pw->pw_dir);  ADDENV(tmp);
        snprintf(tmp, sizeof tmp, "USER=%s",    pw->pw_name); ADDENV(tmp);
        snprintf(tmp, sizeof tmp, "LOGNAME=%s", pw->pw_name); ADDENV(tmp);
        snprintf(tmp, sizeof tmp, "XDG_RUNTIME_DIR=/run/user/%d", s->uid); ADDENV(tmp);
        snprintf(tmp, sizeof tmp, "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%d/bus", s->uid); ADDENV(tmp);
    } else {
        ADDENV("HOME=/root"); ADDENV("USER=root"); ADDENV("LOGNAME=root");
    }

    if (s->envp) for (int k = 0; s->envp[k] && eo < 23; k++) ADDENV(s->envp[k]);

    if (s->envfile){
        int ff = open(s->envfile, O_RDONLY);
        if (ff >= 0){
            static char fbuf[4096];
            ssize_t fn = read(ff, fbuf, sizeof fbuf - 1); close(ff);
            if (fn > 0){
                fbuf[fn] = 0;
                char *ln = fbuf;
                while (ln && *ln && eo < 23){
                    char *nl = strchr(ln, '\n'); if (nl) *nl = 0;
                    char *q = ln; while (*q == ' ' || *q == '\t') q++;
                    if (!strncmp(q, "export ", 7)) q += 7;
                    char *eq = strchr(q, '=');
                    if (*q && *q != '#' && eq){
                        char *val = eq + 1; size_t vl = strlen(val);
                        if (vl >= 2 && (val[0] == '"' || val[0] == '\'') && val[vl-1] == val[0]){
                            memmove(val, val + 1, vl - 2); val[vl-2] = 0;
                        }
                        ADDENV(q);
                    }
                    ln = nl ? nl + 1 : 0;
                }
            }
        } else { sayn("[pn-init] envfile: open failed (continuing without it)"); }
    }
    ENVP[eo] = (char*)0;
    #undef ADDENV
    return ENVP;
}

static void start_svc(svc_t *s){
    if (s->uid) ensure_user_runtime(s->uid);
    pid_t p = fork();
    if (p == 0){
        child_drop_rt();
        setsid();
        signal(SIGCHLD, SIG_DFL);

        char *const *envp = (s->uid || s->envp || s->envfile) ? build_envp(s) : (char *const *)0;
        if (s->uid){

            struct passwd *pw = getpwuid(s->uid);
            gid_t gid = pw ? pw->pw_gid : (gid_t)s->uid;
            if (pw) initgroups(pw->pw_name, gid);
            if (setgid(gid) != 0) _exit(126);
            if (setuid(s->uid) != 0) _exit(126);
            if (pw && pw->pw_dir) (void)!chdir(pw->pw_dir);
        }
        if (envp){
            execve(s->argv[0], s->argv, envp);
        } else {
            execv(s->argv[0], s->argv);
        }
        _exit(127);
    }
    if (p > 0){
        s->pid = p; s->start_at = time(0); s->next_try = 0; s->restarts++;
        g_state_dirty = 1;
        cg_place(p, s);
        say("[pn-init] started "); say(s->name);
        if (s->uid){ say(" (uid "); sayd((unsigned)s->uid); say(")"); }
        if (s->oneshot) say(" [oneshot]");
        sayn("");
    }
}

static int wd_fd = -1;
static void wd_open(void){
    wd_fd = open("/dev/watchdog", O_WRONLY);
    if (wd_fd < 0){ sayn("[pn-init] no /dev/watchdog node (using software-reset fallback on give-up)"); return; }
    int to = WD_TIMEOUT_S; ioctl(wd_fd, WDIOC_SETTIMEOUT, &to);
    sayn("[pn-init] HW watchdog armed");
}
static void wd_pet(void){ if (wd_fd >= 0) ioctl(wd_fd, WDIOC_KEEPALIVE, 0); }
static void wd_disarm(void){ if (wd_fd >= 0){ (void)!write(wd_fd, "V", 1); close(wd_fd); wd_fd = -1; } }

static int probe(const char *msg, const char *want){
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (s < 0) return 0;
    struct timeval tv = { 1, 0 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
    strncpy(a.sun_path, g_pnd_sock, sizeof a.sun_path - 1);
    int ok = 0;
    if (connect(s, (struct sockaddr*)&a, sizeof a) == 0 && write(s, msg, strlen(msg)) > 0){
        char b[32]; ssize_t n = read(s, b, sizeof b - 1);
        if (n > 0){ b[n] = 0; ok = strncmp(b, want, strlen(want)) == 0; }
    }
    close(s);
    return ok;
}
static int pnd_ping(void){ return probe("ping\n", "pong"); }
static int pnd_canary(void){ return probe("canary\n", "ok"); }

static int path_exists(const char *p){ struct stat st; return stat(p, &st) == 0; }
static int integrity_sweep(void){
    int fails = 0;

    int cg_mounted = path_exists(CG_ROOT "/cgroup.controllers");
    int cg_wr = (mkdir(CG_ROOT "/.pninit.probe", 0755) == 0 || errno == EEXIST);
    if (cg_wr) rmdir(CG_ROOT "/.pninit.probe");
    if (!cg_mounted || !cg_wr){ sayn("[pn-init] integrity: FAIL (CRITICAL) cgroup2 not mounted/writable -> degrading"); fails++; }
    if (!path_exists("/run"))         { sayn("[pn-init] integrity: FAIL /run missing");         fails++; }
    if (!path_exists("/tmp"))         { sayn("[pn-init] integrity: FAIL /tmp missing");         fails++; }
    if (!path_exists("/dev/console")) { sayn("[pn-init] integrity: FAIL /dev/console missing"); fails++; }

    if (wd_fd < 0 && !path_exists("/dev/watchdog"))
        sayn("[pn-init] integrity: note no /dev/watchdog -> software-reset fallback in effect");

    if (g_caps_incomplete || !g_caps_enforced){
        sayn("[pn-init] integrity: FAIL cgroup caps not fully enforced (no never-OOM guarantee; DEGRADED)"); fails++;
    }
    if (fails == 0) sayn("[pn-init] integrity: PASS (cgroup2 rw, /run, /tmp, /dev/console, watchdog, caps enforced)");
    return fails;
}

#define KILLALL_GRACE_S     2

static void reap_pending(void){
    for (;;){
        int st; pid_t p = waitpid(-1, &st, WNOHANG);
        if (p <= 0) break;
        for (int i = 0; i < NSVC; i++) if (SVCS[i].pid == p) SVCS[i].pid = 0;
    }
}

static void stop_services_reverse(void){
    for (int i = NSVC - 1; i >= 0; i--){
        svc_t *s = &SVCS[i];
        reap_pending();
        if (s->pid <= 0) continue;
        say("[pn-init] shutdown: stopping "); say(s->name);
        say(" (pid "); sayd((unsigned)s->pid); say(") SIGTERM ... ");
        kill(s->pid, SIGTERM);
        int stopped = 0;
        for (int t = 0; t < SVC_STOP_TIMEOUT_S * 10; t++){
            int st; pid_t r = waitpid(s->pid, &st, WNOHANG);
            if (r == s->pid || (r < 0 && errno == ECHILD)){ stopped = 1; break; }
            struct timespec ts = {0, 100*1000*1000}; nanosleep(&ts, (void*)0);
        }
        if (stopped){ sayn("stopped"); s->pid = 0; continue; }
        say("no exit in "); sayd(SVC_STOP_TIMEOUT_S); say("s -> SIGKILL ... ");
        kill(s->pid, SIGKILL);
        { int st; (void)waitpid(s->pid, &st, 0); }
        sayn("killed");
        s->pid = 0;
    }
}

static void swapoff_all(void){
    int fd = open("/proc/swaps", O_RDONLY);
    if (fd < 0) return;
    static char buf[4096];
    ssize_t r = read(fd, buf, sizeof buf - 1); close(fd);
    if (r <= 0) return;
    buf[r] = 0;
    char *ln = strchr(buf, '\n');
    while (ln && *(++ln)){
        char *e = strchr(ln, '\n'); if (e) *e = 0;
        char *sp = ln; while (*sp && *sp != ' ' && *sp != '\t') sp++;
        *sp = 0;
        if (*ln){
            if (syscall(SYS_swapoff, ln) == 0){ say("[pn-init] shutdown: swapoff "); sayn(ln); }
            else                              { say("[pn-init] shutdown: swapoff FAILED "); sayn(ln); }
        }
        ln = e ? e + 1 : (char*)0;
    }
}

static void fs_teardown(void){
    swapoff_all();
    static char mnts[64][128];
    int n = 0;
    int fd = open("/proc/self/mounts", O_RDONLY);
    if (fd >= 0){
        static char buf[16384];
        ssize_t r = read(fd, buf, sizeof buf - 1); close(fd);
        if (r > 0){
            buf[r] = 0;
            char *ln = buf;
            while (ln && *ln && n < 64){
                char *nl = strchr(ln, '\n'); if (nl) *nl = 0;
                char *sp = strchr(ln, ' ');
                if (sp){
                    char *mp = sp + 1; char *e = strchr(mp, ' ');
                    if (e && (size_t)(e - mp) < sizeof mnts[0]){
                        memcpy(mnts[n], mp, (size_t)(e - mp)); mnts[n][e - mp] = 0; n++;
                    }
                }
                ln = nl ? nl + 1 : (char*)0;
            }
        }
    }
    sync();
    for (int i = n - 1; i >= 0; i--){
        if (!strcmp(mnts[i], "/")) continue;
        if (umount2(mnts[i], 0) == 0)               { say("[pn-init] shutdown: unmounted "); sayn(mnts[i]); }
        else if (umount2(mnts[i], MNT_DETACH) == 0) { say("[pn-init] shutdown: detached ");  sayn(mnts[i]); }
        else                                        { say("[pn-init] shutdown: could not unmount "); sayn(mnts[i]); }
    }
    if (mount((const char*)0, "/", (const char*)0, MS_REMOUNT|MS_RDONLY, (const char*)0) == 0)
        sayn("[pn-init] shutdown: / remounted read-only (journal clean for the next boot)");
    else
        sayn("[pn-init] shutdown: / remount-ro failed (a writer is still open) -- sync()ed; journal replays next boot");
    sync();
}

static void do_shutdown(int poweroff){
    sayn(poweroff
         ? "[pn-init] shutdown: poweroff requested via SIGUSR1 -> orderly stop (reverse registration order)"
         : "[pn-init] shutdown: reboot requested via SIGTERM/Ctrl-Alt-Del -> orderly stop (reverse registration order)");
    wd_disarm();
    stop_services_reverse();
    sayn("[pn-init] shutdown: services down -> TERM to remaining processes");
    kill(-1, SIGTERM);
    for (int t = 0; t < KILLALL_GRACE_S * 10; t++){
        reap_pending();
        struct timespec ts = {0, 100*1000*1000}; nanosleep(&ts, (void*)0);
    }
    kill(-1, SIGKILL); reap_pending();
    plog_reason(poweroff
        ? "[pn-init] poweroff -- RESET-REASON: shutdown requested via SIGUSR1 (orderly poweroff)"
        : "[pn-init] reboot -- RESET-REASON: shutdown requested via SIGTERM/Ctrl-Alt-Del (orderly reboot)");
    if (g_plog_fd >= 0){ close(g_plog_fd); g_plog_fd = -1; }
    fs_teardown();
    reboot(poweroff ? LINUX_REBOOT_CMD_POWER_OFF : LINUX_REBOOT_CMD_RESTART);

    if (errno == EPERM || cmdline_has("pn.container")){
        sayn("[pn-init] shutdown: reboot(2) nicht erlaubt (Container) -> PID1 beendet sich, das ist hier das Ausschalten");
        _exit(0);
    }
    sayn("[pn-init] shutdown: reboot(2) FAILED -- box left halted-ish; manual power cycle needed");
}

#ifdef PN_INIT_TEST
static int find_env(const svc_t *s, const char *key, const char **valout){
    if (!s->envp) return 0;
    size_t kl = strlen(key);
    for (int i = 0; s->envp[i]; i++)
        if (!strncmp(s->envp[i], key, kl) && s->envp[i][kl] == '='){
            if (valout) *valout = s->envp[i] + kl + 1;
            return 1;
        }
    return 0;
}
static const svc_t *find_svc(const char *name){
    for (int i = 0; i < NSVC; i++) if (!strcmp(SVCS[i].name, name)) return &SVCS[i];
    return (const svc_t*)0;
}
static int g_tfail = 0;
#define PN_STR_(x) #x
#define PN_STR(x)  PN_STR_(x)
#define T_OK(cond, msg) do{ if (cond){ printf("  PASS  %s\n", msg); } \
    else { printf("  FAIL  %s\n", msg); g_tfail++; } }while(0)
#define T_STREQ(got, want, msg) do{ const char *_g=(got), *_w=(want); \
    if (_g && !strcmp(_g,_w)){ printf("  PASS  %s\n", msg); } \
    else { printf("  FAIL  %s  (got=\"%s\" want=\"%s\")\n", msg, _g?_g:"(null)", _w); g_tfail++; } }while(0)

static int file_has_line(const char *path, const char *line){
    int fd = open(path, O_RDONLY); if (fd < 0) return 0;
    static char buf[8192]; ssize_t n = read(fd, buf, sizeof buf - 1); close(fd);
    if (n <= 0) return 0;
    buf[n] = 0;
    size_t ll = strlen(line);
    for (char *p = buf; *p; ){
        char *nl = strchr(p, '\n'); size_t len = nl ? (size_t)(nl - p) : strlen(p);
        if (len == ll && !strncmp(p, line, ll)) return 1;
        if (!nl) break;
        p = nl + 1;
    }
    return 0;
}

static void test_respawn_env(void){
    printf("\n== pn-init RESPAWN env-application test (start_svc -> build_envp, boot == respawn) ==\n");
    const char *EF  = "/tmp/pn-init.test.envfile";
    const char *OUT = "/tmp/pn-init.test.respawn.env";
    int ef = open(EF, O_WRONLY|O_CREAT|O_TRUNC, 0644);
    if (ef >= 0){ static const char c[] =
        "# throwaway non-secret envfile (models envfile=/etc/brainbox/{pnd,secrets}.env)\n"
        "TEST_FILE_KEY=filevalue\n"
        "export EXPORT_ME=xyz\n";
        (void)!write(ef, c, sizeof c - 1); close(ef); }

    static char *ENVENTRIES[] = { "TEST_INLINE_KEY=inlinevalue",
                                  "TEST_SPACED=claude -p --model {model}", (char*)0 };
    static char cmd[128];
    snprintf(cmd, sizeof cmd, "env > %s 2>/dev/null; exit 0", OUT);
    static char *ARGV[] = { "/bin/sh", "-c", cmd, (char*)0 };

    svc_t s; memset(&s, 0, sizeof s);
    s.name = "envtest"; s.enabled = 1; s.backoff = BACKOFF_MIN_S;
    s.uid = 0; s.envp = ENVENTRIES; s.envfile = EF; s.argv = ARGV;

    for (int life = 1; life <= 2; life++){
        unlink(OUT);
        s.pid = 0;
        start_svc(&s);
        if (s.pid > 0){ int st; (void)waitpid(s.pid, &st, 0); }
        const char *tag = (life == 1) ? "FIRST-start" : "RESPAWN";
        char m[112];
        snprintf(m,sizeof m,"[%s] inline env= TEST_INLINE_KEY present",       tag); T_OK(file_has_line(OUT,"TEST_INLINE_KEY=inlinevalue"), m);
        snprintf(m,sizeof m,"[%s] inline env= value WITH SPACES preserved",   tag); T_OK(file_has_line(OUT,"TEST_SPACED=claude -p --model {model}"), m);
        snprintf(m,sizeof m,"[%s] envfile= key TEST_FILE_KEY applied",        tag); T_OK(file_has_line(OUT,"TEST_FILE_KEY=filevalue"), m);
        snprintf(m,sizeof m,"[%s] envfile= 'export ' stripped (EXPORT_ME)",   tag); T_OK(file_has_line(OUT,"EXPORT_ME=xyz"), m);
    }
    unlink(OUT); unlink(EF);
}

static svc_t *find_svc_mut(const char *name){
    for (int i = 0; i < NSVC; i++) if (!strcmp(SVCS[i].name, name)) return &SVCS[i];
    return (svc_t*)0;
}

static void write_conf(const char *body){
    int fd = open(CONF_PATH, O_WRONLY|O_CREAT|O_TRUNC, 0644);
    if (fd < 0){ perror("write_conf"); return; }
    (void)!write(fd, body, strlen(body));
    close(fd);
}

static void test_cmdline_extra(void){
    printf("-- Schalter aus /etc/pn-init.cmdline --\n");
    unlink(CMDLINE_EXTRA_PATH);
    T_OK(!cmdline_has("pn.testschalter"),
         "ohne Datei: unbekannter Schalter bleibt unbekannt");
    int vor = cmdline_int("pn.testzahl=", 42);
    T_OK(vor == 42, "ohne Datei: Zahlenschalter behaelt seinen Vorgabewert");

    FILE *f = fopen(CMDLINE_EXTRA_PATH, "w");
    T_OK(f != 0, "Wegwerf-Schalterdatei anlegbar");
    if (!f) return;

    fprintf(f, "pn.testschalter\npn.testzahl=7\npn.testtext=abc\n");
    fclose(f);

    T_OK(cmdline_has("pn.testschalter"), "mit Datei: Schalter wird erkannt");
    T_OK(cmdline_int("pn.testzahl=", 42) == 7, "mit Datei: Zahlenschalter wirkt (7 statt 42)");
    char buf[64] = {0};
    T_OK(cmdline_str("pn.testtext=", buf, sizeof buf) && !strcmp(buf, "abc"),
         "mit Datei: Textschalter wird bis zum Zeilenende gelesen, nicht darueber hinaus");

    T_OK(!cmdline_has("pn.gibtesnicht"), "erfundener Schalter bleibt auch mit Datei unbekannt");

    unlink(CMDLINE_EXTRA_PATH);
    T_OK(!cmdline_has("pn.testschalter"), "nach dem Loeschen ist der Schalter wieder weg");
}

static void test_publish(void){
    printf("\n== pn-init veroeffentlicht seine Lage (/run/pn-init) ==\n");
    (void)mkdir(PNRUN_DIR, 0755);

    write_conf("a|sacred|/bin/true one\nb||/bin/true two\n");
    T_OK(load_conf() == 2, "Ausgangs-Konfiguration mit 2 Diensten geladen");
    { svc_t *a = find_svc_mut("a"); if (a){ a->pid = 4242; a->restarts = 3; } }
    publish_state();

    T_OK(file_has_line(PNRUN_DIR "/config", "cap " PN_STR(CONF_MAX_SVC)),
         "config nennt den einkompilierten Deckel");
    T_OK(file_has_line(PNRUN_DIR "/config", "active 2"),  "config nennt die Zahl gefahrener Dienste");
    T_OK(file_has_line(PNRUN_DIR "/config", "ignored 0"), "ohne Ueberlauf: ignored 0");
    T_OK(file_has_line(PNRUN_DIR "/config", "truncated 0"), "ohne Abschnitt: truncated 0");
    T_OK(file_has_line(PNRUN_DIR "/services", "a 4242 0 3 running"),
         "services nennt Name, PID, Neustarts und Zustand des laufenden Dienstes");
    T_OK(file_has_line(PNRUN_DIR "/services", "b 0 0 0 pending"),
         "ein noch nicht gestarteter Dienst steht als pending drin (nicht als running)");

    {
        static char body[CONF_MAX_SVC * 40 + 4096];
        size_t o = 0;
        for (int i = 0; i < CONF_MAX_SVC + 1; i++)
            o += (size_t)snprintf(body + o, sizeof body - o, "d%d||/bin/true %d\n", i, i);
        write_conf(body);
        T_OK(load_conf() == CONF_MAX_SVC, "ein Dienst ueber dem Deckel -> Tabelle bleibt beim Deckel");
        publish_state();
        T_OK(file_has_line(PNRUN_DIR "/config", "ignored 1"),
             "⛔ der ueberzaehlige Dienst wird BEZIFFERT, nicht nur ins Bootlog geraunt");
        T_OK(!file_has_line(PNRUN_DIR "/services", "d" PN_STR(CONF_MAX_SVC) " 0 0 0 pending"),
             "und er steht NICHT in der Dienstliste — PID 1 behauptet nicht, ihn zu fahren");
    }

    write_conf("a|sacred|/bin/true one\nb||/bin/true two\n");
    (void)load_conf();
}

static void test_reload(void){
    printf("\n== pn-init SIGHUP-reload (P1) ==\n");
    write_conf("a|sacred|/bin/true one\nb||/bin/true two\n");
    T_OK(load_conf() == 2, "Ausgangs-Konfiguration mit 2 Diensten geladen");
    int slot_before = g_conf_slot;
    svc_t *a = find_svc_mut("a");
    if (a){ a->pid = 4242; a->restarts = 3; a->backoff = 8; }

    T_OK(do_reload() == 0, "unveraenderte conf -> 0 Aenderungen (idempotent)");
    T_OK(NSVC == 2, "Dienstzahl unveraendert");
    T_OK(g_conf_slot != slot_before, "Slot getauscht -> Doppelpuffer ist wirklich aktiv");
    a = find_svc_mut("a");
    T_OK(a && a->pid == 4242 && a->restarts == 3 && a->backoff == 8,
         "laufender Dienst behaelt pid/restarts/backoff (kein heimlicher Neustart)");

    T_STREQ(a && a->argv ? a->argv[1] : 0, "one", "argv des ueberlebenden Dienstes bleibt gueltig");

    write_conf("a|sacred|/bin/true one\nb||/bin/true two\nc||/bin/true three\n");
    T_OK(do_reload() == 1, "neuer Dienst -> genau 1 Aenderung");
    T_OK(NSVC == 3 && find_svc("c") != 0, "c steht in der Tabelle");
    a = find_svc_mut("a");
    T_OK(a && a->pid == 4242, "bestehender Dienst blieb dabei unangetastet");
    { const svc_t *c = find_svc("c"); T_OK(c && c->next_try == 0, "neuer Dienst startet im naechsten Tick"); }

    write_conf("a|sacred|/bin/true CHANGED\nb||/bin/true two\nc||/bin/true three\n");
    a = find_svc_mut("a"); if (a) a->pid = 0;
    T_OK(do_reload() == 1, "geaenderte argv -> 1 Aenderung");
    a = find_svc_mut("a");
    T_STREQ(a && a->argv ? a->argv[1] : 0, "CHANGED", "neue argv ist aktiv");
    T_OK(a && a->next_try == 0, "geaenderter Dienst wartet nicht hinter altem Backoff");

    write_conf("a|sacred|/bin/true CHANGED\nc||/bin/true three\n");
    T_OK(do_reload() == 1, "entfallener Dienst -> 1 Aenderung");
    T_OK(NSVC == 2 && find_svc("b") == 0, "b ist aus der Tabelle verschwunden");

    write_conf("# nur ein Kommentar, kein Dienst\n");
    T_OK(do_reload() == 0, "leere/unbrauchbare conf -> 0 Aenderungen");
    T_OK(NSVC == 2 && find_svc("a") != 0, "laufende Konfiguration bleibt vollstaendig gueltig");
    unlink(CONF_PATH);
    T_OK(do_reload() == 0 && NSVC == 2, "fehlende conf-Datei aendert ebenfalls nichts");
    T_STREQ(find_svc("a") ? find_svc("a")->argv[1] : 0, "CHANGED",
            "auch nach zwei Fehlversuchen zeigen die argv noch auf gueltigen Speicher");
}

int main(void){
    printf("== pn-init load_conf() unit test (env= space preservation) ==\n");

    static const char CONF[] =
        "# unit-test conf\n"
        "sshd|sacred|/usr/sbin/sshd -D -e\n"
        "pnd|pnd sacred user=1000 pndsock=/run/user/1000/pnd.sock "
            "env=PN_BATCH_HIGH=1500 env=PN_MEM_FLOOR=1500 env=PN_MAX_CONCURRENT=4|"
            "/usr/lib/brainarbeit/run-engine pnd --serve --sock /run/user/1000/pnd.sock\n"
        "pn-llmd|sacred user=1000 env=PN_LLM_POOL=2 env=PN_LLM_MODEL=sonnet "
            "env=PN_LLM_CMD=claude -p --model {model}|"
            "/usr/lib/brainarbeit/run-engine pn-llmd --serve\n";
    int fd = open(CONF_PATH, O_WRONLY|O_CREAT|O_TRUNC, 0644);
    if (fd < 0){ perror("open conf"); return 2; }
    if (write(fd, CONF, sizeof CONF - 1) != (ssize_t)(sizeof CONF - 1)){ perror("write conf"); return 2; }
    close(fd);

    int n = load_conf();
    T_OK(n == 3, "load_conf parsed 3 services");

    const svc_t *llmd = find_svc("pn-llmd");
    T_OK(llmd != 0, "pn-llmd service present");
    if (llmd){
        const char *v = 0;

        T_OK(find_env(llmd, "PN_LLM_CMD", &v), "pn-llmd has PN_LLM_CMD env");
        T_STREQ(v, "claude -p --model {model}", "PN_LLM_CMD keeps spaces (not truncated to 'claude')");

        T_OK(find_env(llmd, "PN_LLM_POOL", &v) && !strcmp(v,"2"),    "pn-llmd PN_LLM_POOL=2");
        T_OK(find_env(llmd, "PN_LLM_MODEL",&v) && !strcmp(v,"sonnet"),"pn-llmd PN_LLM_MODEL=sonnet");

        T_OK(!find_env(llmd, "-p", 0) && !find_env(llmd, "--model", 0), "no stray -p/--model env leaked");
        T_OK(llmd->sacred == 1 && llmd->uid == 1000, "pn-llmd flags: sacred + user=1000 intact");

        T_STREQ(llmd->argv ? llmd->argv[0] : 0, "/usr/lib/brainarbeit/run-engine", "pn-llmd argv0 intact");
        T_STREQ(llmd->argv && llmd->argv[1] ? llmd->argv[1] : 0, "pn-llmd", "pn-llmd argv1 intact");
    }

    const svc_t *pnd = find_svc("pnd");
    T_OK(pnd != 0, "pnd service present");
    if (pnd){
        const char *v = 0;
        T_OK(find_env(pnd,"PN_BATCH_HIGH",&v)&&!strcmp(v,"1500"),   "pnd PN_BATCH_HIGH=1500");
        T_OK(find_env(pnd,"PN_MEM_FLOOR",&v)&&!strcmp(v,"1500"),    "pnd PN_MEM_FLOOR=1500");
        T_OK(find_env(pnd,"PN_MAX_CONCURRENT",&v)&&!strcmp(v,"4"),  "pnd PN_MAX_CONCURRENT=4");
        T_OK(pnd->is_pnd && pnd->sacred && pnd->uid==1000,         "pnd flags: pnd+sacred+user=1000");
        T_STREQ(g_pnd_sock, "/run/user/1000/pnd.sock", "pndsock= flag applied");
    }
    unlink(CONF_PATH);

    test_respawn_env();
    test_publish();
    test_reload();

    test_cmdline_extra();

    printf("== %s ==\n", g_tfail ? "FAIL" : "ALL PASS");
    return g_tfail ? 1 : 0;
}
#else
int main(void){

    if (getpid() != 1){
        static const char msg[] =
            "pn-init: refusing to run — not PID 1.\n"
            "This is the system's init (PID1) binary; running it on a live box would fight the\n"
            "real init and can REBOOT the host. Service control: use `pnctl` (list/status/restart).\n";
        (void)!write(2, msg, sizeof msg - 1);
        return 2;
    }

    mkdir("/dev",0755);
    mount("devtmpfs","/dev","devtmpfs",MS_NOSUID,(void*)0);
    int cfd = open("/dev/console", O_RDWR|O_NOCTTY);
    if (cfd >= 0){ dup2(cfd,0); dup2(cfd,1); dup2(cfd,2); if (cfd>2) close(cfd); }

    sayn("");
    sayn("[pn-init] v1 — PID1 up (supervision tree + tiered watchdog)");

    #ifndef SCHED_RESET_ON_FORK
    #define SCHED_RESET_ON_FORK 0x40000000
    #endif
    { struct sched_param sp; memset(&sp,0,sizeof sp); sp.sched_priority = 1;
      long r = syscall(SYS_sched_setscheduler, 0, SCHED_FIFO | SCHED_RESET_ON_FORK, &sp);
      if (r == 0) sayn("[pn-init] PID1 scheduling: SCHED_FIFO prio 1 + RESET_ON_FORK (only PID1 is RT; children revert to SCHED_OTHER so cpu.max stays effective)");
      else {
          say("[pn-init] WARN: SCHED_FIFO unavailable errno="); sayd((unsigned)errno);
          sayn(" -> fallback nice(-20) (PID1 still preferred under contention; children reset to nice 0)");

          (void)!nice(-20);
      } }

    mount_one("proc",    "/proc",          "proc",    MS_NOSUID|MS_NOEXEC|MS_NODEV);
    mount_one("sysfs",   "/sys",           "sysfs",   MS_NOSUID|MS_NOEXEC|MS_NODEV);
    mount_one("tmpfs",   "/run",           "tmpfs",   MS_NOSUID|MS_NODEV);
    mount_one("tmpfs",   "/tmp",           "tmpfs",   MS_NOSUID|MS_NODEV);
    mount_one("cgroup2", "/sys/fs/cgroup", "cgroup2", MS_NOSUID|MS_NOEXEC|MS_NODEV);
    sayn("[pn-init] filesystems mounted (proc sys run tmp cgroup2)");

    { char sdev[64]; if (cmdline_str("pn.swapdev=", sdev, sizeof sdev)) try_swapon(sdev); }

    cg_setup_slices();

    int fullsystem = detect_fullsystem();
    if (fullsystem){
        sayn("[pn-init] full-system mode: real root detected -> bringing up the whole host");
        remount_root_rw();
        udev_start();
        fixup_dev_perms();
        mount_fstab();

        if (cg_ready){
            unsigned long sw0 = g_swaptotal;
            g_swaptotal = meminfo_bytes("SwapTotal:");
            if (g_swaptotal > sw0){
                sayn("[pn-init] cgroup: fstab swap now live -> re-sizing caps to credit usable swap");
                cg_apply_caps();
            }
        }
        mount_runtime_extras();
        apply_hostname();
        net_up();
    } else {
        sayn("[pn-init] initramfs/stub mode: skipping full-system bring-up (use pn.fullsystem to force)");
    }

    struct sigaction sa; memset(&sa,0,sizeof sa);
    sa.sa_handler = on_sig; sigemptyset(&sa.sa_mask); sa.sa_flags = 0;
    sigaction(SIGTERM,&sa,(void*)0); sigaction(SIGINT,&sa,(void*)0);
    sigaction(SIGUSR1,&sa,(void*)0); sigaction(SIGCHLD,&sa,(void*)0);
    sigaction(SIGHUP,&sa,(void*)0);
    signal(SIGPIPE, SIG_IGN);
    reboot(LINUX_REBOOT_CMD_CAD_OFF);

    load_conf();

    if (cmdline_has("pn.poisonpnd")){
        sayn("[pn-init] TEST MODE: pnd poisoned (expect give-up -> reset)");
        for (int i=0;i<NSVC;i++) if (SVCS[i].is_pnd) SVCS[i].argv = A_PND_POISON;
    }
    if (cmdline_has("pn.churn")){
        sayn("[pn-init] TEST MODE: reaper churn + zombie watch (soak)");
        for (int i=0;i<NSVC;i++){
            if (!strcmp(SVCS[i].name,"churn") || !strcmp(SVCS[i].name,"zcheck")) SVCS[i].enabled = 1;
            if (!strcmp(SVCS[i].name,"portal")) SVCS[i].enabled = 0;
        }
    }

    if (cmdline_has("pn.miscstorm")){
        sayn("[pn-init] TEST MODE: pn-misc memory storm (expect cgroup-OOM inside pn-misc; box lives)");
        for (int i=0;i<NSVC;i++){ if (!strcmp(SVCS[i].name,"miscstorm")||!strcmp(SVCS[i].name,"miscwitness")) SVCS[i].enabled=1;
            if (!strcmp(SVCS[i].name,"portal")) SVCS[i].enabled=0; }
    }
    if (cmdline_has("pn.batchstorm")){
        sayn("[pn-init] TEST MODE: pn-batch memory storm (expect cgroup-OOM inside pn-batch; box lives)");
        for (int i=0;i<NSVC;i++){ if (!strcmp(SVCS[i].name,"batchstorm")) SVCS[i].enabled=1; }
    }
    if (cmdline_has("pn.critstorm")){
        sayn("[pn-init] TEST MODE: pn-critical memory storm (expect LEAF cgroup-OOM inside pn-critical/critstorm; sacred co-tenant critwitness SURVIVES; box lives)");
        for (int i=0;i<NSVC;i++){ if (!strcmp(SVCS[i].name,"critstorm")||!strcmp(SVCS[i].name,"critwitness")) SVCS[i].enabled=1; }
    }
    if (cmdline_has("pn.bothstorm")){
        sayn("[pn-init] TEST MODE: pn-batch + pn-misc concurrent fill (expect NO global OOM; box lives)");
        for (int i=0;i<NSVC;i++){ if (!strcmp(SVCS[i].name,"batchstorm")||!strcmp(SVCS[i].name,"miscstorm")) SVCS[i].enabled=1;
            if (!strcmp(SVCS[i].name,"portal")) SVCS[i].enabled=0; }
    }
    if (cmdline_has("pn.cpustorm")){
        sayn("[pn-init] TEST MODE: CPU busy-loop storm in pn-batch (cpu.max must cap it; watchdog still pets; critical responsive)");
        for (int i=0;i<NSVC;i++){ if (!strcmp(SVCS[i].name,"cpustorm")) SVCS[i].enabled=1; }
    }
    if (cmdline_has("pn.critcpustorm")){
        sayn("[pn-init] TEST MODE: CPU busy-loop storm in the UNCAPPED pn-critical tier (PID1 SCHED_FIFO must keep petting; NO spurious reset)");
        for (int i=0;i<NSVC;i++){ if (!strcmp(SVCS[i].name,"critcpustorm")) SVCS[i].enabled=1; }
    }

    int crashmax = cmdline_int("pn.crashmax=", CRASHLOOP_DEFAULT);
    unsigned bootc = bump_bootcount();
    int recovery = 0;
    if (bootc){ say("[pn-init] boot #"); sayd(bootc); say(" of "); sayd((unsigned)crashmax); sayn(" before crash-loop escalation"); }

    if (fullsystem) plog_open(bootc);
    if (bootc && bootc >= (unsigned)crashmax){
        recovery = 1;
        sayn("[pn-init] !!! CRASH-LOOP DETECTED -> RECOVERY MODE: not resetting; sshd stays up; awaiting a human !!!");
    }

    int recovery_minimal = cmdline_has("pn.recovery");
    if (recovery_minimal){
        recovery = 1;
        sayn("[pn-init] RECOVERY MODE (pn.recovery): minimal bring-up — sacred services only, no auto-reset");
    }

    if (g_caps_incomplete || !g_caps_enforced){
        if (cmdline_has("pn.allow_uncapped") && !cmdline_has("pn.require_caps")){
            sayn("[pn-init] cgroup: caps INCOMPLETE but pn.allow_uncapped set -> proceeding with full (ungoverned) bring-up by operator override");
        } else {
            sayn("[pn-init] cgroup: never-OOM caps NOT enforced -> FAIL-CLOSED: sacred-only bring-up (workload tiers held back ungoverned; box reachable via sshd for repair)");
            recovery_minimal = 1;
        }
    }
    if (cmdline_has("pn.require_caps") && !g_caps_enforced){
        sayn("[pn-init] cgroup: pn.require_caps=1 and caps NOT enforced -> STRICT fail-closed (sacred-only)");
        recovery_minimal = 1;
    }

    mkdir("/run/pn-init", 0755);
    if (g_caps_incomplete || !g_caps_enforced){
        int mf = open("/run/pn-init/caps_incomplete", O_WRONLY|O_CREAT|O_TRUNC, 0644);
        if (mf >= 0){ (void)!write(mf, "1\n", 2); close(mf); }
    } else {
        int mf = open("/run/pn-init/caps_enforced", O_WRONLY|O_CREAT|O_TRUNC, 0644);
        if (mf >= 0){ (void)!write(mf, "1\n", 2); close(mf); }
    }

    { int mf = open("/run/pn-init/shutdown.v2", O_WRONLY|O_CREAT|O_TRUNC, 0644);
      if (mf >= 0){ static const char m[] = "orderly reverse-order stop + remount-ro\n";
                    (void)!write(mf, m, sizeof m - 1); close(mf); } }

    { char *const m[] = {"/bin/busybox","insmod","/i6300esb.ko",(char*)0}; run_sync(m); }
    wd_open();

    integrity_sweep();

    for (int i=0;i<NSVC;i++)
        if (SVCS[i].enabled && (!recovery_minimal || SVCS[i].sacred)){
            start_svc(&SVCS[i]);

            #define ONESHOT_WAIT_S 90
            if (SVCS[i].oneshot && SVCS[i].pid > 0){
                int st; pid_t r = 0; time_t t0 = time(0);
                while ((r = waitpid(SVCS[i].pid, &st, WNOHANG)) == 0){
                    wd_pet();
                    if (time(0) - t0 > ONESHOT_WAIT_S){
                        say("[pn-init] "); say(SVCS[i].name);
                        sayn(" (oneshot) exceeded bring-up bound -> continuing (fail-open, still running)");
                        break;
                    }
                    struct timespec ts = {0, 100*1000*1000}; nanosleep(&ts, 0);
                }
                if (r == SVCS[i].pid){
                    SVCS[i].done = 1; SVCS[i].pid = 0; SVCS[i].next_try = (time_t)-1;
                    say("[pn-init] "); say(SVCS[i].name); sayn(" (oneshot) completed -> next service");
                }
            }
        }

    publish_state();

    if (cmdline_has("pn.sig=usr1")){ char *const a[]={"/bin/busybox","sh","-c","sleep 12; echo '[sigtest] sending SIGUSR1 to PID1'; kill -USR1 1",(char*)0}; spawn_async(a); }
    if (cmdline_has("pn.sig=term")){ char *const a[]={"/bin/busybox","sh","-c","sleep 12; echo '[sigtest] sending SIGTERM to PID1'; kill -TERM 1",(char*)0}; spawn_async(a); }

    if (cmdline_has("pn.cgdump")){ char *const a[]={"/bin/busybox","sh","-c",
        "sleep 6; cd /sys/fs/cgroup; "
        "echo \"[cgdump] caps_enforced=$(cat /run/pn-init/caps_enforced 2>/dev/null) caps_incomplete=$(cat /run/pn-init/caps_incomplete 2>/dev/null)\"; "

        "echo \"[cgdump] pn-init/config: $(cat /run/pn-init/config 2>/dev/null | tr '\\n' ' ')\"; "
        "cat /run/pn-init/services 2>/dev/null | while read -r z; do echo \"[cgdump]   SVC $z\"; done; "
        "for s in pn-critical.slice pn-batch.slice pn-misc.slice; do "
        "  echo \"[cgdump] $s memory.max=$(cat $s/memory.max 2>&1) memory.high=$(cat $s/memory.high 2>&1) cpu.max=$(cat $s/cpu.max 2>&1) io.max=$(cat $s/io.max 2>/dev/null | tr '\\n' ' ') oom.group=$(cat $s/memory.oom.group 2>&1) subtree=$(cat $s/cgroup.subtree_control 2>&1)\"; "
        "  for leaf in $s/*/; do [ -f \"$leaf/cgroup.procs\" ] && echo \"[cgdump]   LEAF $leaf procs=[$(cat $leaf/cgroup.procs 2>/dev/null | tr '\\n' ' ')] memory.max=$(cat $leaf/memory.max 2>&1) oom.group=$(cat $leaf/memory.oom.group 2>&1)\"; done; "
        "done; "
        "echo \"[cgdump] root subtree_control: $(cat cgroup.subtree_control 2>&1)\"; "
        "echo \"[cgdump] root cgroup.procs(should be ~PID1 + kthreads only): $(cat cgroup.procs 2>/dev/null | tr '\\n' ' ')\"",(char*)0}; spawn_async(a); }

    if (cmdline_has("pn.schedcheck")){ char *const a[]={"/bin/busybox","sh","-c",
        "sleep 7; "
        "pol(){ awk '{print $41}' /proc/$1/stat 2>/dev/null; }; "
        "echo \"[sched] PID1 policy=$(pol 1) (expect 1=SCHED_FIFO)\"; "
        "for s in pn-critical.slice pn-batch.slice pn-misc.slice; do "
        "  for leaf in /sys/fs/cgroup/$s/*/; do for pid in $(cat $leaf/cgroup.procs 2>/dev/null); do "
        "    nm=$(awk '{print $2}' /proc/$pid/stat 2>/dev/null); "
        "    echo \"[sched] $s leaf pid=$pid $nm policy=$(pol $pid) (expect 0=SCHED_OTHER)\"; "
        "  done; done; done",(char*)0}; spawn_async(a); }

    if (cmdline_has("pn.stormwatch")){ char *const a[]={"/bin/busybox","sh","-c",
        "cd /sys/fs/cgroup; n=0; while [ $n -lt 12 ]; do "
        "  for s in pn-critical.slice pn-batch.slice pn-misc.slice; do "
        "    cur=$(cat $s/memory.current 2>/dev/null); ev=$(cat $s/memory.events 2>/dev/null | tr '\\n' ' '); "
        "    thr=$(cat $s/cpu.stat 2>/dev/null | grep -E 'nr_throttled|throttled_usec' | tr '\\n' ' '); "
        "    echo \"[stormwatch] $s current=$cur events=[$ev] cpu.throttle=[$thr]\"; done; "
        "  free=$(cat /proc/meminfo | grep MemAvailable); echo \"[stormwatch] $free\"; "
        "  n=$((n+1)); sleep 4; done; echo '[stormwatch] DONE — box is still alive'",(char*)0}; spawn_async(a); }

    if (cmdline_has("pn.fakeudevd")){
        char *const ua[] = {"/bin/busybox","sh","-c","echo '[fakeudevd] up (foreground pid '$$')'; exec sleep 600",(char*)0};
        pid_t up = fork();
        if (up == 0){ child_drop_rt(); signal(SIGCHLD, SIG_DFL); execv(ua[0], ua); _exit(127); }
        if (up > 0){ cg_place_daemon(up, TIER_CRITICAL, "udevd"); sayn("[pn-init] TEST: fake udevd placed via the udevd governance path (foreground)"); }
    }

    const time_t boot_time = time(0);
    time_t pnd_bad_since = 0, gave_up_at = 0, last_canary = 0, healthy_since = 0;
    int    give_up = 0, canary_seen = 0;

    int    boot_grace_s = cmdline_int("pn.bootgrace=", BOOT_GRACE_S);
    int    giveup_win_s = cmdline_int("pn.giveup=",    GIVEUP_WIN_S);
    if (boot_grace_s < 1)  boot_grace_s = 1;
    if (giveup_win_s < 1)  giveup_win_s = 1;
    const int g_petlog = cmdline_has("pn.petlog");

    for (;;){
        struct timespec ts = { TICK_S, 0 };
        nanosleep(&ts, (void*)0);

        for (;;){
            int status; pid_t p = waitpid(-1, &status, WNOHANG);
            if (p <= 0) break;
            int matched = 0;
            for (int i=0;i<NSVC;i++){
                svc_t *s = &SVCS[i];
                if (s->pid == p){
                    s->pid = 0;
                    g_state_dirty = 1;
                    if (s->oneshot){

                        s->done = 1; s->next_try = (time_t)-1;
                        say("[pn-init] "); say(s->name); sayn(" (oneshot) completed -> reaped, not restarting");
                    } else {
                        if (time(0) - s->start_at >= STABLE_S) s->backoff = BACKOFF_MIN_S;
                        s->next_try = time(0) + s->backoff;
                        if (s->backoff < BACKOFF_MAX_S) s->backoff *= 2;
                        say("[pn-init] "); say(s->name); sayn(" exited -> reaped, will restart (backoff)");
                    }
                    matched = 1; break;
                }
            }
            if (!matched) sayn("[pn-init] reaped orphan zombie");
        }

        if (g_reload){
            g_reload = 0;
            if (!g_reboot && !g_poweroff) do_reload();
        }

        time_t now = time(0);

        if (!g_reboot && !g_poweroff)
            for (int i=0;i<NSVC;i++)
                if (SVCS[i].enabled && SVCS[i].pid == 0 && !(SVCS[i].oneshot && SVCS[i].done)
                    && (!recovery_minimal || SVCS[i].sacred)
                    && now >= SVCS[i].next_try) start_svc(&SVCS[i]);

        if (g_state_dirty && !g_reboot && !g_poweroff) publish_state();

        int healthy = pnd_ping();
        if (healthy && now - last_canary >= CANARY_EVERY_S){
            last_canary = now;
            if (pnd_canary()){
                if (!canary_seen){ canary_seen = 1; sayn("[pn-init] L2 canary OK (queue dispatches)"); }
            } else { sayn("[pn-init] L2 canary FAILED (queue not dispatching)"); healthy = 0; }
        }

        if (healthy){
            if (!healthy_since) healthy_since = now;
            if (bootc && now - healthy_since >= CLEAN_AFTER_S){
                clear_bootcount(); bootc = 0; sayn("[pn-init] stable -> crash-loop counter cleared");
            }
        } else healthy_since = 0;

        if (!recovery && now - boot_time >= boot_grace_s){
            if (!healthy){
                if (!pnd_bad_since) pnd_bad_since = now;
                if (!give_up && now - pnd_bad_since >= giveup_win_s){
                    give_up = 1; gave_up_at = now;

                    plog_reason("[pn-init] WATCHDOG: pnd unrecoverable -> SUSPEND pet -> reset imminent"
                                " -- RESET-REASON: pnd unhealthy > give-up window despite restarts (HW watchdog bite)");
                }
            } else {
                if (pnd_bad_since) sayn("[pn-init] pnd healthy again (kept petting across the restart)");
                pnd_bad_since = 0;

                if (give_up && wd_fd < 0){
                    give_up = 0; gave_up_at = 0;
                    plog_reason("[pn-init] WATCHDOG: pnd antwortet wieder VOR der Vollstreckung"
                                " -- RESET ABGESAGT (Begnadigung; Petting laeuft weiter)");
                }
            }
        }

        if (!give_up && !g_reboot && !g_poweroff){
            wd_pet();

            if (g_petlog){ say("[pn-init] PET t="); sayd((unsigned)(now - boot_time)); sayn("s (watchdog kept alive under load)"); }
        }

        if (give_up && wd_fd < 0 && now - gave_up_at >= WD_TIMEOUT_S){
            plog_reason("[pn-init] (no HW watchdog) software-reset fallback -> restart"
                        " -- RESET-REASON: software-reset fallback after give-up (no HW watchdog)");
            kill(-1, SIGKILL); sync(); reboot(LINUX_REBOOT_CMD_RESTART);
        }

        if (g_poweroff) do_shutdown(1);
        if (g_reboot)   do_shutdown(0);
    }
    return 0;
}
#endif
