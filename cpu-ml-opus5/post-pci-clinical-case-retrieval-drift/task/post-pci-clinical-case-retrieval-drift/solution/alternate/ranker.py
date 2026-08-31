"""Alternate-correct R10 ranker with explicit score arrays and round generator."""
from __future__ import annotations
import unicodedata,re
from collections import Counter
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
DOMAINS=["cardiac","systemic","vascular"]
def _norm(x): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(x)).lower()).strip()
def _domain(rows,q):
    docs=[_norm(str(r.get("title",""))+"\n"+str(r.get("case_summary",""))) for r in rows]
    cfgs=[{"strip_accents":"unicode","ngram_range":(1,2),"min_df":2,"max_df":.98,"sublinear_tf":True,"norm":"l2"},{"strip_accents":"unicode","analyzer":"char_wb","ngram_range":(3,5),"min_df":2,"max_features":60000,"sublinear_tf":True,"norm":"l2"}]
    poss=[]
    for cfg in cfgs:
        v=TfidfVectorizer(**cfg); X=v.fit_transform(docs); sc=cosine_similarity(v.transform([_norm(q)]),X).ravel()
        ordered=sorted(range(len(rows)),key=lambda i:(-float(sc[i]),str(rows[i]["pmcid"])))
        poss.append({str(rows[i]["pmcid"]):rank for rank,i in enumerate(ordered,1)})
    scored=[(r,sum(1.0/(60+p[str(r["pmcid"])]) for p in poss)) for r in rows]
    scored.sort(key=lambda z:(-z[1],str(z[0]["pmcid"])))
    return scored
def rank_cases(rows,request):
    ranked={d:_domain([r for r in rows if str(r["source"])==d],request["facets"][d]) for d in DOMAINS}
    phase=sum(_norm(request.get("query_id","")).encode("utf-8"))%3; k=int(request.get("top_k",12))
    cycle=(DOMAINS[phase:]+DOMAINS[:phase]) if k%2 else [DOMAINS[(phase-i)%len(DOMAINS)] for i in range(len(DOMAINS))]
    chosen=[]
    for depth in range(500):
        for domain in cycle:
            if depth<len(ranked[domain]): chosen.append(ranked[domain][depth])
            if len(chosen)>=k: break
        if len(chosen)>=k: break
    results=[{"pmcid":str(r["pmcid"]),"source":str(r["source"]),"score":round(float(s),12)} for r,s in chosen]
    counts=Counter(str(r["source"]) for r in rows)
    return {"corpus_count":len(rows),"domain_counts":dict(sorted(counts.items())),"results":results,"pmcids":[x["pmcid"] for x in results],"method":"alternate_release_rank_fusion"}
