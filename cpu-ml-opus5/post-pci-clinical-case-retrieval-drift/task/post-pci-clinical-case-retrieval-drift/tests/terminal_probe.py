#!/usr/bin/env python3
"""Recompute the environment-owned terminal over seed-selected real-corpus scenarios."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from reference_engine import rank_cases
from reference_engine_alt import rank_cases as rank_cases_alt

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("TASK_STATE_DIR", "/var/lib/post_pci_retrieval"))
CANDIDATE = STATE_DIR / "candidate" / "src"
FIXTURE = HERE / "verifier_fixture" / "data"
SCENARIOS = json.loads((HERE / "hidden_scenarios.json").read_text())
SOURCE_FILES = ("__init__.py", "artifact_loader.py", "text_views.py", "ranker.py", "cli.py")
MAX_SOURCE_BYTES = 512 * 1024
TIMEOUT_SEC = int(os.environ.get("PROBE_ITEM_TIMEOUT_SEC", "90"))


class _ScmpArgCmp(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_uint),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def _install_candidate_seccomp() -> None:
    """Prevent candidate writes and network side channels during hidden replay."""
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


def _lock_verifier_tree() -> None:
    """Make every verifier asset root-only before any candidate subprocess starts."""
    if os.geteuid() != 0:
        raise PermissionError("terminal probe must run as root to isolate verifier assets")
    if HERE.is_symlink() or not HERE.is_dir():
        raise ValueError("verifier root must be a real directory")
    for current, dirnames, filenames in os.walk(HERE, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in filenames:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"symlink in verifier tree: {path.relative_to(HERE)}")
            os.chown(path, 0, 0, follow_symlinks=False)
            os.chmod(path, 0o600)
        for name in dirnames:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"symlink in verifier tree: {path.relative_to(HERE)}")
            os.chown(path, 0, 0, follow_symlinks=False)
            os.chmod(path, 0o700)
    os.chown(HERE, 0, 0, follow_symlinks=False)
    os.chmod(HERE, 0o700)


def _copy_regular(src: Path, dst: Path) -> None:
    info = src.lstat()
    if not stat.S_ISREG(info.st_mode) or src.is_symlink() or info.st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"unsafe candidate file: {src.name}")
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    try: data = os.read(fd, MAX_SOURCE_BYTES + 1)
    finally: os.close(fd)
    if len(data) != info.st_size: raise ValueError("candidate changed during probe snapshot")
    dst.write_bytes(data); os.chmod(dst, 0o444)


def _readonly_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file(): os.chmod(path, 0o444)
        elif path.is_dir(): os.chmod(path, 0o555)
    os.chmod(root, 0o555)


def _mutate_fixture(root: Path, variant: str) -> None:
    if variant == "canonical_with_quarantine_copy":
        return
    if variant == "extra_unlisted_copy":
        source = root / "cases" / "cardiac_cases.jsonl"
        (root / "cases" / "cardiac_delivery_retry.jsonl").write_bytes(source.read_bytes())
        return
    if variant in {"provenance_projection_and_reverse", "provenance_projection_manifest_rotate"}:
        manifest_path = root / "source_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["files"]:
            if not entry.get("active"): continue
            path = root / entry["path"]
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            rows.reverse()
            for index, row in enumerate(rows):
                row["display_id"] = str(row.pop("pmcid", "")) if index % 2 == 0 else f"citation-{index:04d}"
                row["final_diagnosis"] = "withheld_post_adjudication"
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
            entry["rows"] = len(rows)
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if variant == "provenance_projection_manifest_rotate": manifest["files"] = manifest["files"][1:] + manifest["files"][:1]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        return
    manifest_path = root / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for domain_index, entry in enumerate(manifest["files"]):
        if not entry.get("active"):
            continue
        path = root / entry["path"]
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if variant in {"redact_and_reverse", "journal_redaction_reverse"}:
            rows.reverse()
        elif variant == "strip_and_rotate":
            rows = rows[domain_index+1:] + rows[:domain_index+1]
        for index, row in enumerate(rows):
            if variant == "strip_and_rotate":
                row.pop("final_diagnosis", None); row.pop("domain_hits", None)
            elif variant == "redact_and_reverse":
                row["final_diagnosis"] = f"withheld_{(index + domain_index) % 11}"
                row["domain_hits"] = {"cardiac": index % 2, "vascular": 77, "systemic": 5-index % 3}
            elif variant == "journal_redaction_reverse":
                row["journal"] = f"display_withheld_{(index + 3*domain_index) % 13}"
            elif variant == "unicode_whitespace":
                for field in ("title", "case_summary"):
                    value = unicodedata.normalize("NFD", str(row.get(field, "")))
                    parts = value.split(" ")
                    if len(parts) > 3:
                        value = "\u00a0".join(parts[:3]) + " " + " ".join(parts[3:])
                    row[field] = value
        path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows))
        entry["rows"] = len(rows)
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if variant == "redact_and_reverse":
        manifest["files"] = list(reversed(manifest["files"]))
    elif variant == "strip_and_rotate":
        manifest["files"] = manifest["files"][1:] + manifest["files"][:1]
    elif variant == "journal_redaction_reverse":
        manifest["files"] = manifest["files"][2:] + manifest["files"][:2]
    elif variant == "unicode_whitespace":
        manifest["files"] = list(reversed(manifest["files"]))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _preexec(uid: int, gid: int):
    def apply() -> None:
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (55, 55))
        resource.setrlimit(resource.RLIMIT_AS, (2_500_000_000, 2_500_000_000))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2_000_000, 2_000_000))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if os.geteuid() == 0 and uid != 0:
            os.setgroups([]); os.setgid(gid); os.setuid(uid)
        try: ctypes.CDLL(None).prctl(38, 1, 0, 0, 0)
        except Exception: pass
        _install_candidate_seccomp()
    return apply


def _verify_verifier_isolation(uid: int, gid: int) -> tuple[bool, dict[str, bool]]:
    """Prove the candidate uid cannot import the oracle or read hidden scenarios."""
    if uid == 0 or gid == 0:
        return False, {"reference_import_denied": False, "hidden_scenarios_denied": False}
    root = repr(str(HERE))
    attacks = {
        "reference_import_denied": (
            f"import sys; sys.path.insert(0, {root}); "
            "from reference_engine import rank_cases"
        ),
        "hidden_scenarios_denied": (
            f"from pathlib import Path; Path({root}).joinpath('hidden_scenarios.json').read_bytes()"
        ),
    }
    results: dict[str, bool] = {}
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONHASHSEED": "0", "HOME": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name, code in attacks.items():
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", code], cwd="/tmp", env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False, preexec_fn=_preexec(uid, gid),
            )
            results[name] = proc.returncode != 0
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            results[name] = False
    return all(results.values()), results


def _run_candidate(package_parent: Path, artifact_root: Path, request: dict[str, Any]) -> tuple[bool, Any, str]:
    uid = int(os.environ.get("PROBE_CANDIDATE_UID", "65534"))
    gid = int(os.environ.get("PROBE_CANDIDATE_GID", "65534"))
    stdout = package_parent / "stdout.txt"; stderr = package_parent / "stderr.txt"
    stdout.touch(); stderr.touch(); os.chmod(stdout, 0o666); os.chmod(stderr, 0o666)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "PYTHONPATH": str(package_parent),
           "PYTHONHASHSEED": "0", "HOME": str(package_parent), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    with stdout.open("wb") as out, stderr.open("wb") as err:
        proc = subprocess.Popen([sys.executable, "-m", "src.cli", "--artifact-root", str(artifact_root), "--request-stdin"],
                                cwd=package_parent, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                preexec_fn=_preexec(uid, gid))
        try: proc.communicate(json.dumps(request).encode(), timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait(timeout=5); return False, None, "candidate_timeout"
        finally:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
    if proc.returncode != 0:
        return False, None, f"candidate_exit_{proc.returncode}:{stderr.read_text(errors='replace')[-180:]}"
    try: return True, json.loads(stdout.read_text().strip().splitlines()[-1]), "ok"
    except Exception as exc: return False, None, f"candidate_json:{exc}"


def _results_match(actual: Any, expected: Any, tolerance: float = 1e-8) -> bool:
    if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != len(expected):
        return False
    for got, want in zip(actual, expected):
        if not isinstance(got, dict) or not isinstance(want, dict):
            return False
        if str(got.get("pmcid")) != str(want.get("pmcid")) or got.get("source") != want.get("source"):
            return False
        try:
            if abs(float(got.get("score")) - float(want.get("score"))) > tolerance:
                return False
        except (TypeError, ValueError):
            return False
    return True


def probe() -> dict[str, Any]:
    uid = int(os.environ.get("PROBE_CANDIDATE_UID", "65534"))
    gid = int(os.environ.get("PROBE_CANDIDATE_GID", "65534"))
    try:
        _lock_verifier_tree()
    except Exception as exc:
        return {"healthy": False, "reason": f"verifier_lock_failed:{exc}"}
    isolation_ok, isolation_checks = _verify_verifier_isolation(uid, gid)
    if not isolation_ok:
        return {"healthy": False, "reason": "verifier_isolation_failed", "isolation_checks": isolation_checks}
    try:
        state = json.loads((STATE_DIR / "state.json").read_text())
    except Exception as exc:
        return {"healthy": False, "reason": f"private_state_unavailable:{exc}",
                "isolation_checks": isolation_checks}
    if not state.get("submitted"):
        return {"healthy": False, "reason": "environment_terminal_not_submitted",
                "isolation_checks": isolation_checks}
    try:
        receipt = json.loads((STATE_DIR / "recovery_receipt.json").read_text())
    except Exception as exc:
        return {"healthy": False, "reason": f"private_state_unavailable:{exc}",
                "isolation_checks": isolation_checks}
    if state.get("poison_active") or not state.get("recovered") or receipt.get("family") != "stale_vector_cache":
        return {"healthy": False, "reason": "injected_failure_active",
                "isolation_checks": isolation_checks}
    if not CANDIDATE.is_dir():
        return {"healthy": False, "reason": "missing_environment_candidate",
                "isolation_checks": isolation_checks}

    seed = int(state.get("instance_seed", 0))
    # Guarantee coverage of structurally distinct families while keeping per-instance selection deterministic.
    groups = {}
    for scenario in SCENARIOS: groups.setdefault(scenario.get("family", "other"), []).append(scenario)
    required_families = ["baseline", "allocator", "provenance", "landing_noise"]
    selected = [groups[f][(seed + idx) % len(groups[f])] for idx, f in enumerate(required_families)]
    fingerprints = []
    scenario_details = []
    for scenario in selected:
        with tempfile.TemporaryDirectory(prefix="terminal-probe-") as tmp_name:
            tmp = Path(tmp_name); os.chmod(tmp, 0o755)
            package = tmp / "src"; package.mkdir(); os.chmod(package, 0o755)
            try:
                for name in SOURCE_FILES: _copy_regular(CANDIDATE / name, package / name)
            except Exception as exc:
                return {"healthy": False, "reason": f"snapshot_copy:{exc}",
                        "isolation_checks": isolation_checks}
            os.chmod(package, 0o555)
            artifact_root = tmp / "data"
            shutil.copytree(FIXTURE, artifact_root)
            _mutate_fixture(artifact_root, scenario["artifact_variant"])
            expected = rank_cases(artifact_root, scenario["request"])
            expected_alt = rank_cases_alt(artifact_root, scenario["request"])
            if expected["pmcids"] != expected_alt["pmcids"] or not _results_match(expected["results"], expected_alt["results"]):
                return {"healthy": False, "reason": "differential_reference_disagreement", "scenario": scenario["name"], "isolation_checks": isolation_checks}
            _readonly_tree(artifact_root)
            ok, actual, reason = _run_candidate(tmp, artifact_root, scenario["request"])
            if not ok or not isinstance(actual, dict):
                return {"healthy": False, "reason": reason, "scenario": scenario["name"],
                        "isolation_checks": isolation_checks}
            checks = {
                "pmcids": actual.get("pmcids") == expected["pmcids"],
                "results": _results_match(actual.get("results"), expected["results"]),
                "corpus_count": actual.get("corpus_count") == 1500,
                "domain_counts": actual.get("domain_counts") == {"cardiac": 500, "systemic": 500, "vascular": 500},
            }
            scenario_details.append({"name": scenario["name"], "checks": checks})
            if not all(checks.values()):
                failed = [name for name, passed in checks.items() if not passed]
                return {"healthy": False, "reason": "terminal_value_mismatch:" + ",".join(failed),
                        "scenario": scenario["name"], "checks": checks,
                        "isolation_checks": isolation_checks}
            fingerprints.append(hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest())
    terminal_hash = hashlib.sha256((str(seed) + "|" + "|".join(fingerprints)).encode()).hexdigest()
    return {"healthy": True, "reason": "terminal_matches_r10", "scenario_count": len(selected),
            "terminal_hash": terminal_hash, "details": scenario_details,
            "isolation_checks": isolation_checks}


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True, separators=(",", ":")))
