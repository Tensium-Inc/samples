# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN apt-get update -y \
  && apt-get install -y --no-install-recommends bash ca-certificates libseccomp2 tini tmux asciinema \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --no-cache-dir \
       numpy==2.3.5 scipy==1.17.0 scikit-learn==1.8.0 \
       pypdf==6.10.0 python-docx==1.2.0 lxml==6.0.2

RUN groupadd --gid 1000 ubuntu \
  && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash ubuntu

WORKDIR /workspace

COPY backend/ /opt/task_service/
RUN chown -R root:root /opt/task_service \
  && chmod 0700 /opt/task_service \
  && find /opt/task_service -type f -exec chmod 0600 {} + \
  && chmod 0700 /opt/task_service/daemon.py /opt/task_service/task-entrypoint.sh \
  && ln -s /opt/task_service/task-entrypoint.sh /usr/local/bin/task-entrypoint

# Agent-visible workspace = the agent's entire world.
COPY workspace/ /workspace/target/
RUN chown -R ubuntu:ubuntu /workspace/target \
  && find /workspace/target -type d -exec chmod 0755 {} + \
  && find /workspace/target -type f -exec chmod 0644 {} + \
  && chmod 0755 /workspace/target/env_cli.py /workspace/target/scripts/run_retrieval.py

# --- Anti-leak build guard (KEEP) ------------------------------------------
# Fail the BUILD if any oracle / gold / hidden-validation / reference material ends up
# inside the agent workspace. Extend the lists to every name your task could use.
RUN set -eu; \
  for d in _private gold oracle verifier_fixture hidden; do \
    if [ -e "/workspace/target/$d" ]; then echo "REFUSAL: reference dir in workspace ($d)" >&2; exit 1; fi; \
  done; \
  for f in solve.sh terminal_probe.py reference_engine.py reference_runtime.py hidden_scenarios.json accepted_actions.jsonl release_receipts.json ranking_evidence.json; do \
    if find /workspace/target -name "$f" -print -quit | grep -q .; then \
      echo "REFUSAL: verifier or gated-evidence material in workspace ($f)" >&2; exit 1; \
    fi; \
  done; \
  if grep -R -n -E "GOLD_FIX|FROZEN_ORACLE|REFERENCE_ENGINE|RRF_OFFSET" /workspace/target 2>/dev/null; then \
    echo "REFUSAL: answer-like leakage in workspace" >&2; exit 1; \
  fi

ENV WORKSPACE_DIR=/workspace/target \
    TASK_SOCKET=/run/post_pci_retrieval.sock \
    TASK_STATE_DIR=/var/lib/post_pci_retrieval
WORKDIR /workspace/target
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/task-entrypoint"]
CMD ["/bin/bash"]
