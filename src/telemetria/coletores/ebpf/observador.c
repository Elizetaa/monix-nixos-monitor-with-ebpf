#define _GNU_SOURCE
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <mntent.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/vfs.h>
#include <time.h>
#include <unistd.h>
#include "metricas.h"

#define U64(value) ((unsigned long long)(value))
#define MAX_FILESYSTEMS 128

/* Objetos separados: a falha de um hook não desabilita as outras categorias. */
struct collector {
    const char *name;
    const char *object_name;
    const char *source;
    struct bpf_object *object;
    struct bpf_link *links[4];
    unsigned int link_count;
    char error[256];
};

static struct collector collectors[] = {
    { .name = "memory", .object_name = "memoria", .source = "ebpf:fexit/si_meminfo,si_mem_available" },
    { .name = "memory_events", .object_name = "paginas", .source = "ebpf:raw_tp/mm_page_alloc,mm_page_free,mm_page_free_batched" },
    { .name = "block_io", .object_name = "io", .source = "ebpf:raw_tp/block_rq_issue,block_rq_complete" },
    { .name = "temperature_zones", .object_name = "zonas", .source = "ebpf:raw_tp/thermal_temperature" },
    { .name = "gpu_events", .object_name = "drm", .source = "ebpf:raw_tp/drm_sched_job_queue,drm_sched_job_run,drm_sched_job_done" },
    { .name = "filesystems", .object_name = "filesystems", .source = "ebpf:fexit/fd_statfs;inventory:procfs:/proc/self/mounts" },
};
static volatile sig_atomic_t running = 1;
static int possible_cpus;

static void stop_handler(int number)
{
    (void)number;
    running = 0;
}

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now))
        return 0;
    return (uint64_t)now.tv_sec * 1000000000ULL + now.tv_nsec;
}

static void quote(const char *text)
{
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)text; *p; p++) {
        if (*p == '"' || *p == '\\')
            printf("\\%c", *p);
        else if (*p < 32)
            printf("\\u%04x", *p);
        else
            putchar(*p);
    }
    putchar('"');
}

/* Após begin_result o chamador imprime data e fecha o objeto. */
static void begin_result(struct collector *c, const char *status, const char *error)
{
    quote(c->name);
    printf(":{\"source\":");
    quote(c->source);
    printf(",\"status\":");
    quote(status);
    printf(",\"error\":");
    if (error)
        quote(error);
    else
        printf("null");
    printf(",\"data\":");
}

static void unavailable(struct collector *c, const char *error)
{
    begin_result(c, "error", error);
    printf("null}");
}

static void close_collector(struct collector *c)
{
    for (unsigned int i = 0; i < c->link_count; i++)
        bpf_link__destroy(c->links[i]);
    c->link_count = 0;
    bpf_object__close(c->object);
    c->object = NULL;
}

static void load_collector(struct collector *c, const char *directory)
{
    char path[PATH_MAX];
    int ret = snprintf(path, sizeof(path), "%s/%s.bpf.o", directory, c->object_name);
    if (ret < 0 || (size_t)ret >= sizeof(path)) {
        snprintf(c->error, sizeof(c->error), "Caminho do objeto muito longo");
        return;
    }
    c->object = bpf_object__open_file(path, NULL);
    long error = libbpf_get_error(c->object);
    if (!c->object || error) {
        c->object = NULL;
        snprintf(c->error, sizeof(c->error), "Abrir %s.bpf.o: %s", c->object_name,
                 strerror(error ? (int)-error : errno));
        return;
    }
    ret = bpf_object__load(c->object);
    if (ret) {
        snprintf(c->error, sizeof(c->error), "Carregar %s.bpf.o: %s", c->object_name, strerror(-ret));
        goto failed;
    }
    int owner = bpf_object__find_map_fd_by_name(c->object, "owner");
    if (owner >= 0) {
        __u32 key = 0, pid = getpid();
        if (bpf_map_update_elem(owner, &key, &pid, BPF_ANY)) {
            snprintf(c->error, sizeof(c->error), "Configurar PID: %s", strerror(errno));
            goto failed;
        }
    }
    struct bpf_program *program;
    bpf_object__for_each_program(program, c->object) {
        struct bpf_link *link = bpf_program__attach(program);
        error = libbpf_get_error(link);
        if (!link || error) {
            snprintf(c->error, sizeof(c->error), "Attach %s: %s",
                     bpf_program__section_name(program), strerror(error ? (int)-error : errno));
            goto failed;
        }
        if (c->link_count == sizeof(c->links) / sizeof(c->links[0])) {
            bpf_link__destroy(link);
            snprintf(c->error, sizeof(c->error), "Limite de programas por coletor");
            goto failed;
        }
        c->links[c->link_count++] = link;
    }
    return;
failed:
    close_collector(c);
}

static int read_percpu(int fd, void *total, size_t size)
{
    size_t stride = (size + 7) & ~(size_t)7;
    void *values = calloc((size_t)possible_cpus, stride);
    if (!values)
        return -1;
    int ret = bpf_map_lookup_elem(fd, &(__u32){0}, values);
    if (!ret) {
        memset(total, 0, size);
        for (int cpu = 0; cpu < possible_cpus; cpu++) {
            __u64 *words = (__u64 *)((char *)values + cpu * stride);
            for (size_t word = 0; word < size / sizeof(__u64); word++)
                ((__u64 *)total)[word] += words[word];
        }
    }
    free(values);
    return ret;
}

static void memory_result(struct collector *c)
{
    struct memory_sample sample;
    /* A leitura provoca as duas funções. Não interpretamos o texto de procfs. */
    char ignored[4096];
    int fd = bpf_object__find_map_fd_by_name(c->object, "samples");
    struct memory_sample empty = {};
    if (bpf_map_update_elem(fd, &(__u32){0}, &empty, BPF_ANY)) {
        unavailable(c, "Não foi possível iniciar a amostragem de memória");
        return;
    }
    int trigger = open("/proc/meminfo", O_RDONLY | O_CLOEXEC);
    if (trigger < 0) {
        unavailable(c, "Não foi possível provocar a amostragem por /proc/meminfo");
        return;
    }
    ssize_t length = read(trigger, ignored, sizeof(ignored));
    close(trigger);
    if (length <= 0 || bpf_map_lookup_elem(fd, &(__u32){0}, &sample) || !sample.timestamp_ns ||
        !sample.available_timestamp_ns || !sample.total_bytes || sample.free_bytes > sample.total_bytes ||
        sample.available_bytes > sample.total_bytes) {
        unavailable(c, "si_meminfo/si_mem_available não produziram uma amostra válida no mapa BPF");
        return;
    }
    begin_result(c, "available", NULL);
    printf("{\"total_bytes\":%llu,\"free_bytes\":%llu,\"shared_bytes\":%llu,\"buffer_bytes\":%llu,\"available_bytes\":%llu}}",
           U64(sample.total_bytes), U64(sample.free_bytes), U64(sample.shared_bytes),
           U64(sample.buffer_bytes), U64(sample.available_bytes));
}

static void pages_result(struct collector *c)
{
    struct page_counters total;
    int fd = bpf_object__find_map_fd_by_name(c->object, "counters");
    if (read_percpu(fd, &total, sizeof(total))) {
        unavailable(c, "Falha ao ler contadores de páginas");
        return;
    }
    begin_result(c, "available", NULL);
    printf("{\"allocated_pages\":%llu,\"freed_pages\":%llu,\"page_size_bytes\":%ld}}",
           U64(total.allocated_pages), U64(total.freed_pages), sysconf(_SC_PAGESIZE));
}

static void io_result(struct collector *c)
{
    struct io_key keys[MONIX_MAX_DEVICES], current, next;
    struct io_counters values[MONIX_MAX_DEVICES];
    unsigned int count = 0;
    int fd = bpf_object__find_map_fd_by_name(c->object, "counters");
    int ret = 0;
    __u64 lost;
    if (read_percpu(bpf_object__find_map_fd_by_name(c->object, "lost"), &lost, sizeof(lost))) {
        unavailable(c, "Falha ao ler diagnóstico de perdas de I/O");
        return;
    }
    void *key = NULL;
    while (count < MONIX_MAX_DEVICES && !(ret = bpf_map_get_next_key(fd, key, &next))) {
        if (bpf_map_lookup_elem(fd, &next, &values[count])) {
            unavailable(c, "Falha ao ler contadores de I/O");
            return;
        }
        keys[count++] = next;
        current = next;
        key = &current;
    }
    if (ret && errno != ENOENT) {
        unavailable(c, "Falha ao enumerar dispositivos de I/O");
        return;
    }
    begin_result(c, lost ? "partial" : "available", lost ? "Mapa cheio; eventos de I/O perdidos" : NULL);
    printf("{\"lost_events\":%llu,\"devices\":[", U64(lost));
    for (unsigned int i = 0; i < count; i++) {
        struct io_counters *v = &values[i];
        printf("%s{\"device_id\":\"%u:%u\",\"operation\":\"%s\",\"issued\":%llu,"
               "\"completion_events\":%llu,\"completed_bytes\":%llu,\"errors\":%llu,"
               "\"latency_samples\":%llu,\"mean_latency_ms\":",
               i ? "," : "", keys[i].major, keys[i].minor, keys[i].operation ? "write" : "read",
               U64(v->issued), U64(v->completions), U64(v->completed_bytes), U64(v->errors), U64(v->latency_samples));
        if (v->latency_samples)
            printf("%.6f", (double)v->latency_ns / (double)v->latency_samples / 1000000.0);
        else
            printf("null");
        putchar('}');
    }
    printf("]}}");
}

static void zones_result(struct collector *c, double interval)
{
    __u32 keys[MONIX_MAX_ZONES], current, next;
    struct thermal_sample values[MONIX_MAX_ZONES];
    unsigned int count = 0;
    int fd = bpf_object__find_map_fd_by_name(c->object, "samples");
    void *key = NULL;
    __u64 lost;
    if (read_percpu(bpf_object__find_map_fd_by_name(c->object, "lost"), &lost, sizeof(lost))) {
        unavailable(c, "Falha ao ler diagnóstico de perdas térmicas");
        return;
    }
    bool partial = lost != 0;
    int ret = 0;
    uint64_t now = monotonic_ns();
    double ages[MONIX_MAX_ZONES];
    bool valid[MONIX_MAX_ZONES];
    while (count < MONIX_MAX_ZONES && !(ret = bpf_map_get_next_key(fd, key, &next))) {
        if (bpf_map_lookup_elem(fd, &next, &values[count])) {
            unavailable(c, "Falha ao ler zonas térmicas");
            return;
        }
        struct thermal_sample *sample = &values[count];
        ages[count] = now >= sample->timestamp_ns ? (double)(now - sample->timestamp_ns) / 1e9 : INFINITY;
        valid[count] = ages[count] <= fmax(15, 3 * interval) &&
                       sample->temperature_millicelsius >= -40000 && sample->temperature_millicelsius <= 150000;
        partial |= !valid[count];
        keys[count++] = next;
        current = next;
        key = &current;
    }
    if (ret && errno != ENOENT) {
        unavailable(c, "Falha ao enumerar zonas térmicas");
        return;
    }
    begin_result(c, count ? (partial ? "partial" : "available") : "pending",
                 lost ? "Mapa cheio; eventos térmicos perdidos" :
                 count ? (partial ? "Zona sem leitura recente ou válida" : NULL) : "Aguardando eventos térmicos do kernel");
    if (!count) {
        printf("null}");
        return;
    }
    putchar('[');
    for (unsigned int i = 0; i < count; i++) {
        printf("%s{\"id\":%u,\"type\":", i ? "," : "", keys[i]);
        values[i].type[sizeof(values[i].type) - 1] = '\0';
        quote(values[i].type);
        printf(",\"age_seconds\":");
        if (isfinite(ages[i]))
            printf("%.3f", ages[i]);
        else
            printf("null");
        printf(",\"temperature_celsius\":");
        if (valid[i])
            printf("%.3f", values[i].temperature_millicelsius / 1000.0);
        else
            printf("null");
        printf(",\"status\":\"%s\"}", valid[i] ? "available" : "unavailable");
    }
    printf("]}");
}

static void gpu_result(struct collector *c)
{
    struct gpu_counters total;
    if (read_percpu(bpf_object__find_map_fd_by_name(c->object, "counters"), &total, sizeof(total))) {
        unavailable(c, "Falha ao ler eventos DRM");
        return;
    }
    begin_result(c, "available", NULL);
    printf("{\"queued\":%llu,\"run\":%llu,\"done\":%llu}}", U64(total.queued), U64(total.run), U64(total.done));
}

static bool local_filesystem(const char *type)
{
    const char *types[] = { "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "exfat",
                            "ntfs", "ntfs3", "reiserfs", "jfs", "bcachefs", "iso9660", "udf" };
    for (size_t i = 0; i < sizeof(types) / sizeof(types[0]); i++)
        if (!strcmp(type, types[i]))
            return true;
    return false;
}

struct filesystem_row {
    char *device, *mountpoint, *type;
    struct filesystem_sample sample;
    int error;
};

static void filesystems_result(struct collector *c)
{
    FILE *mounts = setmntent("/proc/self/mounts", "r");
    struct filesystem_row *rows = calloc(MAX_FILESYSTEMS, sizeof(*rows));
    if (!mounts || !rows) {
        if (mounts)
            endmntent(mounts);
        free(rows);
        unavailable(c, "Não foi possível enumerar montagens locais");
        return;
    }
    int map = bpf_object__find_map_fd_by_name(c->object, "samples");
    struct mntent *mount;
    unsigned int count = 0;
    bool partial = false;
    const char *error = NULL;
    while ((mount = getmntent(mounts))) {
        if (!local_filesystem(mount->mnt_type))
            continue;
        if (count == MAX_FILESYSTEMS) {
            partial = true;
            error = "Limite de 128 montagens atingido";
            break;
        }
        struct filesystem_row row = {};
        int fd = open(mount->mnt_dir, O_PATH | O_CLOEXEC);
        if (fd < 0) {
            row.error = errno;
        } else {
            struct statfs ignored;
            struct filesystem_sample empty = {};
            if (bpf_map_update_elem(map, &(__u32){0}, &empty, BPF_ANY) || fstatfs(fd, &ignored) ||
                bpf_map_lookup_elem(map, &(__u32){0}, &row.sample))
                row.error = errno;
            else if (!row.sample.timestamp_ns)
                row.error = ENODATA;
            else if (row.sample.error)
                row.error = -row.sample.error;
            close(fd);
        }
        bool duplicate = false;
        for (unsigned int i = 0; i < count && !row.error; i++) {
            if (rows[i].error || strcmp(mount->mnt_type, rows[i].type))
                continue;
            if (row.sample.fsid[0] || row.sample.fsid[1])
                duplicate = !memcmp(row.sample.fsid, rows[i].sample.fsid, sizeof(row.sample.fsid));
            else
                duplicate = !strcmp(mount->mnt_fsname, rows[i].device);
            if (duplicate)
                break;
        }
        if (duplicate)
            continue;
        row.device = strdup(mount->mnt_fsname);
        row.mountpoint = strdup(mount->mnt_dir);
        row.type = strdup(mount->mnt_type);
        if (!row.device || !row.mountpoint || !row.type) {
            free(row.device);
            free(row.mountpoint);
            free(row.type);
            partial = true;
            error = "Sem memória para enumerar montagens";
            break;
        }
        partial |= row.error != 0;
        rows[count++] = row;
    }
    endmntent(mounts);
    begin_result(c, partial ? "partial" : count ? "available" : "unavailable",
                 error ? error : partial ? "Algumas amostras VFS falharam" : count ? NULL : "Nenhum filesystem local visível");
    putchar('[');
    for (unsigned int i = 0; i < count; i++) {
        struct filesystem_row *row = &rows[i];
        struct filesystem_sample *s = &row->sample;
        printf("%s{\"device\":", i ? "," : "");
        quote(row->device);
        printf(",\"mountpoint\":");
        quote(row->mountpoint);
        printf(",\"fstype\":");
        quote(row->type);
        printf(",\"error\":");
        if (row->error)
            quote(strerror(row->error));
        else
            printf("null");
        printf(",\"data\":");
        if (row->error) {
            printf("null");
        } else {
            printf("{\"total_bytes\":%llu,\"free_bytes\":%llu,\"available_bytes\":%llu,\"used_bytes\":%llu,\"usage_percent\":",
                   U64(s->total_bytes), U64(s->free_bytes), U64(s->available_bytes), U64(s->total_bytes - s->free_bytes));
            if (s->total_bytes)
                printf("%.6f", 100.0 * (double)(s->total_bytes - s->free_bytes) / (double)s->total_bytes);
            else
                printf("null");
            putchar('}');
        }
        putchar('}');
        free(row->device);
        free(row->mountpoint);
        free(row->type);
    }
    free(rows);
    printf("]}");
}

int main(int argc, char **argv)
{
    double interval = 2;
    if (argc != 1) {
        char *end = NULL;
        if (argc != 3 || strcmp(argv[1], "--interval"))
            return 2;
        interval = strtod(argv[2], &end);
        if (end == argv[2] || *end || !isfinite(interval) || interval <= 0 || interval > 86400)
            return 2;
    }
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || (size_t)length >= sizeof(executable) - 1)
        return 1;
    executable[length] = '\0';
    char *slash = strrchr(executable, '/');
    if (!slash)
        return 1;
    *slash = '\0';
    possible_cpus = libbpf_num_possible_cpus();
    if (possible_cpus <= 0)
        return 1;
    struct sigaction action = { .sa_handler = stop_handler };
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);
    size_t count = sizeof(collectors) / sizeof(collectors[0]);
    for (size_t i = 0; i < count; i++)
        load_collector(&collectors[i], executable);
    while (running) {
        putchar('{');
        for (size_t i = 0; i < count; i++) {
            struct collector *c = &collectors[i];
            if (i)
                putchar(',');
            if (c->error[0]) {
                unavailable(c, c->error);
                continue;
            }
            switch (i) {
            case 0: memory_result(c); break;
            case 1: pages_result(c); break;
            case 2: io_result(c); break;
            case 3: zones_result(c, interval); break;
            case 4: gpu_result(c); break;
            case 5: filesystems_result(c); break;
            }
        }
        puts("}");
        fflush(stdout);
        struct timespec delay = { .tv_sec = (time_t)interval,
            .tv_nsec = (long)((interval - floor(interval)) * 1000000000.0) };
        while (running && nanosleep(&delay, &delay) && errno == EINTR) {}
    }
    for (size_t i = 0; i < count; i++)
        close_collector(&collectors[i]);
    return 0;
}
