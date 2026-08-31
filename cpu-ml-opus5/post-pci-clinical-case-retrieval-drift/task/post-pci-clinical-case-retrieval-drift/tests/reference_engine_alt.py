"""Independent R10 reference: same behavior, differently structured implementation."""
from __future__ import annotations
import json,hashlib,re,unicodedata
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
DOMAINS=["cardiac","systemic","vascular"]
def norm(x): return " ".join(unicodedata.normalize("NFKC",str(x)).lower().split())
def rank_cases(root:Path, request:dict):
    registry={}
    for raw in (root/"release_registry.jsonl").read_text().splitlines():
        if raw.strip():
            x=json.loads(raw); registry[str(x["record_key"])]=x
    manifest=json.loads((root/"source_manifest.json").read_text()); by=defaultdict(list); seen=set()
    for ent in manifest["files"]:
        if not ent.get("active"): continue
        p=root/ent["path"]
        if hashlib.sha256(p.read_bytes()).hexdigest()!=ent["sha256"]: raise ValueError("digest")
        raw_rows=[json.loads(z) for z in p.read_text().splitlines() if z.strip()]
        if len(raw_rows)!=ent["rows"]: raise ValueError("rows")
        for row in raw_rows:
            meta=registry[str(row["record_key"])]
            if str(meta["domain"])!=str(ent["domain"]): raise ValueError("domain")
            pid=str(meta["pmcid"])
            if pid in seen: raise ValueError("dup")
            seen.add(pid); c=dict(row); c.update(pmcid=pid,source=str(meta["domain"])); by[c["source"]].append(c)
    if len(seen)!=manifest["expected_distinct_pmcids"]: raise ValueError("total")
    ranked={}
    for domain in DOMAINS:
        rows=by[domain]; docs=[norm(r.get("title"," ")+"\n"+r.get("case_summary","")) for r in rows]; q=norm(request["facets"][domain])
        configs=[dict(strip_accents="unicode",ngram_range=(1,2),min_df=2,max_df=.98,sublinear_tf=True,norm="l2"),dict(strip_accents="unicode",analyzer="char_wb",ngram_range=(3,5),min_df=2,max_features=60000,sublinear_tf=True,norm="l2")]
        positions=[]
        for cfg in configs:
            vec=TfidfVectorizer(**cfg); X=vec.fit_transform(docs); s=cosine_similarity(vec.transform([q]),X).ravel()
            idx=np.array(sorted(range(len(rows)),key=lambda i:(-float(s[i]),str(rows[i]["pmcid"]))))
            positions.append({str(rows[i]["pmcid"]):j+1 for j,i in enumerate(idx)})
        items=[]
        for row in rows:
            pid=str(row["pmcid"]); score=sum(1.0/(60+pos[pid]) for pos in positions); items.append((row,score))
        ranked[domain]=sorted(items,key=lambda z:(-z[1],str(z[0]["pmcid"])))
    phase=sum(norm(request.get("query_id","")).encode())%len(DOMAINS)
    cycle=(DOMAINS[phase:]+DOMAINS[:phase]) if int(request.get("top_k",12))%2 else [DOMAINS[(phase-i)%len(DOMAINS)] for i in range(len(DOMAINS))]
    k=int(request.get("top_k",12)); chosen=[]
    for depth in range(max(len(ranked[d]) for d in cycle)):
        for d in cycle:
            if depth<len(ranked[d]): chosen.append(ranked[d][depth])
            if len(chosen)==k: break
        if len(chosen)==k: break
    results=[{"pmcid":str(r["pmcid"]),"source":r["source"],"score":round(float(s),12)} for r,s in chosen]
    counts=Counter(x["source"] for x in sum(by.values(),[]))
    return {"corpus_count":sum(counts.values()),"domain_counts":dict(sorted(counts.items())),"results":results,"pmcids":[x["pmcid"] for x in results],"method":"alt_release_engine"}
