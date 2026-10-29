#!/usr/bin/env python3
"""hashing.py - code and corpus fingerprints for pre-registration and the bot gate.
stdlib only. Both hashes are the first 16 hex chars of sha256, CRLF-normalised, so the same tree
hashes identically on Windows and on the VM.

CODE_FILES_ENGINE / CODE_FILES_BOT are paths relative to the qdesk package folder (the git repo);
`code_hash` resolves relative paths against QDESK_DIR and hashes "<missing>" for absent files so a
hash is always produced (and changes when a file appears).
"""
import hashlib
import os

QDESK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../qdesk
REPO_ROOT = os.path.dirname(QDESK_DIR)                                     # parent of the package

CODE_FILES_ENGINE = [
    "features/features.py",
    "common/dsl.py",
    "common/fees.py",
    "common/settle.py",
    "quant/rule_engine_1s.py",
    "quant/rule_replay_events.py",
    "quant/score.py",
]
CODE_FILES_BOT = [
    "bot/qbot.py",
    "bot/paper_fill.py",
    "bot/gate.py",
    "common/dsl.py",
    "features/features.py",
]


def _resolve(fn):
    return fn if os.path.isabs(fn) else os.path.join(QDESK_DIR, fn)


# copied from C:\g\tlfix\open_book_maker_v2\make_strategy.py (code_hash) - takes the file list as an argument
def code_hash(paths):
    """sha256 (first 16 hex) over the given sources, in the given order (name + CRLF-normalised content)."""
    h = hashlib.sha256()
    for fn in paths:
        p = _resolve(fn)
        h.update(fn.replace("\\", "/").encode() + b"\0")
        try:
            with open(p, "rb") as f:
                h.update(f.read().replace(b"\r\n", b"\n"))
        except FileNotFoundError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:16]


# copied from C:\g\tlfix\open_book_maker_v2\make_strategy.py (corpus_hash)
def corpus_hash(files):
    """sha256 (first 16 hex) over the replay corpus: basename + content of every file."""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda x: os.path.basename(x)):
        h.update(os.path.basename(p).encode() + b"\0")
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()[:16]


def engine_hash():
    return code_hash(CODE_FILES_ENGINE)


def bot_hash():
    return code_hash(CODE_FILES_BOT)
