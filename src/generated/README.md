# Monix: biblioteca Python e agente local de hardware

O pacote `generated` coleta hardware de um computador Linux/NixOS, mantém
**um único snapshot atual em RAM** e oferece consultas Python e HTTP.
A refatoração preserva os coletores, o programa eBPF CO-RE e o loader C.
Prometheus e Grafana são integrações opcionais: biblioteca e API funcionam
sem instalar ou executar esses serviços.

## Arquitetura

```text
Kernel/hardware → coletores → normalização → Monitor: snapshot atômico em RAM
                                               ↓
                     get_snapshot() ← mesmo estado → API JSON
                                                   → /metrics opcional
                                                          ↓
                                                Prometheus → Grafana
```

`monitor.py` controla um único worker e o ciclo de vida. `normalization.py`
organiza as amostras em estruturas serializáveis. `api.py` somente consulta
o estado existente; `agent.py` fornece configuração/CLI e sinais de encerramento.
Importar `Monitor` não importa HTTP, cria threads nem carrega BPF.

A primeira coleta começa imediatamente. O intervalo é **entre inícios de
coletas**, usando relógio monotônico. Se uma coleta ultrapassa o intervalo,
os horários perdidos são descartados, sem fila nem sobreposição. JSON,
amostras Prometheus e instante da publicação são substituídos juntos sob
um lock. Consultas recebem cópias e não executam coletores ou normalização.

Uma falha remove os dados anteriores daquele coletor, registra o erro e
preserva os demais. Erros repetidos iguais não geram traceback em cada ciclo.
Há apenas o snapshot atual e metadados limitados por coletor, sem banco,
histórico, CSV ou JSON periódicos. Os exemplos gravam JSON somente quando
executados explicitamente.

## Estrutura

```text
src/generated/
├── __init__.py                 # from generated import Monitor
├── __main__.py                 # python -m generated
├── monitor.py                  # coleta e estado em RAM
├── normalization.py            # contrato JSON
├── configuration.py            # defaults, validação e TOML
├── api.py                      # HTTP de leitura
├── agent.py                    # CLI do agente
├── exporter.py                 # entrada legada Prometheus
├── collectors/
│   ├── cpu/                    # collector.py, cpu_usage.c/.bpf.c, Makefile
│   ├── gpu/                    # DRM/sysfs e nvidia-smi
│   ├── memory/                 # /proc/meminfo
│   ├── storage/                # sysfs, mountinfo e statvfs
│   └── thermal/                # hwmon e thermal zones
├── common/                     # Sample, caminhos, renderer Prometheus
├── config/
│   ├── agent.toml              # configuração principal
│   ├── prometheus.yml          # integração opcional
│   └── grafana/                # datasource/dashboard provisionados
├── examples/
│   ├── export_json.py          # biblioteca → JSON de uma amostra real
│   └── api_client.py           # API → JSON recebido
├── tests/                      # simulações somente no ambiente de testes
├── scripts/start-stack.sh      # stack local opcional
├── build/                      # artefatos nativos, ignorados pelo Git
├── pyproject.toml              # monix-generated e comando monix-agent
├── Makefile
├── shell.nix
└── compose.yml
```

## Instalação e NixOS

Biblioteca/API exigem **Python 3.11+**, sem dependências Python em runtime.
O servidor existente foi reaproveitado com `http.server`, suficiente para
consultas de um pequeno snapshot, sem framework ou servidor ASGI adicional.

Direto do repositório, sem instalação:

```sh
# Na raiz:
export PYTHONPATH="$PWD/src"
python3 -m generated --config src/generated/config/agent.toml
```

Instalação em ambiente virtual:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./src/generated
.venv/bin/monix-agent --config src/generated/config/agent.toml
```

O build Python usa setuptools. O wheel contém fontes nativos e configurações,
sem binários específicos da máquina. Para eBPF com o pacote instalado, compile
o helper no repositório e configure `cpu_helper` com caminho absoluto; mantenha
o objeto `.bpf.o` ao lado do executável.

Compilar eBPF exige clang com backend BPF, compilador C, libbpf, pkg-config,
bpftool e BTF em `/sys/kernel/btf/vmlinux`. Compilar não exige root; carregar
o programa exige privilégios BPF e kernel com BPF/tracepoints/BTF. O loader
CO-RE acompanha `sched_switch`. Não há objetos pinados; encerrá-lo destrói
o link do programa.

```sh
cd src/generated
nix-shell
make check
make run                     # API; TOML, eBPF auto por padrão

# Para exigir CPU via eBPF:
make check-native
make build
make run EBPF=required        # compila se necessário e usa sudo
```

O shell Nix inclui ferramentas Python/nativas. Prometheus e Grafana entram
somente com `nix-shell --arg observability true`.

## Configuração e execução

Arquivo principal: `config/agent.toml`.

```toml
[monitor]
interval_seconds = 5
machine_id = "computer-01"       # opcional
ebpf = "auto"                   # auto | required | disabled
# cpu_helper = "../build/cpu_usage"
# ambient_sensors = ["nct6798:temp1"]
# collectors = ["cpu", "gpu", "memory", "storage", "thermal"]

[api]
host = "127.0.0.1"
port = 9108

[prometheus]
enabled = false
```

Identificador automático: `/etc/machine-id`, depois `/var/lib/dbus/machine-id`,
com hostname como último recurso. Configure um ID único para instalações
clonadas ou inventário do laboratório. O snapshot também inclui `hostname`.

Intervalos devem ser finitos e estar em `(0,86400]` segundos; portas são
inteiros de 1 a 65535. Tipos, chaves/seções desconhecidas, coletores duplicados
e seletores térmicos inválidos são rejeitados. `cpu_helper` relativo é
resolvido a partir do arquivo TOML.

TOML é carregado quando passado em `--config`; sem arquivo, usam-se os
padrões centralizados em `configuration.py`. A CLI sobrescreve o arquivo:

```sh
# Na raiz:
PYTHONPATH=src python3 -m generated --config src/generated/config/agent.toml \
  --interval 2 --machine-id lab-pc-01 --port 8000 --ebpf disabled

# Dentro de src/generated:
make run INTERVAL=2 EBPF=disabled PORT=8000
make snapshot INTERVAL=1 EBPF=disabled   # JSON real e encerramento
make run COLLECTOR=memory
```

`auto` tenta o helper e registra fallback para `/proc/stat` se indisponível.
`required` nunca troca silenciosamente para procfs: a falha aparece na saúde
e availability, preservando os outros coletores. `disabled` não inicia BPF.
Uso de CPU fica `null` durante a baseline inicial. `--once` aguarda CPU
utilizável quando habilitada e falha com código 1 se não houver amostra no prazo.

`Ctrl+C`/SIGTERM encerra a API, para o worker e fecha os coletores. `stop`
aguarda a rotina em andamento antes de fechar seus recursos. Se um driver
bloquear além do timeout, lança `RuntimeError`; tente o fechamento novamente
depois que a rotina terminar. Na biblioteca, use `finally` ou `with`.

## Interface Python

Com o pacote instalado ou `PYTHONPATH` apontando para `src`:

```python
from generated import Monitor

monitor = Monitor(interval=5, machine_id="computer-01")
try:
    monitor.start()
    snapshot = monitor.wait_for_snapshot(timeout=20, min_sequence=2)
    if snapshot is not None:
        print(snapshot["cpu"])
        print(snapshot["gpu"])
        print(snapshot["memory"])
        print(snapshot["storage"])
        print(snapshot["motherboard"])
        print(monitor.get_snapshot("memory"))
finally:
    monitor.stop()
```

| Interface | Comportamento |
| --- | --- |
| `Monitor(interval=5, machine_id=None, ...)` | Configura sem iniciar; opções incluem `ebpf`, `cpu_helper`, `ambient_sensors`, `hostname`, `categories` |
| `start()` | Inicia um worker; idempotente enquanto ativo; retorna o monitor |
| `stop(timeout=5)` | Para/fecha recursos; pode ser chamado novamente |
| `get_snapshot()` | Cópia do estado; antes da primeira coleta: timestamp `null`, sequência 0, `meta.ready=false` |
| `get_snapshot(category)` | Dados da categoria; metadados no snapshot completo |
| `wait_for_snapshot(timeout=10, min_sequence=1, require_categories=())` | Aguarda publicação válida ou retorna `None`, sem executar coleta |
| `get_health()` | Estado, idade e diagnóstico por coletor |
| `with Monitor(...) as monitor:` | Inicia e encerra automaticamente |

Categorias: `cpu`, `gpu`, `memory`, `storage`, `motherboard`, `thermal`.
Selecionar CPU também inclui temperaturas. Coletores padrão são recriados
em reinícios. `paths`/`collectors` permitem fixtures; objetos injetados são
responsabilidade do consumidor e são reutilizados após fechamento.

## API de leitura

Cada endpoint consulta a mesma instância, com suporte a requisições concorrentes.
O padrão é `127.0.0.1:9108`. Não há endpoints de controle de sensores/coleta.

| Endpoint | Resposta |
| --- | --- |
| `GET /api/metrics` | Snapshot JSON completo |
| `GET /api/metrics/{category}` | Categoria com máquina, timestamp, sequência, availability e meta |
| `GET /health` | Estado/diagnóstico; `/healthz` é alias compatível |
| `GET /metrics` | Formato Prometheus, somente quando habilitado |

```sh
curl http://127.0.0.1:9108/api/metrics
curl http://127.0.0.1:9108/api/metrics/cpu
curl http://127.0.0.1:9108/health
```

JSON retorna **200** após a primeira publicação, mesmo sem leituras, parcial ou desatualizada;
`availability`, `collectors` e `meta` explicam essas condições. Antes de existir
publicação, retorna **503**, mantendo estrutura JSON e `meta.ready=false`.
Saúde retorna 200 somente com monitor ativo, dados recentes e nenhum erro de
coletor; os demais estados usam 503. Hardware opcional sem leituras não é
falha completa do serviço. Endpoints desconhecidos, inclusive `/metrics`
desabilitado, retornam **404**; métodos de controle retornam **405**.

Exemplo abreviado, com valores ilustrativos, de `/api/metrics/memory`:

```json
{
  "schema_version": 1,
  "machine_id": "computer-01",
  "hostname": "lab-pc-01",
  "timestamp": "2026-10-09T13:00:00.000Z",
  "collection_interval_seconds": 5.0,
  "sequence": 2,
  "availability": {
    "memory": {"status": "available", "available": true, "error": null, "source": "procfs"}
  },
  "meta": {"has_snapshot": true, "ready": true, "stale": false, "running": true, "age_seconds": 0.2, "stale_after_seconds": 15.0},
  "memory": {
    "source": "procfs", "total_bytes": 17179869184,
    "used_bytes": 8589934592, "available_bytes": 8589934592,
    "free_bytes": 2147483648, "allocated_bytes": 7516192768,
    "usage_percent": 50.0
  }
}
```

Para rede interna, use VPN ou gateway com autenticação/TLS e restrinja acesso.
A API não implementa autenticação/TLS embutidos; rede interna não é
automaticamente confiável. `host="0.0.0.0"` expõe todas as interfaces e
deve acompanhar essas proteções.

## Contrato dos dados

`schema_version=1` identifica o contrato. Timestamp: ISO 8601 UTC, milissegundos,
sufixo `Z`, ao concluir a coleta. `sequence` aumenta por publicação e reinicia
com o monitor. `collection_interval_seconds` registra a amostragem configurada.

Idade usa relógio monotônico. Dados ficam stale em `max(15,3×interval)` segundos
sem publicação. Consultas não atualizam idade, timestamp ou sequência.
`meta.running` distingue execução e encerramento; um snapshot parado pode
ainda ser recente, mas `/health` indica serviço parado.
`meta.has_snapshot` informa se uma publicação foi concluída; `meta.ready`
informa se ela contém leituras disponíveis, distinguindo coleta concluída
sem métricas de ausência da primeira coleta.

`availability` tem `status`, `available`, `source`, `error` por categoria:
estados `available`, `partial`, `unavailable`, `error`. `collectors` tem
sucesso, erro, duração e último timestamp bem-sucedido. Valores ausentes
são `null`, listas sem dispositivos são `[]`, sem zeros fabricados.
Sensores com fault/desabilitados/leituras implausíveis são omitidos; não há
inventário/diagnóstico individual de todos os canais possíveis.

| Categoria | Campos | Origem |
| --- | --- | --- |
| `cpu` | `name`, `model`, `vendor`, `logical_cores`, `usage_percent`, `source`, `ebpf_attached`, `temperature_celsius`, `temperatures`, `cores` | Identificação `/proc/cpuinfo`, uso eBPF/procfs, temperatura hwmon/thermal zone |
| `gpu[]` | `id`, `name`, `model`, `vendor`, `pci_address`, uso, temperatura, `fan_percent`, `fan_rpm`, `temperatures`, `fans`, `sources` | DRM/sysfs/hwmon; NVIDIA nvidia-smi |
| `memory` | `total/used/available/free/allocated_bytes`, `usage_percent` | `/proc/meminfo` |
| `storage[]` | `device`, `model`, `serial`, `total_bytes`, `partitions[]`, `filesystems[]` | sysfs e mountinfo/statvfs |
| `motherboard[]` | `chip`, `sensor`, `label`, `role`, `temperature_celsius`, `source` | hwmon: sistema, placa, chipset, VRM e outros |
| `thermal[]` | Identificadores e temperaturas dos demais sensores | hwmon |
| `ambient_estimate_celsius` | Estimativa opcional do ar interno/entrada | Sensores identificados |
| `unmapped_filesystems`, `unmapped_partitions` | Topologia desconhecida ou incompleta | sysfs |

Bytes são inteiros; uso e temperatura são números em porcentagem e °C.
Temperatura/fan escalares representam o máximo das leituras; listas preservam
cada sensor. CPU `source` identifica a origem do uso, enquanto cada temperatura
tem sua própria origem. Fan NVIDIA percentual pode ultrapassar 100%.

eBPF mede tarefas diferentes de idle (PID 0), com contadores cumulativos,
snapshots coerentes e extrapolação desde o último `sched_switch`, sem zerar
mapas. CPUs sem baseline ainda não entram no agregado. IRQ durante idle e
iowait fazem a medida diferir de `/proc/stat`; fallback trata iowait como idle
e não soma guest duas vezes. `Tctl` AMD pode ser controle térmico; observe
`Tdie` quando disponível.

RAM **livre**: `MemFree`, páginas sem uso. **Disponível**: `MemAvailable`,
estimativa alocável sem swap, incluindo cache recuperável. **Utilizada**:
`total-disponível`. **Alocada**: `total-free-buffers-cached-SReclaimable+Shmem`,
limitada a `[0,total]`. Sem `MemAvailable`, usa-se a contabilidade de alocada
como estimativa. Não se soma RSS por processo para evitar duplicar páginas.

Capacidade física de discos é distinta dos seus filesystems. Partições
desmontadas continuam identificadas. Filesystems têm `device`,
`physical_devices`, `mountpoint`, `fstype`, `total/used/free/available_bytes`,
`backing_known`, `exclusive`, `source`. Livre inclui blocos reservados;
disponível é utilizável por usuários. Bind mounts/subvolumes são deduplicados.
LVM/RAID/Btrfs são resolvidos quando a topologia está visível. Um filesystem
compartilhado aparece associado a vários discos com `exclusive=false`;
essas referências não devem ser somadas como capacidades independentes.
Pseudo filesystems/remotos são excluídos; mapas incompletos são explícitos.

AMD usa `gpu_busy_percent`/hwmon. NVIDIA precisa do driver e `nvidia-smi`
no PATH, com timeout de 2 s. Seu fan percentual indica velocidade pretendida,
não RPM. GPU sem fan/N/A mantém os demais dados. Intel pode não expor uso;
frequência não é convertida em porcentagem. Leituras ausentes ficam `null`.

## Sensores de ambiente

A estimativa prioriza Inlet/Intake/Ambient e depois SYSTIN/System/Mainboard/
Motherboard/Chassis/Case. CPU, GPU, VRM, PCH e AUXTIN não entram automaticamente.
Os sensores da placa preservam identificação e role, incluindo chipset/VRM.
Não existe relação universal entre `temp1` e localização física.

Seleção manual substitui a automática, após confirmar os canais na placa:

```sh
make run EXTRA_ARGS='--ambient-sensor nct6798:temp1'
```

Também pode ser configurada em `monitor.ambient_sensors`. Sem sensor adequado,
a estimativa é `null`. Ela reflete ar interno/entrada do gabinete; medir
temperatura real da sala exige sensor ambiente dedicado.

## Exemplos reais

Na raiz do repositório:

```sh
python3 src/generated/examples/export_json.py --ebpf disabled --interval 1 \
  --output /tmp/metrics-library.json

# Com o agente ativo:
python3 src/generated/examples/api_client.py \
  --url http://127.0.0.1:9108/api/metrics --output /tmp/metrics-api.json
```

Ambos aceitam timeout e retornam código 1 em erro. O primeiro aceita `--config`,
aguarda uma amostra e encerra `Monitor`. O cliente valida HTTP, content-type e
versão/estado básico do contrato. A gravação é explícita, exclusiva dos exemplos.

## Prometheus e Grafana opcionais

Habilite `prometheus.enabled=true` ou use:

```sh
make exporter EBPF=disabled
# Na raiz:
PYTHONPATH=src python3 -m generated --prometheus --ebpf disabled
```

`/metrics` consulta o mesmo snapshot que `/api/metrics`, sem outro coletor.
Snapshot expirado retorna 503 sem entregar dados antigos ao Prometheus.
Nomes existentes `monix_cpu_*`, `monix_gpu_*`, `monix_memory_*`,
`monix_storage_*`, `monix_motherboard_*` e diagnósticos foram preservados.
Acrescentaram-se memória livre e identificação/topologia de discos/partições.
Mantém-se `hostname`; Prometheus acrescenta `instance`/`job`.

`config/prometheus.yml` usa `127.0.0.1:9108`: atualizar o target se a API mudar
de porta/endereço. Para máquinas remotas, configure targets com proteção de
rede; o dashboard seleciona `instance`. A entrada legada `exporter.py` mantém
Prometheus habilitado por padrão e `--once` em texto Prometheus:

```sh
make sample INTERVAL=1
python3 exporter.py --once --ebpf disabled --interval 1
```

Segundo terminal, em `src/generated`, para stack Nix opcional:

```sh
nix-shell --arg observability true
export GRAFANA_ADMIN_PASSWORD='monix-local-admin'
make stack-native
```

Prometheus: <http://127.0.0.1:9090>. Grafana: <http://127.0.0.1:3000>, usuário
`admin`, senha acima, pasta **Monix**, dashboard com 27 painéis.
`Ctrl+C` encerra a stack; `.runtime/` preserva seu histórico opcional,
separado do estado do agente. Caminhos: `MONIX_RUNTIME_DIR`, `GRAFANA_HOME`.
A senha inicial vale na primeira criação do banco Grafana.

Docker Compose no Linux, mantendo agente no host:

```sh
cp .env.example .env
# Ajuste GRAFANA_ADMIN_PASSWORD no .env.
make stack-up
make stack-down                # preserva volumes
```

Stack com rede do host e listeners loopback. Não executar stack local/Docker
nas mesmas portas simultaneamente. Nada disso é necessário em cada computador;
API e biblioteca funcionam sozinhas.

## Testes e limpeza

```sh
# Dentro de src/generated:
make check
make test
make check-native              # componente eBPF
make build

# Na raiz, sem make/instalação:
PYTHONPATH=src python3 -m unittest discover -s src/generated/tests -v
```

Testes cobrem importação sem HTTP/threads, lifecycle, intervalo, overrun,
atomicidade, cópias defensivas, RAM sem persistência, HTTP concorrente,
exemplos e regressões dos coletores. Simulações são usadas somente em testes.
Ambientes que proíbem sockets locais indicam skip nos testes HTTP por
`PermissionError`; eles executam fora dessa restrição.

No shell de observabilidade:

```sh
promtool check config config/prometheus.yml
python3 exporter.py --once --ebpf disabled --interval 1 > /tmp/monix.metrics
promtool check metrics < /tmp/monix.metrics
```

`make clean` remove artefatos nativos e preserva a stack opcional. Foram
removidos o ciclo/classe duplicado `TelemetryExporter`, a criação de coletores
na camada HTTP, imports soltos e hacks de caminho dos testes. Bootstraps
pequenos permanecem nos scripts diretos; defaults, categorias e validação são
compartilhados. Código nativo, dashboards e rotinas funcionais foram preservados.

## Integração futura e limites

Uma aplicação externa poderá consultar `/api/metrics`, verificar versão,
máquina, timestamp, sequência, availability e stale, e encaminhar ao servidor
central. Sua frequência de consultas não altera a amostragem; o servidor
poderá deduplicar snapshots e manter seu histórico. Esta entrega implementa
somente coleta e consulta local.

Disponibilidade de GPU/fans/sensores depende do driver e hardware. Canais
inválidos são omitidos, sem diagnóstico individual; topologias incompletas
são marcadas. Agendamento é aproximado, sujeito ao sistema operacional.
Chamadas bloqueadas de drivers não são interrompidas à força. Ativação eBPF
depende das permissões do kernel; helpers simulados não substituem validação
privilegiada em cada máquina.

Referências: [hwmon](https://docs.kernel.org/hwmon/sysfs-interface.html),
[AMDGPU](https://docs.kernel.org/gpu/amdgpu/thermal.html),
[NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html),
[Prometheus](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
e [Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/).
