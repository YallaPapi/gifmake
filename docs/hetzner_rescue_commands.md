# Hetzner Rescue Mode: Complete Command List

Server: 88.99.142.89 | Partition: /dev/sdb2 | OS: Windows Server 2022

Run each command individually. Verify output before moving to next.

---

## Phase 1: Mount

### 1. Fix NTFS dirty flag
```
ntfsfix -d /dev/sdb2
```
Expected: "NTFS partition /dev/sdb2 was processed successfully"

### 2. Mount read-write
```
mkdir -p /mnt/win && mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win
```
Expected: no error

### 3. Verify mount
```
ls /mnt/win/Windows/System32/config/SAM && echo "OK"
```
Expected: path printed + "OK"

---

## Phase 2: Backup

### 4. Back up all hives
```
mkdir -p /mnt/win/Windows/System32/config/RegBack_rescue && cp -p /mnt/win/Windows/System32/config/SAM /mnt/win/Windows/System32/config/RegBack_rescue/ && cp -p /mnt/win/Windows/System32/config/SYSTEM /mnt/win/Windows/System32/config/RegBack_rescue/ && cp -p /mnt/win/Windows/System32/config/SOFTWARE /mnt/win/Windows/System32/config/RegBack_rescue/ && cp -p /mnt/win/Windows/System32/config/DEFAULT /mnt/win/Windows/System32/config/RegBack_rescue/ && echo "BACKUP OK"
```

---

## Phase 3: SAM (Password + Unlock)

### 5. Blank Administrator password
```
sampasswd -r -u 0x1f4 /mnt/win/Windows/System32/config/SAM
```
Expected: "Password cleared" or similar

### 6. Unlock + enable Administrator account
```
printf '1\ny\n' | chntpw -u Administrator /mnt/win/Windows/System32/config/SAM
```
Expected: "Password cleared!" and "OK" at end. Then:

```
printf '4\ny\n' | chntpw -u Administrator /mnt/win/Windows/System32/config/SAM
```
Expected: "Unlocked!" and "OK" at end

---

## Phase 4: Determine Active ControlSet

### 7. Find which ControlSet is active
```
printf 'cd Select\ncat Current\nq\nn\n' | chntpw -e /mnt/win/Windows/System32/config/SYSTEM 2>&1 | grep -i "dword"
```
Expected: Shows "1" or "2". Almost always 1 = ControlSet001. Use that CS number for all following commands.

---

## Phase 5: SYSTEM Hive — Firewall (CRITICAL)

### 8. Disable firewall — ControlSet001 DomainProfile
```
cat > /tmp/fw1.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile]
"EnableFirewall"=dword:00000000
"DoNotAllowExceptions"=dword:00000000
"DefaultInboundAction"=dword:00000000
"DefaultOutboundAction"=dword:00000000
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/fw1.reg
```

### 9. Disable firewall — ControlSet001 PublicProfile
```
cat > /tmp/fw2.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile]
"EnableFirewall"=dword:00000000
"DoNotAllowExceptions"=dword:00000000
"DefaultInboundAction"=dword:00000000
"DefaultOutboundAction"=dword:00000000
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/fw2.reg
```

### 10. Disable firewall — ControlSet001 StandardProfile
```
cat > /tmp/fw3.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile]
"EnableFirewall"=dword:00000000
"DoNotAllowExceptions"=dword:00000000
"DefaultInboundAction"=dword:00000000
"DefaultOutboundAction"=dword:00000000
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/fw3.reg
```

### 11. Disable firewall — ControlSet002 DomainProfile
```
cat > /tmp/fw4.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet002\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile]
"EnableFirewall"=dword:00000000
"DoNotAllowExceptions"=dword:00000000
"DefaultInboundAction"=dword:00000000
"DefaultOutboundAction"=dword:00000000
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/fw4.reg
```

### 12. Disable firewall — ControlSet002 PublicProfile
```
cat > /tmp/fw5.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet002\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile]
"EnableFirewall"=dword:00000000
"DoNotAllowExceptions"=dword:00000000
"DefaultInboundAction"=dword:00000000
"DefaultOutboundAction"=dword:00000000
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/fw5.reg
```

### 13. Disable firewall — ControlSet002 StandardProfile
```
cat > /tmp/fw6.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet002\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile]
"EnableFirewall"=dword:00000000
"DoNotAllowExceptions"=dword:00000000
"DefaultInboundAction"=dword:00000000
"DefaultOutboundAction"=dword:00000000
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/fw6.reg
```

### 14. Disable MpsSvc (firewall service) — both ControlSets
```
cat > /tmp/mps.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\MpsSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet002\Services\MpsSvc]
"Start"=dword:00000004

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/mps.reg
```

---

## Phase 6: SYSTEM Hive — RDP (CRITICAL)

### 15. Enable RDP + AllowRemoteRPC
```
cat > /tmp/rdp1.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Control\Terminal Server]
"fDenyTSConnections"=dword:00000000
"AllowRemoteRPC"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/rdp1.reg
```

### 16. Disable NLA + SecurityLayer + set port 3389 + enable listener
```
cat > /tmp/rdp2.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Control\Terminal Server\WinStations\RDP-Tcp]
"UserAuthentication"=dword:00000000
"SecurityLayer"=dword:00000000
"MinEncryptionLevel"=dword:00000001
"PortNumber"=dword:00000d3d
"fEnableWinStation"=dword:00000001
"MaxInstanceCount"=dword:ffffffff
"MaxIdleTime"=dword:00000000
"MaxConnectionTime"=dword:00000000
"MaxDisconnectionTime"=dword:00000000
"fResetBroken"=dword:00000000
"fReconnectSame"=dword:00000000

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/rdp2.reg
```

### 17. Ensure RDP services are set to Automatic
```
cat > /tmp/rdp3.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\TermService]
"Start"=dword:00000002

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\UmRdpService]
"Start"=dword:00000002

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\SessionEnv]
"Start"=dword:00000002

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\RDPWD]
"Start"=dword:00000002

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\TDTCP]
"Start"=dword:00000002

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/rdp3.reg
```

---

## Phase 7: SYSTEM Hive — Disable Dangerous Services

### 18. Disable Windows Update + Defender + Telemetry services
```
cat > /tmp/svc.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\wuauserv]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\UsoSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WaaSMedicSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\BITS]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\DoSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WinDefend]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WdNisSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WdFilter]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WdBoot]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\Sense]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\SecurityHealthService]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\wscsvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\DiagTrack]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\dmwappushservice]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\SysMain]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WSearch]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\Spooler]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\defragsvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\PolicyAgent]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\edgeupdate]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\edgeupdatem]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\uhssvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\WerSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\RemoteRegistry]
"Start"=dword:00000004

EOF
reged -I -C /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' /tmp/svc.reg
```

---

## Phase 8: SOFTWARE Hive — Auto-Login (CRITICAL)

### 19. Configure auto-login + disable CAD + clear legal notices
```
cat > /tmp/login.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon]
"AutoAdminLogon"="1"
"DefaultUserName"="Administrator"
"DefaultPassword"=""
"DefaultDomainName"="."
"ForceAutoLogon"="1"
"Shell"="explorer.exe"
"PasswordExpiryWarning"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System]
"DisableCAD"=dword:00000001
"DisableLockWorkstation"=dword:00000001
"LegalNoticeCaption"=""
"LegalNoticeText"=""
"EnableFirstLogonAnimation"=dword:00000000
"InactivityTimeoutSecs"=dword:00000000

EOF
reged -I -C /mnt/win/Windows/System32/config/SOFTWARE 'HKEY_LOCAL_MACHINE\SOFTWARE' /tmp/login.reg
```

---

## Phase 9: SOFTWARE Hive — RDP + Firewall Policy Overrides (CRITICAL)

### 20. Disable firewall + RDP + NLA via Group Policy (overrides SYSTEM hive)
```
cat > /tmp/policy1.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services]
"fDenyTSConnections"=dword:00000000
"UserAuthentication"=dword:00000000
"SecurityLayer"=dword:00000000
"MinEncryptionLevel"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\WindowsFirewall\DomainProfile]
"EnableFirewall"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\WindowsFirewall\PublicProfile]
"EnableFirewall"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\WindowsFirewall\PrivateProfile]
"EnableFirewall"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\CredentialsDelegation]
"AllowEncryptionOracle"=dword:00000002

EOF
reged -I -C /mnt/win/Windows/System32/config/SOFTWARE 'HKEY_LOCAL_MACHINE\SOFTWARE' /tmp/policy1.reg
```

---

## Phase 10: SOFTWARE Hive — Disable Windows Update Policy

### 21. Block Windows Update via policy
```
cat > /tmp/wu.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate]
"DisableWindowsUpdateAccess"=dword:00000001
"DoNotConnectToWindowsUpdateInternetLocations"=dword:00000001
"SetDisableUXWUAccess"=dword:00000001
"WUServer"="https://localhost:8530"
"WUStatusServer"="https://localhost:8530"

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU]
"NoAutoUpdate"=dword:00000001
"AUOptions"=dword:00000001
"UseWUServer"=dword:00000001
"NoAutoRebootWithLoggedOnUsers"=dword:00000001
"AlwaysAutoRebootAtScheduledTime"=dword:00000000

EOF
reged -I -C /mnt/win/Windows/System32/config/SOFTWARE 'HKEY_LOCAL_MACHINE\SOFTWARE' /tmp/wu.reg
```

---

## Phase 11: SOFTWARE Hive — Disable Defender Policy

### 22. Disable Windows Defender via policy
```
cat > /tmp/def.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender]
"DisableAntiSpyware"=dword:00000001
"DisableAntiVirus"=dword:00000001
"DisableRoutinelyTakingAction"=dword:00000001
"ServiceKeepAlive"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection]
"DisableRealtimeMonitoring"=dword:00000001
"DisableBehaviorMonitoring"=dword:00000001
"DisableOnAccessProtection"=dword:00000001
"DisableScanOnRealtimeEnable"=dword:00000001
"DisableIOAVProtection"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender\Spynet]
"SpynetReporting"=dword:00000000
"SubmitSamplesConsent"=dword:00000002

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender Security Center\Notifications]
"DisableNotifications"=dword:00000001

EOF
reged -I -C /mnt/win/Windows/System32/config/SOFTWARE 'HKEY_LOCAL_MACHINE\SOFTWARE' /tmp/def.reg
```

---

## Phase 12: SOFTWARE Hive — Disable Telemetry + OOBE + Misc

### 23. Disable telemetry, OOBE, Server Manager, maintenance, screensaver, IE ESC, shutdown tracker
```
cat > /tmp/misc.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\DataCollection]
"AllowTelemetry"=dword:00000000
"DoNotShowFeedbackNotifications"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\OOBE]
"DisableFirstRunCustomize"=dword:00000001
"OobeInProgress"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\OOBE]
"DisablePrivacyExperience"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\ServerManager]
"DoNotOpenServerManagerAtLogon"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Reliability]
"ShutdownReasonOn"=dword:00000000
"ShutdownReasonUI"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\CurrentVersion\Schedule\Maintenance]
"MaintenanceDisabled"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Personalization]
"NoLockScreen"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Control Panel\Desktop]
"ScreenSaveActive"="0"
"ScreenSaverIsSecure"="0"

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1A7-37EF-4b3f-8CFC-4F3A74704073}]
"IsInstalled"=dword:00000000

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1A8-37EF-4b3f-8CFC-4F3A74704073}]
"IsInstalled"=dword:00000000

EOF
reged -I -C /mnt/win/Windows/System32/config/SOFTWARE 'HKEY_LOCAL_MACHINE\SOFTWARE' /tmp/misc.reg
```

---

## Phase 13: SOFTWARE Hive — RunOnce Safety Net

### 24. Add RunOnce commands that execute on first boot as a safety net
```
cat > /tmp/runonce.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce]
"FixFirewall"="cmd.exe /c netsh advfirewall set allprofiles state off"
"FixPwExpiry"="cmd.exe /c net accounts /maxpwage:unlimited"
"FixRDPGroup"="cmd.exe /c net localgroup \"Remote Desktop Users\" Administrator /add"

EOF
reged -I -C /mnt/win/Windows/System32/config/SOFTWARE 'HKEY_LOCAL_MACHINE\SOFTWARE' /tmp/runonce.reg
```

---

## Phase 14: DEFAULT Hive — User Profile Defaults

### 25. Disable screensaver and Server Manager for default user profile
```
cat > /tmp/default.reg << 'EOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\DEFAULT\Software\Microsoft\ServerManager]
"DoNotOpenServerManagerAtLogon"=dword:00000001

[HKEY_LOCAL_MACHINE\DEFAULT\Control Panel\Desktop]
"ScreenSaveActive"="0"
"ScreenSaverIsSecure"="0"
"ScreenSaveTimeOut"="0"

[HKEY_LOCAL_MACHINE\DEFAULT\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement]
"ScoobeSystemSettingEnabled"=dword:00000000

EOF
reged -I -C /mnt/win/Windows/System32/config/DEFAULT 'HKEY_LOCAL_MACHINE\DEFAULT' /tmp/default.reg
```

---

## Phase 15: Remove Hibernation File

### 26. Delete hiberfil.sys if it exists
```
rm -f /mnt/win/hiberfil.sys && echo "hiberfil.sys removed (or didn't exist)"
```

---

## Phase 16: Disable Scheduled Tasks (rename to .disabled)

### 27. Disable Windows Update tasks
```
cd /mnt/win/Windows/System32/Tasks/Microsoft/Windows/WindowsUpdate 2>/dev/null && for f in *; do [ -f "$f" ] && mv "$f" "${f}.disabled" && echo "Disabled: $f"; done; cd /
```

### 28. Disable Update Orchestrator tasks (THE reboot trigger)
```
cd /mnt/win/Windows/System32/Tasks/Microsoft/Windows/UpdateOrchestrator 2>/dev/null && for f in *; do [ -f "$f" ] && mv "$f" "${f}.disabled" && echo "Disabled: $f"; done; cd /
```

### 29. Disable WaaSMedic tasks (re-enables update services)
```
cd /mnt/win/Windows/System32/Tasks/Microsoft/Windows/WaaSMedic 2>/dev/null && for f in *; do [ -f "$f" ] && mv "$f" "${f}.disabled" && echo "Disabled: $f"; done; cd /
```

### 30. Disable Windows Defender tasks
```
cd "/mnt/win/Windows/System32/Tasks/Microsoft/Windows/Windows Defender" 2>/dev/null && for f in *; do [ -f "$f" ] && mv "$f" "${f}.disabled" && echo "Disabled: $f"; done; cd /
```

### 31. Disable Defrag tasks
```
cd /mnt/win/Windows/System32/Tasks/Microsoft/Windows/Defrag 2>/dev/null && for f in *; do [ -f "$f" ] && mv "$f" "${f}.disabled" && echo "Disabled: $f"; done; cd /
```

---

## Phase 17: Block Update Servers via Hosts File

### 32. Add update server blocks to hosts file
```
cat >> /mnt/win/Windows/System32/drivers/etc/hosts << 'EOF'

# Block Windows Update + Telemetry
0.0.0.0 windowsupdate.microsoft.com
0.0.0.0 update.microsoft.com
0.0.0.0 download.windowsupdate.com
0.0.0.0 download.microsoft.com
0.0.0.0 ntservicepack.microsoft.com
0.0.0.0 dl.delivery.mp.microsoft.com
0.0.0.0 ctldl.windowsupdate.com
0.0.0.0 fe2cr.update.microsoft.com
0.0.0.0 settings-win.data.microsoft.com
0.0.0.0 vortex.data.microsoft.com
0.0.0.0 watson.telemetry.microsoft.com
0.0.0.0 watson.microsoft.com
0.0.0.0 definitionupdates.microsoft.com
0.0.0.0 go.microsoft.com
0.0.0.0 sls.update.microsoft.com
EOF
echo "Hosts file updated"
```

---

## Phase 18: Verify Network Config

### 33. Check what IP is configured in Windows registry
```
printf 'cd ControlSet001\Services\Tcpip\Parameters\Interfaces\nls\nq\nn\n' | chntpw -e /mnt/win/Windows/System32/config/SYSTEM 2>&1 | grep "{"
```
Note: This lists interface GUIDs. For each GUID, run:
```
printf 'cd ControlSet001\\Services\\Tcpip\\Parameters\\Interfaces\\{GUID_HERE}\ncat EnableDHCP\ncat IPAddress\ncat SubnetMask\ncat DefaultGateway\ncat NameServer\nq\nn\n' | chntpw -e /mnt/win/Windows/System32/config/SYSTEM 2>&1
```
Verify: IP=88.99.142.89, Gateway should be visible in Hetzner Robot "IPs" tab

---

## Phase 19: Unmount + Final NTFS Fix

### 34. Sync and unmount
```
sync && umount /mnt/win && echo "UNMOUNTED OK"
```

### 35. Clear dirty flag one final time (MUST be last NTFS operation)
```
ntfsfix -d /dev/sdb2
```
Expected: "processed successfully"

---

## Phase 20: Deactivate Rescue + Reboot

### 36. (Manual) In Hetzner Robot:
1. Go to Server > Rescue tab > Deactivate rescue mode
2. Go to Reset tab > Execute hardware reset

### 37. Wait 2-3 min, then test RDP
```
# From local machine:
# mstsc /v:88.99.142.89
# Username: Administrator
# Password: (blank — just press Enter)
```

---

## TOTAL: 35 commands + 2 manual steps

## Summary of ALL changes:

### SAM Hive
- Administrator password blanked
- Administrator account unlocked + enabled

### SYSTEM Hive (ControlSet001 + 002)
- Firewall: EnableFirewall=0, DoNotAllowExceptions=0, DefaultInboundAction=0, DefaultOutboundAction=0, DisableNotifications=1 (all 3 profiles x 2 ControlSets = 30 values)
- MpsSvc service disabled (firewall engine)
- PolicyAgent service disabled (IPSec)
- RDP: fDenyTSConnections=0, AllowRemoteRPC=1
- NLA: UserAuthentication=0, SecurityLayer=0, MinEncryptionLevel=1
- RDP-Tcp: fEnableWinStation=1, PortNumber=3389, MaxInstanceCount=unlimited, all timeouts=0, fResetBroken=0, fReconnectSame=0
- Services set to Auto: TermService, UmRdpService, SessionEnv, RDPWD, TDTCP
- Services DISABLED: wuauserv, UsoSvc, WaaSMedicSvc, BITS, DoSvc, WinDefend, WdNisSvc, WdFilter, WdBoot, Sense, SecurityHealthService, wscsvc, DiagTrack, dmwappushservice, SysMain, WSearch, Spooler, defragsvc, PolicyAgent, edgeupdate, edgeupdatem, uhssvc, WerSvc, RemoteRegistry

### SOFTWARE Hive
- Auto-login: AutoAdminLogon=1, DefaultUserName=Administrator, DefaultPassword="", DefaultDomainName=".", ForceAutoLogon=1
- DisableCAD=1, DisableLockWorkstation=1
- Legal notices cleared
- Shell=explorer.exe, PasswordExpiryWarning=0
- EnableFirstLogonAnimation=0, InactivityTimeoutSecs=0
- GP: fDenyTSConnections=0, UserAuthentication=0, SecurityLayer=0
- GP: Firewall disabled (Domain, Public, Private profiles)
- GP: AllowEncryptionOracle=2 (CredSSP fix)
- GP: Windows Update fully disabled + pointed to fake WSUS
- GP: Defender fully disabled (antispyware, antivirus, real-time, behavior, cloud)
- GP: Telemetry=0, feedback notifications off
- OOBE disabled, Server Manager disabled, shutdown tracker disabled
- Automatic maintenance disabled, lock screen disabled, screensaver off
- IE Enhanced Security disabled (both admin + users)
- RunOnce: firewall off, password never expires, RDP group membership

### DEFAULT Hive
- Server Manager disabled for new profiles
- Screensaver disabled for new profiles
- OOBE nag disabled

### File System
- hiberfil.sys removed
- Hosts file blocks Microsoft update + telemetry servers
- Scheduled tasks renamed to .disabled: WindowsUpdate, UpdateOrchestrator, WaaSMedic, Windows Defender, Defrag
- NTFS dirty flag cleared
