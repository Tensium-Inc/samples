# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN apt-get update -y \
  && apt-get install -y --no-install-recommends bash ca-certificates ripgrep tmux asciinema \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --no-cache-dir \
       "numpy==2.3.5" "pandas==2.3.3" "scikit-learn==1.7.2" "pyarrow==21.0.0" "PyYAML==6.0.2"

RUN groupadd --gid 1000 ubuntu \
  && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash ubuntu

WORKDIR /workspace

COPY workspace/ /workspace/target/
RUN chown -R ubuntu:ubuntu /workspace/target

COPY backend/ops_backend.py /opt/task/ops_backend.py
COPY backend/ops_daemon.py /opt/task/ops_daemon.py
COPY backend/entrypoint.sh /opt/task/entrypoint.sh
COPY backend/candidate_worker.py /opt/task/candidate_worker.py
COPY backend/validation_transactions.parquet /opt/task/validation_transactions.parquet
COPY backend/validation_probes.parquet /opt/task/validation_probes.parquet
COPY backend/validation_oracle.json /opt/task/validation_oracle.json
RUN mkdir -p /opt/task /var/lib/asof-task \
  && chown root:root /opt/task/* /var/lib/asof-task \
  && chmod 0700 /opt/task /opt/task/* /var/lib/asof-task \
  && chmod 0755 /opt/task/entrypoint.sh

RUN set -e; \
  for d in _private gold oracle textbook negative_controls; do \
    if [ -e "/workspace/target/$d" ]; then echo "REFUSAL: reference dir in workspace ($d)" >&2; exit 1; fi; \
  done; \
  for f in solve.sh terminal_probe.py candidate_worker.py grade.py freeze_terminal.py \
           hidden_transactions.parquet hidden_probes.parquet frozen_terminal.json; do \
    if [ -e "/workspace/target/$f" ] || [ -e "/workspace/target/tests/$f" ]; then \
      echo "REFUSAL: verifier material in workspace ($f)" >&2; exit 1; fi; \
  done; \
  if grep -R -n -E "GOLD featurize|GOLD FIX|terminal_sha256|orig_date|reference fix" \
       /workspace/target 2>/dev/null; then \
    echo "REFUSAL: answer-like leakage in workspace" >&2; exit 1; \
  fi; \
  true

ENV PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    WORKSPACE_DIR=/workspace/target \
    TASK_INSTANCE_SEED=41729

WORKDIR /workspace/target
ENTRYPOINT ["/opt/task/entrypoint.sh"]
CMD ["/bin/bash"]
