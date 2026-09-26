#!/usr/bin/env python3
"""Causal receive-order replay and executable bid markouts, NOT a fill backtest."""
import argparse
from collections import Counter, deque
from datetime import datetime
import json
import math
from pathlib import Path
from core import Config, WickDetector, gate


def timestamp(value):
    if isinstance(value,str) and 'T' in value:
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    v=float(value)
    return v/1000 if v>1e11 else v

class Books:
    def __init__(self):
        self.tokens={}; self.levels={}; self.latest={}; self.round=None
    def market(self, meta):
        self.tokens={str(v):k for k,v in meta['tokens'].items()}
        self.round=meta['round_start']; self.levels={}; self.latest={}
    def event(self, e, received):
        kind=e.get('event_type'); changed=[]
        if kind=='book':
            token=str(e['asset_id'])
            if token not in self.tokens: return []
            self.levels[token]={side:{float(x['price']):float(x['size']) for x in e.get(side,[]) if float(x['size'])>0} for side in ('bids','asks')}
            changed=[token]
        elif kind=='price_change':
            for change in e.get('price_changes',[]):
                token=str(change['asset_id'])
                if token not in self.levels: continue  # require initial snapshot
                side=change['side']
                if side not in ('BUY','SELL'): continue
                levels=self.levels[token]['bids' if side=='BUY' else 'asks']
                p,q=float(change['price']),float(change['size'])
                if q>0: levels[p]=q
                else: levels.pop(p,None)
                changed.append(token)
        result=[]
        for token in set(changed):
            levels=self.levels[token]; side=self.tokens[token]
            self.latest.pop(side,None)
            if not levels['bids'] or not levels['asks']: continue
            bid,ask=max(levels['bids']),min(levels['asks'])
            if not 0<bid<=ask<1: continue
            if 'timestamp' not in e: continue
            t=timestamp(e['timestamp'])
            if not 0<=received-t<=1: continue
            b=dict(t=t,recv_ts=received,bid=bid,ask=ask,ask_size=levels['asks'][ask],
                   bid_size=levels['bids'][bid],side=side,round_start=self.round)
            self.latest[side]=b; result.append(b)
        return result


def run(paths, output, cfg, fair_path=None):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    detector=WickDetector(cfg); books=Books(); counts=Counter(); daily=Counter()
    history=deque(); pending=[]; follow=[]; fair_events=[]; fair_index=0; fairs={}
    if fair_path:
        with open(fair_path) as f:
            fair_events=[json.loads(line) for line in f if line.strip()]
        if any(a['recv_ts']>b['recv_ts'] for a,b in zip(fair_events,fair_events[1:])):
            raise ValueError('Fair events must be ordered by availability (recv_ts)')
    last_receive=-math.inf; session=None
    with (output/'events.jsonl').open('w') as out:
        def emit(kind, **data):
            counts[kind]+=1
            out.write(json.dumps(dict(kind=kind,**data))+'\n')
        for path in paths:
            with open(path) as f:
                for line in f:
                    row=json.loads(line); now=row['recv_ts']; payload=row['payload']; source=row['source']
                    if now<last_receive: raise ValueError('Logs must be in receive order; do not sort by source time')
                    last_receive=now; counts['raw_rows']+=1
                    if row['session']!=session:
                        session=row['session']; detector=WickDetector(cfg); books=Books(); history.clear()
                        pending.clear(); follow.clear(); fairs.clear()
                    while fair_index<len(fair_events) and fair_events[fair_index]['recv_ts']<=now:
                        fair=fair_events[fair_index]; fairs[(fair['round_start'],fair['side'])]=fair; fair_index+=1
                    if source=='status' and ('error' in payload or payload.get('status')=='connected'):
                        if payload.get('feed')=='kraken': detector=WickDetector(cfg)
                        if payload.get('feed')=='polymarket': books=Books(); history.clear()
                        pending.clear()
                        emit('feed_reset',t=now,detail=payload)
                    if source=='market':
                        books.market(payload); history.clear(); pending.clear()
                        emit('market_seen',round_start=books.round)
                    if source=='polymarket' and row.get('round_start')==books.round:
                        events=payload if isinstance(payload,list) else [payload]
                        for e in events:
                            for b in books.event(e,now):
                                history.append(b)
                                # First valid subsequent book at each horizon, no interpolation.
                                for study in follow:
                                    if b['side']!=study['side'] or b['round_start']!=study['round_start']: continue
                                    for horizon in (.5,1,2,5,10):
                                        if horizon not in study['done'] and now>=study['t']+horizon:
                                            study['done'].add(horizon)
                                            emit('bid_markout',signal_id=study['id'],horizon=horizon,
                                                 actual_delay=now-study['t'],valid=now-study['t']<=horizon+1,
                                                 bid_minus_initial_ask=b['bid']-study['ask'],
                                                 net_proxy=b['bid']-study['ask']-2*cfg.fee_per_share-2*cfg.slippage_per_share,
                                                 depth_sufficient=b['bid_size']>=cfg.notional/(study['ask']+cfg.fee_per_share+cfg.slippage_per_share))
                                    if study.get('baseline_mid') is not None and not study.get('recovered') and b['bid']>=study['baseline_mid']:
                                        study['recovered']=True
                                        emit('quote_recovery_proxy',signal_id=study['id'],seconds=now-study['t'])
                    while history and history[0]['recv_ts']<now-20: history.popleft()
                    follow=[s for s in follow if now-s['t']<=11]
                    if source=='kraken' and payload.get('channel')=='trade' and payload.get('type')=='update':
                        for tick in payload.get('data',[]):
                            if tick.get('symbol')!='BTC/USD': continue
                            ts=timestamp(tick['timestamp'])
                            if not 0<=now-ts<=cfg.max_age:
                                counts['stale_spot']+=1; detector=WickDetector(cfg); continue
                            start=int(ts//300)*300
                            sig=detector.update(ts,float(tick['price']),start)
                            if not sig: continue
                            sig['detected_recv']=now; sig['id']=counts['wick']+1
                            emit('wick',**sig)
                            b=books.latest.get(sig['side']) if books.round==start else None
                            if b and 0<=now-b['t']<=cfg.max_age:
                                base=next((x for x in reversed(history) if x['side']==sig['side'] and x['recv_ts']<=sig['base_t'] and sig['base_t']-x['t']<=cfg.max_age),None)
                                study=dict(id=sig['id'],t=now,side=sig['side'],round_start=start,ask=b['ask'],done=set(),
                                           baseline_mid=(base['bid']+base['ask'])/2 if base else None)
                                follow.append(study)
                            pending.append(sig)
                    for sig in pending[:]:
                        if now<sig['detected_recv']+cfg.latency_seconds: continue
                        day=int(now//86400)
                        b=books.latest.get(sig['side'])
                        fair=fairs.get((sig['round_start'],sig['side']))
                        reason=gate(sig,b,fair,now,daily[day],cfg,killed=Path('KILL').exists())
                        if reason=='eligible': daily[day]+=1
                        emit('decision',signal_id=sig['id'],t=now,reason=reason,mode='SHADOW_NO_FILL')
                        counts['gate_'+reason]+=1; pending.remove(sig)
        emit('end_of_data',unassessed_signals=len(pending),unfinished_markout_studies=len(follow))
    report=dict(status='RESEARCH_ONLY_NO_TRADING_EVIDENCE',counts=dict(counts),
                fair_provider='external' if fair_path else 'MISSING_ENTRIES_BLOCKED',
                assumptions=cfg.__dict__,daily_shadow_candidates=dict(daily),
                warning='Markouts are not fills, realized PnL, queue simulation, or proof of an edge.')
    (output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('logs',nargs='+'); p.add_argument('--output',default='reports')
    p.add_argument('--fair-events',help='Optional causally available validated fair-probability JSONL')
    p.add_argument('--fee-per-share',type=float,default=.02)
    p.add_argument('--latency',type=float,default=.5)
    args=p.parse_args()
    if not 0<=args.fee_per_share<1 or not math.isfinite(args.fee_per_share): p.error('Invalid fee')
    if not 0<=args.latency<=10 or not math.isfinite(args.latency): p.error('Invalid latency')
    print(json.dumps(run(args.logs,args.output,Config(fee_per_share=args.fee_per_share,latency_seconds=args.latency),args.fair_events),indent=2))
