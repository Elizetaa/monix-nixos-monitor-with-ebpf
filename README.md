# Proposta de Pesquisa

## Sistema de Gerenciamento de Computadores de Laboratório com eBPF Integrado ao NixOS

## Visão Geral

Este projeto propõe o desenvolvimento de um sistema de gerenciamento de computadores de laboratório utilizando **eBPF (Extended Berkeley Packet Filter)** integrado ao **NixOS**. A proposta busca implementar mecanismos de monitoramento, auditoria e controle diretamente no kernel, permitindo maior segurança, observabilidade e administração das máquinas do laboratório com baixo impacto de desempenho.

# Metas

## Segurança e Controle

- Impedir o desligamento dos computadores, exceto por usuários administradores.
- Limitar acessos simultâneos via SSH (ex.: máximo de dois usuários diferentes por máquina).

## Auditoria

- Registrar histórico de conexões SSH.
- Registrar histórico de eventos e comandos mais utilizados.

## Telemetria (Opcional)

- Construir uma base de dados sobre a utilização dos computadores.
- Coletar métricas de:
  - CPU
  - GPU
  - Memória RAM
  - Armazenamento

## Coleta e Análise de Dados

- Criar uma série temporal das métricas coletadas.
- Separar os dados por:
  - usuário;
  - computador.

## Visualização (Opcional)

- Desenvolver um dashboard interativo.
- Exibir o uso das máquinas em tempo real.

# Modelo Lógico

## Entidade: Computador

| Coluna | Tipo | Descrição |
|---------|------|-----------|
| `id_computador` | INT (PK) | Identificador único do computador |
| `hostname` | VARCHAR | Nome da máquina (ex.: `lab-pc-01`) |
| `endereco_ip` | VARCHAR | Endereço IP atual ou estático |
| `localizacao` | VARCHAR | Bancada ou sala onde o computador está localizado |

## Entidade: Telemetria_Hardware

| Coluna | Tipo | Descrição |
|---------|------|-----------|
| `id_telemetria` | INT (PK) | Identificador do registro |
| `id_computador` | INT (FK) | Referência ao computador |
| `timestamp` | DATETIME | Data e hora da medição |
| `temp_cpu` | FLOAT | Temperatura da CPU (°C) |
| `temp_gpu` | FLOAT | Temperatura da GPU (°C) |
| `temp_placa_mae` | FLOAT | Temperatura da placa-mãe |
| `uso_cpu` | FLOAT | Utilização da CPU (%) |
| `uso_gpu` | FLOAT | Utilização da GPU (%) |
| `uso_ram` | FLOAT | Utilização da memória RAM (%) |
| `uso_rom` | FLOAT | Utilização do armazenamento (%) |

## Entidade: Monitoramento_Ventoinhas

| Coluna | Tipo | Descrição |
|---------|------|-----------|
| `id_leitura_fan` | INT (PK) | Identificador da leitura |
| `id_computador` | INT (FK) | Referência ao computador |
| `timestamp` | DATETIME | Data e hora da medição |
| `nome_ventoinha` | VARCHAR | Nome da ventoinha (ex.: `CPU_FAN`, `SYS_FAN1`) |
| `velocidade_rpm` | INT | Velocidade em rotações por minuto |
| `falha` | BOOLEAN | `true` caso seja detectada falha ou parada |

## Entidade: Acessos_SSH

| Coluna | Tipo | Descrição |
|---------|------|-----------|
| `id_acesso` | INT (PK) | Identificador do acesso |
| `id_computador` | INT (FK) | Referência ao computador |
| `data_hora` | DATETIME | Momento da tentativa ou login |
| `usuario` | VARCHAR | Usuário utilizado |
| `ip_origem` | VARCHAR | IP de origem da conexão |
| `sucesso` | BOOLEAN | `true` para login bem-sucedido; `false` para falha |

## Entidade: Status_Sistema

| Coluna | Tipo | Descrição |
|---------|------|-----------|
| `id_status` | INT (PK) | Identificador do registro |
| `id_computador` | INT (FK) | Referência ao computador |
| `timestamp` | DATETIME | Data e hora da medição |
| `tempo_atividade` | BIGINT | Tempo de atividade (uptime) em segundos |
| `reinicializacoes` | INT | Quantidade total de reinicializações |

# Relacionamentos

- **Computador** possui múltiplos registros em **Telemetria_Hardware**.
- **Computador** possui múltiplos registros em **Monitoramento_Ventoinhas**.
- **Computador** possui múltiplos registros em **Acessos_SSH**.
- **Computador** possui múltiplos registros em **Status_Sistema**.

```text
Computador (1)
    ├── (N) Telemetria_Hardware
    ├── (N) Monitoramento_Ventoinhas
    ├── (N) Acessos_SSH
    └── (N) Status_Sistema
```

# Resultados Esperados

Ao final do projeto, espera-se obter um sistema capaz de:

- Monitorar continuamente o estado das máquinas do laboratório.
- Aplicar políticas de segurança utilizando eBPF diretamente no kernel.
- Registrar eventos e acessos para fins de auditoria.
- Produzir uma base histórica de telemetria para análises futuras.
- Disponibilizar informações em tempo real para administradores, por meio de um dashboard opcional.