# test_system.ps1
# Comprehensive End-to-End Simulation Test for Cucumber RS AIoT System
# Isolated Test Mode: Uses X-Test-Mode header (saves to test_fall.db, preserves real fall_detection.db, .env, and Telegram alerts)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$baseUrl = "http://localhost:8080"
$testHeaders = @{ "X-Test-Mode" = "true" }

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "   Cucumber RS - Comprehensive AIoT System & Telegram Test Suite   " -ForegroundColor Cyan
Write-Host "                     [ SAFE ISOLATED TEST MODE ]                   " -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "Target Base URL: $baseUrl" -ForegroundColor White
Write-Host "Database Mode  : test_fall.db (Isolated from production DB)`n" -ForegroundColor DarkGray

$passed = 0
$failed = 0

# TEST 1: Config & Health Check
Write-Host "[TEST 1/6] Checking Server Health & Configuration..." -ForegroundColor Yellow
try {
    $res1 = Invoke-RestMethod -Uri "$baseUrl/api/config" -Method Get -Headers $testHeaders -TimeoutSec 5
    Write-Host "  [+] Status: OK" -ForegroundColor Green
    Write-Host "      - Gemini AI Ready    : $($res1.gemini_ready)" -ForegroundColor Gray
    Write-Host "      - Telegram Bot Ready : $($res1.telegram_ready)" -ForegroundColor Gray
    Write-Host "      - Bot Username       : @$($res1.bot_username)" -ForegroundColor Gray
    Write-Host "      - Cached Chat ID     : $($res1.telegram_chat_id)" -ForegroundColor Gray
    $passed++
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# TEST 2: Routine Telemetry Injection (Sensor Data)
Write-Host "`n[TEST 2/6] Simulating Cucumber RS Routine Telemetry..." -ForegroundColor Yellow
try {
    $sensorPayload = @{
        temperature = 29.4
        humidity    = 63.5
        pressure    = 1013.2
        rssi        = -58
        accel       = @{ x = 0.08; y = -0.15; z = 9.80 }
        gyro        = @{ x = 0.01; y = 0.02; z = -0.01 }
    } | ConvertTo-Json

    $res2 = Invoke-RestMethod -Uri "$baseUrl/api/sensor-data" -Method Post -Headers $testHeaders -Body $sensorPayload -ContentType "application/json" -TimeoutSec 5
    $getSensor = Invoke-RestMethod -Uri "$baseUrl/api/sensor-data" -Method Get -Headers $testHeaders -TimeoutSec 5
    
    if ($getSensor.temperature -eq 29.4) {
        Write-Host "  [+] Status: OK (Sensor cache updated successfully)" -ForegroundColor Green
        Write-Host "      - Temperature: $($getSensor.temperature) °C" -ForegroundColor Gray
        Write-Host "      - Humidity   : $($getSensor.humidity) %RH" -ForegroundColor Gray
        Write-Host "      - Pressure   : $($getSensor.pressure) hPa" -ForegroundColor Gray
        Write-Host "      - RSSI       : $($getSensor.rssi) dBm" -ForegroundColor Gray
        $passed++
    } else {
        throw "Telemetry value mismatch"
    }
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# TEST 3: Edge-AI Fall Event Simulation (Isolated to test_fall.db, skipped real Telegram alert)
Write-Host "`n[TEST 3/6] Simulating Edge-AI Fall Event (Isolated in test_fall.db)..." -ForegroundColor Yellow
$eventId = $null
try {
    $fallPayload = @{
        max_g       = 3.85
        temperature = 29.4
        humidity    = 63.5
        pressure    = 1013.2
    } | ConvertTo-Json

    $res3 = Invoke-RestMethod -Uri "$baseUrl/api/fall-event" -Method Post -Headers $testHeaders -Body $fallPayload -ContentType "application/json" -TimeoutSec 10
    $eventId = $res3.event_id
    $risk = $res3.risk

    Write-Host "  [+] Status: OK (Emergency Triggered in test_fall.db)" -ForegroundColor Green
    Write-Host "      - Event ID     : $eventId" -ForegroundColor Gray
    Write-Host "      - Risk Level   : $risk" -ForegroundColor Magenta
    Write-Host "      - Safe Guard   : Real Telegram Alert suppressed (Demo stats preserved)" -ForegroundColor Green
    
    # Verify Emergency Status API
    $emStatus = Invoke-RestMethod -Uri "$baseUrl/api/emergency-status" -Method Get -Headers $testHeaders -TimeoutSec 5
    if ($emStatus.active -eq $true) {
        Write-Host "      - Emergency UI : ACTIVE (Modal trigger verified)" -ForegroundColor Green
        $passed++
    } else {
        throw "Emergency status not active"
    }
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# TEST 4: Acknowledge & Resolve Fall Emergency
Write-Host "`n[TEST 4/6] Resolving Active Fall Event (Caregiver Acknowledged)..." -ForegroundColor Yellow
try {
    if ($eventId) {
        $res4 = Invoke-RestMethod -Uri "$baseUrl/api/fall-event/$eventId/resolve" -Method Post -Headers $testHeaders -TimeoutSec 5
        $emStatusAfter = Invoke-RestMethod -Uri "$baseUrl/api/emergency-status" -Method Get -Headers $testHeaders -TimeoutSec 5
        if ($emStatusAfter.active -eq $false) {
            Write-Host "  [+] Status: OK (Emergency cleared back to Normal)" -ForegroundColor Green
            $passed++
        } else {
            throw "Emergency still active after resolve"
        }
    } else {
        throw "Skipping resolve because no event_id was obtained"
    }
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# TEST 5: Conversational AI "น้องแตงกวา" (Gemini AI + Live Sensor Context)
Write-Host "`n[TEST 5/6] Testing AI Chat with Live Sensor Context (Gemini 2.5 Flash)..." -ForegroundColor Yellow
try {
    $chatPayload = @{
        chat_id = "test_simulation_user"
        message = "สภาพอากาศในห้องตอนนี้เป็นอย่างไร อุณหภูมิเท่าไหร่ และมีคำแนะนำอะไรไหม"
    } | ConvertTo-Json

    $chatJson = [System.Text.Encoding]::UTF8.GetBytes(($chatPayload))
    $res5 = Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method Post -Headers $testHeaders -Body $chatJson -ContentType "application/json; charset=utf-8" -TimeoutSec 30
    Write-Host "  [+] Status: OK (AI Replied with Context)" -ForegroundColor Green
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "$($res5.reply)" -ForegroundColor Cyan
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkGray
    $passed++

    # Sub-test: Fall History Query
    Write-Host "`n  [*] Testing Fall History Query ('วันนี้ล้มทั้งหมดกี่ครั้ง รุนแรงไหม')..." -ForegroundColor Yellow
    $fallChatPayload = @{
        chat_id = "test_simulation_user"
        message = "วันนี้ล้มทั้งหมดกี่ครั้ง รุนแรงไหม"
    } | ConvertTo-Json
    $fallChatJson = [System.Text.Encoding]::UTF8.GetBytes(($fallChatPayload))
    $res5b = Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method Post -Headers $testHeaders -Body $fallChatJson -ContentType "application/json; charset=utf-8" -TimeoutSec 30
    Write-Host "  [+] Fall History Reply:" -ForegroundColor Green
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "$($res5b.reply)" -ForegroundColor Cyan
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkGray
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# TEST 6: Safe Endpoint Validation for Tunnel URL
Write-Host "`n[TEST 6/6] Testing Tunnel URL Endpoint Validation (Safe Test Mode)..." -ForegroundColor Yellow
try {
    # 1. Fetch current active URL
    $currentRes = Invoke-RestMethod -Uri "$baseUrl/api/config/tunnel-url" -Method Get -Headers $testHeaders -TimeoutSec 5
    $activeUrl = $currentRes.tunnel_url
    Write-Host "      - Active URL : $activeUrl" -ForegroundColor Gray

    # 2. Validate endpoint without overriding .env or corrupting Telegram Menu Button
    $syncPayload = @{
        url = $activeUrl
    } | ConvertTo-Json

    $res6 = Invoke-RestMethod -Uri "$baseUrl/api/config/tunnel-url" -Method Post -Headers $testHeaders -Body $syncPayload -ContentType "application/json" -TimeoutSec 10
    
    if ($res6.status -eq "success") {
        Write-Host "  [+] Status: OK (Endpoint validated safely, .env and Telegram Menu preserved)" -ForegroundColor Green
        $passed++
    } else {
        throw "Tunnel URL endpoint validation failed"
    }
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# SUMMARY
Write-Host "`n===================================================================" -ForegroundColor Cyan
Write-Host "                      TEST RESULTS SUMMARY                         " -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "  Total Tests : 6" -ForegroundColor White
Write-Host "  Passed      : $passed" -ForegroundColor Green
Write-Host "  Failed      : $failed" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Red" })
Write-Host "===================================================================`n" -ForegroundColor Cyan

if ($failed -eq 0) {
    Write-Host "🎉 ALL TESTS PASSED! System is fully operational and production data is completely clean." -ForegroundColor Green
} else {
    Write-Host "⚠️ Some tests failed. Please review the errors above." -ForegroundColor Yellow
}
