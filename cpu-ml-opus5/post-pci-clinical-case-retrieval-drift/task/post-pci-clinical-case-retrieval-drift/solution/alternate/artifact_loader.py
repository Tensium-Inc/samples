"""Alternate-correct registry loader using eager maps and comprehensions."""
from __future__ import annotations
import json,hashlib
from pathlib import Path

def load_cases(root:Path):
    reg={x["record_key"]:x for x in (json.loads(line) for line in (root/"release_registry.jsonl").read_text().splitlines() if line.strip())}
    m=json.loads((root/"source_manifest.json").read_text()); out=[]; used=set()
    for e in m["files"]:
        if not e.get("active"): continue
        p=root/e["path"]
        if hashlib.sha256(p.read_bytes()).hexdigest()!=e["sha256"]: raise ValueError("digest mismatch")
        rows=[json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        if len(rows)!=e["rows"]: raise ValueError("row mismatch")
        for r in rows:
            meta=reg[str(r["record_key"])]
            if meta["domain"]!=e["domain"]: raise ValueError("domain mismatch")
            pid=str(meta["pmcid"]); key=str(r["record_key"])
            if (pid,key) in used or any(pid==p for p,_ in used): raise ValueError("duplicate identity")
            used.add((pid,key)); x=dict(r); x["pmcid"]=pid; x["source"]=str(meta["domain"]); out.append(x)
    if len(out)!=m["expected_total_rows"]: raise ValueError("total mismatch")
    return out

def load_request(path:Path): return json.loads(path.read_text())
