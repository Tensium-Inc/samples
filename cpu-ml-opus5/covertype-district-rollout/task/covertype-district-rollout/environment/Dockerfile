# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN apt-get update -y \
  && apt-get install -y --no-install-recommends bash ca-certificates git jq ripgrep sudo procps \
  && rm -rf /var/lib/apt/lists/*

# Privilege separation. The agent (ubuntu) may run exactly one thing as root -- the
# environment daemon -- and nothing else. A blanket NOPASSWD:ALL would let it read the
# held-out district straight off disk and the isolation gate would be meaningless.
#   envdata : may read the hidden district. ONLY root is in it.
#   runners : the group `runner` belongs to. Deliberately has NO access to /opt/env.
#
# `runner` executes agent-authored code, so it is treated as hostile. Putting it in envdata
# (as an earlier revision did) let agent code read the held-out labels and write the state
# directly -- the terminal was bypassable with `return df["cover_type"]`.
RUN groupadd --gid 1000 ubuntu \
  && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash ubuntu \
  && groupadd --gid 2000 envdata \
  && groupadd --gid 2002 runners \
  && useradd --uid 2001 --gid 2002 --create-home --shell /bin/bash runner \
  && echo "ubuntu ALL=(root) NOPASSWD: /usr/local/bin/envd" > /etc/sudoers.d/envd \
  && chmod 0440 /etc/sudoers.d/envd \
  && mkdir -p /workspace/target \
  && chown -R ubuntu:ubuntu /workspace \
  && git config --system user.email "agent@example.com" \
  && git config --system user.name "agent"

# The pipeline runs on CPU only. Pinned so the terminal is reproducible: the grader compares
# a canonical metric bundle, and an unpinned sklearn would move it.
RUN python -m pip install --no-cache-dir --upgrade pip \
  && python -m pip install --no-cache-dir \
       "numpy==2.1.3" "scipy==1.14.1" "scikit-learn==1.5.2" "pandas==2.2.3" \
       "PyYAML==6.0.2" "joblib==1.4.2" "pytest==8.3.3"

# --- Remove setuid escalation vectors -----------------------------------------
# This class of failure cost a real run: an agent tried `sudo usermod -a -G envdata ubuntu` then
# `newgrp envdata` to read the held-out district. sudo refused it, but `newgrp` is
# setuid root, prompted for a password with no TTY, and BLOCKED THE EPISODE FOREVER.
# None of these are needed by the task. Stripping setuid closes the escalation path and
# makes the commands fail fast instead of hanging. `sudo` keeps its bit -- the daemon
# depends on it, and it is already scoped to exactly one command.
# Stripping setuid is not enough: `su` still PROMPTS on stdin (it does not need a TTY the
# way sudo does), so it blocks the episode forever waiting for input that never comes.
# Two runs hung this way -- once on `newgrp`, once on `su`. These binaries are pure
# escalation surface for this task, so they are removed outright: the command then fails
# instantly with "command not found" instead of waiting.
RUN rm -f /usr/bin/su /usr/bin/newgrp /usr/bin/sg /usr/bin/passwd \
          /usr/bin/gpasswd /usr/bin/chsh /usr/bin/chfn /usr/bin/chage /usr/bin/expiry \
  && for b in mount umount; do chmod a-s "/usr/bin/$b" 2>/dev/null || true; done \
  && chmod a-s /usr/sbin/unix_chkpwd 2>/dev/null || true

# --- Bound a single agent command -------------------------------------------------------
# A single command once consumed an entire step budget that trained ~20 models in a 6.5-minute sweep: the
# step budget was never consulted because the step never returned. A CPU cap makes a runaway
# command fail fast and forces the work into separate steps, which the budget does account
# for. Sized so normal experimentation is untouched: one fit ~19s CPU, a 3-fold sweep ~44s.
# 300s CPU only kills genuinely pathological commands.
# Applies to the agent's login shells (`bash -lc`), not to solve.sh or the daemon.
RUN printf 'ulimit -St 300 2>/dev/null || true\n' > /etc/profile.d/99-cpu-cap.sh \
  && chmod 0644 /etc/profile.d/99-cpu-cap.sh \
  && printf 'ubuntu hard cpu 10\nubuntu soft cpu 6\n' >> /etc/security/limits.conf

ENV WORKSPACE_DIR=/workspace/target
ENV PYTHONHASHSEED=0
ENV OMP_NUM_THREADS=2
COPY workspace/ /workspace/target/

# --- Anti-leak guard (KEEP) -------------------------------------------------
# The hidden district must never reach the agent's tree, and neither must the oracle.
RUN if [ -e /workspace/target/reference.patch ] || [ -e /workspace/target/solution ]; then \
      echo "FATAL: reference solution leaked into workspace" >&2; exit 1; fi
# The district NAME may legitimately appear in source (the schema has four areas and the
# one-hot encoder must know them). What must never appear is district DATA.
RUN if grep -rqiE "cache.?la.?poudre" /workspace/target --include="*.csv"; then \
      echo "FATAL: hidden district data leaked into workspace" >&2; exit 1; fi
RUN if find /workspace/target -name "*oracle*" -o -name "*hidden*" | grep -q .; then \
      echo "FATAL: oracle/hidden artifact present in workspace" >&2; exit 1; fi
RUN chown -R ubuntu:ubuntu /workspace

# --- Environment daemon: root-owned, outside the agent tree ------------------
COPY env/ /opt/env/
RUN printf '#!/bin/bash\nexec /usr/local/bin/python3 /opt/env/envd.py "$@"\n' > /usr/local/bin/envd \
  && cp /opt/env/runas-runner /usr/local/bin/runas-runner \
  && cp /opt/env/predict_runner.py /usr/local/lib/predict_runner.py \
  && cp /opt/env/entrypoint.sh /usr/local/bin/entrypoint.sh \
  && chmod 0755 /usr/local/bin/entrypoint.sh \
  && chmod 0755 /usr/local/bin/envd /usr/local/bin/runas-runner /usr/local/lib/predict_runner.py \
  && rm -f /opt/env/runas-runner /opt/env/predict_runner.py /opt/env/entrypoint.sh \
  && mkdir -p /opt/env/state \
  && chown -R root:envdata /opt/env \
  && chmod 0750 /opt/env /opt/env/hidden /opt/env/state \
  && chmod 0640 /opt/env/hidden/*.csv /opt/env/bars.json \
  && chmod 0755 /opt/env/envd.py \
  && chmod 0700 /opt/env/state
# /opt/env is root:envdata 0750 and NOBODY else is in envdata, so neither `ubuntu` nor
# `runner` can traverse it: not the labels, not the bars, not the state.

# --- The handoff the untrusted process is allowed to touch, and nothing else -------------
#   /srv/deploy        the agent's own deployed code. root:root, world-readable, NOT writable
#   /srv/handoff       features.csv written by root each scoring round (labels dropped)
#   /srv/handoff/out   the only thing `runner` may write: its predictions
RUN mkdir -p /srv/deploy /srv/handoff/out \
  && chown root:root /srv/deploy /srv/handoff \
  && chmod 0755 /srv/deploy /srv/handoff \
  && chown runner:runners /srv/handoff/out \
  && chmod 0700 /srv/handoff/out

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
WORKDIR /workspace/target
CMD ["/bin/bash"]
