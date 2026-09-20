# Set up a new VM in Google Cloud console — self-contained runbook

**Purpose:** stand up a fresh Google Cloud VM and get the BTC TWAP Lock-In harness (`s1_harness`)
running on it. Written to be handed to a future session — including on a **brand-new PC with
nothing installed and no SSH key yet.** Follow the parts in order.

> **If you are the AI assistant reading this:** drive Parts 3–7 for the user over SSH once the
> VM exists and the user has run the Part 2 block in the browser terminal (the one step only they
> can do). Confirm the country is non-US before anything else. Never ask for or handle a private
> key; the harness stays DRY until the user themselves creates `.env` with a burner key.

---

## 🔴 THE ONE RULE THAT MUST NOT BE BROKEN — region

Polymarket **geoblocks US IPs for trading.** A signed order from a US host silently times out.
**Choose a non-US region** (we use `europe-west4`, Netherlands). Public data reads work anywhere;
it is the signed order that dies. A us-east4 box failed exactly this way once and was deleted.

---

## Part 0 — Brand-new PC prerequisites (skip if already set up)

You need an SSH client and an SSH key. On Windows 10/11 the OpenSSH client is built in
(`ssh`, `scp`, `ssh-keygen` work in PowerShell). On Mac/Linux they're already there.

**0a. Do you already have a key?** Check:
```bash
# Windows PowerShell:  dir $env:USERPROFILE\.ssh\*.pub
# Mac/Linux:           ls ~/.ssh/*.pub
```
If you see `id_ed25519.pub` or `id_rsa.pub`, you have one — skip to 0c.

**0b. No key? Make one** (press Enter at every prompt for defaults, no passphrase needed):
```bash
ssh-keygen -t ed25519 -C "polymarket-vm"
```
This creates `~/.ssh/id_ed25519` (private — never share) and `~/.ssh/id_ed25519.pub` (public).

**0c. Print your PUBLIC key** — you'll paste this into the VM in Part 2:
```bash
# Windows PowerShell:  type $env:USERPROFILE\.ssh\id_ed25519.pub    (or id_rsa.pub)
# Mac/Linux:           cat ~/.ssh/id_ed25519.pub
```
Copy the whole line (starts `ssh-ed25519` or `ssh-rsa`, ends with a comment). **This public half
is safe to share; the private half stays on this PC forever.**

---

## Part 1 — Create the VM (GCP console → Compute Engine → Create instance)

| Field | Value |
|---|---|
| **Name** | anything, e.g. `arena-ai-bots` |
| **Region / Zone** | **`europe-west4` (Netherlands)** — or any **non-US** region |
| **Machine type** | **`e2-small`** (2 vCPU / 2 GB). `e2-micro` also works for one bot |
| **Boot disk → image** | Ubuntu **24.04 LTS**, **x86/64 · amd64 · noble** — NOT Arm64, NOT 26.04 |
| **Boot disk size** | 10–15 GB standard is plenty |
| **Firewall** | leave OFF — do **not** tick "Allow HTTP/HTTPS". The bot only makes outbound calls; default SSH (22) is enough |
| everything else | defaults |

Create it. Note the **External IP** (e.g. `34.34.13.7`).

**Cost:** with the $300 free credit an e2-small is a few dollars/month; the credit lasts 90 days.

---

## Part 2 — Create your user on the VM (browser terminal; only you can do this)

GCP's "add SSH key at create time" is unreliable — this is the path that always works:

1. In the console, click the **SSH** button next to the VM (opens a browser terminal as a temp
   sudo user).
2. Paste this **entire block**, replacing `<PASTE_YOUR_PUBLIC_KEY_HERE>` with the line from Part 0c,
   then press Enter:

```bash
sudo useradd -m -s /bin/bash ubuntupolymarket3
echo 'ubuntupolymarket3 ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/ubuntupolymarket3
sudo mkdir -p /home/ubuntupolymarket3/.ssh
sudo tee /home/ubuntupolymarket3/.ssh/authorized_keys >/dev/null <<'KEY'
<PASTE_YOUR_PUBLIC_KEY_HERE>
KEY
sudo chown -R ubuntupolymarket3:ubuntupolymarket3 /home/ubuntupolymarket3/.ssh
sudo chmod 700 /home/ubuntupolymarket3/.ssh
sudo chmod 600 /home/ubuntupolymarket3/.ssh/authorized_keys
echo "DONE — user ready"
```

> **The key that worked on the original PC (administrator@WIN-ELBLN3V8JAF)** — use this ONLY if
> you are on that same PC and its key is unchanged; otherwise use YOUR OWN from Part 0c:
> ```
> ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQCs4dj8oF+fZHJLgQZYYRruZChudR87OW+EoQI6NtPklRdZ5y01XqSIVM3EIL4t0j7cF1ja90XNHU53gnPURS/u+MrPZQlfUXHfHgD3ks6UFh/ZKwLtkGKPYCOjlf3xvtpyhgXdthtHAQkUBDUxZAVUTmp8mI7z/HhNsOErykFQDexe2YEWTZHiCN4QrFXLFnxCp0jeh2fINYPQ16VPVeLeHGitNrzSlNmm0aKXiK4cYT9gMzRg0C398jPQgCOa9DgsSBDK7QB4JQKSQ8R9vC23ICfXSYyFLW/pMT9PpWswLQMQErhvbxT5dO8wd+mlUKV97HEPhAWfqFU7N1qG7O72Pa1hV/Oxh62LJ21ybTBxqjdwcjjc+rwl3AqIyEEueJ32DBFWrofU/BXTocCnRB1Q3LpqDeyD/sDjSO9uL2RTdiG82Se0ky6shN1h1inFei3j92//dJ24sOI2rTEA7QFxVFiwvLjsrCo+0VjRxqLzf1a5t4tibUWM9CF5+rpCPyTTTvqcqZFeCt7Pi1D5fARyJY+tsAQEgvszNdU39NQDa4prJUnoW2+Uc2fejLn9HrauuRZ2168HxVbu58CksFPiUNvi9AJDq5JOxIJLABI2Mq6UUftO5Hy4/Y8fp0Kng5LsJYVfXraeQZmvOoipUQjiVGL72ffGvXNxt7hxrAQaMQ== administrator@WIN-ELBLN3V8JAF
> ```

3. When you see `DONE — user ready`, continue.

---

## Part 3 — SSH alias on your PC

Add this to `~/.ssh/config` (`%USERPROFILE%\.ssh\config` on Windows), replacing the IP and the
IdentityFile with **your** key from Part 0:

```
Host twapvm
    HostName <NEW_EXTERNAL_IP>
    User ubuntupolymarket3
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
```

Test:
```bash
ssh twapvm 'whoami && curl -s https://ipinfo.io/country'
```
Expect `ubuntupolymarket3` and a **non-US** country code (e.g. `NL`). If it prints `US`, stop and
rebuild the VM in a non-US region.

---

## Part 4 — Install dependencies (assistant can run this over SSH)

```bash
ssh twapvm 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3 python3-pip python3-venv build-essential python3-dev pkg-config libssl-dev libffi-dev'
ssh twapvm 'pip3 install --break-system-packages websockets py-clob-client'
```
The harness needs only `websockets` + `py-clob-client` beyond the standard library. The
`--break-system-packages` flag is required on Ubuntu 24.04 (PEP 668).

---

## Part 5 — Get the harness onto the VM

**Option A — from GitHub (works from any PC).** The harness lives in the private repo
`georgeskarlatos89-byte/btc-5min-twap-lockin` (folder `s1_harness/`). On the VM:
```bash
ssh twavm 'gh auth login   # or: git clone with a token'   # needs the georgeskarlatos89-byte login
```
Simplest if `gh` isn't on the VM: clone with a fine-grained token that has read access:
```bash
ssh twapvm 'git clone https://<TOKEN>@github.com/georgeskarlatos89-byte/btc-5min-twap-lockin.git && cp -r btc-5min-twap-lockin/s1_harness ~/'
```

**Option B — copy from your PC (no GitHub needed on the VM).** If the `s1_harness` folder is on
your PC:
```bash
scp -r "<PATH_TO>/s1_harness" twapvm:~/
```
(Original PC path: `C:/Users/Administrator/Downloads/Compressed/btc-5-minute dobre andrei all prerequisistes/s1_harness`)

Verify:
```bash
ssh twapvm 'ls ~/s1_harness/trader.py && echo OK'
```

---

## Part 6 — Run it (DRY first, always) + keep it alive

**6a. Confirm it starts (dry, places nothing, needs no key):**
```bash
ssh twapvm 'cd ~/s1_harness && timeout 20 python3 trader.py'
```
Expect a line like `trader starting | arbiter=0/50 | dry n=0 | live=0`. No `.env` = guaranteed DRY.

**6b. Run it as a service so it survives logout/reboot:**
```bash
ssh twapvm 'sudo tee /etc/systemd/system/twap-harness.service >/dev/null <<EOF
[Unit]
Description=TWAP Lock-In harness (DRY)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=ubuntupolymarket3
WorkingDirectory=/home/ubuntupolymarket3/s1_harness
ExecStart=/usr/bin/python3 /home/ubuntupolymarket3/s1_harness/trader.py
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntupolymarket3/twap-harness.log
StandardError=append:/home/ubuntupolymarket3/twap-harness.log
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now twap-harness.service && systemctl is-active twap-harness.service'
```

**6c. Going live (much later, only when the gates pass).** Per `s1_harness/CREDENTIALS.md`, live
needs ALL of: a `.env` with `LIVE_TRADING=1` + a **burner** `POLY_PRIVATE_KEY`; ≥50 arbiter rounds;
≥20 dry signals at ≥90%; no `KILL` file; inside risk caps. **Create the `.env` yourself on the VM,
in the terminal — never paste a private key into any chat or file.** Kill switch any time:
`touch ~/s1_harness/KILL`.

---

## Part 7 — Telegram (when wiring alerts)

Route this strategy's alerts to the **BTC-5-minute** thread:
```
group : -1002282822022     thread : 600     combined : -1002282822022_600
```
The `telegram()` helper splits the combined string on the last `_` into chat_id + message_thread_id.
The bot token lives on the VM in a secrets file (chmod 600) — never in a doc or chat.

---

## Reference values (as of 2026-09-20)

| thing | value |
|---|---|
| **New TWAP VM** | `arena-ai-bots`, europe-west4-a, IP `34.34.13.7`, alias `twapvm`, user `ubuntupolymarket3` |
| **Existing live fleet box (do NOT touch)** | `ssh polyvps` → `34.178.162.193`, europe-west4, the ~13-bot fleet |
| **Harness repo (private)** | `github.com/georgeskarlatos89-byte/btc-5min-twap-lockin` |
| **Active GitHub account** | `georgeskarlatos89-byte` (AlawsBram still logged in, inactive) |
| **OS to pick** | Ubuntu 24.04 LTS, x86/64 amd64 noble |
