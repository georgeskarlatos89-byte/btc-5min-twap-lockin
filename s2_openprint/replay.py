"""Replay normalized JSONL. Outcomes optional; never labels candidates actual fills."""
import argparse,json,collections
from engine import Config,evaluate

def replay(rows,c):
    counts=collections.Counter(); seen=set(); trades=[]
    for r in sorted(rows,key=lambda x:x['now']):
        key=(r['duration'],r['start'])
        if key in seen: continue
        d=evaluate(r,c); counts[d.get('reason',d['action'])]+=1
        if d['action']!='candidate': continue
        seen.add(key)
        if r.get('winner') in ('Up','Down'):
            win=int(r['winner']==d['side'])
            trades.append(dict(start=r['start'],duration=r['duration'],win=win,
                               pnl=d['shares']*win-d['budget']))
    n=len(trades); w=sum(t['win'] for t in trades)
    lo=hi=None
    if n:
        z=1.96; p=w/n; den=1+z*z/n
        mid=(p+z*z/(2*n))/den
        half=z*((p*(1-p)/n+z*z/(4*n*n))**.5)/den
        lo,hi=mid-half,mid+half
    return dict(status='RESEARCH_ONLY_NOT_PROMOTED',counts=dict(counts),resolved_candidates=n,
                hypothetical_pnl=sum(t['pnl'] for t in trades),win_rate=w/n if n else None,
                descriptive_wilson_95=[lo,hi],
                caveat='Overlapping 5m/15m outcomes are correlated. CI is descriptive, not a promotion test. No execution or maker fills assumed.',trades=trades)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('input'); p.add_argument('--output',default='report.json')
    a=p.parse_args()
    with open(a.input) as f: rows=[json.loads(l) for l in f if l.strip()]
    result=replay(rows,Config())
    with open(a.output,'w') as f: json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='trades'},indent=2))
