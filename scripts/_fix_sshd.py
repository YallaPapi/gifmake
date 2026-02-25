"""Fix sshd on VPS via WinRM."""
import os
import winrm
import base64

VPS_IP = "88.99.142.89"
VPS_USER = "Administrator"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

VPS_PASS = require_env("GIFMAKE_VPS_WINRM_PASSWORD")

def run_ps(cmd: str) -> str:
    s = winrm.Session(f'http://{VPS_IP}:5985/wsman', auth=(VPS_USER, VPS_PASS), transport='ntlm')
    encoded = base64.b64encode(cmd.encode('utf-16-le')).decode('ascii')
    r = s.run_cmd('powershell', ['-EncodedCommand', encoded])
    out = r.std_out.decode('utf-8', errors='replace').strip()
    err = r.std_err.decode('utf-8', errors='replace').strip()
    # Filter out CLIXML progress noise
    if 'CLIXML' in err and 'Preparing modules' in err:
        err = ''
    if err:
        return f"{out}\nSTDERR: {err}"
    return out

# Step 1: Check event log for sshd errors
print("=== Step 1: Check event log for sshd errors ===")
result = run_ps("""
$events = Get-WinEvent -LogName System -MaxEvents 50 | Where-Object { $_.Message -like '*sshd*' -or $_.Message -like '*OpenSSH*' }
$events | Select-Object TimeCreated, LevelDisplayName, Message | Format-List
""")
print(result or "(no sshd events found in System log)")

print("\n=== Step 1b: Check OpenSSH/sshd log ===")
result = run_ps("""
$events = Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 10 -ErrorAction SilentlyContinue
if ($events) { $events | Select-Object TimeCreated, LevelDisplayName, Message | Format-List }
else { Write-Output 'No OpenSSH/Operational log' }
""")
print(result)

print("\n=== Step 1c: Try sshd -t (config test) ===")
result = run_ps("""
$env:Path = $env:Path + ';C:\\Windows\\System32\\OpenSSH'
& 'C:\\Windows\\System32\\OpenSSH\\sshd.exe' -t 2>&1 | Out-String
""")
print(result)

# Step 2: Fix sshd_config - move global options before Match block
print("\n=== Step 2: Fix sshd_config ===")
new_config = r"""# OpenSSH Server configuration
Port 22
AddressFamily any
ListenAddress 0.0.0.0

# Logging
SyslogFacility AUTH
LogLevel INFO

# Authentication
LoginGraceTime 2m
PermitRootLogin yes
StrictModes yes
MaxAuthTries 6
MaxSessions 10
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication yes
PermitEmptyPasswords no

# Connection keepalive
TCPKeepAlive yes
ClientAliveInterval 60
ClientAliveCountMax 10

# Subsystem
Subsystem sftp sftp-server.exe

# Admin authorized keys
Match Group administrators
    AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
"""

result = run_ps(f"""
$config = @'
{new_config}
'@
Set-Content -Path 'C:\\ProgramData\\ssh\\sshd_config' -Value $config -Force
Write-Output 'sshd_config written successfully'
Get-Content 'C:\\ProgramData\\ssh\\sshd_config' | Measure-Object -Line | Select-Object -ExpandProperty Lines
""")
print(result)

# Step 3: Fix host key permissions
print("\n=== Step 3: Fix host key permissions ===")
result = run_ps("""
$sshDir = 'C:\\ProgramData\\ssh'

# Fix host key permissions - must be owned by SYSTEM/Admins only
$keys = @('ssh_host_rsa_key', 'ssh_host_ecdsa_key', 'ssh_host_ed25519_key', 'ssh_host_dsa_key')
foreach ($key in $keys) {
    $path = Join-Path $sshDir $key
    if (Test-Path $path) {
        # Remove inheritance and set correct ACL
        $acl = Get-Acl $path
        $acl.SetAccessRuleProtection($true, $false)
        $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) } | Out-Null

        $sysRule = New-Object System.Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')
        $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'Allow')
        $acl.AddAccessRule($sysRule)
        $acl.AddAccessRule($adminRule)
        Set-Acl -Path $path -AclObject $acl
        Write-Output "Fixed permissions on $key"
    }
}

# Fix administrators_authorized_keys permissions
$authKeys = Join-Path $sshDir 'administrators_authorized_keys'
if (Test-Path $authKeys) {
    $acl = Get-Acl $authKeys
    $acl.SetAccessRuleProtection($true, $false)
    $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) } | Out-Null
    $sysRule = New-Object System.Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')
    $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'Allow')
    $acl.AddAccessRule($sysRule)
    $acl.AddAccessRule($adminRule)
    Set-Acl -Path $authKeys -AclObject $acl
    Write-Output 'Fixed permissions on administrators_authorized_keys'
}

Write-Output 'All key permissions fixed'
""")
print(result)

# Step 4: Test config and start sshd
print("\n=== Step 4: Test config and start sshd ===")
result = run_ps("""
$testResult = & 'C:\\Windows\\System32\\OpenSSH\\sshd.exe' -t 2>&1 | Out-String
Write-Output "Config test: $testResult"

if ($LASTEXITCODE -eq 0 -or $testResult -eq '') {
    Stop-Service sshd -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-Service sshd -ErrorAction Stop
    $status = Get-Service sshd
    Write-Output "sshd status: $($status.Status)"
} else {
    Write-Output "Config test FAILED - not starting sshd"
}
""")
print(result)

# Step 5: Verify port 22 is listening
print("\n=== Step 5: Verify port 22 ===")
result = run_ps("""
Get-NetTCPConnection -LocalPort 22 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State | Format-Table -AutoSize
""")
print(result or "Port 22 NOT listening")
