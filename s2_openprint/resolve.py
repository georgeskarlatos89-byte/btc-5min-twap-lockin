"""Attach only explicitly resolved Gamma outcomes. Re-run for pending markets."""
import argparse,json,time
from capture import get,array

def winner(m):
    if not m.get('closed') or m.get('umaResolutionStatus')!='resolved': return None
    outcomes=array(m.get('outcomes',[])); prices=array(m.get('outcomePrices',[]))
    if len(outcomes)!=2 or set(outcomes)!={'Up','Down'} or len(prices)!=2: return None
    prices=[float(p) for p in prices]
    if sorted(prices)!=[0.,1.]: return None
    return outcomes[prices.index(1.)]

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('input'); p.add_argument('output'); a=p.parse_args()
    if a.input==a.output: p.error('Use a separate output path')
    cache={}
    with open(a.input) as src,open(a.output,'w') as dst:
        for line in src:
            r=json.loads(line); slug=r['slug']
            if slug not in cache:
                try:
                    markets=get('https://gamma-api.polymarket.com/markets?slug='+slug)
                    cache[slug]=winner(markets[0]) if markets else None
                except Exception: cache[slug]=None
                time.sleep(.1)
            r['winner']=cache[slug]; dst.write(json.dumps(r)+'\n')
    print('Resolved',sum(v is not None for v in cache.values()),'of',len(cache),'markets; others remain pending.')
