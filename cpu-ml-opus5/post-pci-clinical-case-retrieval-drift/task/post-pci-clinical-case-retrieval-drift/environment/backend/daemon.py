#!/usr/bin/env python3
"""Root-owned state broker for the post-PCI retrieval incident.

The agent can invoke operations over a world-writable Unix socket, but cannot read or mutate
the authoritative action log, recovery state, or deployed candidate snapshots. Candidate code
is always copied symlink-safely and executed in a separate, resource-bounded subprocess after
dropping to the configured unprivileged uid/gid.
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import errno
import hashlib
import json
import os
import resource
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from reference_runtime import rank_cases as reference_rank_cases

OBSERVATIONS = [
    "inspect.sources", "inspect.schema", "audit.query", "profile.corpus",
    "sample.records", "test.visible",
]
SEQUENCE = [
    "reproduce", "deploy", "validate.runtime",
    "diagnostics.read", "recovery.apply", "validate.runtime",
    "audit.provenance", "audit.lexical", "audit.fusion",
    "deploy.ranker", "validate.ranker", "audit.balance", "shadow.challenge", "shadow.challenge", "deploy.final",
    "validate.final", "shadow.replay", "cutover.check",
    "cutover", "promote", "submit",
]
SOURCE_FILES = ("__init__.py", "artifact_loader.py", "text_views.py", "ranker.py", "cli.py")
RECOVERY_FAMILY = "stale_vector_cache"
VALIDATE_LIMIT = 4
CHALLENGE_LIMIT = 2
MAX_SOURCE_BYTES = 512 * 1024
RANKING_EVIDENCE = Path(__file__).resolve().with_name("ranking_evidence.json")


class _ScmpArgCmp(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_uint),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def _install_candidate_seccomp() -> None:
    """Keep candidate inference read-only and unable to exfiltrate probe inputs."""
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
        ctypes.POINTER(_ScmpArgCmp),
    ]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int

    allow = 0x7FFF0000
    deny = 0x00050000 | errno.EPERM
    masked_eq = 7
    ctx = lib.seccomp_init(allow)
    if not ctx:
        raise RuntimeError("seccomp_init failed")
    try:
        blocked = {
            "openat2", "creat", "mkdir", "mkdirat", "mknod", "mknodat",
            "unlink", "unlinkat", "rename", "renameat", "renameat2",
            "link", "linkat", "symlink", "symlinkat", "truncate", "ftruncate",
            "chmod", "fchmod", "fchmodat", "chown", "fchown", "fchownat", "lchown",
            "setxattr", "lsetxattr", "fsetxattr", "removexattr", "lremovexattr", "fremovexattr",
            "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
            "sendto", "sendmsg", "sendmmsg", "recvfrom", "recvmsg", "recvmmsg",
            "ptrace", "process_vm_readv", "process_vm_writev", "open_by_handle_at",
            "io_uring_setup", "io_uring_enter", "io_uring_register",
        }
        for name in sorted(blocked):
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and lib.seccomp_rule_add_array(ctx, deny, number, 0, None) != 0:
                raise RuntimeError(f"seccomp rule failed: {name}")

        write_flags = {
            os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND,
            getattr(os, "O_TMPFILE", 0),
        }
        for name, flags_arg in (("open", 1), ("openat", 2)):
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number < 0:
                continue
            for flag in sorted(write_flags - {0}):
                comparison = _ScmpArgCmp(flags_arg, masked_eq, flag, flag)
                if lib.seccomp_rule_add_array(ctx, deny, number, 1, ctypes.byref(comparison)) != 0:
                    raise RuntimeError(f"seccomp write-open rule failed: {name}:{flag}")
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError("seccomp_load failed")
    finally:
        lib.seccomp_release(ctx)


class Broker:
    def __init__(self, workspace: Path, state_dir: Path, candidate_uid: int, candidate_gid: int):
        self.workspace = workspace.resolve()
        self.data = self.workspace / "data"
        self.state_dir = state_dir.resolve()
        self.state_file = self.state_dir / "state.json"
        self.actions_file = self.state_dir / "accepted_actions.jsonl"
        self.candidate = self.state_dir / "candidate"
        self.recovery_receipt = self.state_dir / "recovery_receipt.json"
        self.uid = candidate_uid
        self.gid = candidate_gid
        self.candidate_timeout = int(os.environ.get("TASK_CANDIDATE_TIMEOUT_SEC", "75"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        if not self.state_file.exists():
            self._save({
                "observation_index": 0, "next_index": 0, "validate_count": 0,
                "recovery_locked": False,
                "recovered": False, "poison_active": False, "candidate_hash": None,
                "snapshot_generation": 0, "shadow_stable": False, "submitted": False,
                "ranker_clean": False, "final_clean": False, "cutover_ready": False,
                "final_candidate_hash": None, "challenge_count": 0, "challenge_seen": [],
                "instance_seed": int(os.environ.get("TASK_INSTANCE_SEED", "104729")),
            })

    def _load(self) -> dict[str, Any]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def _save(self, state: dict[str, Any]) -> None:
        tmp = self.state_dir / ".state.tmp"
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_file)

    def _log(self, op: str, accepted: bool, payload: dict[str, Any], op_class: str = "operational") -> None:
        item = {
            "seq": sum(1 for _ in self.actions_file.open()) + 1 if self.actions_file.exists() else 1,
            "ts_ns": time.time_ns(), "op": op, "op_class": op_class,
            "accepted": bool(accepted), "payload": payload,
        }
        fd = os.open(self.actions_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")

    def _reject(self, op: str, error: str, **extra: Any) -> dict[str, Any]:
        payload = {"error": error, **extra}
        self._log(op, False, payload)
        return {"ok": False, "op": op, **payload}

    def _accept(self, state: dict[str, Any], op: str, payload: dict[str, Any]) -> dict[str, Any]:
        state["next_index"] += 1
        self._save(state)
        self._log(op, True, payload)
        return {"ok": True, "op": op, **payload,
                "next": SEQUENCE[state["next_index"]] if state["next_index"] < len(SEQUENCE) else None}

    def _accept_observation(self, state: dict[str, Any], op: str, payload: dict[str, Any]) -> dict[str, Any]:
        state["observation_index"] += 1
        self._save(state)
        self._log(op, True, payload, op_class="observation")
        if state["observation_index"] < len(OBSERVATIONS):
            next_op = OBSERVATIONS[state["observation_index"]]
        else:
            next_op = SEQUENCE[state["next_index"]]
        return {"ok": True, "op": op, **payload, "next": next_op}

    def _accept_retry(self, state: dict[str, Any], op: str, payload: dict[str, Any], retry_index: int) -> dict[str, Any]:
        """Accept a real validation observation but route the next action back to redeploy."""
        state["next_index"] = retry_index
        self._save(state)
        self._log(op, True, payload)
        return {"ok": True, "op": op, **payload, "next": SEQUENCE[retry_index]}

    def status(self) -> dict[str, Any]:
        state = self._load()
        if state["observation_index"] < len(OBSERVATIONS):
            next_op = OBSERVATIONS[state["observation_index"]]
        else:
            next_op = SEQUENCE[state["next_index"]] if state["next_index"] < len(SEQUENCE) else None
        return {
            "ok": True, "op": "status",
            "next": next_op,
            "validate_remaining": VALIDATE_LIMIT - state["validate_count"],
            "challenge_remaining": CHALLENGE_LIMIT - state.get("challenge_count", 0),
            "snapshot_generation": state["snapshot_generation"],
            "candidate_hash": state["candidate_hash"],
            "recovery_locked": state["recovery_locked"], "submitted": state["submitted"],
        }

    def _safe_snapshot(self) -> str:
        source_dir = self.workspace / "src"
        stage = self.state_dir / ".candidate.new"
        if stage.exists():
            shutil.rmtree(stage)
        (stage / "src").mkdir(parents=True)
        digest = hashlib.sha256()
        for name in SOURCE_FILES:
            src = source_dir / name
            info = src.lstat()
            if not stat.S_ISREG(info.st_mode) or src.is_symlink() or info.st_size > MAX_SOURCE_BYTES:
                raise ValueError(f"unsafe candidate source: {name}")
            fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                data = os.read(fd, MAX_SOURCE_BYTES + 1)
            finally:
                os.close(fd)
            if len(data) != info.st_size:
                raise ValueError(f"candidate source changed during snapshot: {name}")
            (stage / "src" / name).write_bytes(data)
            os.chmod(stage / "src" / name, 0o444)
            digest.update(name.encode() + b"\0" + data)
        os.chmod(stage / "src", 0o555)
        os.chmod(stage, 0o555)
        if self.candidate.exists():
            shutil.rmtree(self.candidate)
        os.replace(stage, self.candidate)
        return digest.hexdigest()[:20]

    def _preexec(self):
        def drop() -> None:
            os.setsid()
            resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
            resource.setrlimit(resource.RLIMIT_AS, (2_500_000_000, 2_500_000_000))
            resource.setrlimit(resource.RLIMIT_FSIZE, (2_000_000, 2_000_000))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            if os.geteuid() == 0 and self.uid != 0:
                os.setgroups([])
                os.setgid(self.gid)
                os.setuid(self.uid)
            try:
                ctypes.CDLL(None).prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
            except Exception:
                pass
            _install_candidate_seccomp()
        return drop

    def _copy_candidate_for_run(self, base: Path, from_workspace: bool = False) -> Path:
        package = base / "src"
        package.mkdir(parents=True)
        source_dir = self.workspace / "src" if from_workspace else self.candidate / "src"
        for name in SOURCE_FILES:
            src = source_dir / name
            info = src.lstat()
            if not stat.S_ISREG(info.st_mode) or src.is_symlink():
                raise ValueError(f"unsafe candidate file: {name}")
            fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                data = os.read(fd, MAX_SOURCE_BYTES + 1)
            finally:
                os.close(fd)
            (package / name).write_bytes(data)
            os.chmod(package / name, 0o444)
        os.chmod(package, 0o555)
        return package

    def _run_candidate(self, artifact_root: Path, request: dict[str, Any], *, from_workspace: bool = False) -> tuple[bool, dict[str, Any] | str, float]:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="retrieval-run-") as tmp_name:
            tmp = Path(tmp_name)
            os.chmod(tmp, 0o755)
            self._copy_candidate_for_run(tmp, from_workspace=from_workspace)
            stdout = tmp / "stdout.txt"; stderr = tmp / "stderr.txt"
            stdout.touch(); stderr.touch(); os.chmod(stdout, 0o666); os.chmod(stderr, 0o666)
            env = {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "PYTHONPATH": str(tmp), "PYTHONHASHSEED": "0", "HOME": str(tmp),
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            }
            with stdout.open("wb") as out, stderr.open("wb") as err:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "src.cli", "--artifact-root", str(artifact_root), "--request-stdin"],
                    cwd=tmp, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err,
                    preexec_fn=self._preexec(),
                )
                try:
                    proc.communicate(json.dumps(request).encode(), timeout=self.candidate_timeout)
                except subprocess.TimeoutExpired:
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    proc.wait(timeout=5)
                    return False, "timeout", time.monotonic() - started
                finally:
                    # A successful parent must not leave a detached helper alive for grade time.
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
            if proc.returncode != 0:
                return False, f"exit_{proc.returncode}:{stderr.read_text(errors='replace')[-240:]}", time.monotonic() - started
            try:
                payload = json.loads(stdout.read_text(encoding="utf-8").strip().splitlines()[-1])
            except Exception as exc:  # noqa: BLE001
                return False, f"invalid_json:{exc}", time.monotonic() - started
            return True, payload, time.monotonic() - started

    def _public_request(self) -> dict[str, Any]:
        return json.loads((self.data / "query_facets.json").read_text(encoding="utf-8"))

    def _mutated_artifacts(self) -> tempfile.TemporaryDirectory:
        holder = tempfile.TemporaryDirectory(prefix="retrieval-counterfactual-")
        os.chmod(holder.name, 0o755)
        root = Path(holder.name) / "data"
        shutil.copytree(self.data, root)
        manifest_path = root / "source_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["files"]:
            if not entry.get("active"): continue
            path = root / entry["path"]
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            rows.reverse()
            for index, row in enumerate(rows):
                row["final_diagnosis"] = f"redacted_{index % 7}"
                row["domain_hits"] = {"cardiac": 99-index % 3, "vascular": index % 5, "systemic": 42}
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
            entry["rows"] = len(rows)
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["files"] = manifest["files"][1:] + manifest["files"][:1]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        for path in root.rglob("*"):
            if path.is_file(): os.chmod(path, 0o444)
            elif path.is_dir(): os.chmod(path, 0o555)
        os.chmod(root, 0o555)
        holder.artifact_root = root  # type: ignore[attr-defined]
        return holder

    @staticmethod
    def _shape(payload: dict[str, Any]) -> tuple[bool, str]:
        if not {"corpus_count", "domain_counts", "results", "pmcids", "method"}.issubset(payload):
            return False, "missing_output_keys"
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != 12:
            return False, "result_count_not_12"
        pmcids = payload.get("pmcids")
        if not isinstance(pmcids, list) or len(pmcids) != 12 or len(set(map(str, pmcids))) != 12:
            return False, "pmcids_not_unique_12"
        for item in results:
            if not isinstance(item, dict) or set(item) != {"pmcid", "source", "score"}:
                return False, "result_schema_mismatch"
            if item["source"] not in {"cardiac", "vascular", "systemic"} or not isinstance(item["score"], (int, float)):
                return False, "result_type_mismatch"
        return True, "ok"

    def _metadata_invariance(self) -> tuple[bool, dict[str, Any]]:
        request = self._public_request()
        ok_a, base, _ = self._run_candidate(self.data, request)
        if not ok_a or not isinstance(base, dict): return False, {"reason": str(base)}
        holder = self._provenance_projected_artifacts()
        try:
            ok_b, changed, _ = self._run_candidate(holder.artifact_root, request)  # type: ignore[attr-defined]
        finally: holder.cleanup()
        if not ok_b or not isinstance(changed, dict): return False, {"reason": str(changed)}
        stable = base.get("pmcids") == changed.get("pmcids")
        return stable, {"ranking_stable": stable, "base_count": base.get("corpus_count"), "counterfactual_count": changed.get("corpus_count")}

    @staticmethod
    def _results_match(actual: Any, expected: Any, tolerance: float = 1e-10) -> bool:
        if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != len(expected):
            return False
        for got, want in zip(actual, expected):
            if not isinstance(got, dict) or not isinstance(want, dict):
                return False
            if str(got.get("pmcid")) != str(want.get("pmcid")):
                return False
            if str(got.get("source")) != str(want.get("source")):
                return False
            try:
                if abs(float(got.get("score")) - float(want.get("score"))) > tolerance:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    @classmethod
    def _rank_phase_match(cls, actual: dict[str, Any], expected: dict[str, Any]) -> bool:
        """Compare the four ranked cases inside each domain without requiring panel interleave."""
        actual_results = actual.get("results")
        expected_results = expected.get("results")
        if not isinstance(actual_results, list) or not isinstance(expected_results, list):
            return False
        for domain in ("cardiac", "vascular", "systemic"):
            got = [item for item in actual_results if isinstance(item, dict) and item.get("source") == domain]
            want = [item for item in expected_results if item.get("source") == domain]
            if not cls._results_match(got, want):
                return False
        return True

    def _semantic_match(
        self,
        artifact_root: Path,
        request: dict[str, Any],
        *,
        require_full_panel: bool,
    ) -> bool:
        ok, actual, _ = self._run_candidate(artifact_root, request)
        if not ok or not isinstance(actual, dict):
            return False
        expected = reference_rank_cases(artifact_root, request)
        if not require_full_panel:
            return self._rank_phase_match(actual, expected)
        expected_k = int(request.get("top_k", 12))
        results = actual.get("results"); pmcids = actual.get("pmcids")
        shape_ok = bool(isinstance(results, list) and len(results) == expected_k and isinstance(pmcids, list) and len(pmcids) == expected_k and len(set(map(str, pmcids))) == expected_k)
        return bool(
            shape_ok
            and actual.get("corpus_count") == expected["corpus_count"]
            and actual.get("domain_counts") == expected["domain_counts"]
            and actual.get("pmcids") == expected["pmcids"]
            and self._results_match(actual.get("results"), expected["results"])
        )

    def _provenance_projected_artifacts(self) -> tempfile.TemporaryDirectory:
        holder = tempfile.TemporaryDirectory(prefix="retrieval-provenance-")
        os.chmod(holder.name, 0o755)
        root = Path(holder.name) / "data"
        shutil.copytree(self.data, root)
        manifest_path = root / "source_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["files"]:
            if not entry.get("active"): continue
            path = root / entry["path"]
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            rows.reverse()
            for index, row in enumerate(rows):
                # Serving projection rewrites/removes display identity; stable record_key survives.
                row["display_id"] = str(row.pop("pmcid", "")) if index % 2 == 0 else f"citation-{index:04d}"
                row["final_diagnosis"] = "withheld_post_adjudication"
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
            entry["rows"] = len(rows)
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["files"] = manifest["files"][2:] + manifest["files"][:2]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        for path in root.rglob("*"):
            if path.is_file(): os.chmod(path, 0o444)
            elif path.is_dir(): os.chmod(path, 0o555)
        os.chmod(root, 0o555)
        holder.artifact_root = root  # type: ignore[attr-defined]
        return holder

    @staticmethod
    def _mismatch_count(actual: dict[str, Any], expected: dict[str, Any]) -> tuple[int, list[str]]:
        dims=[]; count=0
        got=actual.get("pmcids"); want=expected.get("pmcids")
        if not isinstance(got,list): return 99,["execution_or_shape"]
        maxlen=max(len(got),len(want))
        count += sum(1 for i in range(maxlen) if (got[i] if i<len(got) else None)!=(want[i] if i<len(want) else None))
        if actual.get("corpus_count")!=expected.get("corpus_count"): dims.append("population"); count+=4
        if actual.get("domain_counts")!=expected.get("domain_counts"): dims.append("domain_counts"); count+=4
        if got!=want: dims.append("panel_identity_or_order")
        return count, dims or ["none"]

    def _challenge_drift(self, probe: str) -> tuple[int, list[str]]:
        if probe == "provenance":
            holder=self._provenance_projected_artifacts()
            try:
                root=holder.artifact_root  # type: ignore[attr-defined]
                request=dict(self._public_request())
                ok, actual, _ = self._run_candidate(root, request, from_workspace=True)
                if not ok or not isinstance(actual,dict): return 99,["execution_or_shape"]
                expected=reference_rank_cases(root, request)
                return self._mismatch_count(actual, expected)
            finally: holder.cleanup()
        holder=self._mutated_artifacts()
        try:
            root=holder.artifact_root  # type: ignore[attr-defined]
            request=dict(self._public_request()); request["query_id"]="shadow_allocator_phase_delta"; request["top_k"]=10
            ok, actual, _ = self._run_candidate(root, request, from_workspace=True)
            if not ok or not isinstance(actual,dict): return 99,["execution_or_shape"]
            expected=reference_rank_cases(root, request)
            return self._mismatch_count(actual, expected)
        finally: holder.cleanup()

    def _shadow_semantic_match(self) -> bool:
        # Replay multiple structurally different hidden families on the deployed snapshot.
        holder_a = self._mutated_artifacts()
        try:
            req_a = dict(self._public_request()); req_a["query_id"] = "shadow_replay_allocator"; req_a["top_k"] = 11
            if not self._semantic_match(holder_a.artifact_root, req_a, require_full_panel=True):  # type: ignore[attr-defined]
                return False
        finally: holder_a.cleanup()
        holder_b = self._provenance_projected_artifacts()
        try:
            req_b = dict(self._public_request()); req_b["query_id"] = "shadow_replay_identity"; req_b["top_k"] = 13
            return self._semantic_match(holder_b.artifact_root, req_b, require_full_panel=True)  # type: ignore[attr-defined]
        finally: holder_b.cleanup()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        op = str(request.get("op", ""))
        if op == "health":
            return {"ok": True, "op": "health"}
        if op == "status":
            return self.status()
        state = self._load()
        if state["recovery_locked"]:
            return self._reject(op, "incident_locked_by_wrong_recovery")
        if state["observation_index"] < len(OBSERVATIONS):
            expected = OBSERVATIONS[state["observation_index"]]
        else:
            expected = SEQUENCE[state["next_index"]] if state["next_index"] < len(SEQUENCE) else None
        if op != expected:
            return self._reject(op, "out_of_order", expected=expected)

        try:
            if op == "inspect.sources":
                manifest = json.loads((self.data / "source_manifest.json").read_text())
                active = [entry for entry in manifest["files"] if entry.get("active")]
                listed = {Path(entry["path"]).name for entry in active}
                unlisted = sorted(path.name for path in (self.data / "cases").glob("*.jsonl") if path.name not in listed)
                return self._accept_observation(state, op, {
                    "active_files": [Path(entry["path"]).name for entry in active],
                    "expected_rows": manifest["expected_total_rows"], "unlisted_landing_files": unlisted,
                    "authority": "source_manifest.json",
                    "identity_registry_present": (self.data / "release_registry.jsonl").exists(),
                })

            if op == "inspect.schema":
                first = json.loads((self.data / "cases" / "cardiac_cases.jsonl").read_text().splitlines()[0])
                with zipfile.ZipFile(self.data / "operations_handoff.docx") as archive:
                    xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
                if "OPERATIONS HANDOFF" not in xml:
                    raise ValueError("handoff document unavailable")
                return self._accept_observation(state, op, {
                    "landing_fields": sorted(first),
                    "handoff_present": True,
                    "serving_boundary": "pre_outcome",
                    "note": "field eligibility is not declared in the landing schema",
                })

            if op == "audit.query":
                reader = PdfReader(str(self.data / "patient_case.pdf"))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                query = self._public_request()
                return self._accept_observation(state, op, {
                    "pdf_pages": len(reader.pages), "pdf_text_chars": len(text),
                    "facet_names": sorted(query["facets"]),
                    "facet_char_counts": {k: len(v) for k, v in sorted(query["facets"].items())},
                    "query_id": query.get("query_id"), "top_k": query.get("top_k"),
                    "outcome_boundary": "pre_ct_result",
                })

            if op == "profile.corpus":
                ok, payload, elapsed = self._run_candidate(self.data, self._public_request(), from_workspace=True)
                if not ok or not isinstance(payload, dict):
                    return self._accept_observation(state, op, {"candidate_ran": False, "reason": str(payload), "elapsed_sec": round(elapsed, 3)})
                top = collections.Counter(item.get("source") for item in payload.get("results", []))
                return self._accept_observation(state, op, {
                    "candidate_ran": True, "reported_corpus_count": payload.get("corpus_count"),
                    "reported_domain_counts": payload.get("domain_counts"),
                    "top12_source_counts": dict(sorted(top.items())), "method": payload.get("method"),
                    "elapsed_sec": round(elapsed, 3),
                })

            if op == "sample.records":
                samples = []
                for domain in ("cardiac", "vascular", "systemic"):
                    first = json.loads((self.data / "cases" / f"{domain}_cases.jsonl").read_text().splitlines()[0])
                    samples.append({"source": domain, "pmcid": first["pmcid"], "title": first["title"],
                                    "summary_prefix": first["case_summary"][:150]})
                return self._accept_observation(state, op, {"samples": samples, "note": "sample output is limited to pre-outcome text"})

            if op == "test.visible":
                proc = subprocess.run(
                    [sys.executable, "-m", "unittest", "-q", "tests.test_visible"], cwd=self.workspace,
                    env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0", "HOME": "/tmp", "LANG": "C.UTF-8"},
                    text=True, capture_output=True, timeout=120, preexec_fn=self._preexec(), check=False,
                )
                if proc.returncode != 0:
                    return self._reject(op, "visible_test_failed", summary=(proc.stderr or proc.stdout)[-240:])
                return self._accept_observation(state, op, {"passed": True, "returncode": proc.returncode,
                                                            "summary": (proc.stderr or proc.stdout)[-240:]})

            if op == "reproduce":
                ok, payload, elapsed = self._run_candidate(self.data, self._public_request(), from_workspace=True)
                if not ok or not isinstance(payload, dict):
                    return self._accept(state, op, {"candidate_ran": False, "reason": str(payload), "elapsed_sec": round(elapsed, 3)})
                evidence = json.loads(RANKING_EVIDENCE.read_text(encoding="utf-8"))
                anchors = evidence.get("public_anchors", {})
                ids = set(map(str, payload.get("pmcids", [])))
                anchor_hits = {domain: sum(pmcid in ids for pmcid in values) for domain, values in anchors.items()}
                return self._accept(state, op, {"candidate_ran": True, "reported_corpus_count": payload.get("corpus_count"),
                                                "anchor_hits": anchor_hits, "method": payload.get("method"),
                                                "elapsed_sec": round(elapsed, 3)})

            if op in {"deploy", "deploy.ranker", "deploy.final"}:
                snapshot_hash = self._safe_snapshot()
                state["candidate_hash"] = snapshot_hash
                state["snapshot_generation"] += 1
                if op == "deploy":
                    state["poison_active"] = True; state["recovered"] = False
                    self.recovery_receipt.unlink(missing_ok=True)
                elif op == "deploy.ranker":
                    state["ranker_clean"] = False
                elif op == "deploy.final":
                    state["final_clean"] = False; state["shadow_stable"] = False; state["cutover_ready"] = False
                    state["final_candidate_hash"] = snapshot_hash
                return self._accept(state, op, {"candidate_hash": snapshot_hash, "generation": state["snapshot_generation"]})

            if op == "validate.runtime":
                if state["validate_count"] >= VALIDATE_LIMIT:
                    return self._reject(op, "validate_rate_limited", limit=VALIDATE_LIMIT)
                state["validate_count"] += 1
                if state["poison_active"] and not state["recovered"]:
                    return self._accept(state, op, {"status": "dirty", "reason": "injected_failure",
                                                    "scope": "runtime_integrity_only",
                                                    "remaining": VALIDATE_LIMIT-state["validate_count"]})
                ok, payload, _ = self._run_candidate(self.data, self._public_request())
                clean, reason = self._shape(payload) if ok and isinstance(payload, dict) else (False, str(payload))
                return self._accept(state, op, {"status": "clean" if clean else "dirty", "reason": reason,
                                                "scope": "runtime_integrity_only",
                                                "remaining": VALIDATE_LIMIT-state["validate_count"]})

            if op == "diagnostics.read":
                return self._accept(state, op, {"family": RECOVERY_FAMILY,
                                                "symptom": "deployed_vector_snapshot_serves_pre_cutover_schema"})

            if op == "recovery.apply":
                family = str(request.get("family", ""))
                if family != RECOVERY_FAMILY:
                    state["recovery_locked"] = True
                    self._save(state)
                    return self._reject(op, "wrong_family_recovery_locked", got=family, expected=RECOVERY_FAMILY)
                state["poison_active"] = False; state["recovered"] = True
                receipt = {"family": family, "candidate_hash": state["candidate_hash"], "applied_ns": time.time_ns()}
                self.recovery_receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n")
                os.chmod(self.recovery_receipt, 0o600)
                return self._accept(state, op, {"family": family, "cache_generation": state["snapshot_generation"]})

            if op == "audit.provenance":
                stable, detail = self._metadata_invariance()
                ok, payload, _ = self._run_candidate(self.data, self._public_request())
                reported = payload if ok and isinstance(payload, dict) else {}
                manifest = json.loads((self.data / "source_manifest.json").read_text(encoding="utf-8"))
                return self._accept(state, op, {
                    "candidate_counterfactual_stability": "stable" if stable else "unstable",
                    "counterfactual": "row_order_plus_post_adjudication_rewrite_plus_display_identifier_projection",
                    "identity_authority": "release_registry.jsonl",
                    "join_key": "record_key",
                    "canonical_output_id": "pmcid",
                    "candidate_reported_corpus_count": reported.get("corpus_count"),
                    "candidate_reported_domain_counts": reported.get("domain_counts"),
                    "release_manifest_rows": manifest.get("expected_total_rows"),
                    "release_manifest_distinct": manifest.get("expected_distinct_pmcids"),
                    "base_count": detail.get("base_count"), "counterfactual_count": detail.get("counterfactual_count"),
                })

            if op == "audit.lexical":
                evidence = json.loads(RANKING_EVIDENCE.read_text(encoding="utf-8"))
                return self._accept(state, op, {
                    "receipt_kind": "release_index_behavior",
                    "domains": evidence["lexical_fingerprints"],
                    "anchor_identity_source": "broker-held incident anchors",
                    "note": "counts and positions are release observations, not a published vectorizer configuration",
                })

            if op == "audit.fusion":
                evidence = json.loads(RANKING_EVIDENCE.read_text(encoding="utf-8"))
                return self._accept(state, op, {
                    "receipt_kind": "cross_view_calibration",
                    "raw_similarity_scales": evidence["fusion_receipts"]["raw_similarity_scales"],
                    "calibration": evidence["fusion_receipts"]["calibration"],
                    "note": "calibration cases expose only the two within-view rank positions and the accepted release value",
                })

            if op == "validate.ranker":
                if state["validate_count"] >= VALIDATE_LIMIT:
                    return self._reject(op, "validate_rate_limited", limit=VALIDATE_LIMIT)
                state["validate_count"] += 1
                clean = self._semantic_match(
                    self.data,
                    self._public_request(),
                    require_full_panel=False,
                )
                state["ranker_clean"] = clean
                payload = {
                    "status": "clean" if clean else "dirty",
                    "reason": "hidden_rank_semantics",
                    "remaining": VALIDATE_LIMIT-state["validate_count"],
                }
                return self._accept(state, op, payload) if clean else self._accept_retry(state, op, payload, 9)

            if op == "audit.balance":
                ok, payload, _ = self._run_candidate(self.data, self._public_request())
                if not ok or not isinstance(payload, dict):
                    return self._accept(state, op, {"candidate_ran": False, "reason": str(payload)})
                counts = collections.Counter(item.get("source") for item in payload.get("results", []))
                evidence = json.loads(RANKING_EVIDENCE.read_text(encoding="utf-8"))
                return self._accept(state, op, {
                    "candidate_ran": True,
                    "public_source_counts": dict(sorted(counts.items())),
                    "required_spread": "max_minus_min_le_1",
                    "allocator": evidence["panel_allocator_receipt"],
                    "note": "infer the unique cyclic release behavior from these receipts; no implementation parameters are published",
                })

            if op == "validate.final":
                if state["validate_count"] >= VALIDATE_LIMIT:
                    return self._reject(op, "validate_rate_limited", limit=VALIDATE_LIMIT)
                state["validate_count"] += 1
                clean = self._semantic_match(
                    self.data,
                    self._public_request(),
                    require_full_panel=True,
                )
                state["final_clean"] = clean
                payload = {"status": "clean" if clean else "dirty", "reason": "hidden_panel_contract",
                           "remaining": VALIDATE_LIMIT-state["validate_count"]}
                return self._accept(state, op, payload) if clean else self._accept_retry(state, op, payload, 14)

            if op == "shadow.challenge":
                probe = str(request.get("probe", ""))
                if probe not in {"allocator", "provenance"}:
                    return self._reject(op, "challenge_probe_required", allowed=["allocator", "provenance"])
                if state.get("challenge_count", 0) >= CHALLENGE_LIMIT:
                    return self._reject(op, "challenge_rate_limited", limit=CHALLENGE_LIMIT)
                if probe in set(state.get("challenge_seen", [])):
                    return self._reject(op, "challenge_probe_already_used", probe=probe)
                drift, detail = self._challenge_drift(probe)
                state["challenge_count"] = state.get("challenge_count", 0) + 1
                state.setdefault("challenge_seen", []).append(probe)
                bucket = "0" if drift == 0 else ("1-3" if drift <= 3 else ("4-8" if drift <= 8 else "9+"))
                payload = {
                    "probe": probe,
                    "status": "stable" if drift == 0 else "drift",
                    "drift_bucket": bucket,
                    "dimensions": detail,
                    "remaining": CHALLENGE_LIMIT - state["challenge_count"],
                    "disclosure": "coarse mismatch magnitude only; scenario identities and expected PMCIDs withheld",
                }
                return self._accept(state, op, payload)

            if op == "shadow.replay":
                if not state.get("final_clean") or not state.get("ranker_clean"):
                    return self._reject(op, "validated_candidate_required")
                stable = self._shadow_semantic_match()
                state["shadow_stable"] = stable
                payload = {
                    "status": "stable" if stable else "unstable",
                    "reason": "hidden_counterfactual_contract",
                }
                return self._accept(state, op, payload) if stable else self._accept_retry(state, op, payload, 14)

            if op == "cutover.check":
                ready = bool(
                    state.get("shadow_stable") and state.get("final_clean") and state.get("ranker_clean")
                    and state.get("candidate_hash") == state.get("final_candidate_hash")
                )
                state["cutover_ready"] = ready
                payload = {"status": "ready" if ready else "blocked", "snapshot_generation": state["snapshot_generation"],
                           "candidate_hash": state.get("candidate_hash")}
                return self._accept(state, op, payload) if ready else self._reject(op, "cutover_precheck_failed")

            if op == "cutover":
                if not state.get("cutover_ready") or not state["shadow_stable"] or not state.get("final_clean") or not state.get("ranker_clean"):
                    return self._reject(op, "shadow_not_stable")
                return self._accept(state, op, {"cutover": "frozen_candidate"})

            if op == "promote":
                return self._accept(state, op, {"promotion": "accepted"})

            if op == "submit":
                state["submitted"] = True
                return self._accept(state, op, {"candidate_hash": state["candidate_hash"],
                                                "note": "terminal recomputed by verifier"})
        except subprocess.TimeoutExpired:
            return self._reject(op, "operation_timeout")
        except Exception as exc:  # noqa: BLE001
            return self._reject(op, "operation_error", detail=str(exc)[:300])
        return self._reject(op, "unhandled_operation")


def serve(args: argparse.Namespace) -> int:
    broker = Broker(Path(args.workspace), Path(args.state_dir), args.candidate_uid, args.candidate_gid)
    socket_path = Path(args.socket) if args.socket else None
    if args.tcp_port:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", args.tcp_port))
    else:
        if socket_path is None: raise ValueError("--socket is required without --tcp-port")
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path)); os.chmod(socket_path, 0o666)
    server.listen(16)

    def stop(_signum, _frame):
        try: server.close()
        finally:
            if socket_path is not None: socket_path.unlink(missing_ok=True)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                data = bytearray()
                while len(data) <= 16384:
                    block = conn.recv(4096)
                    if not block: break
                    data.extend(block)
                    if b"\n" in block: break
                if len(data) > 16384: raise ValueError("request too large")
                request = json.loads(bytes(data).splitlines()[0])
                response = broker.handle(request)
            except Exception as exc:  # noqa: BLE001
                response = {"ok": False, "error": f"bad_request:{str(exc)[:200]}"}
            conn.sendall(json.dumps(response, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket")
    parser.add_argument("--tcp-port", type=int, default=0, help="author-side fallback when AF_UNIX is sandboxed")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--candidate-uid", type=int, required=True)
    parser.add_argument("--candidate-gid", type=int, required=True)
    return serve(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
