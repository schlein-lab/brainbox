#define _GNU_SOURCE
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <sys/mman.h>

#define MAX_CHUNKS 4096

int main(int argc, char **argv){
    long chunk_mib = (argc > 1) ? strtol(argv[1], 0, 10) : 32;
    if (chunk_mib < 1) chunk_mib = 32;
    size_t chunk = (size_t)chunk_mib * 1024UL * 1024UL;
    long page = sysconf(_SC_PAGESIZE); if (page <= 0) page = 4096;
    unsigned long total_mib = 0;

    static char *blocks[MAX_CHUNKS];
    int nblk = 0;

    char msg[160];
    int l = snprintf(msg, sizeof msg,
        "[memhog] pid=%d growing HOT anon RSS in %ld MiB steps until my tier memory.max OOM-kills me\n",
        (int)getpid(), chunk_mib);
    (void)!write(1, msg, (size_t)l);

    for (;;){
        if (nblk < MAX_CHUNKS){
            char *p = mmap(0, chunk, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
            if (p != MAP_FAILED){
                for (size_t off = 0; off < chunk; off += (size_t)page) p[off] = (char)1;
                blocks[nblk++] = p;
                total_mib += (unsigned long)chunk_mib;
            }
        }

        for (int b = 0; b < nblk; b++)
            for (size_t off = 0; off < chunk; off += (size_t)page) blocks[b][off] ^= (char)0x5a;
        l = snprintf(msg, sizeof msg, "[memhog] hot working set ~%lu MiB\n", total_mib);
        (void)!write(1, msg, (size_t)l);
        usleep(120000);
    }
    return 0;
}
