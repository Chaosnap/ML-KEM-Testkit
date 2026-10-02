#!/usr/bin/env bash
# vivado_batch.sh - run scripts/vivado_reports.tcl in a throw-away container.
#
#   scripts/vivado_batch.sh <reports_dir> [clock_period_ns]
#   e.g. scripts/vivado_batch.sh reports/f206_0
#
# Uses the vivado:2024 image with ~/Downloads/Vivado mounted where the
# interactive vivado-container mounts it, but runs batch mode only (no GUI)
# in its own `docker run --rm` container, so it neither needs nor disturbs
# the GUI container. Run one at a time (each needs several GB of memory).
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: $0 <reports_dir> [clock_period_ns]"; exit 1; }
out=$1
period=${2:-10}

repo=$(cd "$(dirname "$0")/.." && pwd)
host_mnt=$(cd "$repo/../.." && pwd)                 # .../Downloads/Vivado
ctr_mnt=/home/vivadouser/project
ctr_repo=$ctr_mnt/${repo#"$host_mnt"/}
name=$(basename "$out" | tr -c 'a-zA-Z0-9_\n' '_')
work=build/vivado_${name}
mkdir -p "$repo/$work"

docker run --rm --platform linux/amd64 --name "vivado-batch-$name" \
    -v "$host_mnt:$ctr_mnt" -w "$ctr_repo/$work" \
    --entrypoint /bin/bash vivado:2024 -lc "
        source /home/vivadouser/Vivado/2024.1/settings64.sh >/dev/null
        vivado -mode batch -nolog -nojournal \
            -source $ctr_repo/scripts/vivado_reports.tcl \
            -tclargs $ctr_repo/$out $period" > "$repo/$work/run.log" 2>&1 || true
grep -E '=== period|^ERROR' "$repo/$work/run.log" | head -5
