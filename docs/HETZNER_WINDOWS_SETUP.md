# Hetzner Dedicated Server — Windows Setup Guide

Last updated: 2026-02-17

## Server Details

| Field | Value |
|-------|-------|
| IP | 65.109.25.233 |
| Subnet | /26 (255.255.255.192) |
| Gateway | 65.109.25.193 |
| DNS | 185.12.64.1, 185.12.64.2 |
| Admin Password | Hetzner2026! |
| Boot Mode | Legacy BIOS (MBR) |

## Quick Reference (Old Hetzner: 88.99.142.89)

The old server used Hetzner's built-in WinPE installer. DHCP worked out of the box.
Network config script was at `C:\network-setup.ps1` as a fallback.

---

## Method 1: DigiRDP One-Click Script (Free, 180-day eval)

### Step 1: Activate Rescue Mode

1. Log into Hetzner Robot panel
2. Go to your server → **Rescue** tab
3. Select **Linux 64-bit** → Activate
4. **Save the root password** they display
5. Go to **Reset** tab → **Execute an automatic hardware reset**
6. Wait 1-2 minutes

### Step 2: SSH Into Rescue

```bash
ssh root@65.109.25.233
# Enter the rescue password from Step 1
```

### Step 3: Generate Install Command

Go to https://hetzner.digirdp.com/ and fill in:
- **Server IP:** 65.109.25.233
- **Gateway:** 65.109.25.193
- **Windows Version:** 2022
- **Administrator Password:** Hetzner2026!

Click "Generate Installation Script" — copy the command.

Or use curl directly:
```bash
curl -s -X POST "https://hetzner.digirdp.com/api/generate.php" \
  -H "Content-Type: application/json" \
  -d '{"ip":"65.109.25.233","gateway":"65.109.25.193","version":"2022","password":"Hetzner2026!"}'
```
This returns a JSON with a `command` field — copy and run that command.

### Step 4: Run the Installer

Paste the command from Step 3 into the rescue SSH session. Example:
```bash
curl -sL https://hetzner.digirdp.com/api/installer.php?token=YOUR_TOKEN_HERE | bash
```

Takes 15-30 minutes. It wipes the primary disk and installs Windows.

### Step 5: Reboot Into Windows

After the script finishes:
1. In Hetzner Robot → **Rescue** tab → **Deactivate rescue mode**
2. **Reset** tab → hardware reset
3. Wait 3-5 minutes

### Step 6: RDP In

```
mstsc /v:65.109.25.233
```
- Username: **Administrator**
- Password: **Hetzner2026!**

---

## Method 2: Manual Network Fix (If RDP doesn't work after install)

If Windows installed but RDP isn't responding, the NIC name might be wrong.

### Option A: Use Hetzner KVM Console

1. Robot panel → **KVM Console** (or request LARA access via support)
2. Log in as Administrator / Hetzner2026!
3. Open PowerShell as Admin and run:

```powershell
Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | ForEach-Object {
    New-NetIPAddress -InterfaceIndex $_.ifIndex -IPAddress '65.109.25.233' -PrefixLength 26 -DefaultGateway '65.109.25.193' -ErrorAction SilentlyContinue
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses @('185.12.64.1','185.12.64.2')
}
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
netsh advfirewall firewall add rule name="Allow RDP" dir=in action=allow protocol=tcp localport=3389
```

### Option B: Inject Script From Rescue Mode

1. Boot into rescue mode (see Method 1, Steps 1-2)
2. Mount Windows partition:
```bash
apt-get install -y ntfs-3g
mkdir -p /mnt/win
mount -t ntfs-3g /dev/sda2 /mnt/win
# If sda2 doesn't work, try sda1 or check: fdisk -l /dev/sda
```
3. Create startup script:
```bash
mkdir -p /mnt/win/Windows/Setup/Scripts/
cat > /mnt/win/Windows/Setup/Scripts/SetupComplete.cmd << 'EOF'
@echo off
powershell -ExecutionPolicy Bypass -Command "Get-NetAdapter | Where {$_.Status -eq 'Up'} | Select -First 1 | ForEach { New-NetIPAddress -InterfaceIndex $_.ifIndex -IPAddress '65.109.25.233' -PrefixLength 26 -DefaultGateway '65.109.25.193' -ErrorAction SilentlyContinue; Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses @('185.12.64.1','185.12.64.2') }"
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
netsh advfirewall firewall set rule group="remote desktop" new enable=Yes
EOF
```
4. Also create a persistent scheduled task version:
```bash
cat > /mnt/win/network-setup.ps1 << 'EOF'
Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | ForEach-Object {
    New-NetIPAddress -InterfaceIndex $_.ifIndex -IPAddress '65.109.25.233' -PrefixLength 26 -DefaultGateway '65.109.25.193' -ErrorAction SilentlyContinue
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses @('185.12.64.1','185.12.64.2')
}
Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
netsh advfirewall firewall add rule name="Allow RDP" dir=in action=allow protocol=tcp localport=3389
EOF
```
5. Unmount and reboot:
```bash
umount /mnt/win
sync
reboot
```

---

## Method 3: Hetzner Paid Windows (23 EUR/month)

1. Robot panel → your server → **OS** tab
2. Select **Windows Server 2022 Standard**
3. Hetzner installs it automatically, sends credentials by email
4. DHCP works out of the box, RDP enabled
5. This is how the old Hetzner (88.99.142.89) was set up

---

## After Windows is Running: Software Setup

### 1. Install Python
- Download from https://www.python.org/downloads/
- Run installer → CHECK "Add Python to PATH" → Install
- Verify: `python --version`

### 2. Install Git
- Download from https://git-scm.com/download/win
- Install with defaults
- Verify: `git --version`

### 3. Clone gifmake
```cmd
cd C:\Users\Administrator\Desktop
git clone <REPO_URL> gifmake
cd gifmake
pip install -r requirements.txt
```

### 4. Install Playwright Browsers
```cmd
playwright install chromium
```

### 5. Install AdsPower
- Download from https://www.adspower.com/download
- Install
- Launch → configure API key and account profiles
- API runs at localhost:50325

### 6. Copy Config Files
Transfer these from your local machine:
- `config/account_profiles.json`
- `config/api_keys.json`
- `subreddit_profiles.json`
- `subreddit_tiers_grok.json`
- `subreddit_data_v3.json`

### 7. Test
```cmd
python src/main.py
```
Verify GUI launches, AdsPower connects, warmup session works.

---

## Network Reference

| Server | IP | Gateway | DNS | Subnet |
|--------|-----|---------|-----|--------|
| New Hetzner | 65.109.25.233 | 65.109.25.193 | 185.12.64.1, 185.12.64.2 | /26 |
| Old Hetzner | 88.99.142.89 | 88.99.142.65 | 185.12.64.1, 185.12.64.2 | /26 |

Hetzner DNS: 185.12.64.1 and 185.12.64.2 (always use these)

## Troubleshooting

**Can't SSH into rescue mode?**
→ Reactivate rescue in Robot panel, do a hardware reset, wait 2 min

**Windows installed but no RDP?**
→ Use KVM console (Method 2 Option A) or inject script from rescue (Method 2 Option B)

**DigiRDP says "two drives required"?**
→ May not work with single disk. Fall back to Method 3 (paid) or wimlib manual install.

**QEMU approach?**
→ Don't. It hangs, gets stuck at "Press any key", and wastes hours.
