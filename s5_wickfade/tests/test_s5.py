import tempfile
import unittest
from pathlib import Path
from core import Config, WickDetector, gate, latency_verdict
from replay import Books, run

class Tests(unittest.TestCase):
    def test_symmetric_wicks(self):
        for direction,side in [(1,'Down'),(-1,'Up')]:
            d=WickDetector()
            for t,p in [(10,100000),(11,100000+direction*90),(12,100000+direction*30)]: s=d.update(t,p,0)
            self.assertEqual(s['side'],side)
            self.assertIsNone(d.update(13,100000+direction*30,0))
    def test_gap_and_duplicate(self):
        d=WickDetector(); d.update(10,100000,0); d.update(10,100090,0)
        self.assertIsNone(d.wick)
        d.update(15,100090,0); self.assertIsNone(d.wick)
    def test_exact_cutoff(self):
        d=WickDetector(); d.update(178,100000,0); d.update(179,100090,0)
        self.assertIsNone(d.update(180,100030,0))
    def test_small_spike(self):
        d=WickDetector(); d.update(10,100000,0); d.update(11,100040,0)
        self.assertIsNone(d.wick)
    def test_round_reset(self):
        d=WickDetector(); d.update(10,100000,0); d.update(11,100090,0)
        self.assertIsNone(d.update(301,100030,300))
    def data(self):
        return (dict(t=14,round_start=0,side='Down'),
                dict(t=14,round_start=0,side='Down',bid=.40,ask=.42,ask_size=100),
                dict(t=14,round_start=0,side='Down',value=.5,validated=True))
    def test_eligible(self): self.assertEqual(gate(*self.data(),14.5,0),'eligible')
    def test_missing_fair(self):
        s,b,f=self.data(); self.assertEqual(gate(s,b,None,14.5,0),'missing_book_or_fair')
    def test_overpriced(self):
        s,b,f=self.data(); b['ask']=.55
        self.assertEqual(gate(s,b,f,14.5,0),'insufficient_net_edge')
    def test_depth(self):
        s,b,f=self.data(); b['ask_size']=2
        self.assertEqual(gate(s,b,f,14.5,0),'insufficient_depth')
    def test_stale(self):
        s,b,f=self.data(); b['t']=12
        self.assertEqual(gate(s,b,f,14.5,0),'stale_or_future_data')
    def test_wrong_market(self):
        s,b,f=self.data(); b['round_start']=300
        self.assertEqual(gate(s,b,f,14.5,0),'wrong_market_or_side')
    def test_unvalidated(self):
        s,b,f=self.data(); f['validated']=False
        self.assertEqual(gate(s,b,f,14.5,0),'unvalidated_fair')
    def test_cap_and_kill(self):
        self.assertEqual(gate(*self.data(),14.5,3),'daily_cap')
        self.assertEqual(gate(*self.data(),14.5,0,killed=True),'kill_switch')
    def test_nan(self):
        s,b,f=self.data(); f['value']=float('nan')
        self.assertEqual(gate(s,b,f,14.5,0),'invalid_numbers')
    def test_latency(self):
        self.assertEqual(latency_verdict([],[]),'UNMEASURED_BLOCK_LIVE')
        self.assertEqual(latency_verdict([.4]*30,[.5]*30),'KILL')
        self.assertEqual(latency_verdict([2]*30,[.5]*30),'PASS_LATENCY_ONLY')
    def test_book_delta(self):
        b=Books(); b.market({'round_start':0,'tokens':{'Up':'1','Down':'2'}})
        b.event({'event_type':'book','asset_id':'1','timestamp':14,'bids':[{'price':'.4','size':'50'}],'asks':[{'price':'.5','size':'60'},{'price':'.6','size':'70'}]},14)
        got=b.event({'event_type':'price_change','timestamp':14.5,'price_changes':[{'asset_id':'1','side':'SELL','price':'.5','size':'0'}]},14.5)
        self.assertEqual(got[0]['ask'],.6)
    def test_empty_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.jsonl'; p.write_text('')
            self.assertEqual(run([p],Path(tmp)/'reports',Config())['fair_provider'],'MISSING_ENTRIES_BLOCKED')

if __name__=='__main__': unittest.main()
