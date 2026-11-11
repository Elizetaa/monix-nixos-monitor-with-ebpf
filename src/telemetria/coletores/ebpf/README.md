# Coletores eBPF

O programa e o loader de CPU existentes foram preservados em
`cpu_usage.bpf.c`/`cpu_usage.c`. `observador.c` carrega seis objetos adicionais,
independentemente: um erro de attach descarta apenas os links daquele objeto.
Os dois processos emitem JSON por linha; `processo.py` supervisiona stdout,
stderr, término e idade das amostras, sem ler métricas de sistema como fallback.

| Objeto | Hooks | Mapas e significado |
| --- | --- | --- |
| `cpu_usage` | raw `sched_switch` | Tempo busy/idle por CPU; loader calcula deltas, incluindo trecho ainda em execução |
| `memoria` | fexit `si_meminfo`, `si_mem_available` | Amostra de RAM em bytes; captura os resultados do próprio kernel |
| `paginas` | raw `mm_page_alloc/free/free_batched` | Contadores por CPU; soma páginas segundo a ordem da alocação/liberação |
| `io` | raw `block_rq_issue/complete` | Início por endereço de request e contadores por major:minor/operação |
| `filesystems` | fexit `fd_statfs` | Captura `kstatfs`; monta capacidades em bytes usando `f_frsize` |
| `zonas` | raw `thermal_temperature` | Último evento por ID de zona, temperatura e instante monotônico |
| `drm` | raw `drm_sched_job_queue/run/done` | Contadores por CPU dos jobs que passam pelo escalonador DRM |

## Amostragem e semântica

`amostragem.bpf.h` restringe os hooks ativos ao PID do loader. Antes de cada
amostragem, o loader limpa o mapa; dados sem novos timestamps são erros, nunca
zeros substitutos. A leitura de `/proc/meminfo` provoca `si_meminfo` seguido de
`si_mem_available`. O texto lido é descartado, e o cálculo de disponibilidade
continua sendo o do kernel. `fstatfs` provoca `fd_statfs`; sua estrutura de
saída em userspace também é descartada. Montagens locais são descobertas em
`/proc/self/mounts`, filtradas antes da chamada e deduplicadas pelo fsid BPF e
tipo, com o dispositivo como alternativa de identificação quando fsid é zero.

I/O mede somente leitura/escrita no nível de blocos, sem atribuição a processo.
Conclusões podem ser parciais: seus bytes/eventos são contados, mas a latência
só é registrada na conclusão final com início conhecido. Uma reemissão reinicia
o relógio; a média cumulativa mede da **última emissão até a conclusão final**,
sem incluir espera anterior na fila. Erros de conclusão são contados; os bytes
reportados não devem ser interpretados como bytes necessariamente bem-sucedidos.
Pedidos em voo antes do attach podem ter bytes contados sem amostra de latência.
Nenhuma latência é inventada quando não há pares: a média fica `null`.

Páginas alocadas/liberadas descrevem atividade desde o attach. Sua diferença
não reconstrói RAM ocupada nem memória por processo. DRM conta eventos de todos
os drivers que usam `gpu_sched`, sem identificar GPU/processo e sem calcular
utilização ou tempo real de execução no hardware. Drivers como NVIDIA podem
exigir outras interfaces para as métricas que continuam em `sistema/`.

Zonas térmicas dependem de eventos espontâneos do kernel: não são provocadas
por leituras de sysfs. Sem evento, ficam pendentes; valores fora de −40…150 °C
ou sem atualização por `max(15,3×intervalo)` ficam indisponíveis. hwmon mantém
cobertura de outros sensores; não serve de fallback para `temperature_zones`.

Mapas limitam-se a 16.384 requests em voo, 128 pares dispositivo/operação e
128 zonas; a enumeração de filesystems limita-se a 128 entradas. Falhas de
atualização em I/O/zonas são contadas e sinalizadas como parciais. Contadores
não são zerados durante a observação; a leitura entre vários contadores não
é uma transação global. Todos os mapas/links são liberados ao encerrar.
Execução é voltada ao host Linux; o filtro de PID pressupõe o mesmo namespace
de PID do kernel, sem suporte adicional a containers.

## Referências do kernel

- [Cálculos de RAM e MemAvailable](https://github.com/torvalds/linux/blob/v6.18/mm/show_mem.c).
- [Ordem da coleta em meminfo](https://github.com/torvalds/linux/blob/v6.18/fs/proc/meminfo.c).
- [VFS/statfs](https://github.com/torvalds/linux/blob/v6.18/fs/statfs.c).
- [Eventos de blocos](https://github.com/torvalds/linux/blob/v6.18/include/trace/events/block.h),
  [memória](https://docs.kernel.org/trace/events-kmem.html),
  [térmicos](https://github.com/torvalds/linux/blob/v6.18/drivers/thermal/thermal_trace.h) e
  [DRM](https://github.com/torvalds/linux/blob/v6.18/drivers/gpu/drm/scheduler/gpu_scheduler_trace.h).
