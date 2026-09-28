"""Add an immediate single retry for cut-off Moon Dev liquidation answers. Plumbing only:
no strategy constant, gate or trigger rule is touched. Every replacement must match exactly once."""
import io, sys

HELPER = '''def moondev_fetch_json(url, hdr, tries=2):
    """2026-09-28: the ~290 KB liquidation answer sometimes arrives cut off mid-way
    (JSONDecodeError / IncompleteRead: 40 times on S3, 13 on S46, 8 on S10 so far). The poll was
    simply lost until the next one. Now it is asked again at once, one time.
    NOT retried: HTTP errors (401/403 key rejected, 429 rate limit) and timeouts - those keep
    their old handling in the caller, and a retry there would only add load or block the loop."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError:
            raise
        except Exception as ex:
            cut_off = isinstance(ex, ValueError) or ex.__class__.__name__ == "IncompleteRead"
            if not cut_off or i + 1 >= tries:
                raise
            last = ex
            kind = "incomplete download" if ex.__class__.__name__ == "IncompleteRead" else "incomplete JSON"
            log(f"moondev liq answer cut off ({kind}) - asking again at once")
    raise last

'''

for path in sys.argv[1:]:
    s = io.open(path, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"; s = s.replace("\r\n", "\n")
    assert "def moondev_fetch_json" not in s, path + ": already patched"
    assert s.count("async def moondev_loops():") == 1, path
    done = 0
    for old in ('''            req = urllib.request.Request(LIQ_URL, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read().decode())
''', '''            req=urllib.request.Request(LIQ_URL, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                payload=json.loads(r.read().decode())
'''):
        if s.count(old) == 1:
            s = s.replace(old, "            payload = moondev_fetch_json(LIQ_URL, hdr)\n"); done += 1
    assert done == 1, (path, done)
    s = s.replace("async def moondev_loops():", HELPER + "async def moondev_loops():")
    io.open(path, "w", encoding="utf-8", newline="").write(s.replace("\n", nl))
    print("patched", path)
