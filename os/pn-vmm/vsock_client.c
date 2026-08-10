#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <linux/vm_sockets.h>

int main(void) {
    int fd = socket(AF_VSOCK, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); printf("PN_VSOCK_SOCKET_FAIL\n"); return 1; }
    struct sockaddr_vm addr;
    memset(&addr, 0, sizeof addr);
    addr.svm_family = AF_VSOCK;
    addr.svm_cid = 2;
    addr.svm_port = 1234;
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        perror("connect"); printf("PN_VSOCK_CONNECT_FAIL\n"); return 2;
    }
    printf("PN_VSOCK_CONNECTED\n"); fflush(stdout);
    const char *m = "ping-from-guest";
    if (write(fd, m, strlen(m)) < 0) perror("write");
    char buf[256];
    int n = (int)read(fd, buf, sizeof buf - 1);
    if (n > 0) { buf[n] = 0; printf("PN_VSOCK_ECHO=%s\n", buf); }
    else printf("PN_VSOCK_NOECHO n=%d\n", n);
    fflush(stdout);
    close(fd);
    return 0;
}
