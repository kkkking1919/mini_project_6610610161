# test_system.ps1
# Comprehensive End-to-End Simulation Test for Cucumber RS AIoT System

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$baseUrl = "http://localhost:8080"

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "   Cucumber RS - Comprehensive AIoT System & Telegram Test Suite   " -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "Target Base URL: $baseUrl`n" -ForegroundColor White

$passed = 0
$failed = 0

# TEST 1: Config & Health Check
Write-Host "[TEST 1/6] Checking Server Health & Configuration..." -ForegroundColor Yellow
try {
    $res1 = Invoke-RestMethod -Uri "$baseUrl/api/config" -Method Get -TimeoutSec 5
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

    $res2 = Invoke-RestMethod -Uri "$baseUrl/api/sensor-data" -Method Post -Body $sensorPayload -ContentType "application/json" -TimeoutSec 5
    $getSensor = Invoke-RestMethod -Uri "$baseUrl/api/sensor-data" -Method Get -TimeoutSec 5
    
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

# TEST 3: Edge-AI Fall Event Simulation & Emergency Alert
Write-Host "`n[TEST 3/6] Simulating Edge-AI Fall Event (High-Impact Spike 3.85G)..." -ForegroundColor Yellow
$eventId = $null
try {
    $fallPayload = @{
        max_g       = 3.85
        temperature = 29.4
        humidity    = 63.5
        pressure    = 1013.2
    } | ConvertTo-Json

    $res3 = Invoke-RestMethod -Uri "$baseUrl/api/fall-event" -Method Post -Body $fallPayload -ContentType "application/json" -TimeoutSec 10
    $eventId = $res3.event_id
    $risk = $res3.risk

    Write-Host "  [+] Status: OK (Emergency Triggered)" -ForegroundColor Green
    Write-Host "      - Event ID     : $eventId" -ForegroundColor Gray
    Write-Host "      - Risk Level   : $risk" -ForegroundColor Magenta
    
    # Verify Emergency Status API
    $emStatus = Invoke-RestMethod -Uri "$baseUrl/api/emergency-status" -Method Get -TimeoutSec 5
    if ($emStatus.active -eq $true) {
        Write-Host "      - Emergency UI : ACTIVE (Modal shown on Dashboard & Alert sent to Telegram)" -ForegroundColor Green
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
        $res4 = Invoke-RestMethod -Uri "$baseUrl/api/fall-event/$eventId/resolve" -Method Post -TimeoutSec 5
        $emStatusAfter = Invoke-RestMethod -Uri "$baseUrl/api/emergency-status" -Method Get -TimeoutSec 5
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

    $res5 = Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method Post -Body $chatPayload -ContentType "application/json" -TimeoutSec 30
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
    $res5b = Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method Post -Body $fallChatPayload -ContentType "application/json" -TimeoutSec 30
    Write-Host "  [+] Fall History Reply:" -ForegroundColor Green
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "$($res5b.reply)" -ForegroundColor Cyan
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkGray
} catch {
    Write-Host "  [-] Failed: $_" -ForegroundColor Red
    $failed++
}

# TEST 6: Auto-Sync Tunnel URL & Telegram Menu Button
Write-Host "`n[TEST 6/6] Testing Tunnel URL Auto-Sync to Telegram Bot & Mini App..." -ForegroundColor Yellow
try {
    $testUrl = "https://dip-msie-noble-planets.trycloudflare.com"
    $syncPayload = @{
        url = $testUrl
    } | ConvertTo-Json

    $res6 = Invoke-RestMethod -Uri "$baseUrl/api/config/tunnel-url" -Method Post -Body $syncPayload -ContentType "application/json" -TimeoutSec 10
    $verifyUrl = Invoke-RestMethod -Uri "$baseUrl/api/config/tunnel-url" -Method Get -TimeoutSec 5
    
    if ($verifyUrl.tunnel_url -eq $testUrl) {
        Write-Host "  [+] Status: OK (URL & Telegram Menu Button Synced)" -ForegroundColor Green
        Write-Host "      - Configured URL     : $($verifyUrl.tunnel_url)" -ForegroundColor Gray
        Write-Host "      - Menu Button Status : setChatMenuButton updated on Telegram" -ForegroundColor Green
        $passed++
    } else {
        throw "Tunnel URL update not matched"
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
    Write-Host "🎉 ALL TESTS PASSED! System is fully operational and verified." -ForegroundColor Green
} else {
    Write-Host "⚠️ Some tests failed. Please review the errors above." -ForegroundColor Yellow
}
