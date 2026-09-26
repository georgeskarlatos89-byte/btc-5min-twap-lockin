"""Read-only S2 forward recorder. Legacy RTDS topics match the existing repo.
Logs candidate spot/TWAP refs and raw metadata; does not certify market semantics.
"""
import argparse,asyncio,json,time,urllib.request
from pathlib import Path
from collections import deque
from engine import evaluate

HERE=Path(__file__).resolve().parent

def get(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'S2-research/1'}),timeout=4) as r:
        return json.load(r)

def array(x): return json.loads(x) if isinstance(x,str) else x

async def run(minutes,out):
    import websockets
    out.mkdir(parents=True,exist_ok=True)
    feeds={'crypto_prices_chainlink':deque(maxlen=3000),'crypto_prices_twap_sixty':deque(maxlen=3000)}
    stop=time.time()+minutes*60; attempted=set(); cache={}
    def log(kind,data):
        with (out/(kind+'.jsonl')).open('a') as f:
            f.write(json.dumps(data,allow_nan=False)+'\n')
    async def stream():
        while time.time()<stop and not (HERE/'KILL').exists():
            try:
                async with websockets.connect('wss://ws-live-data.polymarket.com',max_size=2**20) as ws:
                    await ws.send(json.dumps({'action':'subscribe','subscriptions':[
                        # 2026-09-26 (S2 record Part #1): RTDS matches the filters string EXACTLY. json.dumps default
                        # emits '{"symbol": "btc/usd"}' (space) -> zero update frames on both topics, reproduced on two
                        # machines; compact '{"symbol":"btc/usd"}' -> ~1 tick/s per topic. Same form as the S1 observer.
                        {'topic':t,'type':'update','filters':json.dumps({'symbol':'btc/usd'},separators=(',',':'))} for t in feeds]}))
                    async def heartbeat():
                        while True:
                            await asyncio.sleep(5); await ws.send('PING')
                    hb=asyncio.create_task(heartbeat())
                    try:
                        while time.time()<stop:
                            raw=await asyncio.wait_for(ws.recv(),20)
                            try: m=json.loads(raw)
                            except ValueError: continue
                            if not isinstance(m,dict): continue
                            p=m.get('payload',{}); topic=m.get('topic')
                            if topic not in feeds or m.get('type')!='update' or p.get('symbol')!='btc/usd': continue
                            received=time.time(); ts=float(p['timestamp'])/1000
                            v=float(p['full_accuracy_value'])/1e18 if 'full_accuracy_value' in p else float(p['value'])
                            if v<=0 or ts>received: continue
                            feeds[topic].append((ts,received,v))
                            log('ticks',dict(topic=topic,observed=ts,received=received,value=v))
                    finally:
                        hb.cancel(); await asyncio.gather(hb,return_exceptions=True)
            except Exception as e:
                # str(asyncio.TimeoutError()) is '' -> the 20 s recv timeout was logged as {"error": ""} (18x in the
                # first local run). Keep the exception class so a silent feed is diagnosable.
                log('health',dict(time=time.time(),error=str(e) or type(e).__name__)); await asyncio.sleep(2)
    async def sample(T,start):
        key=(T,start); attempted.add(key)
        spot=feeds['crypto_prices_chainlink']
        refs=[x for x in spot if x[0]<=start and x[1]<=start and start-x[0]<=2]
        if not refs:
            log('health',dict(time=time.time(),start=start,duration=T,skip='missing_bell_reference')); return
        ref=max(refs)
        label='5m' if T==300 else '15m'; slug=f'btc-updown-{label}-{start}'
        try:
            m=cache.get(key)
            if m is None: raise ValueError('metadata not prefetched; skip latency-sensitive sample')
            s=max(spot)
            side='Up' if s[2]>=ref[2] else 'Down'
            tokens=dict(zip(array(m['outcomes']),array(m['clobTokenIds'])))
            b=await asyncio.to_thread(get,'https://clob.polymarket.com/book?token_id='+tokens[side])
            received=time.time()
            asks=sorted((float(x['price']),float(x['size'])) for x in b['asks'])
            bid=max(float(x['price']) for x in b['bids'])
            r=dict(start=start,duration=T,now=received,open=ref[2],open_ts=ref[0],open_received=ref[1],
                   reference_source='chainlink_spot_candidate',spot=s[2],spot_ts=s[0],spot_received=s[1],
                   book_ts=float(b['timestamp'])/1000,book_received=received,ask=asks[0][0],ask_size=asks[0][1],
                   bid=bid,side=side,tick=float(b['tick_size']),min_order_size=float(b['min_order_size']),slug=slug)
            log('samples',r); log('decisions',dict(slug=slug,**evaluate(r)))
        except Exception as e: log('health',dict(time=time.time(),slug=slug,error=str(e)))
    task=asyncio.create_task(stream())
    try:
        while time.time()<stop and not (HERE/'KILL').exists():
            now=time.time()
            for T in (300,900):
                start=int(now//T)*T
                # Fetch next-round metadata away from the entry window.
                future=start+T; key=(T,future)
                if future-now<60 and key not in cache:
                    label='5m' if T==300 else '15m'
                    try:
                        markets=await asyncio.to_thread(get,f'https://gamma-api.polymarket.com/markets?slug=btc-updown-{label}-{future}')
                        if markets:
                            cache[key]=markets[0]; log('markets',dict(start=future,duration=T,metadata=markets[0]))
                    except Exception as e: log('health',dict(time=time.time(),error=str(e)))
                if 10<=time.time()-start<=15 and (T,start) not in attempted:
                    await sample(T,start)
            await asyncio.sleep(.2)
    finally:
        task.cancel(); await asyncio.gather(task,return_exceptions=True)
        summary=dict(time=time.time(),event='capture_stopped',buffered_ticks={k:len(v) for k,v in feeds.items()},attempted_rounds=len(attempted))
        log('health',summary)
        print(json.dumps(summary))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--minutes',type=float,default=60)
    p.add_argument('--out',type=Path,default=HERE/'data'); a=p.parse_args()
    asyncio.run(run(a.minutes,a.out))
