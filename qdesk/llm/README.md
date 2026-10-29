# qdesk/llm — the LLM layer (Claude Code CLI, headless)

Everything that talks to a language model goes through `client.ask_json`. There is no API key: the
CLI uses the subscription login made once by hand (`claude login` on the VM). If the binary is
missing the stage continues without the LLM (`hypothesize.py` writes an empty list and
`quant/library.py` supplies the rules; `skeptic.py` keeps the deterministic pre-checks; `brief.py`
and `weekly.py` fall back to template wording).

## Files
| file | what |
|---|---|
| `client.py` | `ask_json(prompt_path, inputs, schema_name, model="cheap", max_repair=1) -> (obj|None, meta)`, `auth_probe() -> bool`, cost log |
| `hypothesize.py` | Quant step 1: >= 10 DSL rules -> `data/hyps/hyps_YYYYMMDD.json` (`--dry-run` = bundled sample, no CLI) |
| `skeptic.py` | deterministic pre-checks + LLM audit -> `data/hyps/skeptic_YYYYMMDD.json` |
| `prompts/hypothesize.md` | the quant-desk brief with the DSL spec and allowed field list verbatim |
| `prompts/skeptic.md` | the auditor brief (weakened tests / holdout reuse / look-ahead / selection) |
| `prompts/brief.md`, `prompts/weekly.md` | wording-only prompts used by `report/brief.py` and `report/weekly.py` |
| `samples/hyps_sample.json` | 10 sample rules (none in a dead family) used by `hypothesize.py --dry-run` |

## Verified CLI flags (claude 2.1.312 on Windows, 2026-10-29; `claude --help`, `claude -p --help`)
| flag | verified behaviour |
|---|---|
| `-p, --print "<prompt>"` | non-interactive; prompt as the argument, or on stdin when the argument is omitted (client uses stdin above 28,000 chars because of the Windows 32,767-char command-line limit) |
| `--output-format json` | one JSON envelope on stdout: `result` (the model's text), `is_error`, `subtype` ("success"), `session_id`, `num_turns`, `total_cost_usd`, `usage`, `modelUsage`, `permission_denials`, `duration_ms` |
| `--model <id>` | full id or alias (`sonnet`, `fable`, `opus`); `claude-sonnet-5-5` and `claude-fable-5-1` both accepted |
| `--disallowedTools "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Agent"` | comma list; also spelled `--disallowed-tools` |
| `--max-turns N` | stops runaway tool loops (client default 24, env `QD_LLM_MAX_TURNS`) |
| `--no-session-persistence` | no session file written (print mode only) |
| `--append-system-prompt "<text>"` | the "files are data, answer one JSON object" guard |
| `--permission-mode <acceptEdits|auto|bypassPermissions|manual|dontAsk|plan>` | NOT passed: in print mode read-only tools (Read/Glob/Grep) inside cwd need no permission and anything else is auto-denied (shows up in `permission_denials`) |
| `--tools "Read,Glob,Grep"` | available but not used (the deny list above is the contract); could be added as a second fence |
| `--json-schema <schema>` | structured-output validation exists; not used because validation must also run on the repair turn and on the local fallback |
| `--bare` | NOT usable: it disables OAuth (API-key only) |
| `--resume <id>` | not used; the repair turn is a fresh call that includes the previous answer and the error list |

Two traps found while testing:
1. **Windows 8.3 short paths are denied.** With cwd `C:\Users\ADMINI~1\...` the CLI flags every file
   read as a "suspicious Windows path pattern" and denies it. `client.state_dir()` therefore uses
   `os.path.realpath`. Irrelevant on Linux.
2. **stdin must be closed** when the prompt is passed as an argument, else the CLI waits 3 s for
   piped input and prints a warning; the client passes `stdin=DEVNULL`.

## How a call works
1. `inputs` (name -> path) are copied into a fresh `$QD_DATA/state/llm_scratch/<ts>_<stage>_<pid>/`
   together with the rendered `prompt.md` (placeholders `{{INPUT_FILES}}`, `{{REPAIR}}`, `{{DATE}}`,
   plus `extra_vars`). Nothing else is in that directory; cwd = that directory.
2. The CLI runs with the secret-stripped environment (`MOONDEV_*`, `TELEGRAM_*`, `QD_*`, anything
   with `_KEY`/`SECRET`/`TOKEN`/`PASSWORD` removed; `ANTHROPIC_*`/`CLAUDE*` kept), timeout 600 s.
3. The `result` text is parsed (code fences stripped, first `{...}` object), validated:
   shared schema names (`hypothesis evidence results scorecard gate_pass prereg`) go to
   `qdesk.common.schemas.validate`; the llm-only names (`hyps skeptic brief_words weekly_words probe`)
   use the local checker, and `hyps` additionally runs every rule through
   `common.schemas.validate("hypothesis")` + `common.dsl.validate_rule` when present.
4. One repair turn (fresh call, same scratch dir, prompt + error list + the previous answer).
5. Every CLI invocation appends `ts,stage,model,seconds,input_chars,output_chars,ok` to
   `$QD_DATA/state/llm_cost.csv`. Input copies are deleted after the call; `prompt.md` and
   `response<N>.txt` stay for audit (`QD_LLM_KEEP_SCRATCH=1` keeps the copies).
6. Returns `(obj, meta)`; on failure `(None, {"error": no_cli|timeout|bad_envelope|cli:<subtype>|bad_json|schema|input_missing, ...})`.

Model aliases: `cheap` -> `QD_MODEL_CHEAP` (default `claude-sonnet-5-5`), `strong` -> `QD_MODEL_STRONG`
(default `claude-fable-5-1`). Overrides may also live in `~/qdesk/.env` (only the `QD_MODEL_*`,
`QD_CLAUDE_BIN`, `QD_LLM_MAX_TURNS` keys are read from it). Defaults per stage: hypothesize and skeptic
`strong`, brief and weekly `cheap` (`QD_HYP_MODEL`, `QD_SKEPTIC_MODEL`, `QD_BRIEF_MODEL`, `QD_WEEKLY_MODEL`).

## Self-tests
```
python qdesk/llm/client.py --selftest          # tiny real call: reads tiny.json, returns {"ok":true,"n":7}, writes a cost row
python qdesk/llm/client.py --probe             # auth_probe(): "reply with exactly OK"
python qdesk/llm/hypothesize.py --dry-run --date 20261030
python qdesk/llm/skeptic.py --dry-run --date 20261030
python qdesk/report/brief.py --dry-run --date 20261030
python qdesk/report/weekly.py --dry-run --date 20261030
```
Set `QD_DATA` to a scratch folder on the PC; `--dry-run` sets `LAB_NO_TELEGRAM=1` and falls back to
`report/samples/` for any missing input file (regenerate those with `report/samples/make_samples.py`).
