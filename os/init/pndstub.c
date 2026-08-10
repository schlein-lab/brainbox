#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/un.h>

int main(int argc, char **argv){
    signal(SIGPIPE, SIG_IGN);
    const char *mode = argc > 1 ? argv[1] : "";
    const char *sock = getenv("PND_SOCK"); if (!sock) sock = "/run/pnd.sock";
    if (argc > 2) sock = argv[2];

    if (!strcmp(mode, "--poison")){ dprintf(1, "[pnd] poisoned, exiting\n"); return 1; }

    long deadline = 0;
    if (!strcmp(mode, "--crash-once") && access("/run/pnd.once", F_OK) != 0){
        int f = open("/run/pnd.once", O_CREAT|O_WRONLY, 0644); if (f >= 0) close(f);
        deadline = time(0) + 8;
    }

    unlink(sock);
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
    strncpy(a.sun_path, sock, sizeof a.sun_path - 1);
    if (bind(s, (struct sockaddr*)&a, sizeof a) != 0){ perror("[pnd] bind"); return 2; }
    listen(s, 16);
    dprintf(1, "[pnd] up (pid %d) sock=%s%s\n", getpid(), sock, deadline ? " [crash-once]" : "");

    for (;;){
        if (deadline && time(0) >= deadline){ dprintf(1, "[pnd] first life: simulated crash\n"); return 7; }
        struct pollfd p = { s, POLLIN, 0 };
        if (poll(&p, 1, 500) <= 0) continue;
        int c = accept(s, 0, 0); if (c < 0) continue;
        char buf[64]; ssize_t n = read(c, buf, sizeof buf - 1);
        if (n > 0){
            buf[n] = 0;
            if      (!strncmp(buf, "ping", 4))   (void)!write(c, "pong\n", 5);
            else if (!strncmp(buf, "canary", 6)) (void)!write(c, "ok\n", 3);
            else                                 (void)!write(c, "?\n", 2);
        }
        close(c);
    }
}
