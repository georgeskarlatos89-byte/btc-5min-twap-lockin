"""Offline test of moondev_fetch_json as patched into each strategy file (no network)."""
import ast, io, json, sys, types, urllib.request, urllib.error, http.client

def load_helper(path):
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "moondev_fetch_json")
    mod = types.ModuleType("m"); logs = []
    mod.__dict__.update(urllib=urllib, json=json, log=lambda m: logs.append(m))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), path, "exec"), mod.__dict__)
    return mod.moondev_fetch_json, logs

class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self):
        if isinstance(self.body, Exception): raise self.body
        return self.body

def run(path):
    fetch, logs = load_helper(path)
    good = json.dumps({"liquidations": [{"symbol": "BTC"}]}).encode()
    real = urllib.request.urlopen
    def script(seq):
        calls = {"n": 0}
        def fake(req, timeout=None):
            x = seq[calls["n"]]; calls["n"] += 1
            if isinstance(x, urllib.error.HTTPError) or isinstance(x, TimeoutError): raise x
            return Resp(x)
        urllib.request.urlopen = fake
        return calls
    try:
        c = script([good]); assert fetch("https://x.test/a", {}) == json.loads(good) and c["n"] == 1
        c = script([good[:20], good]); assert fetch("https://x.test/a", {}) == json.loads(good) and c["n"] == 2, "cut-off JSON must be retried once"
        c = script([http.client.IncompleteRead(b"x" * 10, 50), good]); assert fetch("https://x.test/a", {})["liquidations"] and c["n"] == 2, "IncompleteRead must be retried once"
        c = script([good[:20], good[:30], good])
        try: fetch("https://x.test/a", {}); raise SystemExit("two cut-off answers must raise")
        except ValueError: assert c["n"] == 2, "never more than one retry"
        for code in (401, 403, 429, 500):
            c = script([urllib.error.HTTPError("https://x.test/a", code, "x", {}, None), good])
            try: fetch("https://x.test/a", {}); raise SystemExit("HTTP errors must not be retried")
            except urllib.error.HTTPError as e: assert e.code == code and c["n"] == 1
        c = script([TimeoutError("timed out"), good])
        try: fetch("https://x.test/a", {}); raise SystemExit("timeouts must not be retried")
        except TimeoutError: assert c["n"] == 1
        assert sum("asking again" in l for l in logs) == 3 and not any("error" in l.lower() or "HTTP" in l for l in logs), logs
    finally:
        urllib.request.urlopen = real
    print("OK", path, "| retry log lines never contain 'error'/'HTTP', so the monitor does not count them")

for p in sys.argv[1:]: run(p)
