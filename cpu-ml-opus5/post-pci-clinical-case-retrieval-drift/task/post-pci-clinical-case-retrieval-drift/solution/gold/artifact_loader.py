"""Manifest + registry authoritative loader for release R10."""
from __future__ import annotations
import hashlib, json, stat
from pathlib import Path
from typing import Any
REQUIRED_FIELDS={"record_key","title","case_summary"}

def _sha256(path: Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as s:
        for block in iter(lambda:s.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def _regular(path: Path)->None:
    mode=path.lstat().st_mode
    if not stat.S_ISREG(mode) or path.is_symlink(): raise ValueError(f"manifest path is not a regular file: {path}")

def _registry(root: Path)->dict[str,dict[str,str]]:
    out={}
    for line in (root/"release_registry.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        item=json.loads(line); key=str(item["record_key"])
        if key in out: raise ValueError("duplicate record_key in release registry")
        out[key]={"pmcid":str(item["pmcid"]),"domain":str(item["domain"])}
    return out

def load_cases(artifact_root: Path)->list[dict[str,Any]]:
    artifact_root=artifact_root.resolve(); manifest=json.loads((artifact_root/"source_manifest.json").read_text())
    reg=_registry(artifact_root); rows=[]; seen=set(); seen_keys=set()
    for entry in manifest["files"]:
        if not entry.get("active"): continue
        rel=Path(str(entry["path"]))
        if rel.is_absolute() or ".." in rel.parts: raise ValueError("unsafe manifest path")
        path=(artifact_root/rel).resolve()
        if artifact_root not in path.parents: raise ValueError("manifest path escaped artifact root")
        _regular(path)
        if _sha256(path)!=str(entry["sha256"]): raise ValueError(f"sha256 mismatch: {rel}")
        current=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(current)!=int(entry["rows"]): raise ValueError(f"row count mismatch: {rel}")
        declared_domain=str(entry["domain"])
        for row in current:
            if not REQUIRED_FIELDS.issubset(row): raise ValueError(f"missing serving fields in {rel}")
            key=str(row["record_key"]); meta=reg.get(key)
            if meta is None: raise ValueError(f"unregistered record_key: {key}")
            if meta["domain"]!=declared_domain: raise ValueError(f"registry domain mismatch: {key}")
            pmcid=meta["pmcid"]
            if key in seen_keys or pmcid in seen: raise ValueError("duplicate active release identity")
            seen_keys.add(key); seen.add(pmcid)
            item=dict(row); item["pmcid"]=pmcid; item["source"]=declared_domain; rows.append(item)
    if len(rows)!=int(manifest["expected_total_rows"]): raise ValueError("active total does not match manifest")
    if len(seen)!=int(manifest["expected_distinct_pmcids"]): raise ValueError("distinct PMCID total does not match manifest")
    return rows

def load_request(path: Path)->dict[str,Any]: return json.loads(path.read_text(encoding="utf-8"))
