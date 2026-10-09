#!/usr/bin/env bash
# Execute on the GPU server after uploading the frozen data and scripts.
set -euo pipefail
QA_RUN_DIR=${1:?Usage: launch_pilot.sh RUN_DIR MODEL_DIR [UNIT_NAME]}
QA_MODEL_DIR=${2:?Usage: launch_pilot.sh RUN_DIR MODEL_DIR [UNIT_NAME]}
QA_UNIT=${3:-qa-operations-$(date +%Y%m%d-%H%M%S)}
QA_PYTHON=${QA_PYTHON:-/home/chun/semantic-qa/venv/bin/python}
test -f "$QA_RUN_DIR/data/frozen.json"
test -f "$QA_RUN_DIR/scripts/train_pilot.py"
test -f "$QA_RUN_DIR/scripts/download_model.py"
systemd-run --user --unit="$QA_UNIT" --working-directory="$QA_RUN_DIR" \
  --property=StandardInput=null \
  --property="StandardOutput=append:$QA_RUN_DIR/pipeline.log" \
  --property="StandardError=append:$QA_RUN_DIR/pipeline.log" \
  --setenv=PYTHONUNBUFFERED=1 --setenv=HF_ENDPOINT=https://hf-mirror.com \
  "$QA_PYTHON" "$QA_RUN_DIR/scripts/train_pilot.py" pipeline \
  --run "$QA_RUN_DIR" --model "$QA_MODEL_DIR"
