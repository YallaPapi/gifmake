#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Nuclear OpenSSH reinstall for Windows Server 2022.
    Fixes Error 1067 by completely removing and reinstalling OpenSSH Server.
.NOTES
    Run this in an ELEVATED PowerShell session.
    If you are connected via SSH, run this via RDP or console — it WILL kill your SSH session.
#>

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " OpenSSH Nuclear Reinstall Script"       -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------
# PHASE 1: Stop and remove everything
# -------------------------------------------------------------------
Write-Host "[Phase 1] Stopping and removing existing OpenSSH..." -ForegroundColor Yellow

# Stop services (ignore errors if they don't exist)
foreach ($svc in @("sshd", "ssh-agent")) {
    $service = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne "Stopped") {
            Write-Host "  Stopping $svc..."
            Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        # Kill any lingering process
        Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

# Kill any sshd process that survived
Get-Process -Name "sshd" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# Remove OpenSSH Server capability
$serverCap = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Server*" }
if ($serverCap -and $serverCap.State -eq "Installed") {
    Write-Host "  Removing OpenSSH Server capability..."
    Remove-WindowsCapability -Online -Name $serverCap.Name
    Write-Host "  Removed." -ForegroundColor Green
} else {
    Write-Host "  OpenSSH Server capability not found or already removed." -ForegroundColor DarkGray
}

# Remove OpenSSH Client capability (optional but thorough)
$clientCap = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Client*" }
if ($clientCap -and $clientCap.State -eq "Installed") {
    Write-Host "  Removing OpenSSH Client capability..."
    Remove-WindowsCapability -Online -Name $clientCap.Name
    Write-Host "  Removed." -ForegroundColor Green
}

# -------------------------------------------------------------------
# PHASE 2: Delete all SSH config, keys, and leftover files
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 2] Deleting all SSH configuration and keys..." -ForegroundColor Yellow

$pathsToDelete = @(
    "C:\ProgramData\ssh",
    "C:\Windows\System32\OpenSSH",
    "$env:USERPROFILE\.ssh\known_hosts"
)

foreach ($p in $pathsToDelete) {
    if (Test-Path $p) {
        Write-Host "  Deleting $p..."
        Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Remove any stale service registrations
$staleSshd = Get-Service -Name "sshd" -ErrorAction SilentlyContinue
if ($staleSshd) {
    Write-Host "  Removing stale sshd service registration..."
    sc.exe delete sshd | Out-Null
    Start-Sleep -Seconds 2
}

$staleAgent = Get-Service -Name "ssh-agent" -ErrorAction SilentlyContinue
if ($staleAgent) {
    Write-Host "  Removing stale ssh-agent service registration..."
    sc.exe delete ssh-agent | Out-Null
    Start-Sleep -Seconds 2
}

Write-Host "  Cleanup complete." -ForegroundColor Green

# -------------------------------------------------------------------
# PHASE 3: Verify BFE dependency is running
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 3] Verifying BFE (Base Filtering Engine)..." -ForegroundColor Yellow

$bfe = Get-Service -Name "BFE" -ErrorAction SilentlyContinue
if (-not $bfe) {
    Write-Host "  CRITICAL: BFE service not found. This is a broken Windows installation." -ForegroundColor Red
    exit 1
}

if ($bfe.StartType -eq "Disabled") {
    Write-Host "  BFE is DISABLED — enabling it..." -ForegroundColor Red
    Set-Service -Name "BFE" -StartupType Automatic
}

if ($bfe.Status -ne "Running") {
    Write-Host "  BFE is not running — starting it..." -ForegroundColor Red
    Start-Service -Name "BFE"
    Start-Sleep -Seconds 3
}

$bfe = Get-Service -Name "BFE"
if ($bfe.Status -eq "Running") {
    Write-Host "  BFE is running (StartType: $($bfe.StartType))." -ForegroundColor Green
} else {
    Write-Host "  CRITICAL: Cannot start BFE. sshd will NOT work without it." -ForegroundColor Red
    exit 1
}

# -------------------------------------------------------------------
# PHASE 4: Check if port 22 is in use
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 4] Checking if port 22 is already in use..." -ForegroundColor Yellow

$port22 = netstat -ano | Select-String ":22\s" | Select-String "LISTEN"
if ($port22) {
    Write-Host "  WARNING: Something is already listening on port 22:" -ForegroundColor Red
    Write-Host $port22
    $pidMatch = $port22 -match '\s(\d+)\s*$'
    if ($pidMatch) {
        $pid = $Matches[1]
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Process: $($proc.Name) (PID: $pid, Path: $($proc.Path))" -ForegroundColor Red
            Write-Host "  Kill this process before continuing, or change sshd port." -ForegroundColor Red
        }
    }
    Write-Host "  Continuing anyway — sshd install may still succeed if the process exits..." -ForegroundColor DarkYellow
}
else {
    Write-Host "  Port 22 is free." -ForegroundColor Green
}

# -------------------------------------------------------------------
# PHASE 5: Install OpenSSH Server fresh
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 5] Installing OpenSSH Server..." -ForegroundColor Yellow

# Install client first (dependency)
$clientCap = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Client*" }
if ($clientCap.State -ne "Installed") {
    Write-Host "  Installing OpenSSH Client..."
    Add-WindowsCapability -Online -Name $clientCap.Name | Out-Null
}

# Install server
$serverCap = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Server*" }
if ($serverCap.State -ne "Installed") {
    Write-Host "  Installing OpenSSH Server..."
    Add-WindowsCapability -Online -Name $serverCap.Name | Out-Null
    Write-Host "  Installed." -ForegroundColor Green
} else {
    Write-Host "  Already installed." -ForegroundColor Green
}

# Verify the service exists now
Start-Sleep -Seconds 3
$sshd = Get-Service -Name "sshd" -ErrorAction SilentlyContinue
if (-not $sshd) {
    Write-Host "  CRITICAL: sshd service does not exist after installation." -ForegroundColor Red
    exit 1
}

# -------------------------------------------------------------------
# PHASE 6: Generate fresh host keys
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 6] Generating host keys..." -ForegroundColor Yellow

$sshDir = "C:\ProgramData\ssh"
if (-not (Test-Path $sshDir)) {
    New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
}

# The install should have generated keys, but regenerate to be safe
$keyTypes = @("rsa", "ecdsa", "ed25519")
foreach ($type in $keyTypes) {
    $keyPath = Join-Path $sshDir "ssh_host_${type}_key"
    if (Test-Path $keyPath) {
        Remove-Item -Path $keyPath -Force
        Remove-Item -Path "${keyPath}.pub" -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  Generating $type host key..."
    & ssh-keygen.exe -t $type -f $keyPath -N '""' -q
    if (-not (Test-Path $keyPath)) {
        # Try alternative: let sshd generate them on first start
        Write-Host "  ssh-keygen may not be in PATH yet, keys will be generated on first start." -ForegroundColor DarkYellow
        break
    }
}

# -------------------------------------------------------------------
# PHASE 7: Write clean sshd_config
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 7] Writing clean sshd_config..." -ForegroundColor Yellow

$sshdConfig = @"
# OpenSSH Server Configuration - Clean Install
# Generated by fix_openssh_nuclear.ps1

Port 22
AddressFamily any
ListenAddress 0.0.0.0
ListenAddress ::

# Host keys
HostKey __PROGRAMDATA__/ssh/ssh_host_rsa_key
HostKey __PROGRAMDATA__/ssh/ssh_host_ecdsa_key
HostKey __PROGRAMDATA__/ssh/ssh_host_ed25519_key

# Authentication
PubkeyAuthentication yes
PasswordAuthentication yes
PermitEmptyPasswords no
PermitRootLogin yes

# Logging
SyslogFacility LOCAL0
LogLevel INFO

# Subsystem
Subsystem sftp sftp-server.exe

# IMPORTANT: Comment out the default admin match block that forces authorized_keys
# to administrators_authorized_keys. This is the #1 cause of pubkey auth failures.
# Match Group administrators
#       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
"@

$configPath = Join-Path $sshDir "sshd_config"
Set-Content -Path $configPath -Value $sshdConfig -Encoding UTF8
Write-Host "  Config written to $configPath" -ForegroundColor Green

# -------------------------------------------------------------------
# PHASE 8: Set correct NTFS permissions on ssh directory and keys
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 8] Setting NTFS permissions..." -ForegroundColor Yellow

# sshd_config: SYSTEM and Administrators only, no inheritance
$configAcl = Get-Acl $configPath
$configAcl.SetAccessRuleProtection($true, $false)  # Disable inheritance, remove inherited rules
$configAcl.Access | ForEach-Object { $configAcl.RemoveAccessRule($_) } | Out-Null
$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule("SYSTEM", "FullControl", "Allow")
$adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule("BUILTIN\Administrators", "FullControl", "Allow")
$configAcl.AddAccessRule($systemRule)
$configAcl.AddAccessRule($adminRule)
Set-Acl -Path $configPath -AclObject $configAcl
Write-Host "  sshd_config permissions set (SYSTEM + Administrators only)." -ForegroundColor Green

# Host key files: same treatment
foreach ($type in $keyTypes) {
    $keyPath = Join-Path $sshDir "ssh_host_${type}_key"
    if (Test-Path $keyPath) {
        $keyAcl = Get-Acl $keyPath
        $keyAcl.SetAccessRuleProtection($true, $false)
        $keyAcl.Access | ForEach-Object { $keyAcl.RemoveAccessRule($_) } | Out-Null
        $keyAcl.AddAccessRule($systemRule)
        $keyAcl.AddAccessRule($adminRule)
        Set-Acl -Path $keyPath -AclObject $keyAcl
        Write-Host "  Permissions set on ssh_host_${type}_key" -ForegroundColor Green
    }
}

# -------------------------------------------------------------------
# PHASE 9: Configure and start the service
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 9] Configuring sshd service..." -ForegroundColor Yellow

Set-Service -Name "sshd" -StartupType Automatic
Write-Host "  sshd set to Automatic startup." -ForegroundColor Green

# Also enable ssh-agent
$agent = Get-Service -Name "ssh-agent" -ErrorAction SilentlyContinue
if ($agent) {
    Set-Service -Name "ssh-agent" -StartupType Automatic
    Start-Service -Name "ssh-agent" -ErrorAction SilentlyContinue
    Write-Host "  ssh-agent set to Automatic and started." -ForegroundColor Green
}

Write-Host ""
Write-Host "[Phase 10] Starting sshd..." -ForegroundColor Yellow

try {
    Start-Service -Name "sshd"
    Start-Sleep -Seconds 3
    $sshd = Get-Service -Name "sshd"
    if ($sshd.Status -eq "Running") {
        Write-Host "  sshd is RUNNING." -ForegroundColor Green
    } else {
        Write-Host "  sshd status: $($sshd.Status)" -ForegroundColor Red
        throw "sshd did not reach Running state"
    }
}
catch {
    Write-Host ""
    Write-Host "  FAILED to start sshd. Checking event log for details..." -ForegroundColor Red
    Write-Host ""
    Get-WinEvent -LogName Application -FilterXPath "*[System[Provider[@Name='sshd'] or Provider[@Name='OpenSSH']]]" -MaxEvents 5 -ErrorAction SilentlyContinue |
        Format-List TimeCreated, Id, LevelDisplayName, Message
    Get-WinEvent -LogName System -FilterXPath "*[System[Provider[@Name='Service Control Manager']]]" -MaxEvents 5 -ErrorAction SilentlyContinue |
        Where-Object { $_.Message -like "*sshd*" } |
        Format-List TimeCreated, Id, Message
    Write-Host "  Check the output above for the root cause." -ForegroundColor Yellow
    exit 1
}

# -------------------------------------------------------------------
# PHASE 11: Add firewall rule (even if firewall is off, for when it's re-enabled)
# -------------------------------------------------------------------
Write-Host ""
Write-Host "[Phase 11] Adding firewall rule for port 22..." -ForegroundColor Yellow

# Remove any existing SSH rules to avoid duplicates
Get-NetFirewallRule -DisplayName "*SSH*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "*sshd*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue

New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" `
    -DisplayName "OpenSSH Server (sshd) Port 22" `
    -Description "Allow inbound SSH on port 22" `
    -Enabled True `
    -Direction Inbound `
    -Protocol TCP `
    -Action Allow `
    -LocalPort 22 | Out-Null

Write-Host "  Firewall rule added." -ForegroundColor Green

# -------------------------------------------------------------------
# PHASE 12: Verify
# -------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " VERIFICATION" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Service status
$sshd = Get-Service -Name "sshd"
Write-Host "sshd service:  $($sshd.Status) (StartType: $($sshd.StartType))" -ForegroundColor $(if ($sshd.Status -eq "Running") { "Green" } else { "Red" })

# BFE status
$bfe = Get-Service -Name "BFE"
Write-Host "BFE service:   $($bfe.Status) (StartType: $($bfe.StartType))" -ForegroundColor $(if ($bfe.Status -eq "Running") { "Green" } else { "Red" })

# Port binding
Write-Host ""
Write-Host "Port 22 status:" -ForegroundColor Cyan
netstat -ano | Select-String ":22\s" | Select-String "LISTEN"

# Host keys
Write-Host ""
Write-Host "Host keys:" -ForegroundColor Cyan
Get-ChildItem "C:\ProgramData\ssh\ssh_host_*" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  $($_.Name) ($($_.Length) bytes)"
}

# Config permissions
Write-Host ""
Write-Host "sshd_config permissions:" -ForegroundColor Cyan
(Get-Acl "C:\ProgramData\ssh\sshd_config").Access | Format-Table IdentityReference, FileSystemRights, AccessControlType -AutoSize

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " DONE. Test with: ssh user@<server-ip>"  -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
