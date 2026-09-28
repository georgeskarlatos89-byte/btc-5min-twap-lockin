# Keeps the private connection to the VPS dashboard open:  PC localhost:8899  ->  VPS 127.0.0.1:8787
# Started hidden at Windows logon by the scheduled task "UI Dashboard Tunnel". Reconnects by itself.
# Viewer only. To stop it: run "Stop dashboard connection.bat" in this folder.
$ErrorActionPreference = "SilentlyContinue"
$log  = Join-Path $PSScriptRoot "tunnel.log"
$stop = Join-Path $PSScriptRoot "STOP"
$ssh  = "C:\Windows\System32\OpenSSH\ssh.exe"
function Say($m) {
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Move-Item -Force $log "$log.1" }
    Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding utf8
}
# one instance only
$mutex = New-Object System.Threading.Mutex($false, "Global\UiDashboardTunnel")
if (-not $mutex.WaitOne(0)) { exit 0 }
Say "started"
$fails = 0
while ($true) {
    if (Test-Path $stop) { Say "STOP file present - exiting"; break }
    $busy = Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Start-Sleep -Seconds 15; continue }          # something else already holds the port (e.g. the manual launcher)
    $t0 = Get-Date
    & $ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 8899:127.0.0.1:8787 twapvm 2>$null
    $up = [int]((Get-Date) - $t0).TotalSeconds
    if ($up -gt 60) { $fails = 0 } else { $fails++ }
    $wait = [Math]::Min(60, 5 * [Math]::Max(1, $fails))       # 5 s, then longer while the network is down
    Say "connection ended after $up s (exit $LASTEXITCODE); reconnecting in $wait s"
    Start-Sleep -Seconds $wait
}