#!/usr/bin/env python3
"""Public-only, raw timestamped websocket recorder. No private API dependencies."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.request
import uuid
import websockets

class Writer:
    # 2026-09-26 (S5 record Part #1): --gzip streams each day file through gzip (raw payloads unchanged).
    # Measured plain rate was 126 MB per 6 min (~29 GB/day) - not storable for 14 days. A sync-flush every
    # FLUSH_S keeps a killed session's file readable up to the last flush. Replay needs plain JSONL:
    # `gunzip -k data/<day>.jsonl.gz` (or `zcat ... > analysis/<day>.jsonl`) first.
    FLUSH_S=5
    def __init__(self, root, gz=False):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.session=uuid.uuid4().hex
        self.f=None; self.day=None; self.gz=gz; self.last_flush=0.0
    def write(self, source, payload, **extra):
        now=time.time()
        day=datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d')
        if day != self.day:
            if self.f: self.f.close()
            if self.gz:
                import gzip
                self.f=gzip.open(self.root/f'{day}.jsonl.gz','at',compresslevel=6,encoding='utf-8')
            else:
                self.f=(self.root/f'{day}.jsonl').open('a', buffering=1)
            self.day=day; self.last_flush=now
        self.f.write(json.dumps(dict(source=source, recv_ts=now, mono_ns=time.monotonic_ns(),
                                     session=self.session,payload=payload,**extra))+'\n')
        if self.gz and now-self.last_flush>=self.FLUSH_S:
            self.f.flush(); self.last_flush=now
    def close(self):
        if self.f: self.f.close()

def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'S5-research/1.0'}),timeout=10) as r:
        return json.load(r)

def discover(start):
    slug=f'btc-updown-5m-{start}'
    events=get_json('https://gamma-api.polymarket.com/events?slug='+slug)
    for event in events:
        for m in event.get('markets',[]):
            if m.get('slug') != slug or m.get('closed') or not m.get('active'): continue
            outcomes=m['outcomes']; tokens=m['clobTokenIds']
            if isinstance(outcomes,str): outcomes=json.loads(outcomes)
            if isinstance(tokens,str): tokens=json.loads(tokens)
            if len(tokens)!=2 or set(outcomes)!={'Up','Down'}: continue
            return dict(round_start=start,slug=slug,tokens=dict(zip(outcomes,tokens)),market=m)
    raise ValueError('No active exact BTC 5m Up/Down market')

async def heartbeat(ws):
    while True:
        await asyncio.sleep(10)
        await ws.send('PING')

async def kraken(writer):
    while True:
        try:
            async with websockets.connect('wss://ws.kraken.com/v2',ping_interval=20) as ws:
                writer.write('status',{'feed':'kraken','status':'connected'})
                await ws.send(json.dumps({'method':'subscribe','params':{'channel':'trade','symbol':['BTC/USD'],'snapshot':False}}))
                while True:
                    raw=await asyncio.wait_for(ws.recv(),30)
                    writer.write('kraken',json.loads(raw))
        except (OSError,ValueError,asyncio.TimeoutError,websockets.exceptions.WebSocketException) as e:
            # 2026-09-26 (S5 record Part #1): str(TimeoutError()) is '' -> keep the class name so a silent feed is diagnosable
            writer.write('status',{'feed':'kraken','error':str(e) or type(e).__name__})
            await asyncio.sleep(2)

async def polymarket(writer):
    while True:
        start=int(time.time()//300)*300
        try:
            meta=await asyncio.to_thread(discover,start)
            writer.write('market',meta)
            async with websockets.connect('wss://ws-subscriptions-clob.polymarket.com/ws/market',ping_interval=20) as ws:
                await ws.send(json.dumps({'type':'market','assets_ids':list(meta['tokens'].values()),'initial_dump':True}))
                ping=asyncio.create_task(heartbeat(ws))
                try:
                    while time.time()<start+300:
                        remaining=start+300-time.time()
                        try: raw=await asyncio.wait_for(ws.recv(),min(20,max(.01,remaining)))
                        except asyncio.TimeoutError:
                            if time.time()>=start+300: break
                            raise
                        if raw in ('PONG','PING'): continue
                        writer.write('polymarket',json.loads(raw),round_start=start)
                finally:
                    ping.cancel()
                    await asyncio.gather(ping,return_exceptions=True)
        except (OSError,ValueError,KeyError,asyncio.TimeoutError,websockets.exceptions.WebSocketException) as e:
            writer.write('status',{'feed':'polymarket','error':str(e) or type(e).__name__})
            await asyncio.sleep(2)

async def main(args):
    writer=Writer(args.output, gz=args.gzip)
    writer.write('status',{'status':'start','mode':'COLLECT_ONLY','version':1})
    try:
        async with asyncio.timeout(args.minutes*60):
            await asyncio.gather(kraken(writer),polymarket(writer))
    except TimeoutError: pass
    finally:
        writer.write('status',{'status':'stop'})
        writer.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default='data'); p.add_argument('--minutes',type=float,default=60)
    p.add_argument('--gzip',action='store_true',help='stream day files through gzip (data/<day>.jsonl.gz); gunzip before replay')
    args=p.parse_args()
    if args.minutes<=0: p.error('--minutes must be positive')
    asyncio.run(main(args))
