#!/usr/bin/env bash
# Executar no shell Nix; exportador roda em um terminal separado.
set -euo pipefail

generated_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${MONIX_RUNTIME_DIR:-$generated_root/.runtime}"

for dependency in prometheus promtool grafana python3; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    echo "Dependência ausente: $dependency. Execute make setup em src/generated." >&2
    exit 1
  fi
done
promtool check config "$generated_root/config/prometheus.yml"
mkdir -p "$runtime_dir/prometheus" "$runtime_dir/grafana/data" "$runtime_dir/grafana/log" "$runtime_dir/grafana/plugins" "$runtime_dir/provisioning/dashboards"

# O caminho dos dashboards é absoluto nesta configuração local; o original
# continua correto para o container.
python3 - "$generated_root" "$runtime_dir" <<'PY'
from pathlib import Path
import json
import sys

root, runtime = map(Path, sys.argv[1:])
dashboard_path = json.dumps(str(root / "config/grafana/dashboards"))
(runtime / "provisioning/dashboards/monix.yml").write_text(
    "apiVersion: 1\nproviders:\n  - name: Monix\n    orgId: 1\n"
    "    folder: Monix\n    type: file\n    disableDeletion: true\n"
    "    updateIntervalSeconds: 10\n    allowUiUpdates: false\n"
    f"    options:\n      path: {dashboard_path}\n"
)
PY
cp -R "$generated_root/config/grafana/provisioning/datasources" "$runtime_dir/provisioning/"

prometheus_pid=''
grafana_pid=''
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$grafana_pid" ]]; then kill "$grafana_pid" 2>/dev/null || true; fi
  if [[ -n "$prometheus_pid" ]]; then kill "$prometheus_pid" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM

prometheus --config.file="$generated_root/config/prometheus.yml" \
  --storage.tsdb.path="$runtime_dir/prometheus" \
  --storage.tsdb.retention.time=15d --web.listen-address=127.0.0.1:9090 &
prometheus_pid=$!

# Em NixOS os assets ficam ao lado de bin/grafana no pacote do Nix store.
grafana_binary="$(readlink -f -- "$(command -v grafana)")"
grafana_home="${GRAFANA_HOME:-$(dirname -- "$(dirname -- "$grafana_binary")")/share/grafana}"
if [[ ! -f "$grafana_home/conf/defaults.ini" ]]; then
  grafana_home="${GRAFANA_HOME:-/usr/share/grafana}"
fi
GF_PATHS_DATA="$runtime_dir/grafana/data" \
GF_PATHS_LOGS="$runtime_dir/grafana/log" \
GF_PATHS_PLUGINS="$runtime_dir/grafana/plugins" \
GF_PATHS_PROVISIONING="$runtime_dir/provisioning" \
GF_SECURITY_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}" \
GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-monix-local-admin}" \
grafana server --homepath="$grafana_home" --config="$generated_root/config/grafana/grafana.ini" &
grafana_pid=$!

echo "Prometheus: http://127.0.0.1:9090 | Grafana: http://127.0.0.1:3000"
echo "Grafana: usuário ${GRAFANA_ADMIN_USER:-admin}; senha definida em GRAFANA_ADMIN_PASSWORD (padrão local: monix-local-admin)."
echo "Ctrl+C encerra ambos os serviços e preserva os dados em $runtime_dir."
wait -n "$prometheus_pid" "$grafana_pid"
