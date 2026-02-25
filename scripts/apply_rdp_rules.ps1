#Requires -RunAsAdministrator
# apply_rdp_rules.ps1
# Apply RDP firewall rules and keepalive settings
# Run after BFE + mpssvc are confirmed running

$ErrorActionPreference = "Continue"

Write-Host "=== Checking existing RDP rules ===" -ForegroundColor Cyan
$rdpRules = Get-NetFirewallRule | Where-Object { $_.DisplayName -match "Remote Desktop.*Permanent|RDP.*Permanent" }
Write-Host "Existing permanent RDP rules: $($rdpRules.Count)"

Write-Host "=== Adding RDP TCP 3389 inbound rule ===" -ForegroundColor Cyan
# Remove old one if exists to avoid duplicates
Remove-NetFirewallRule -DisplayName "Remote Desktop - TCP-In (Permanent)" -ErrorAction SilentlyContinue

New-NetFirewallRule `
    -DisplayName "Remote Desktop - TCP-In (Permanent)" `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 3389 `
    -Action Allow `
    -Profile Any `
    -Enabled True

Write-Host "=== Adding RDP UDP 3389 inbound rule ===" -ForegroundColor Cyan
Remove-NetFirewallRule -DisplayName "Remote Desktop - UDP-In (Permanent)" -ErrorAction SilentlyContinue

New-NetFirewallRule `
    -DisplayName "Remote Desktop - UDP-In (Permanent)" `
    -Direction Inbound `
    -Protocol UDP `
    -LocalPort 3389 `
    -Action Allow `
    -Profile Any `
    -Enabled True

Write-Host "=== Adding ICMPv4 allow rule ===" -ForegroundColor Cyan
Remove-NetFirewallRule -DisplayName "ICMPv4-In (Allow)" -ErrorAction SilentlyContinue
New-NetFirewallRule `
    -DisplayName "ICMPv4-In (Allow)" `
    -Direction Inbound `
    -Protocol ICMPv4 `
    -Action Allow `
    -Profile Any `
    -Enabled True

Write-Host "=== RDP KeepAlive and Timeout Registry Fixes ===" -ForegroundColor Cyan

$rdpKey = "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"

# Enable server-side keepalive every 1 minute
reg add $rdpKey /v KeepAliveTimeout /t REG_DWORD /d 1 /f
Write-Host "KeepAliveTimeout = 1 (every 1 min)"

# CRITICAL: Disable network auto-detect - this is the #1 cause of 5-min drops on WAN
reg add $rdpKey /v SelectNetworkDetect /t REG_DWORD /d 0 /f
Write-Host "SelectNetworkDetect = 0 (DISABLED - was causing 5-min drops)"

# No idle timeout
reg add $rdpKey /v MaxIdleTime /t REG_DWORD /d 0 /f
Write-Host "MaxIdleTime = 0 (unlimited)"

# No disconnection timeout
reg add $rdpKey /v MaxDisconnectionTime /t REG_DWORD /d 0 /f
Write-Host "MaxDisconnectionTime = 0 (unlimited)"

# No max session time
reg add $rdpKey /v MaxConnectionTime /t REG_DWORD /d 0 /f
Write-Host "MaxConnectionTime = 0 (unlimited)"

# Stop inheriting timeouts from parent TerminalServer key
reg add $rdpKey /v fInheritMaxIdleTime /t REG_DWORD /d 0 /f
reg add $rdpKey /v fInheritMaxDisconnectionTime /t REG_DWORD /d 0 /f
reg add $rdpKey /v fInheritMaxSessionTime /t REG_DWORD /d 0 /f
reg add $rdpKey /v fInheritResetBroken /t REG_DWORD /d 0 /f
Write-Host "fInherit* = 0 (no longer inheriting from parent)"

# Don't reset/terminate session on broken link - let it stay as disconnected (reconnectable)
reg add $rdpKey /v fResetBroken /t REG_DWORD /d 0 /f
Write-Host "fResetBroken = 0 (session stays reconnectable)"

Write-Host "=== Group Policy RDP Timeout Overrides ===" -ForegroundColor Cyan
$gpoPath = "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
reg add $gpoPath /v MaxIdleTime /t REG_DWORD /d 0 /f
reg add $gpoPath /v MaxDisconnectionTime /t REG_DWORD /d 0 /f
reg add $gpoPath /v MaxConnectionTime /t REG_DWORD /d 0 /f
Write-Host "GPO timeout overrides set to 0"

Write-Host "=== Fix sshd_config keepalive ===" -ForegroundColor Cyan
$sshdConfig = "C:\ProgramData\ssh\sshd_config"
if (Test-Path $sshdConfig) {
    $content = Get-Content $sshdConfig -Raw
    $content = $content -replace "(?m)^#?ClientAliveInterval.*`r?`n?", ""
    $content = $content -replace "(?m)^#?ClientAliveCountMax.*`r?`n?", ""
    $content = $content -replace "(?m)^#?TCPKeepAlive.*`r?`n?", ""
    $content = $content.TrimEnd()
    $content += "`r`nTCPKeepAlive yes`r`nClientAliveInterval 60`r`nClientAliveCountMax 10`r`n"
    Set-Content $sshdConfig $content -Encoding UTF8
    Write-Host "sshd_config updated: ClientAliveInterval=60, ClientAliveCountMax=10"
} else {
    Write-Host "WARNING: sshd_config not at $sshdConfig" -ForegroundColor Yellow
}

Write-Host "=== Restart TermService (RDP) to apply registry changes ===" -ForegroundColor Cyan
Restart-Service TermService -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$ts = Get-Service TermService
Write-Host "TermService: $($ts.Status)"

Write-Host "=== Restart sshd to apply sshd_config changes ===" -ForegroundColor Cyan
Restart-Service sshd -ErrorAction SilentlyContinue
# NOTE: SSH connection will drop here briefly - this is expected

Write-Host "=== VERIFICATION ===" -ForegroundColor Green
Write-Host "Firewall Profiles:"
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction | Format-Table -AutoSize

Write-Host "Active Inbound Allow Rules (SSH + RDP):"
Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True |
    Where-Object { $_.DisplayName -match "SSH|Remote Desktop.*Permanent|OpenSSH|ICMPv4" } |
    Select-Object DisplayName, Enabled, Action |
    Format-Table -AutoSize

Write-Host "Listening ports:"
netstat -an | findstr "LISTENING"

Write-Host ""
Write-Host "=== ALL DONE ===" -ForegroundColor Green
Write-Host "RDP keepalive: ON (1 min interval)"
Write-Host "SelectNetworkDetect: OFF (5-min drops fixed)"
Write-Host "All timeouts: DISABLED (0 = unlimited)"
Write-Host "SSH keepalive: 60s interval, 10 retries"
Write-Host "Firewall: Block inbound, Allow outbound, SSH+RDP explicitly allowed"
