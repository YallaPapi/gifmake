"""Enable Windows Firewall with proper rules on VPS via WinRM."""
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
AUTOLOGIN_PASSWORD = require_env("GIFMAKE_VPS_AUTOLOGIN_PASSWORD")

def run_ps(cmd: str) -> str:
    s = winrm.Session(f'http://{VPS_IP}:5985/wsman', auth=(VPS_USER, VPS_PASS), transport='ntlm')
    encoded = base64.b64encode(cmd.encode('utf-16-le')).decode('ascii')
    r = s.run_cmd('powershell', ['-EncodedCommand', encoded])
    out = r.std_out.decode('utf-8', errors='replace').strip()
    err = r.std_err.decode('utf-8', errors='replace').strip()
    # Filter CLIXML progress noise
    if err:
        import re
        lines = err.split('\n')
        real_errors = [l for l in lines if 'CLIXML' not in l and 'Preparing modules' not in l
                      and 'progress' not in l.lower() and l.strip()]
        err = '\n'.join(real_errors)
    if err:
        return f"{out}\nSTDERR: {err}"
    return out

# Step 1: Enable firewall service
print("=== Step 1: Enable Windows Firewall service ===")
result = run_ps("""
Set-Service mpssvc -StartupType Automatic
Start-Service mpssvc -ErrorAction Stop
$svc = Get-Service mpssvc
Write-Output "Firewall service: $($svc.Status)"
""")
print(result)

# Step 2: Create allow rules BEFORE enabling profiles
print("\n=== Step 2: Create firewall allow rules ===")
result = run_ps("""
# Remove any old custom rules first
Get-NetFirewallRule -DisplayName 'Allow RDP Custom' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName 'Allow SSH' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName 'Allow WinRM' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName 'Allow ICMPv4' -ErrorAction SilentlyContinue | Remove-NetFirewallRule

# Allow RDP (3389) from anywhere for now
New-NetFirewallRule -DisplayName 'Allow RDP Custom' -Direction Inbound -Protocol TCP -LocalPort 3389 -Action Allow -Profile Any | Out-Null
Write-Output 'Created: Allow RDP (3389)'

# Allow SSH (22) from anywhere
New-NetFirewallRule -DisplayName 'Allow SSH' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any | Out-Null
Write-Output 'Created: Allow SSH (22)'

# Allow WinRM (5985) from anywhere
New-NetFirewallRule -DisplayName 'Allow WinRM' -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow -Profile Any | Out-Null
Write-Output 'Created: Allow WinRM (5985)'

# Allow ping
New-NetFirewallRule -DisplayName 'Allow ICMPv4' -Direction Inbound -Protocol ICMPv4 -Action Allow -Profile Any | Out-Null
Write-Output 'Created: Allow ICMP (ping)'

Write-Output 'All rules created'
""")
print(result)

# Step 3: Enable firewall profiles (Domain, Private, Public)
print("\n=== Step 3: Enable firewall profiles ===")
result = run_ps("""
Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True -DefaultInboundAction Block -DefaultOutboundAction Allow
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction, DefaultOutboundAction | Format-Table -AutoSize
""")
print(result)

# Step 4: Verify rules are active
print("\n=== Step 4: Verify rules ===")
result = run_ps("""
Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True |
    Where-Object { $_.DisplayName -match 'RDP|SSH|WinRM|ICMP|Remote Desktop' } |
    Select-Object DisplayName, Enabled, Direction, Action | Format-Table -AutoSize
""")
print(result)

# Step 5: Configure auto-login
print("\n=== Step 5: Configure auto-login ===")
result = run_ps(f"""
$regPath = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon'
Set-ItemProperty -Path $regPath -Name AutoAdminLogon -Value '1' -Force
Set-ItemProperty -Path $regPath -Name DefaultUserName -Value 'Administrator' -Force
Set-ItemProperty -Path $regPath -Name DefaultPassword -Value '{AUTOLOGIN_PASSWORD}' -Force
Set-ItemProperty -Path $regPath -Name DefaultDomainName -Value '' -Force

# Disable lock screen
New-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization' -Name NoLockScreen -Value 1 -PropertyType DWord -Force -ErrorAction SilentlyContinue | Out-Null

# Disable auto restart after updates
Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name DisableAutomaticRestartSignOn -Value 1 -Force

Write-Output 'Auto-login configured for Administrator'
""")
print(result)

# Step 6: Set account lockout policy to never lock
print("\n=== Step 6: Set account lockout to never ===")
result = run_ps("""
net accounts /lockoutthreshold:0
""")
print(result)

print("\n=== DONE ===")
