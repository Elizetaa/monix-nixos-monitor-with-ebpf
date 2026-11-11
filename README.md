# Monix — monitor de sistema com eBPF

Projeto de disciplina para explorar instrumentação do kernel. O fluxo é
**coletores → coordenador → `GET /api/metrics`**, com um snapshot JSON em RAM.
Python 3.11+, biblioteca padrão e libbpf; sem framework web, banco ou dashboard.
O código ativo fica em `src/telemetria/`; `src/generated/`, `src/ebpf/` e os
laboratórios permanecem preservados e independentes.

## Execução Linux/NixOS

Na raiz:

```sh
nix develop 'path:.#telemetria'       # ou nix-shell src/telemetria/shell.nix
make -C src/telemetria check-native
make -C src/telemetria build
make -C src/telemetria test

sudo env PYTHONPATH="$PWD/src" "$(command -v python3)" -m telemetria --interval 2
# Em outro terminal:
curl -s http://127.0.0.1:9108/api/metrics | python3 -m json.tool
```

Build exige clang com backend BPF, compilador C, make, pkg-config, libbpf e
bpftool. O shell Nix fornece essas ferramentas. Execução exige root ou
capacidades adequadas, BTF em `/sys/kernel/btf/vmlinux`, suporte a BPF,
raw tracepoints e trampolins BPF/fexit. O alvo de desenvolvimento é Linux 6.18;
hooks ausentes ou incompatíveis são sinalizados por categoria, sem adaptação
legada. DRM depende de um driver que use `gpu_sched` e exponha os eventos
`drm_sched_job_queue/run/done`. `nvidia-smi` é opcional e vem do driver do host.

Opções: `--interval`, `--host`, `--port`, `--helper /caminho/cpu_usage` e
`--observer-helper /caminho/observador`. Os objetos `.bpf.o` ficam ao lado dos
loaders. Ctrl+C/SIGTERM fecha servidor, worker, processos e links BPF; não há
objetos pinados. `make -C src/telemetria clean` remove o build novo.

## Fontes das métricas

| Categoria / métrica | Fonte |
| --- | --- |
| `cpu`: uso total e por núcleo | eBPF, `sched_switch` |
| `memory`: RAM total, livre, disponível, compartilhada e buffers | eBPF, fexit de `si_meminfo` e `si_mem_available` |
| `memory_usage`: RAM usada e uso % | Derivação dos dados BPF: total − disponível |
| `memory_events`: páginas alocadas/liberadas | eBPF, eventos `mm_page_*`; atividade, não RSS nem RAM usada |
| `block_io`: emissões, bytes concluídos, erros e latência média | eBPF, `block_rq_issue/complete`, por dispositivo e leitura/escrita |
| `filesystems`: espaço total/livre/disponível/usado e uso % | eBPF, fexit de `fd_statfs`; montagens enumeradas por procfs |
| `temperature_zones`: temperaturas das zonas térmicas | eBPF, `thermal_temperature`, com idade e disponibilidade por zona |
| `gpu_events`: jobs enfileirados, executados e concluídos | eBPF, eventos DRM; **não equivale a uso global em %** |
| `storage`: inventário e capacidade bruta de blocos/partições | sysfs; não atribui espaço livre a disco bruto |
| `gpu`: identificação, uso %, temperatura e fan RPM | DRM/sysfs/hwmon, conforme driver |
| `gpu_nvidia`: identificação, uso %, temperatura e fan % | Ferramenta externa `nvidia-smi`, separada de sysfs |
| `temperature`: demais sensores identificados | hwmon/sysfs, sem inferir temperatura ambiente |

Para amostrar memória, o loader lê `/proc/meminfo` **somente como disparo** das
funções instrumentadas e descarta o texto. Para filesystems, chama `fstatfs`
e descarta a estrutura recebida em espaço de usuário. Em ambos os casos os
valores publicados vêm exclusivamente dos mapas BPF. Esses disparos continuam
usando interfaces Linux: o objetivo é estudar eBPF, não eliminar o userspace.
Detalhes, mapas e limites: [coletores eBPF](src/telemetria/coletores/ebpf/README.md).

Cada categoria tem `source`, `status`, `data`, `error`. Leituras ausentes ficam
`null`; estados: `available`, `partial`, `unavailable`, `pending`, `error`.
Contadores de eventos são cumulativos desde o attach. Zonas sem eventos ficam
pendentes; leituras antigas são invalidadas após `max(15, 3 × intervalo)` segundos.
HTTP retorna 503 antes do primeiro snapshot e 200 depois, inclusive com falhas
explicadas no JSON. O intervalo é uma espera após cada ciclo; não há histórico.

## Validação e remoções

```sh
make -C src/telemetria validate      # API e dados reais; relata falta de privilégios
# Exige attach de CPU, memória, páginas, I/O e filesystems:
sudo "$(command -v python3)" src/telemetria/tests/validar_host.py --require-ebpf
```

Removidos do fluxo ativo: CPU por procfs e sua opção de CLI, coletor Python de
memória, leitura de espaço por `statvfs` e leitura de thermal zones por sysfs.
Não há fallback de sistema para as operações migradas. Permanecem apenas
fontes complementares sem substituto genérico equivalente implementado.

Verificado: dois loaders C e sete objetos BPF compilaram; 13 testes passaram;
relocações CO-RE conferidas contra o BTF do host. API real e SIGTERM validados.
O carregamento dos programas retornou `EPERM`, corretamente exposto no JSON;
`sudo` não interativo exige senha. **Attach e valores dos novos programas ainda
precisam ser validados com privilégios**, pelo comando acima.
