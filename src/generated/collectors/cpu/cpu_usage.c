#define _POSIX_C_SOURCE 200809L
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

struct cpu_counter {
    uint64_t sequence;
    uint64_t busy_ns;
    uint64_t idle_ns;
    uint64_t last_timestamp_ns;
    uint32_t is_idle;
    uint32_t padding;
};

struct sample {
    uint64_t busy;
    uint64_t idle;
    bool valid;
};

static volatile sig_atomic_t running = 1;

static void stop_handler(int signal_number)
{
    (void)signal_number;
    running = 0;
}

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now))
        return 0;
    return (uint64_t)now.tv_sec * 1000000000ULL + now.tv_nsec;
}

static int sleep_interval(double seconds)
{
    struct timespec requested = {
        .tv_sec = (time_t)seconds,
        .tv_nsec = (long)((seconds - floor(seconds)) * 1000000000.0),
    };
    while (running && nanosleep(&requested, &requested)) {
        if (errno != EINTR)
            return -1;
    }
    return 0;
}

/* Map slots follow the possible-CPU list, which may contain gaps. */
static int possible_cpu_ids(int *ids, int count)
{
    FILE *file = fopen("/sys/devices/system/cpu/possible", "r");
    char *line = NULL;
    size_t capacity = 0;
    int used = 0;
    if (!file)
        return -1;
    if (getline(&line, &capacity, file) < 0) {
        fclose(file);
        free(line);
        return -1;
    }
    fclose(file);
    char *cursor = line;
    while (*cursor && *cursor != '\n') {
        char *end;
        long first = strtol(cursor, &end, 10);
        if (end == cursor || first < 0 || first > INT_MAX)
            goto invalid;
        long last = first;
        cursor = end;
        if (*cursor == '-') {
            last = strtol(cursor + 1, &end, 10);
            if (end == cursor + 1 || last < first || last > INT_MAX)
                goto invalid;
            cursor = end;
        }
        if (last - first >= count - used)
            goto invalid;
        for (long id = first; id <= last; id++)
            ids[used++] = (int)id;
        if (*cursor == ',')
            cursor++;
        else if (*cursor && *cursor != '\n')
            goto invalid;
    }
    free(line);
    return used == count ? 0 : -1;
invalid:
    free(line);
    return -1;
}

static int online_cpus(const int *ids, int count, bool *online)
{
    FILE *file = fopen("/sys/devices/system/cpu/online", "r");
    char *line = NULL;
    size_t capacity = 0;
    if (!file)
        return -1;
    if (getline(&line, &capacity, file) < 0) {
        fclose(file);
        free(line);
        return -1;
    }
    fclose(file);
    memset(online, 0, (size_t)count * sizeof(*online));
    char *cursor = line;
    while (*cursor && *cursor != '\n') {
        char *end;
        long first = strtol(cursor, &end, 10);
        if (end == cursor || first < 0 || first > INT_MAX)
            goto invalid;
        long last = first;
        cursor = end;
        if (*cursor == '-') {
            last = strtol(cursor + 1, &end, 10);
            if (end == cursor + 1 || last < first || last > INT_MAX)
                goto invalid;
            cursor = end;
        }
        for (int slot = 0; slot < count; slot++)
            if (ids[slot] >= first && ids[slot] <= last)
                online[slot] = true;
        if (*cursor == ',')
            cursor++;
        else if (*cursor && *cursor != '\n')
            goto invalid;
    }
    free(line);
    return 0;
invalid:
    free(line);
    return -1;
}

static int take_snapshot(int map_fd, int count, struct cpu_counter *first,
                         struct cpu_counter *second, struct sample *samples,
                         const bool *online)
{
    uint32_t key = 0;
    memset(samples, 0, (size_t)count * sizeof(*samples));
    /* Retry only CPUs that were written during the map copy. */
    for (int attempt = 0; attempt < 8; attempt++) {
        if (bpf_map_lookup_elem(map_fd, &key, first))
            return -1;
        uint64_t now = monotonic_ns();
        if (!now || bpf_map_lookup_elem(map_fd, &key, second))
            return -1;
        bool pending = false;
        for (int cpu = 0; cpu < count; cpu++) {
            if (!online[cpu] || samples[cpu].valid)
                continue;
            const struct cpu_counter *a = &first[cpu];
            const struct cpu_counter *b = &second[cpu];
            if ((a->sequence & 1) || a->sequence != b->sequence ||
                memcmp(a, b, sizeof(*a)) || !a->last_timestamp_ns ||
                now < a->last_timestamp_ns) {
                pending = true;
                continue;
            }
            samples[cpu].busy = a->busy_ns;
            samples[cpu].idle = a->idle_ns;
            /* Include the current run even if no context switch occurred. */
            uint64_t current = now - a->last_timestamp_ns;
            if (a->is_idle)
                samples[cpu].idle += current;
            else
                samples[cpu].busy += current;
            samples[cpu].valid = true;
        }
        if (!pending)
            break;
    }
    return 0;
}

static bool delta_sample(const struct sample *current,
                         const struct sample *previous,
                         uint64_t *busy, uint64_t *total)
{
    if (!current->valid || !previous->valid ||
        current->busy < previous->busy || current->idle < previous->idle)
        return false;
    *busy = current->busy - previous->busy;
    uint64_t idle = current->idle - previous->idle;
    *total = *busy + idle;
    return *total > 0;
}

static void emit_sample(const int *ids, int count, const struct sample *current,
                        const struct sample *previous)
{
    uint64_t sum_busy = 0, sum_total = 0;
    for (int cpu = 0; cpu < count; cpu++) {
        uint64_t busy, total;
        if (delta_sample(&current[cpu], &previous[cpu], &busy, &total)) {
            sum_busy += busy;
            sum_total += total;
        }
    }
    if (!sum_total)
        return; /* No initialized measurement yet: never manufacture zero. */
    printf("{\"usage_percent\":%.6f,\"cores\":{",
           100.0 * (double)sum_busy / (double)sum_total);
    bool comma = false;
    for (int cpu = 0; cpu < count; cpu++) {
        uint64_t busy, total;
        if (delta_sample(&current[cpu], &previous[cpu], &busy, &total)) {
            printf("%s\"%d\":%.6f", comma ? "," : "", ids[cpu],
                   100.0 * (double)busy / (double)total);
            comma = true;
        }
    }
    puts("}}");
    fflush(stdout);
}

static int default_object_path(char *path, size_t capacity)
{
    ssize_t length = readlink("/proc/self/exe", path, capacity - 1);
    if (length < 0 || (size_t)length >= capacity - 1)
        return -1;
    path[length] = '\0';
    char *slash = strrchr(path, '/');
    if (!slash)
        return -1;
    size_t directory_length = (size_t)(slash - path + 1);
    const char name[] = "cpu_usage.bpf.o";
    if (directory_length + sizeof(name) > capacity)
        return -1;
    memcpy(path + directory_length, name, sizeof(name));
    return 0;
}

int main(int argc, char **argv)
{
    double interval = 2.0;
    char object_path[PATH_MAX];
    const char *object_file = NULL;
    for (int index = 1; index < argc; index++) {
        if (!strcmp(argv[index], "--interval") && index + 1 < argc) {
            char *end;
            errno = 0;
            interval = strtod(argv[++index], &end);
            if (errno || *end || !isfinite(interval) || interval <= 0 ||
                interval > 86400) {
                fprintf(stderr, "Invalid interval (0 < seconds <= 86400).\n");
                return 2;
            }
        } else if (!strcmp(argv[index], "--object") && index + 1 < argc) {
            object_file = argv[++index];
        } else {
            fprintf(stderr, "Usage: %s [--interval SECONDS] [--object FILE]\n", argv[0]);
            return 2;
        }
    }
    if (!object_file) {
        if (default_object_path(object_path, sizeof(object_path))) {
            fprintf(stderr, "Cannot resolve BPF object; pass --object FILE.\n");
            return 1;
        }
        object_file = object_path;
    }
    struct sigaction action = {.sa_handler = stop_handler};
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);

    int result = 1;
    struct bpf_object *object = NULL;
    struct bpf_link *link = NULL;
    int *ids = NULL;
    bool *online = NULL;
    struct cpu_counter *first = NULL, *second = NULL;
    struct sample *current = NULL, *previous = NULL;

    object = bpf_object__open_file(object_file, NULL);
    long error = libbpf_get_error(object);
    if (!object || error) {
        fprintf(stderr, "Cannot open BPF object %s: %s\n", object_file,
                strerror(error ? (int)-error : errno));
        object = NULL;
        goto cleanup;
    }
    if (bpf_object__load(object)) {
        fprintf(stderr, "Cannot load CO-RE BPF program; check kernel BTF and BPF privileges.\n");
        goto cleanup;
    }
    struct bpf_program *program =
        bpf_object__find_program_by_name(object, "on_sched_switch");
    if (!program) {
        fprintf(stderr, "BPF program on_sched_switch is missing.\n");
        goto cleanup;
    }
    link = bpf_program__attach(program);
    error = libbpf_get_error(link);
    if (!link || error) {
        fprintf(stderr, "Cannot attach raw tracepoint sched_switch: %s\n",
                strerror(error ? (int)-error : errno));
        link = NULL;
        goto cleanup;
    }
    int map_fd = bpf_object__find_map_fd_by_name(object, "cpu_stats");
    int count = libbpf_num_possible_cpus();
    if (map_fd < 0 || count <= 0) {
        fprintf(stderr, "Cannot obtain per-CPU map or possible CPU count.\n");
        goto cleanup;
    }
    ids = calloc((size_t)count, sizeof(*ids));
    online = calloc((size_t)count, sizeof(*online));
    first = calloc((size_t)count, sizeof(*first));
    second = calloc((size_t)count, sizeof(*second));
    current = calloc((size_t)count, sizeof(*current));
    previous = calloc((size_t)count, sizeof(*previous));
    if (!ids || !online || !first || !second || !current || !previous) {
        fprintf(stderr, "Cannot allocate per-CPU buffers.\n");
        goto cleanup;
    }
    if (possible_cpu_ids(ids, count)) {
        fprintf(stderr, "Cannot parse /sys/devices/system/cpu/possible.\n");
        goto cleanup;
    }

    /* First valid read is a baseline, not an interval starting at boot. */
    bool baseline = true;
    while (running) {
        if (online_cpus(ids, count, online) ||
            take_snapshot(map_fd, count, first, second, current, online)) {
            fprintf(stderr, "Cannot read CPU snapshot: %s\n", strerror(errno));
            goto cleanup;
        }
        if (!baseline)
            emit_sample(ids, count, current, previous);
        memcpy(previous, current, (size_t)count * sizeof(*current));
        baseline = false;
        if (sleep_interval(interval)) {
            fprintf(stderr, "CPU interval sleep failed: %s\n", strerror(errno));
            goto cleanup;
        }
    }
    result = 0;
cleanup:
    free(ids);
    free(online);
    free(first);
    free(second);
    free(current);
    free(previous);
    bpf_link__destroy(link);
    bpf_object__close(object);
    return result;
}
