import hivex
import struct

h = hivex.Hivex('/mnt/win/Windows/System32/config/SOFTWARE', write=True)

def find_key(h, parent, path_parts):
    node = parent
    for part in path_parts:
        child = h.node_get_child(node, part)
        if child is None:
            return None
        node = child
    return node

def find_or_create_key(h, parent, path_parts):
    node = parent
    for part in path_parts:
        child = h.node_get_child(node, part)
        if child is None:
            child = h.node_add_child(node, part)
        node = child
    return node

def sd(h, node, name, value):
    val = {'key': name, 't': 4, 'value': struct.pack('<I', value)}
    h.node_set_value(node, val)

def ss(h, node, name, value):
    encoded = (value + '\0').encode('utf-16-le')
    val = {'key': name, 't': 1, 'value': encoded}
    h.node_set_value(node, val)

root = h.root()

# ===== AUTO-LOGIN (Phase 8) =====
winlogon = find_key(h, root, ['Microsoft', 'Windows NT', 'CurrentVersion', 'Winlogon'])
if winlogon:
    ss(h, winlogon, 'AutoAdminLogon', '1')
    ss(h, winlogon, 'DefaultUserName', 'Administrator')
    ss(h, winlogon, 'DefaultPassword', '')
    ss(h, winlogon, 'DefaultDomainName', '.')
    ss(h, winlogon, 'ForceAutoLogon', '1')
    ss(h, winlogon, 'Shell', 'explorer.exe')
    sd(h, winlogon, 'PasswordExpiryWarning', 0)
    print('  Winlogon: auto-login configured')

# System policies
syspol = find_or_create_key(h, root, ['Microsoft', 'Windows', 'CurrentVersion', 'Policies', 'System'])
sd(h, syspol, 'DisableCAD', 1)
sd(h, syspol, 'DisableLockWorkstation', 1)
ss(h, syspol, 'LegalNoticeCaption', '')
ss(h, syspol, 'LegalNoticeText', '')
sd(h, syspol, 'EnableFirstLogonAnimation', 0)
sd(h, syspol, 'InactivityTimeoutSecs', 0)
print('  System policies: CAD/lock/legal/animation disabled')

# ===== GP: RDP + FIREWALL + CREDSSP (Phase 9) =====
ts_pol = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows NT', 'Terminal Services'])
sd(h, ts_pol, 'fDenyTSConnections', 0)
sd(h, ts_pol, 'UserAuthentication', 0)
sd(h, ts_pol, 'SecurityLayer', 0)
sd(h, ts_pol, 'MinEncryptionLevel', 1)
print('  GP Terminal Services: RDP enabled, NLA off')

for prof in ['DomainProfile', 'PublicProfile', 'PrivateProfile']:
    node = find_or_create_key(h, root, ['Policies', 'Microsoft', 'WindowsFirewall', prof])
    sd(h, node, 'EnableFirewall', 0)
print('  GP WindowsFirewall: all profiles disabled')

cred = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows', 'CredentialsDelegation'])
sd(h, cred, 'AllowEncryptionOracle', 2)
print('  GP CredSSP: AllowEncryptionOracle=2')

# ===== WINDOWS UPDATE POLICY (Phase 10) =====
wu = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows', 'WindowsUpdate'])
sd(h, wu, 'DisableWindowsUpdateAccess', 1)
sd(h, wu, 'DoNotConnectToWindowsUpdateInternetLocations', 1)
sd(h, wu, 'SetDisableUXWUAccess', 1)
ss(h, wu, 'WUServer', 'https://localhost:8530')
ss(h, wu, 'WUStatusServer', 'https://localhost:8530')

au = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows', 'WindowsUpdate', 'AU'])
sd(h, au, 'NoAutoUpdate', 1)
sd(h, au, 'AUOptions', 1)
sd(h, au, 'UseWUServer', 1)
sd(h, au, 'NoAutoRebootWithLoggedOnUsers', 1)
sd(h, au, 'AlwaysAutoRebootAtScheduledTime', 0)
print('  GP WindowsUpdate: fully disabled + fake WSUS')

# ===== DEFENDER POLICY (Phase 11) =====
wd = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows Defender'])
sd(h, wd, 'DisableAntiSpyware', 1)
sd(h, wd, 'DisableAntiVirus', 1)
sd(h, wd, 'DisableRoutinelyTakingAction', 1)
sd(h, wd, 'ServiceKeepAlive', 0)

rtp = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows Defender', 'Real-Time Protection'])
sd(h, rtp, 'DisableRealtimeMonitoring', 1)
sd(h, rtp, 'DisableBehaviorMonitoring', 1)
sd(h, rtp, 'DisableOnAccessProtection', 1)
sd(h, rtp, 'DisableScanOnRealtimeEnable', 1)
sd(h, rtp, 'DisableIOAVProtection', 1)

spy = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows Defender', 'Spynet'])
sd(h, spy, 'SpynetReporting', 0)
sd(h, spy, 'SubmitSamplesConsent', 2)

wdn = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows Defender Security Center', 'Notifications'])
sd(h, wdn, 'DisableNotifications', 1)
print('  GP Defender: fully disabled')

# ===== TELEMETRY + OOBE + MISC (Phase 12) =====
dc = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows', 'DataCollection'])
sd(h, dc, 'AllowTelemetry', 0)
sd(h, dc, 'DoNotShowFeedbackNotifications', 1)

oobe = find_or_create_key(h, root, ['Microsoft', 'Windows', 'CurrentVersion', 'OOBE'])
sd(h, oobe, 'DisableFirstRunCustomize', 1)
sd(h, oobe, 'OobeInProgress', 0)

oobe_pol = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows', 'OOBE'])
sd(h, oobe_pol, 'DisablePrivacyExperience', 1)

sm = find_or_create_key(h, root, ['Microsoft', 'ServerManager'])
sd(h, sm, 'DoNotOpenServerManagerAtLogon', 1)

rel = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows NT', 'Reliability'])
sd(h, rel, 'ShutdownReasonOn', 0)
sd(h, rel, 'ShutdownReasonUI', 0)

maint = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows NT', 'CurrentVersion', 'Schedule', 'Maintenance'])
sd(h, maint, 'MaintenanceDisabled', 1)

pers = find_or_create_key(h, root, ['Policies', 'Microsoft', 'Windows', 'Personalization'])
sd(h, pers, 'NoLockScreen', 1)
print('  Telemetry/OOBE/misc: all disabled')

# ===== RUNONCE SAFETY NET (Phase 13) =====
ro = find_or_create_key(h, root, ['Microsoft', 'Windows', 'CurrentVersion', 'RunOnce'])
ss(h, ro, 'FixFirewall', 'cmd.exe /c netsh advfirewall set allprofiles state off')
ss(h, ro, 'FixPwExpiry', 'cmd.exe /c net accounts /maxpwage:unlimited')
ss(h, ro, 'FixRDPGroup', 'cmd.exe /c net localgroup "Remote Desktop Users" Administrator /add')
print('  RunOnce: 3 safety-net commands set')

h.commit(None)
print('SOFTWARE hive committed OK')
