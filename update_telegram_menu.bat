@echo off
title Update Telegram Mini App Menu Button
chcp 65001 >nul
echo ===================================================================
echo  Updating Telegram Menu Button & Mini App URL from server\.env...
echo ===================================================================
echo.

powershell -NoProfile -Command ^
  "$envMap = @{}; if (Test-Path 'server\.env') { Get-Content 'server\.env' | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { $envMap[$matches[1].Trim()] = $matches[2].Trim() } } };" ^
  "$botToken = $envMap['TELEGRAM_BOT_TOKEN'];" ^
  "$chatId = $envMap['TELEGRAM_CHAT_ID'];" ^
  "$url = $envMap['TUNNEL_URL'];" ^
  "if (-not $botToken -or -not $url) { Write-Host '[ERROR] Please specify TELEGRAM_BOT_TOKEN and TUNNEL_URL in server\.env' -ForegroundColor Red; exit 1 };" ^
  "Write-Host ('Target URL: ' + $url) -ForegroundColor Cyan;" ^
  "$body = @{ menu_button = @{ type = 'web_app'; text = '📱 เปิดแอปสุขภาพ'; web_app = @{ url = $url } } } | ConvertTo-Json -Depth 4;" ^
  "try {" ^
  "  $res = Invoke-RestMethod -Uri ('https://api.telegram.org/bot' + $botToken + '/setChatMenuButton') -Method Post -Body $body -ContentType 'application/json';" ^
  "  if ($res.ok) {" ^
  "    Write-Host '[SUCCESS] Telegram Menu Button successfully updated to new URL!' -ForegroundColor Green;" ^
  "  } else {" ^
  "    Write-Host ('[FAILED] Telegram error: ' + ($res | ConvertTo-Json)) -ForegroundColor Red;" ^
  "  }" ^
  "} catch {" ^
  "  Write-Host ('[ERROR] Failed to contact Telegram API: ' + $_) -ForegroundColor Red;" ^
  "};" ^
  "if ($chatId) {" ^
  "  $msgBody = @{ chat_id = $chatId; text = '📱 *อัปเดตปุ่มเปิด Mini App เรียบร้อยแล้ว!*\nแตะปุ่มด้านล่างเพื่อเปิดแดชบอร์ดได้ทันทีครับ'; parse_mode = 'Markdown'; reply_markup = @{ inline_keyboard = @( @( @{ text = '🚀 เปิด Cucumber RS Mini App'; web_app = @{ url = $url } } ) ) } } | ConvertTo-Json -Depth 5;" ^
  "  try {" ^
  "    $msgRes = Invoke-RestMethod -Uri ('https://api.telegram.org/bot' + $botToken + '/sendMessage') -Method Post -Body $msgBody -ContentType 'application/json';" ^
  "    Write-Host '[SUCCESS] Sent test launch button directly to your chat!' -ForegroundColor Green;" ^
  "  } catch {}" ^
  "}"

echo.
echo ===================================================================
pause
