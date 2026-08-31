FROM python:3.12-slim

# CPU-only ML stack. Versions are pinned exactly, not ranged: the published figure is graded
# by value, and resampling and estimator internals shift between minor releases enough to
# move it.
RUN apt-get update -y \
  && apt-get install -y --no-install-recommends bash ca-certificates util-linux \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --no-cache-dir \
       "numpy==2.2.6" "pandas==2.3.3" "scikit-learn==1.7.2" "PyYAML==6.0.3"

RUN groupadd --gid 1000 ubuntu \
  && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash ubuntu

WORKDIR /workspace

COPY workspace/ /workspace/target/
RUN chown -R ubuntu:ubuntu /workspace/target

# Root-owned change-control daemon. State and ledger live in /var/lib/readmission-task,
# root-owned 0700, so the agent cannot forge them.
COPY backend/ /opt/task/
RUN chmod 0755 /opt/task/entrypoint.sh \
  && chown -R root:root /opt/task \
  && mkdir -p /var/lib/readmission-task /run/readmission-task \
  && chown root:root /var/lib/readmission-task \
  && chmod 0700 /var/lib/readmission-task

# Fail the build if reference or verifier material ends up inside the candidate tree.
RUN set -e; \
  for d in _private reference oracle variants; do \
    if [ -e "/workspace/target/$d" ]; then echo "REFUSAL: reference dir in workspace ($d)" >&2; exit 1; fi; \
  done; \
  for f in solve.sh terminal_probe.py grade.py measure.py; do \
    if [ -e "/workspace/target/$f" ] || [ -e "/workspace/target/tests/$f" ]; then \
      echo "REFUSAL: verifier material in workspace ($f)" >&2; exit 1; fi; \
  done; \
  if grep -R -n -E "encode_from|build_base|average_precision_score|GOLD FIX" \
       --exclude=config.yaml /workspace/target 2>/dev/null; then \
    echo "REFUSAL: the fix leaked into the workspace" >&2; exit 1; \
  fi; \
  true

ENV WORKSPACE_DIR=/workspace/target \
    TASK_STATE_DIR=/var/lib/readmission-task \
    TASK_OPS_SOCKET=/run/readmission-task/ops.sock
WORKDIR /workspace/target
# Start the root daemon, then drop the interactive shell to the unprivileged agent.
ENTRYPOINT ["/opt/task/entrypoint.sh"]
CMD ["/bin/bash"]
