FROM python:3.12-slim
RUN pip install --no-cache-dir "numpy==2.3.5" "pandas==2.3.3" "scikit-learn==1.7.2" "pyarrow==21.0.0" \
 && groupadd --gid 1000 ubuntu && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash ubuntu
WORKDIR /workspace
COPY workspace/ /workspace/target/
RUN chown -R ubuntu:ubuntu /workspace/target
COPY backend/ /opt/task/
RUN mkdir -p /var/lib/nyc-trip-task /run/nyc-trip-task \
 && chown root:root /opt/task /opt/task/* /var/lib/nyc-trip-task \
 && chmod 0700 /opt/task /opt/task/* /var/lib/nyc-trip-task \
 && chmod 0755 /opt/task/entrypoint.sh
RUN set -e; for f in grade.py terminal_probe.py solve.sh; do \
  test ! -e "/workspace/target/$f" && test ! -e "/workspace/target/tests/$f"; done
ENV PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    WORKSPACE_DIR=/workspace/target TASK_STATE_DIR=/var/lib/nyc-trip-task
WORKDIR /workspace/target
ENTRYPOINT ["/opt/task/entrypoint.sh"]
CMD ["/bin/bash"]
