#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>

static const char *g_pnd_sock;

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

int main(int argc, char **argv){
    if (argc < 2){ fprintf(stderr, "usage: %s <pnd.sock>\n", argv[0]); return 2; }
    g_pnd_sock = argv[1];
    int l1 = pnd_ping();
    int l2 = pnd_canary();
    printf("L1 ping->pong: %s\n", l1 ? "PASS" : "FAIL");
    printf("L2 canary->ok: %s\n", l2 ? "PASS" : "FAIL");
    return (l1 && l2) ? 0 : 1;
}
