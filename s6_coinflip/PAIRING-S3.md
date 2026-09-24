# S6 + S3 pairing map

S3 and S6 are intentionally separate processes and separate ledgers. They share the BTC
5m/15m underlying, Moon Dev feed-health rules, and the global exposure budget, but they do
not share orders or pretend one strategy's fills belong to the other.

| | S6 | S3 |
|---|---|---|
| window | round open through first 45s | t+30s through 60% elapsed |
| quote | 0.49 both sides | 0.45 both sides |
| experiment | conditional fill asymmetry | pair completion vs adverse selection |
| default | DRY / flat without Moon Dev key | DRY / flat without Moon Dev key |

The windows overlap for the first 15 seconds. That is acceptable only while the combined
net exposure remains below the portfolio cap; do not double-size the two bots. A future
router should arbitrate quote ownership in that overlap rather than blindly running both.

S6 does not inherit S3's cut-20-seconds exit rule. That difference is deliberate: S6 is
measuring whether its open fill is informed. If S6 later fails the 8 percentage-point
asymmetry kill test, stop it; do not “repair” it by adding an untested exit.
