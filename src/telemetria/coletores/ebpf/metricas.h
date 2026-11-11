/* Layouts compartilhados pelos programas BPF e pelo loader observador.c. */
#ifndef MONIX_METRICAS_H
#define MONIX_METRICAS_H

#define MONIX_MAX_DEVICES 128
#define MONIX_MAX_REQUESTS 16384
#define MONIX_MAX_ZONES 128

struct memory_sample {
    __u64 timestamp_ns;
    __u64 total_bytes;
    __u64 free_bytes;
    __u64 shared_bytes;
    __u64 buffer_bytes;
    __u64 available_timestamp_ns;
    __u64 available_bytes;
    __u64 page_size_bytes;
};

struct page_counters {
    __u64 allocated_pages;
    __u64 freed_pages;
};

struct io_key {
    __u32 major;
    __u32 minor;
    __u32 operation; /* 0: leitura; 1: escrita. */
};

struct io_counters {
    __u64 issued;
    __u64 completions; /* Inclui conclusões parciais. */
    __u64 completed_bytes;
    __u64 errors;
    __u64 latency_samples; /* Apenas requisições concluídas por inteiro. */
    __u64 latency_ns;
};

struct thermal_sample {
    __u64 timestamp_ns;
    __s32 temperature_millicelsius;
    char type[20];
};

struct gpu_counters {
    __u64 queued;
    __u64 run;
    __u64 done;
};

struct filesystem_sample {
    __u64 timestamp_ns;
    __u64 total_bytes;
    __u64 free_bytes;
    __u64 available_bytes;
    __s32 fsid[2];
    __s32 error;
};

#endif
