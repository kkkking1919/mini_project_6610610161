# sync_tunnel_url.ps1
# Auto-detect Cloudflare Tunnel URL and sync to Telegram Bot / Mini App & FastAPI

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "  Cucumber RS - Cloudflare Tunnel & Telegram Auto-Sync" -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan

$maxRetries = 25
$foundUrl = $null

Write-Host "[*] Searching for active Cloudflare Tunnel URL..." -ForegroundColor Yellow

for ($i = 1; $i -le $maxRetries; $i++) {
    Start-Sleep -Seconds 2
    $logs = docker logs miniproject_tunnel --tail 50 2>&1
    foreach ($line in $logs) {
        if ($line -match "https://[a-zA-Z0-9-]+\.trycloudflare\.com") {
            $foundUrl = $matches[0]
        }
    }
    if ($foundUrl) {
        break
    }
    Write-Host "[-] Waiting for tunnel connection... (attempt $i/$maxRetries)" -ForegroundColor Gray
}

if (-not $foundUrl) {
    Write-Host "[!] Could not detect Cloudflare URL within timeout." -ForegroundColor Red
    Write-Host "    Please ensure 'miniproject_tunnel' container is running." -ForegroundColor Yellow
    exit 1
}

Write-Host "`n[+] Cloudflare Tunnel URL: $foundUrl" -ForegroundColor Green
Write-Host "[*] Connecting to FastAPI Server (http://localhost:8080)..." -ForegroundColor Cyan

# Wait for FastAPI
$apiReady = $false
for ($j = 1; $j -le 15; $j++) {
    try {
        $res = Invoke-RestMethod -Uri "http://localhost:8080/api/config" -Method Get -TimeoutSec 3 -ErrorAction Stop
        $apiReady = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if ($apiReady) {
    try {
        $body = @{ url = $foundUrl } | ConvertTo-Json
        $updateRes = Invoke-RestMethod -Uri "http://localhost:8080/api/config/tunnel-url" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
        Write-Host "`n===================================================================" -ForegroundColor Green
        Write-Host " [SUCCESS] Telegram Bot & Telegram Mini App Synced Automatically!" -ForegroundColor Green
        Write-Host "===================================================================" -ForegroundColor Green
        Write-Host "  - Public Tunnel URL   : $foundUrl" -ForegroundColor Cyan
        Write-Host "  - Local Web Dashboard : http://localhost:8080" -ForegroundColor White
        Write-Host "  - ESP32 LAN Endpoint  : http://172.20.10.5:8080/api/sensor-data" -ForegroundColor White
        Write-Host "  - Telegram Bot Menu   : Updated to open Mini App directly" -ForegroundColor Green
        Write-Host "  - Notification        : Broadcast message sent to Telegram" -ForegroundColor Green
        Write-Host "===================================================================`n" -ForegroundColor Green
    } catch {
        Write-Host "[!] Failed to POST URL to FastAPI: $_" -ForegroundColor Red
    }
} else {
    Write-Host "[!] FastAPI server is not responding at http://localhost:8080" -ForegroundColor Red
}
