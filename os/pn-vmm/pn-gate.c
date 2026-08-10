#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <stdarg.h>
#include <sched.h>
#include <pthread.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <poll.h>
#include <time.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>

#ifndef SECCOMP_SET_MODE_FILTER
#define SECCOMP_SET_MODE_FILTER 1
#endif
#ifndef SECCOMP_GET_NOTIF_SIZES
#define SECCOMP_GET_NOTIF_SIZES 3
#endif
#ifndef SECCOMP_FILTER_FLAG_NEW_LISTENER
#define SECCOMP_FILTER_FLAG_NEW_LISTENER (1UL << 3)
#endif
#ifndef SECCOMP_FILTER_FLAG_WAIT_KILLABLE_RECV
#define SECCOMP_FILTER_FLAG_WAIT_KILLABLE_RECV (1UL << 4)
#endif
#ifndef SECCOMP_USER_NOTIF_FLAG_CONTINUE
#define SECCOMP_USER_NOTIF_FLAG_CONTINUE (1UL << 0)
#endif
#ifndef SECCOMP_RET_ALLOW
#define SECCOMP_RET_ALLOW 0x7fff0000U
#endif
#ifndef SECCOMP_RET_USER_NOTIF
#define SECCOMP_RET_USER_NOTIF 0x7fc00000U
#endif
#ifndef AUDIT_ARCH_X86_64
#define AUDIT_ARCH_X86_64 0xC000003E
#endif
#ifndef SECCOMP_IOCTL_NOTIF_RECV
#define SECCOMP_IOCTL_NOTIF_RECV  _IOWR('!', 0, struct seccomp_notif)
#endif
#ifndef SECCOMP_IOCTL_NOTIF_SEND
#define SECCOMP_IOCTL_NOTIF_SEND  _IOWR('!', 1, struct seccomp_notif_resp)
#endif
#ifndef SECCOMP_IOCTL_NOTIF_ID_VALID
#define SECCOMP_IOCTL_NOTIF_ID_VALID _IOW('!', 2, __u64)
#endif

#ifndef __NR_pidfd_open
#define __NR_pidfd_open 434
#endif

static long sys_seccomp(unsigned op, unsigned flags, void *args) {
    return syscall(__NR_seccomp, op, flags, args);
}
static int sys_pidfd_open(pid_t pid, unsigned flags) {
    return (int)syscall(__NR_pidfd_open, pid, flags);
}

static char CTL_HOST[128] = "127.0.0.1";
static int  CTL_PORT = 8088;
static int  DEADLINE_MS = 2000;
static char LOGPATH[256] = "/tmp/pn-gate.log";
static __u16 NOTIF_SZ, RESP_SZ;

static const char *BOOTSTRAP_SKIP[] = {
    "tmux", "sh", "bash", "dash", "busybox", "env", "claude", "node", "python3", "python",
    "pn-gate", "pn_repl_launch.sh", "tee", NULL
};
static char EXTRA_SKIP[1024] = "";

static int    CONTINGENT_ON = 0;
static int    g_permits = 0;
static double g_last_refill = 0.0;
static double g_permits_expire = 0.0;
static double PERMIT_TTL = 1.0;
static pthread_mutex_t g_permit_lock = PTHREAD_MUTEX_INITIALIZER;

static const char *HEAVY_LIST[] = {
    "gcc","g++","cc","cc1","cc1plus","ld","collect2","make","cmake","ninja","rustc","cargo","go",
    "node","clang","clang++","ffmpeg","hifiasm","minimap2","samtools","bwa","spades.py","python3.heavy",
    "pn-heavy", NULL
};
static int is_heavy(const char *base) {
    for (int i=0; HEAVY_LIST[i]; i++) if (!strcmp(base, HEAVY_LIST[i])) return 1;
    return 0;
}

static void logmsg(const char *fmt, ...) {
    char buf[1024];
    struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
    int n = snprintf(buf, sizeof buf, "[%ld.%03ld] ", (long)ts.tv_sec, ts.tv_nsec/1000000);
    va_list ap; va_start(ap, fmt); n += vsnprintf(buf+n, sizeof(buf)-n, fmt, ap); va_end(ap);
    if (n < (int)sizeof buf - 1) { buf[n++]='\n'; buf[n]=0; }
    int fd = open(LOGPATH, O_WRONLY|O_CREAT|O_APPEND, 0600);
    if (fd >= 0) { ssize_t w = write(fd, buf, strlen(buf)); (void)w; close(fd); }
    ssize_t w2 = write(2, buf, strlen(buf)); (void)w2;
}

static int send_fd(int sock, int fd) {
    char cbuf[CMSG_SPACE(sizeof(int))] = {0};
    struct iovec io = { .iov_base = (void*)"x", .iov_len = 1 };
    struct msghdr msg = {0};
    msg.msg_iov = &io; msg.msg_iovlen = 1;
    msg.msg_control = cbuf; msg.msg_controllen = sizeof cbuf;
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(c), &fd, sizeof(int));
    return sendmsg(sock, &msg, 0);
}
static int recv_fd(int sock) {
    char cbuf[CMSG_SPACE(sizeof(int))] = {0}, d;
    struct iovec io = { .iov_base = &d, .iov_len = 1 };
    struct msghdr msg = {0};
    msg.msg_iov = &io; msg.msg_iovlen = 1;
    msg.msg_control = cbuf; msg.msg_controllen = sizeof cbuf;
    if (recvmsg(sock, &msg, 0) < 0) return -1;
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    if (!c || c->cmsg_type != SCM_RIGHTS) return -1;
    int fd; memcpy(&fd, CMSG_DATA(c), sizeof(int));
    return fd;
}

static int install_filter(void) {
    struct sock_filter f[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_execve,   2, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_execveat, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
    };
    struct sock_fprog prog = { .len = sizeof(f)/sizeof(f[0]), .filter = f };
    return sys_seccomp(SECCOMP_SET_MODE_FILTER,
                       SECCOMP_FILTER_FLAG_NEW_LISTENER | SECCOMP_FILTER_FLAG_WAIT_KILLABLE_RECV,
                       &prog);
}

static int read_new_path(pid_t pid, const struct seccomp_data *d, char *out, size_t outsz) {
    unsigned long uptr;
    if (d->nr == __NR_execveat) uptr = (unsigned long)d->args[1];
    else                        uptr = (unsigned long)d->args[0];
    out[0] = 0;
    if (!uptr) return -1;
    char mem[64]; snprintf(mem, sizeof mem, "/proc/%d/mem", (int)pid);
    int fd = open(mem, O_RDONLY);
    if (fd < 0) return -1;
    ssize_t n = pread(fd, out, outsz-1, (off_t)uptr);
    close(fd);
    if (n <= 0) { out[0]=0; return -1; }
    out[(n < (ssize_t)outsz) ? n : outsz-1] = 0;
    out[outsz-1] = 0;
    return 0;
}

static const char *basename_of(const char *p) {
    const char *b = strrchr(p, '/');
    return b ? b+1 : p;
}
static int in_skiplist(const char *base) {
    for (int i=0; BOOTSTRAP_SKIP[i]; i++) if (!strcmp(base, BOOTSTRAP_SKIP[i])) return 1;
    if (EXTRA_SKIP[0]) {
        char tmp[1024]; strncpy(tmp, EXTRA_SKIP, sizeof tmp -1); tmp[sizeof tmp-1]=0;
        for (char *t=strtok(tmp, ":"); t; t=strtok(NULL, ":")) if (!strcmp(base, t)) return 1;
    }
    return 0;
}

static int http_ctl(const char *json, int per_read_ms, char *resp, size_t rsz) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return -1;
    struct timeval tv = { .tv_sec = per_read_ms/1000, .tv_usec = (per_read_ms%1000)*1000 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    struct sockaddr_in a; memset(&a,0,sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons(CTL_PORT);
    if (inet_pton(AF_INET, CTL_HOST, &a.sin_addr) != 1) { close(s); return -1; }
    if (connect(s, (struct sockaddr*)&a, sizeof a) < 0) { close(s); return -1; }
    char req[1024];
    int n = snprintf(req, sizeof req,
        "POST /pn/ctl HTTP/1.1\r\nHost: pn\r\nContent-Type: application/json\r\n"
        "Content-Length: %zu\r\nConnection: close\r\n\r\n%s", strlen(json), json);
    if (write(s, req, n) != n) { close(s); return -1; }
    size_t got = 0; ssize_t k; int verdict = -1;
    while (got < rsz-1) {
        k = read(s, resp+got, rsz-1-got);
        if (k > 0) {
            got += k; resp[got] = 0;

            if (strstr(resp, "\"granted\":true")  || strstr(resp, "\"granted\": true"))  { verdict = 1; break; }
            if (strstr(resp, "\"granted\":false") || strstr(resp, "\"granted\": false")) { verdict = 0; break; }
            continue;
        }
        if (k == 0) break;
        break;
    }
    close(s);
    return verdict;
}

struct waiter { pid_t pid; char id[64]; };
static void *waiter_main(void *arg) {
    struct waiter *w = arg;
    int pfd = sys_pidfd_open(w->pid, 0);
    if (pfd >= 0) {
        struct pollfd pf = { .fd = pfd, .events = POLLIN };
        poll(&pf, 1, -1);
        close(pfd);
    } else {

        char pp[64]; snprintf(pp, sizeof pp, "/proc/%d", (int)w->pid);
        struct stat st;
        while (stat(pp, &st) == 0) { struct timespec ts={0,200*1000*1000}; nanosleep(&ts,NULL); }
    }
    char j[128], r[512];
    snprintf(j, sizeof j, "{\"op\":\"exec-release\",\"turn_id\":\"%s\"}", w->id);
    http_ctl(j, 1500, r, sizeof r);
    free(w);
    return NULL;
}

static unsigned long seq = 0;

static double now_s(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec/1e9; }

static void refill_contingent(void) {
    pthread_mutex_lock(&g_permit_lock);

    int fresh = (g_permits > 0) && (now_s() <= g_permits_expire);
    if (fresh || (now_s() - g_last_refill) < 0.05) { pthread_mutex_unlock(&g_permit_lock); return; }
    g_last_refill = now_s();
    pthread_mutex_unlock(&g_permit_lock);
    char r[512];
    int rc = http_ctl("{\"op\":\"exec-contingent\",\"track\":\"interactive\"}", 1500, r, sizeof r);
    (void)rc;
    int permits = 0;
    char *p = strstr(r, "\"permits\":");
    if (p) permits = atoi(p + 10);
    if (permits < 0) permits = 0;
    pthread_mutex_lock(&g_permit_lock);
    g_permits = permits;
    g_permits_expire = now_s() + PERMIT_TTL;
    pthread_mutex_unlock(&g_permit_lock);
}

static int take_permit(void) {
    int ok = 0;
    pthread_mutex_lock(&g_permit_lock);
    if (now_s() > g_permits_expire) g_permits = 0;
    if (g_permits > 0) { g_permits--; ok = 1; }
    pthread_mutex_unlock(&g_permit_lock);
    return ok;
}

static void exec_note(const char *argv0) {
    char j[256], r[256];
    snprintf(j, sizeof j, "{\"op\":\"exec-note\",\"argv0\":\"%s\"}", argv0);
    http_ctl(j, 800, r, sizeof r);
}

static void handle_notif(int lfd, struct seccomp_notif *req, struct seccomp_notif_resp *resp) {
    memset(resp, 0, RESP_SZ);
    resp->id = req->id;

    char path[512] = {0};
    int rp = read_new_path(req->pid, &req->data, path, sizeof path);
    const char *base = path[0] ? basename_of(path) : "?";
    if (getenv("PN_GATE_DEBUG"))
        logmsg("TRAP pid=%d nr=%d rp=%d path=%s base=%s", (int)req->pid, req->data.nr, rp, path, base);

    __u64 id = req->id;
    if (ioctl(lfd, SECCOMP_IOCTL_NOTIF_ID_VALID, &id) != 0) return;

    int is_proc_self = (strncmp(path, "/proc/", 6) == 0);
    if (path[0] == 0 || is_proc_self || in_skiplist(base)) {

        resp->flags = SECCOMP_USER_NOTIF_FLAG_CONTINUE;
        ioctl(lfd, SECCOMP_IOCTL_NOTIF_SEND, resp);
        return;
    }

    if (CONTINGENT_ON && !is_heavy(base)) {
        if (!take_permit()) refill_contingent();
        if (take_permit()) {
            resp->flags = SECCOMP_USER_NOTIF_FLAG_CONTINUE;
            ioctl(lfd, SECCOMP_IOCTL_NOTIF_SEND, resp);
            exec_note(base);
            return;
        }
    }

    char turn[64]; snprintf(turn, sizeof turn, "exec-%d-%lu", (int)req->pid, __atomic_add_fetch(&seq,1,__ATOMIC_RELAXED));
    char j[768], r[1024];
    snprintf(j, sizeof j,
        "{\"op\":\"exec-acquire\",\"turn_id\":\"%s\",\"pid\":%d,\"argv0\":\"%s\",\"klass\":\"interactive\"}",
        turn, (int)req->pid, base);
    int g = http_ctl(j, DEADLINE_MS, r, sizeof r);

    id = req->id;
    if (ioctl(lfd, SECCOMP_IOCTL_NOTIF_ID_VALID, &id) != 0) {

        char jr[128], rr[256];
        snprintf(jr, sizeof jr, "{\"op\":\"exec-release\",\"turn_id\":\"%s\"}", turn);
        http_ctl(jr, 1000, rr, sizeof rr);
        return;
    }

    if (g == 0) {
        resp->error = -EPERM; resp->flags = 0;
        ioctl(lfd, SECCOMP_IOCTL_NOTIF_SEND, resp);
        logmsg("exec DENY %s pid=%d", base, (int)req->pid);
        return;
    }

    if (g < 0) logmsg("exec FAIL-OPEN %s pid=%d (broker timeout/down)", base, (int)req->pid);

    if (g == 1) {
        struct waiter *w = malloc(sizeof *w);
        if (w) { w->pid = req->pid; strncpy(w->id, turn, sizeof w->id -1); w->id[sizeof w->id-1]=0;
                 pthread_t t; if (pthread_create(&t, NULL, waiter_main, w)==0) pthread_detach(t); else free(w); }
    }
    resp->flags = SECCOMP_USER_NOTIF_FLAG_CONTINUE;
    if (ioctl(lfd, SECCOMP_IOCTL_NOTIF_SEND, resp) != 0 && errno == ENOENT) {

        char jr[128], rr[256];
        snprintf(jr, sizeof jr, "{\"op\":\"exec-release\",\"turn_id\":\"%s\"}", turn);
        http_ctl(jr, 1000, rr, sizeof rr);
    }
}

static int g_lfd = -1;
static void *worker_main(void *unused) {
    (void)unused;
    struct seccomp_notif *req = calloc(1, NOTIF_SZ);
    struct seccomp_notif_resp *resp = calloc(1, RESP_SZ);
    for (;;) {
        memset(req, 0, NOTIF_SZ);
        if (ioctl(g_lfd, SECCOMP_IOCTL_NOTIF_RECV, req) < 0) {
            if (errno == EINTR || errno == ENOENT) continue;
            if (errno == ECANCELED) break;
            logmsg("RECV err errno=%d", errno);
            struct timespec ts={0,50*1000*1000}; nanosleep(&ts,NULL);
            continue;
        }
        handle_notif(g_lfd, req, resp);
    }
    free(req); free(resp);
    return NULL;
}

int main(int argc, char **argv) {

    int pi = 1;
    while (pi < argc && strcmp(argv[pi], "--")) pi++;
    if (pi < argc && !strcmp(argv[pi], "--")) pi++;
    else pi = 1;
    if (pi >= argc) { fprintf(stderr, "usage: pn-gate -- <payload argv...>\n"); return 2; }
    char **payload = &argv[pi];

    const char *base = getenv("ANTHROPIC_BASE_URL");
    const char *ctl  = getenv("PN_GATE_CTL");
    char hp[192] = {0};
    if (ctl && *ctl) strncpy(hp, ctl, sizeof hp -1);
    else if (base && strstr(base, "http://")) strncpy(hp, base+7, sizeof hp -1);
    if (hp[0]) {
        char *colon = strrchr(hp, ':');
        char *slash = strchr(hp, '/'); if (slash) *slash = 0;
        if (colon) { *colon = 0; CTL_PORT = atoi(colon+1); }
        strncpy(CTL_HOST, hp, sizeof CTL_HOST -1);
        if (!CTL_PORT) CTL_PORT = 8088;
    }
    if (getenv("PN_GATE_CONTINGENT")) CONTINGENT_ON = atoi(getenv("PN_GATE_CONTINGENT"));
    if (getenv("PN_GATE_PERMIT_TTL_MS")) PERMIT_TTL = atoi(getenv("PN_GATE_PERMIT_TTL_MS")) / 1000.0;
    if (getenv("PN_GATE_DEADLINE_MS")) DEADLINE_MS = atoi(getenv("PN_GATE_DEADLINE_MS"));
    if (getenv("PN_GATE_SKIP")) strncpy(EXTRA_SKIP, getenv("PN_GATE_SKIP"), sizeof EXTRA_SKIP -1);
    if (getenv("PN_GATE_LOG")) strncpy(LOGPATH, getenv("PN_GATE_LOG"), sizeof LOGPATH -1);

    struct seccomp_notif_sizes sz;
    if (sys_seccomp(SECCOMP_GET_NOTIF_SIZES, 0, &sz) == 0) { NOTIF_SZ = sz.seccomp_notif; RESP_SZ = sz.seccomp_notif_resp; }
    if (!NOTIF_SZ) NOTIF_SZ = sizeof(struct seccomp_notif);
    if (!RESP_SZ)  RESP_SZ  = sizeof(struct seccomp_notif_resp);

    int sp[2];
    if (socketpair(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0, sp) < 0) { perror("socketpair"); return 2; }
    signal(SIGCHLD, SIG_IGN);
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 2; }

    if (pid == 0) {

        close(sp[0]);
        if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) { perror("nnp"); _exit(3); }
        int lfd = install_filter();
        if (lfd < 0) { fprintf(stderr, "pn-gate: NEW_LISTENER failed errno=%d (kernel <5.0?)\n", errno); _exit(4); }
        if (send_fd(sp[1], lfd) < 0) { perror("send_fd"); _exit(5); }
        close(lfd); close(sp[1]);
        execvp(payload[0], payload);
        perror("execvp"); _exit(6);
    }

    close(sp[1]);
    g_lfd = recv_fd(sp[0]);
    close(sp[0]);
    if (g_lfd < 0) { logmsg("pn-gate: failed to receive listener fd"); return 7; }
    logmsg("pn-gate up: ctl=%s:%d deadline=%dms notif_sz=%u payload=%s",
           CTL_HOST, CTL_PORT, DEADLINE_MS, NOTIF_SZ, payload[0]);

    int NW = 4;
    pthread_t th[8];
    for (int i=0;i<NW;i++) pthread_create(&th[i], NULL, worker_main, NULL);

    for (;;) pause();
    return 0;
}
