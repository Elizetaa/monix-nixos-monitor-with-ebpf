# Coletores do sistema

Esta pasta organiza os coletores por domínio. Cada coletor pode conter a
implementação eBPF (quando os dados vêm de eventos do kernel) e o componente
de userspace que carrega o programa, lê os mapas e interpreta os dados.

## Estrutura

- `collectors/cpu/`: monitor de CPU atual. `cpu_alert.bpf.c` acompanha
  `sched:sched_switch`; `cpu_alert.c` carrega o programa, calcula o uso e
  dispara o hook de alerta.
- `collectors/gpu/`: reservado para métricas de GPU.
- `collectors/memory/`: reservado para métricas de RAM/memória.
- `collectors/thermal/`: reservado para temperatura e sensores.
- `collectors/storage/`: reservado para armazenamento e métricas de disco.
- `common/`: reservado a componentes reutilizáveis entre coletores.
- `build/`: executáveis e objetos compilados (gerados por `make`).
- `Makefile` e `shell.nix`: compilação e ambiente de desenvolvimento.

Nem todo dado precisa ou pode ser obtido com eBPF. CPU e eventos de I/O são
bons candidatos a tracepoints/kprobes; RAM pode combinar eventos e métricas
do kernel; GPU, sensores térmicos e capacidade/saúde de discos normalmente
dependem também de interfaces de driver e do sistema (`sysfs`, DRM, NVML ou
ferramentas específicas). A organização deixa cada coletor livre para usar
o mecanismo apropriado, mantendo coleta, leitura e interpretação separadas.

## Interface dos Makefiles

O `Makefile` de `src/ebpf/` orquestra os coletores. Dentro de cada pasta em
`collectors/`, o `Makefile` atende à mesma interface: `make` explica o uso;
`make check` verifica dependências; `make setup` inicia o shell; `make run`
executa a coleta; e `make clean` remove os artefatos daquele coletor.

No momento, somente CPU está implementado. GPU, memória, armazenamento e
térmico têm alvos explícitos que respondem “não implementado”. Por isso,
`make check` geral mostra o resumo e retorna erro enquanto houver coletores
pendentes.

```sh
make                         # explica os alvos gerais
make check                   # verifica todos e apresenta resumo unificado
make setup                   # abre nix-shell usando shell.nix
make setup SETUP_CMD='nix develop'
make run COLLECTOR=cpu THRESHOLD=80 INTERVAL=1
make clean                   # chama clean de todos os coletores
```

Para executar diretamente o coletor CPU, entre em `collectors/cpu/` e use os
mesmos alvos. `make clean` remove `build/cpu_alert` e
`build/cpu_alert.bpf.o`. O programa eBPF do CPU não é pinado no kernel; seu
link é destruído quando o processo termina, inclusive ao receber SIGINT/SIGTERM.
