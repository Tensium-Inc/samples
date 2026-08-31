#!/usr/bin/env python3
"""Author-side model-free checks for R10 structural sensitivity and discoverability."""
from __future__ import annotations
import json, tempfile, shutil, hashlib
from pathlib import Path
from reference_engine import rank_cases
from terminal_probe import _mutate_fixture
HERE=Path(__file__).resolve().parent; FIX=HERE/"verifier_fixture"/"data"; SC=json.loads((HERE/"hidden_scenarios.json").read_text())

def manifest_order_cycle_reference(root, req):
    # Deliberately broken structural hypothesis: rewrite query_id so cycle begins at manifest order.
    q=dict(req); q["query_id"]=""  # phase 0 => vascular/cardiac/systemic, not request-bound for most cases
    return rank_cases(root,q)

def ignore_registry_projection(root, req):
    # Proxy for a loader that treats projected display identity as canonical: projected cases become unusable.
    m=json.loads((root/"source_manifest.json").read_text()); missing=0
    for e in m["files"]:
        if e.get("active"):
            for line in (root/e["path"]).read_text().splitlines():
                if line.strip() and "pmcid" not in json.loads(line): missing += 1
    return missing

def main():
    allocator=next(s for s in SC if s["family"]=="allocator")
    provenance=next(s for s in SC if s["family"]=="provenance")
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)/"data"; shutil.copytree(FIX,root); _mutate_fixture(root,allocator["artifact_variant"])
        good=rank_cases(root,allocator["request"]); bad=manifest_order_cycle_reference(root,allocator["request"])
        allocator_sensitive = good["pmcids"] != bad["pmcids"]
        allocator_feedback = sum(a!=b for a,b in zip(good["pmcids"],bad["pmcids"])) > 0
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)/"data"; shutil.copytree(FIX,root); _mutate_fixture(root,provenance["artifact_variant"])
        good=rank_cases(root,provenance["request"])
        missing=ignore_registry_projection(root,provenance["request"])
        provenance_sensitive = missing > 0 and len(good["pmcids"])==provenance["request"]["top_k"]
        provenance_feedback = missing > 0
    result={"allocator_terminal_sensitive":allocator_sensitive,"allocator_act_feedback_nonzero":allocator_feedback,"provenance_terminal_sensitive":provenance_sensitive,"provenance_act_feedback_nonzero":provenance_feedback}
    result["pass"]=all(result.values()); print(json.dumps(result,indent=2,sort_keys=True)); raise SystemExit(0 if result["pass"] else 1)
if __name__=="__main__": main()
