import unittest
from engine import evaluate,Config
from replay import replay
from resolve import winner

def row():
    return dict(start=900,duration=300,now=910,open=100000,open_ts=900,open_received=900,
                spot=100060,spot_ts=909.5,spot_received=909.6,book_ts=909.8,book_received=910,
                ask=.55,bid=.54,ask_size=100,tick=.01,min_order_size=5,side='Up',
                reference_source='chainlink_spot_candidate',winner='Up')

class Gates(unittest.TestCase):
    def test_candidate(self):
        d=evaluate(row()); self.assertEqual(d['action'],'candidate'); self.assertFalse(d['live_eligible']); self.assertFalse(d['maker_fill'])
    def test_rejections(self):
        for change,reason in [({'now':916},'entry_window'),({'open_received':901},'open_unavailable_at_bell'),
                ({'spot_ts':907},'spot_stale'),({'book_ts':911},'book_stale'),({'open_ts':897},'open_timestamp'),
                ({'spot':100010},'noise_zone'),({'ask':.61},'ask_ceiling'),({'ask':.60},'slippage_ceiling'),
                ({'ask_size':1},'insufficient_top_depth'),({'duration':900},'noise_zone'),
                ({'side':'Down'},'wrong_book_side'),({'spot':float('nan')},'invalid_numeric'),
                ({'reference_source':'exchange_composite'},'reference_source')]:
            with self.subTest(change=change):
                r=row(); r.update(change); self.assertEqual(evaluate(r)['reason'],reason)
    def test_down(self):
        r=row(); r.update(spot=99940,side='Down'); self.assertEqual(evaluate(r)['side'],'Down')
    def test_fee(self):
        self.assertAlmostEqual(.60+.07*.60*.40,.6168)
    def test_kill(self):
        with self.assertRaises(ValueError): Config(threshold_5m=11)
    def test_dedupe(self):
        r=replay([row(),row()],Config()); self.assertEqual(r['resolved_candidates'],1)
    def test_unresolved(self):
        r=row(); r.pop('winner'); self.assertEqual(replay([r],Config())['resolved_candidates'],0)
    def test_outcome_mapping(self):
        m=dict(closed=True,umaResolutionStatus='resolved',outcomes=['Down','Up'],outcomePrices=['1','0'])
        self.assertEqual(winner(m),'Down'); m['umaResolutionStatus']='disputed'; self.assertIsNone(winner(m))
    def test_model_edge(self):
        self.assertEqual(evaluate(row(),Config(sigma_5m=100))['reason'],'model_edge')
    def test_tick(self):
        r=row(); r['tick']=.001; d=evaluate(r); self.assertLess(d['maker_quote_intent'],r['ask'])

if __name__=='__main__': unittest.main()
