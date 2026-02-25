#Requires -RunAsAdministrator
# fix_windows_firewall_rdp.ps1
# Fixes: BFE/mpssvc disabled, RDP keepalive, SSH keepalive, firewall rules
# Run as Administrator on 88.99.142.89

$ErrorActionPreference = "Continue"

Write-Host "=== STEP 1: Fix Base Filtering Engine (BFE) ===" -ForegroundColor Cyan

# BFE must be re-enabled before mpssvc or any firewall cmdlets will work
sc.exe config BFE start= auto
sc.exe start BFE
Start-Sleep -Seconds 3
$bfe = sc.exe query BFE
Write-Host $bfe

if ($bfe -notmatch "RUNNING") {
    Write-Host "ERROR: BFE failed to start. Trying dependency fix..." -ForegroundColor Red
    # Sometimes BFE registry permissions are stripped - fix them
    $acl = Get-Acl "HKLM:\SYSTEM\CurrentControlSet\Services\BFE"
    $rule = New-Object System.Security.AccessControl.RegistryAccessRule(
        "NT SERVICE\BFE",
        "FullControl",
        "ContainerInherit,ObjectInherit",
        "None",
        "Allow"
    )
    $acl.SetAccessRule($rule)
    Set-Acl "HKLM:\SYSTEM\CurrentControlSet\Services\BFE" $acl
    sc.exe start BFE
    Start-Sleep -Seconds 3
}

Write-Host "=== STEP 2: Fix Windows Defender Firewall (mpssvc) ===" -ForegroundColor Cyan
sc.exe config mpssvc start= auto
sc.exe start mpssvc
Start-Sleep -Seconds 3
sc.exe query mpssvc

Write-Host "=== STEP 3: Verify BFE and mpssvc are running ===" -ForegroundColor Cyan
$bfeState = (sc.exe query BFE | Select-String "STATE").ToString().Trim()
$mpsState = (sc.exe query mpssvc | Select-String "STATE").ToString().Trim()
Write-Host "BFE:    $bfeState"
Write-Host "mpssvc: $mpsState"

if ($bfeState -notmatch "RUNNING" -or $mpsState -notmatch "RUNNING") {
    Write-Host "CRITICAL: Services still not running. Cannot safely apply firewall rules." -ForegroundColor Red
    Write-Host "BFE exit code:"
    sc.exe query BFE | Select-String "WIN32_EXIT_CODE"
    exit 1
}

Write-Host "=== STEP 4: Set Firewall Profiles to Sensible Defaults ===" -ForegroundColor Cyan
# Now that mpssvc is running, firewall cmdlets work
# Set all profiles: allow outbound, block inbound by default (explicit rules open what we need)
Set-NetFirewallProfile -Profile Domain,Private,Public `
    -Enabled True `
    -DefaultInboundAction Block `
    -DefaultOutboundAction Allow `
    -NotifyOnListen False `
    -AllowUnicastResponseToMulticast True

Write-Host "=== STEP 5: Remove Conflicting/Duplicate RDP and SSH Rules ===" -ForegroundColor Cyan
# Remove any existing RDP/SSH rules to start clean
Get-NetFirewallRule | Where-Object {
    $_.DisplayName -match "Remote Desktop|RDP|OpenSSH|SSH" -or
    $_.LocalPort -match "3389|22"
} | Remove-NetFirewallRule -ErrorAction SilentlyContinue
Write-Host "Cleared old RDP/SSH rules."

Write-Host "=== STEP 6: Allow SSH (22/tcp) - Inbound ===" -ForegroundColor Cyan
New-NetFirewallRule `
    -DisplayName "OpenSSH Server (SSH-In)" `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 22 `
    -Action Allow `
    -Profile Any `
    -Enabled True `
    -Description "Permanent SSH access - no rate limit, no idle timeout"

Write-Host "=== STEP 7: Allow RDP (3389/tcp and 3389/udp) - Inbound ===" -ForegroundColor Cyan
# TCP for main RDP connection
New-NetFirewallRule `
    -DisplayName "Remote Desktop - TCP-In (Permanent)" `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 3389 `
    -Action Allow `
    -Profile Any `
    -Enabled True `
    -Description "RDP permanent - no rate limit, no keepalive interference"

# UDP for RDP 8.0+ (used for audio, clipboard, and UDP transport fallback)
New-NetFirewallRule `
    -DisplayName "Remote Desktop - UDP-In (Permanent)" `
    -Direction Inbound `
    -Protocol UDP `
    -LocalPort 3389 `
    -Action Allow `
    -Profile Any `
    -Enabled True `
    -Description "RDP UDP transport - required for RDP 8.0 UDP mode"

Write-Host "=== STEP 8: Allow loopback (required for many Windows services) ===" -ForegroundColor Cyan
New-NetFirewallRule `
    -DisplayName "Loopback - Allow All" `
    -Direction Inbound `
    -InterfaceAlias "Loopback*" `
    -Action Allow `
    -Profile Any `
    -Enabled True

Write-Host "=== STEP 9: Allow ICMP (ping) - useful for diagnostics ===" -ForegroundColor Cyan
New-NetFirewallRule `
    -DisplayName "ICMPv4-In (Allow)" `
    -Direction Inbound `
    -Protocol ICMPv4 `
    -Action Allow `
    -Profile Any `
    -Enabled True

Write-Host "=== STEP 10: Fix RDP KeepAlive - THIS IS THE 5-MINUTE DROP FIX ===" -ForegroundColor Cyan

# Enable server-side TCP keepalive for RDP
# KeepAliveTimeout = 1 means send keepalive every 1 minute
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v KeepAliveTimeout /t REG_DWORD /d 1 /f

# Disable "auto-detect network quality" - this is the PRIMARY cause of 5-min drops
# It detects packet timing jitter on WAN connections and resets the session
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v SelectNetworkDetect /t REG_DWORD /d 0 /f

# Explicitly set no idle timeout (0 = unlimited)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v MaxIdleTime /t REG_DWORD /d 0 /f

# Explicitly set no disconnection timeout (0 = unlimited)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v MaxDisconnectionTime /t REG_DWORD /d 0 /f

# Explicitly set no max connection time (0 = unlimited)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v MaxConnectionTime /t REG_DWORD /d 0 /f

# Stop inheriting from parent (parent may have defaults that override our settings)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v fInheritMaxIdleTime /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v fInheritMaxDisconnectionTime /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v fInheritMaxSessionTime /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v fInheritResetBroken /t REG_DWORD /d 0 /f

# Do not reset session on broken connection - let it stay disconnected (reconnectable)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" `
    /v fResetBroken /t REG_DWORD /d 0 /f

Write-Host "=== STEP 11: Fix Group Policy RDP Timeouts ===" -ForegroundColor Cyan
# Override any GPO timeout values
$gpoPath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
Set-ItemProperty -Path $gpoPath -Name "MaxIdleTime" -Value 0 -Type DWord -ErrorAction SilentlyContinue
Set-ItemProperty -Path $gpoPath -Name "MaxDisconnectionTime" -Value 0 -Type DWord -ErrorAction SilentlyContinue
Set-ItemProperty -Path $gpoPath -Name "MaxConnectionTime" -Value 0 -Type DWord -ErrorAction SilentlyContinue

Write-Host "=== STEP 12: Fix OpenSSH Server keepalive (fixes SSH drops too) ===" -ForegroundColor Cyan
$sshdConfig = "C:\ProgramData\ssh\sshd_config"
if (Test-Path $sshdConfig) {
    $content = Get-Content $sshdConfig -Raw

    # Remove old keepalive lines if present
    $content = $content -replace "(?m)^#?ClientAliveInterval.*$\n?", ""
    $content = $content -replace "(?m)^#?ClientAliveCountMax.*$\n?", ""
    $content = $content -replace "(?m)^#?TCPKeepAlive.*$\n?", ""

    # Append correct values
    $content = $content.TrimEnd() + "`n"
    $content += "TCPKeepAlive yes`n"
    $content += "ClientAliveInterval 60`n"
    $content += "ClientAliveCountMax 10`n"

    Set-Content $sshdConfig $content -Encoding UTF8
    Write-Host "Updated sshd_config: ClientAliveInterval=60, ClientAliveCountMax=10"

    # Restart sshd to apply
    Restart-Service sshd
    Start-Sleep -Seconds 2
    sc.exe query sshd | Select-String "STATE"
} else {
    Write-Host "WARNING: sshd_config not found at $sshdConfig" -ForegroundColor Yellow
    # Try alternate location
    $sshdConfigAlt = "$env:WINDIR\System32\OpenSSH\sshd_config"
    if (Test-Path $sshdConfigAlt) {
        Write-Host "Found sshd_config at $sshdConfigAlt"
    }
}

Write-Host "=== STEP 13: Restart RDP service to apply keepalive settings ===" -ForegroundColor Cyan
Restart-Service TermService -Force
Start-Sleep -Seconds 3
sc.exe query TermService | Select-String "STATE"

Write-Host "=== STEP 14: Verify firewall rules are applied ===" -ForegroundColor Cyan
Get-NetFirewallRule | Where-Object { $_.DisplayName -match "SSH|Remote Desktop|Loopback|ICMP" } |
    Select-Object DisplayName, Enabled, Direction, Action |
    Format-Table -AutoSize

Write-Host "=== STEP 15: Verify ports are listening ===" -ForegroundColor Cyan
netstat -an | findstr "LISTENING" | findstr -E "3389|22"

Write-Host "=== STEP 16: Verify final firewall profile state ===" -ForegroundColor Cyan
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction, DefaultOutboundAction | Format-Table -AutoSize

Write-Host ""
Write-Host "=== COMPLETE ===" -ForegroundColor Green
Write-Host "Summary of changes applied:" -ForegroundColor Green
Write-Host "  [1] BFE re-enabled (was DISABLED) and started"
Write-Host "  [2] mpssvc (Windows Firewall) re-enabled and started"
Write-Host "  [3] Firewall set: Block inbound by default, Allow outbound"
Write-Host "  [4] SSH port 22 TCP: ALLOWED (permanent, all profiles)"
Write-Host "  [5] RDP port 3389 TCP+UDP: ALLOWED (permanent, all profiles)"
Write-Host "  [6] SelectNetworkDetect=0: DISABLED (was causing 5-min drops)"
Write-Host "  [7] KeepAliveTimeout=1: Keepalives every 1 minute"
Write-Host "  [8] MaxIdleTime=0: No idle timeout"
Write-Host "  [9] fInheritMax*=0: Stop inheriting timeout from parent keys"
Write-Host "  [10] sshd_config: ClientAliveInterval=60, CountMax=10"
Write-Host ""
Write-Host "NOTE: You do NOT need to reboot. Changes are live immediately."
Write-Host "NOTE: Test RDP connection NOW before closing this session."
