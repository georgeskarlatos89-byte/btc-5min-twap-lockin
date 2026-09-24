# S6 operator playbook

1. Start with `--preflight`; confirm both current markets resolve to tokens and the Moon Dev
   key is present. No key means flat, not blind quoting.
2. Run at least 3 days DRY. DRY fill counts are plumbing evidence only; public tape cannot
   prove queue position.
3. Record every first-leg fill and later outcome. Compute conditional asymmetry by side.
4. Kill the hypothesis if asymmetry exceeds 8 percentage points over 300 fills, or if the
   live fill pattern is not reproducible from the tape.
5. Only after a successful B step, incubate around $10 per side for 2 weeks. Scale neither
   S6 nor S3 from a one-day best-of-sweep.

S6 and S3 share the same venue and underlying. They must not exceed the global $30 net
exposure cap in a 5-minute bucket. S6's first-45-second window is intentionally disjoint
from S3's mid-round harvesting window where possible.
