#!/usr/bin/env bash
# RTG-SLAM launcher for Ubuntu.
#
# Examples:
#   bash run_rtg_slam.sh
#   bash run_rtg_slam.sh configs/tum/fr1_desk.yaml
#   bash run_rtg_slam.sh --multiprocess configs/replica/office0.yaml
#   bash run_rtg_slam.sh --no-conda configs/ours/hotel.yaml

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RTG_ROOT="$PROJECT_ROOT/external/RTG-SLAM"
CONFIG="configs/replica/office0.yaml"
CONDA_ENV="RTG-SLAM"
USE_CONDA=true
ENTRYPOINT="slam.py"

usage() {
    cat <<'EOF'
Usage: bash run_rtg_slam.sh [options] [config.yaml]

Options:
  -m, --multiprocess       Use slam_mp.py instead of the recommended slam.py.
  -e, --conda-env NAME     Use the specified Conda environment (default: RTG-SLAM).
      --no-conda           Use the currently activated Python environment.
  -h, --help               Print this help message.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--multiprocess)
            ENTRYPOINT="slam_mp.py"
            ;;
        -e|--conda-env)
            [[ $# -ge 2 ]] || { echo "Error: $1 requires an environment name." >&2; exit 2; }
            CONDA_ENV="$2"
            shift
            ;;
        --no-conda)
            USE_CONDA=false
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            CONFIG="$1"
            ;;
    esac
    shift
done

[[ -d "$RTG_ROOT" ]] || { echo "Error: RTG-SLAM was not found at $RTG_ROOT" >&2; exit 1; }
[[ -f "$RTG_ROOT/$CONFIG" ]] || { echo "Error: configuration file not found: $RTG_ROOT/$CONFIG" >&2; exit 1; }

cd "$RTG_ROOT"
echo "Running RTG-SLAM ($ENTRYPOINT) with $CONFIG"

if "$USE_CONDA"; then
    command -v conda >/dev/null 2>&1 || {
        echo "Error: conda was not found. Activate the environment and use --no-conda." >&2
        exit 1
    }
    exec conda run --no-capture-output -n "$CONDA_ENV" python "$ENTRYPOINT" --config "$CONFIG"
else
    command -v python >/dev/null 2>&1 || { echo "Error: python was not found." >&2; exit 1; }
    exec python "$ENTRYPOINT" --config "$CONFIG"
fi
