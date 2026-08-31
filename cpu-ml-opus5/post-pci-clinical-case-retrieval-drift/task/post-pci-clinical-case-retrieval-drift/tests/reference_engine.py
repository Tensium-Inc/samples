"""Verifier-only R10 reference engine. Never copied into the agent workspace."""
from __future__ import annotations
import hashlib,json,re,unicodedata
from collections import Counter
from pathlib import Path
from typing import Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel
BASE_DOMAINS=("cardiac","systemic","vascular")

def normalize(text): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(text)).lower()).strip()
def _sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def _registry(root):
    out={}
    for line in (root/"release_registry.jsonl").read_text().splitlines():
        if line.strip():
            x=json.loads(line); out[str(x["record_key"])]=(str(x["pmcid"]),str(x["domain"]))
    return out

def load_cases(root:Path):
    m=json.loads((root/"source_manifest.json").read_text()); reg=_registry(root); rows=[]; seen=set()
    for e in m["files"]:
        if not e.get("active"): continue
        p=root/e["path"]
        if _sha(p)!=e["sha256"]: raise ValueError("reference fixture digest mismatch")
        current=[json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        if len(current)!=e["rows"]: raise ValueError("reference fixture row mismatch")
        for row in current:
            key=str(row["record_key"]); pmcid,domain=reg[key]
            if domain!=e["domain"] or pmcid in seen: raise ValueError("reference identity mismatch")
            seen.add(pmcid); item=dict(row); item["pmcid"]=pmcid; item["source"]=domain; rows.append(item)
    if len(rows)!=1500 or len(seen)!=1500: raise ValueError("reference fixture total mismatch")
    return rows

def _text(r): return normalize(f"{r.get('title','')}\n{r.get('case_summary','')}")
def _positions(scores,rows):
    order=sorted(range(len(rows)),key=lambda i:(-float(scores[i]),str(rows[i]["pmcid"])))
    return {str(rows[i]["pmcid"]):rank for rank,i in enumerate(order,1)}
def _domain(rows,query):
    docs=[_text(r) for r in rows]
    views=[TfidfVectorizer(strip_accents="unicode",ngram_range=(1,2),min_df=2,max_df=.98,sublinear_tf=True,norm="l2"),TfidfVectorizer(strip_accents="unicode",analyzer="char_wb",ngram_range=(3,5),min_df=2,max_features=60000,sublinear_tf=True,norm="l2")]
    ranks=[]
    for v in views:
        matrix=v.fit_transform(docs); ranks.append(_positions(linear_kernel(v.transform([normalize(query)]),matrix).ravel(),rows))
    fused=[]
    for row in rows:
        p=str(row["pmcid"]); fused.append((row,1/(60+ranks[0][p])+1/(60+ranks[1][p])))
    return sorted(fused,key=lambda x:(-x[1],str(x[0]["pmcid"])))
def _cycle(req):
    q=normalize(req.get("query_id","")); phase=sum(q.encode("utf-8"))%3
    if int(req.get("top_k",12))%2: return BASE_DOMAINS[phase:]+BASE_DOMAINS[:phase]
    return tuple(BASE_DOMAINS[(phase-i)%len(BASE_DOMAINS)] for i in range(len(BASE_DOMAINS)))
def _weave(per,cycle,k):
    if k<1: raise ValueError("top_k must be positive")
    out=[]; i=0
    while len(out)<k:
        moved=False
        for d in cycle:
            if i<len(per[d]): out.append(per[d][i]); moved=True
            if len(out)==k: break
        if not moved: break
        i+=1
    if len(out)!=k: raise ValueError("top_k exceeds available release rows")
    return out
def rank_cases(root:Path,request:dict[str,Any]):
    rows=load_cases(root); per={d:_domain([r for r in rows if r["source"]==d],request["facets"][d]) for d in BASE_DOMAINS}
    selected=_weave(per,_cycle(request),int(request.get("top_k",12)))
    results=[{"pmcid":str(r["pmcid"]),"source":r["source"],"score":round(float(s),12)} for r,s in selected]
    counts=Counter(r["source"] for r in rows)
    return {"corpus_count":len(rows),"domain_counts":dict(sorted(counts.items())),"results":results,"pmcids":[x["pmcid"] for x in results],"method":"release_rank_fusion"}
