# s1_bursts.awk - same-millisecond batch bursts per (timestamp, hash), streaming.
# Input: BME events CSV rows. Two generations of 3 seconds each tolerate the ~1 % of rows
# that arrive out of time order. Output: one line "rows price_changes bursts max b5_9 b10_19 b20_49 b50".
function bucket(v) { if (v < 10) h1++; else if (v < 20) h2++; else if (v < 50) h3++; else h4++ }
function flush_prev(   k) { for (k in prev) if (prev[k] >= 5) { b++; if (prev[k] > m) m = prev[k]; bucket(prev[k]) } ; delete prev }
BEGIN { FS = ","; gen = 0 }
{ rows++ }
$2 == "price_change" {
    n++
    s = int($1 / 1000)
    if (s >= gen + 3) {
        flush_prev()
        for (k in cur) prev[k] = cur[k]
        delete cur
        gen = s
    }
    k = $1 SUBSEP $10
    if (k in prev) prev[k]++; else cur[k]++
}
END {
    flush_prev()
    for (k in cur) prev[k] = cur[k]
    flush_prev()
    printf "%d %d %d %d %d %d %d %d\n", rows, n, b, m, h1, h2, h3, h4
}
