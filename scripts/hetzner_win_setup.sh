#!/bin/bash
###############################################################################
# hetzner_win_setup.sh
#
# Run from Hetzner rescue mode (Debian Linux) to configure a Windows Server
# 2022 installation on /dev/sdb2 for headless RDP access.
#
# What it does:
#   1. Mounts the Windows NTFS partition read-write
#   2. Blanks Administrator password + unlocks the account (SAM)
#   3. Enables auto-logon, disables legal notices (SOFTWARE hive)
#   4. Disables firewall, enables RDP, disables NLA, disables Windows Update
#      services (SYSTEM hive)
#   5. Cleanly unmounts
#
# Usage:
#   chmod +x hetzner_win_setup.sh
#   ./hetzner_win_setup.sh
#
# Requires: ntfs-3g, ntfsfix, chntpw (reged), all from apt
###############################################################################

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
WIN_PARTITION="/dev/sdb2"
MOUNT_POINT="/mnt/win"
CONFIG_DIR=""  # Will be set after mount

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_err()   { echo -e "${RED}[ERROR]${NC} $1"; }

die() {
    log_err "$1"
    # Attempt cleanup
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        log_warn "Unmounting $MOUNT_POINT before exit..."
        umount "$MOUNT_POINT" 2>/dev/null || true
    fi
    exit 1
}

# ─── Pre-flight checks ──────────────────────────────────────────────────────
log_info "=== Hetzner Windows Server 2022 Rescue-Mode Setup ==="
echo ""

if [ "$(id -u)" -ne 0 ]; then
    die "This script must be run as root"
fi

if [ ! -b "$WIN_PARTITION" ]; then
    die "Partition $WIN_PARTITION does not exist. Check with: fdisk -l"
fi

# ─── Step 0: Install required packages ───────────────────────────────────────
log_info "Step 0: Ensuring required packages are installed..."

# Check if packages are installed; install if missing
NEED_APT_UPDATE=0
for pkg in ntfs-3g chntpw; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        if [ $NEED_APT_UPDATE -eq 0 ]; then
            log_info "  Running apt-get update..."
            apt-get update -qq
            NEED_APT_UPDATE=1
        fi
        log_info "  Installing $pkg..."
        apt-get install -y -qq "$pkg" || die "Failed to install $pkg"
    fi
done

# Verify critical tools exist on PATH
for tool in ntfsfix chntpw sampasswd reged; do
    if ! command -v "$tool" &>/dev/null; then
        die "Required tool '$tool' not found after install attempt"
    fi
done
log_ok "All required tools available"

# ─── Step 1: Mount NTFS partition ────────────────────────────────────────────
echo ""
log_info "Step 1: Mounting Windows NTFS partition..."

# Unmount if already mounted
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    log_warn "$MOUNT_POINT is already mounted, unmounting first..."
    umount "$MOUNT_POINT" || die "Failed to unmount existing mount"
fi

mkdir -p "$MOUNT_POINT"

# Fix dirty NTFS flag (Windows often leaves this set)
log_info "  Running ntfsfix to clear dirty flag..."
ntfsfix -d "$WIN_PARTITION" 2>&1 || log_warn "ntfsfix returned non-zero (may be OK)"

# Mount read-write with big_writes for performance
log_info "  Mounting $WIN_PARTITION at $MOUNT_POINT..."
mount -t ntfs-3g -o rw,big_writes,remove_hiberfile "$WIN_PARTITION" "$MOUNT_POINT" \
    || die "Failed to mount $WIN_PARTITION"

log_ok "Mounted $WIN_PARTITION at $MOUNT_POINT"

# ─── Locate Windows config directory ────────────────────────────────────────
# Windows registry hives are case-insensitive but NTFS-3G preserves original case.
# Try common variations.
for candidate in \
    "$MOUNT_POINT/Windows/System32/config" \
    "$MOUNT_POINT/windows/system32/config" \
    "$MOUNT_POINT/WINDOWS/system32/config" \
    "$MOUNT_POINT/WINDOWS/System32/config"; do
    if [ -d "$candidate" ]; then
        CONFIG_DIR="$candidate"
        break
    fi
done

if [ -z "$CONFIG_DIR" ]; then
    die "Cannot find Windows\\System32\\config directory under $MOUNT_POINT"
fi

log_ok "Found config directory: $CONFIG_DIR"

# Verify hive files exist
for hive in SAM SOFTWARE SYSTEM; do
    if [ ! -f "$CONFIG_DIR/$hive" ]; then
        die "Hive file $CONFIG_DIR/$hive not found"
    fi
done
log_ok "All registry hive files (SAM, SOFTWARE, SYSTEM) found"

# ─── Back up hive files ─────────────────────────────────────────────────────
echo ""
log_info "Creating backup copies of registry hives..."
BACKUP_DIR="$CONFIG_DIR/RegBack_rescue_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -p "$CONFIG_DIR/SAM"      "$BACKUP_DIR/SAM"
cp -p "$CONFIG_DIR/SOFTWARE"  "$BACKUP_DIR/SOFTWARE"
cp -p "$CONFIG_DIR/SYSTEM"    "$BACKUP_DIR/SYSTEM"
log_ok "Backups saved to $BACKUP_DIR"

# ─── Step 2: SAM — Blank Administrator password ─────────────────────────────
echo ""
log_info "Step 2a: Blanking Administrator password (sampasswd)..."

# sampasswd is the non-interactive password reset tool from chntpw package.
# -r = reset password, -u = target user (by RID in hex)
# RID 0x1f4 = 500 = built-in Administrator
if sampasswd -r -u 0x1f4 "$CONFIG_DIR/SAM" 2>&1; then
    log_ok "Administrator password blanked successfully"
else
    log_warn "sampasswd exited non-zero (may still have worked, check output above)"
fi

# ─── Step 2b: SAM — Unlock Administrator account ────────────────────────────
log_info "Step 2b: Unlocking Administrator account (chntpw -i)..."

# chntpw -i interactive mode flow for unlocking:
#   1. It shows user list and prompts: "Please enter user number (RID) or 0 to exit: [01f4]"
#      -> We press Enter to accept default (Administrator = 0x01f4)
#   2. User Edit Menu appears with options 1-9, q
#      -> We enter "1" to blank password (belt and suspenders, in case sampasswd missed)
#   3. Back to User Edit Menu
#      -> We enter "2" to unlock and enable
#   4. Back to User Edit Menu
#      -> We enter "q" to quit user editing
#   5. Prompts for user number again
#      -> We enter "q" to quit
#   6. "Write hive files? (y/n) [n] :"
#      -> We enter "y" to save
if printf '\n1\n2\nq\nq\ny\n' | chntpw -i "$CONFIG_DIR/SAM" 2>&1; then
    log_ok "Administrator account unlocked and enabled"
else
    # chntpw often returns non-zero even on success
    log_warn "chntpw exited non-zero (this is common, check output above for 'Unlocked!')"
fi

# ─── Step 3: SOFTWARE hive — Auto-logon + disable legal notices ──────────────
echo ""
log_info "Step 3: Configuring SOFTWARE hive (auto-logon, legal notices)..."

# We use reged -I -C to import a .reg file non-interactively.
# -I = import mode
# -C = auto-commit (save without prompting)
# The prefix must match what appears before the hive-relative path.
# For SOFTWARE hive: HKEY_LOCAL_MACHINE\SOFTWARE is the prefix.

SOFT_REG=$(mktemp --suffix=.reg /tmp/soft_XXXXXX)
cat > "$SOFT_REG" << 'REGEOF'
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon]
"AutoAdminLogon"="1"
"DefaultUserName"="Administrator"
"DefaultPassword"=""
"ForceAutoLogon"=dword:00000001
"LegalNoticeCaption"=""
"LegalNoticeText"=""

REGEOF

log_info "  Importing auto-logon settings into SOFTWARE hive..."
if reged -I -C "$CONFIG_DIR/SOFTWARE" 'HKEY_LOCAL_MACHINE\SOFTWARE' "$SOFT_REG" 2>&1; then
    log_ok "SOFTWARE hive: auto-logon configured, legal notices cleared"
else
    log_warn "reged (SOFTWARE) exited non-zero (check output above)"
fi

rm -f "$SOFT_REG"

# ─── Step 4: SYSTEM hive — Determine active ControlSet ──────────────────────
echo ""
log_info "Step 4: Configuring SYSTEM hive..."

# First, determine which ControlSet is active by reading Select\Current.
# We export the Select key and parse the Current value.
log_info "  Determining active ControlSet..."

SELECT_REG=$(mktemp --suffix=.reg /tmp/sel_XXXXXX)
reged -x "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' 'Select' "$SELECT_REG" 2>&1 || true

# Parse the Current value from the exported .reg file
# It will look like: "Current"=dword:00000001
# Use sed instead of grep -P for portability (grep -P may not be available)
CURRENT_CS=$(grep -i '"Current"' "$SELECT_REG" 2>/dev/null | sed -n 's/.*dword:\([0-9a-fA-F]*\).*/\1/p' | head -1)

if [ -z "$CURRENT_CS" ]; then
    log_warn "Could not determine active ControlSet from Select\\Current, defaulting to ControlSet001"
    CS="ControlSet001"
else
    # Convert hex to decimal
    CS_NUM=$((16#$CURRENT_CS))
    CS=$(printf "ControlSet%03d" "$CS_NUM")
    log_ok "Active ControlSet is: $CS (Select\\Current = $CURRENT_CS)"
fi

rm -f "$SELECT_REG"

# ─── Step 4a: SYSTEM hive — Disable Windows Firewall ────────────────────────
log_info "  Step 4a: Disabling Windows Firewall (all profiles)..."

FW_REG=$(mktemp --suffix=.reg /tmp/fw_XXXXXX)
cat > "$FW_REG" << REGEOF
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\DomainProfile]
"EnableFirewall"=dword:00000000

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\PublicProfile]
"EnableFirewall"=dword:00000000

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\StandardProfile]
"EnableFirewall"=dword:00000000

REGEOF

if reged -I -C "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$FW_REG" 2>&1; then
    log_ok "Firewall disabled for all profiles"
else
    log_warn "reged (firewall) exited non-zero (check output above)"
fi
rm -f "$FW_REG"

# ─── Step 4b: SYSTEM hive — Enable RDP ──────────────────────────────────────
log_info "  Step 4b: Enabling RDP (fDenyTSConnections=0)..."

RDP_REG=$(mktemp --suffix=.reg /tmp/rdp_XXXXXX)
cat > "$RDP_REG" << REGEOF
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Control\\Terminal Server]
"fDenyTSConnections"=dword:00000000

REGEOF

if reged -I -C "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$RDP_REG" 2>&1; then
    log_ok "RDP enabled"
else
    log_warn "reged (RDP) exited non-zero (check output above)"
fi
rm -f "$RDP_REG"

# ─── Step 4c: SYSTEM hive — Disable NLA ─────────────────────────────────────
log_info "  Step 4c: Disabling NLA (UserAuthentication=0)..."

NLA_REG=$(mktemp --suffix=.reg /tmp/nla_XXXXXX)
cat > "$NLA_REG" << REGEOF
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Control\\Terminal Server\\WinStations\\RDP-Tcp]
"UserAuthentication"=dword:00000000

REGEOF

if reged -I -C "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$NLA_REG" 2>&1; then
    log_ok "NLA disabled"
else
    log_warn "reged (NLA) exited non-zero (check output above)"
fi
rm -f "$NLA_REG"

# ─── Step 4d: SYSTEM hive — Disable Windows Update service ──────────────────
log_info "  Step 4d: Disabling wuauserv (Windows Update) service..."

WUAU_REG=$(mktemp --suffix=.reg /tmp/wuau_XXXXXX)
cat > "$WUAU_REG" << REGEOF
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Services\\wuauserv]
"Start"=dword:00000004

REGEOF

if reged -I -C "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$WUAU_REG" 2>&1; then
    log_ok "wuauserv disabled (Start=4)"
else
    log_warn "reged (wuauserv) exited non-zero (check output above)"
fi
rm -f "$WUAU_REG"

# ─── Step 4e: SYSTEM hive — Disable UsoSvc service ──────────────────────────
log_info "  Step 4e: Disabling UsoSvc (Update Orchestrator) service..."

USO_REG=$(mktemp --suffix=.reg /tmp/uso_XXXXXX)
cat > "$USO_REG" << REGEOF
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Services\\UsoSvc]
"Start"=dword:00000004

REGEOF

if reged -I -C "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$USO_REG" 2>&1; then
    log_ok "UsoSvc disabled (Start=4)"
else
    log_warn "reged (UsoSvc) exited non-zero (check output above)"
fi
rm -f "$USO_REG"

# ─── Step 4f: SYSTEM hive — Disable WaaSMedicSvc service ────────────────────
log_info "  Step 4f: Disabling WaaSMedicSvc (Update Medic) service..."

WAAS_REG=$(mktemp --suffix=.reg /tmp/waas_XXXXXX)
cat > "$WAAS_REG" << REGEOF
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\${CS}\\Services\\WaaSMedicSvc]
"Start"=dword:00000004

REGEOF

if reged -I -C "$CONFIG_DIR/SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$WAAS_REG" 2>&1; then
    log_ok "WaaSMedicSvc disabled (Start=4)"
else
    log_warn "reged (WaaSMedicSvc) exited non-zero (check output above)"
fi
rm -f "$WAAS_REG"

# ─── Step 5: Unmount ────────────────────────────────────────────────────────
echo ""
log_info "Step 5: Unmounting..."

sync
umount "$MOUNT_POINT" || die "Failed to unmount $MOUNT_POINT"
log_ok "Unmounted $MOUNT_POINT cleanly"

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "${GREEN}ALL DONE${NC}"
echo "============================================================"
echo ""
echo "Changes applied to $WIN_PARTITION:"
echo "  [SAM]      Administrator password blanked"
echo "  [SAM]      Administrator account unlocked/enabled"
echo "  [SOFTWARE] AutoAdminLogon=1"
echo "  [SOFTWARE] DefaultUserName=Administrator"
echo "  [SOFTWARE] DefaultPassword=(empty)"
echo "  [SOFTWARE] ForceAutoLogon=1 (DWORD)"
echo "  [SOFTWARE] LegalNoticeCaption=(empty)"
echo "  [SOFTWARE] LegalNoticeText=(empty)"
echo "  [SYSTEM]   Firewall disabled (Domain, Public, Standard)"
echo "  [SYSTEM]   RDP enabled (fDenyTSConnections=0)"
echo "  [SYSTEM]   NLA disabled (UserAuthentication=0)"
echo "  [SYSTEM]   wuauserv disabled (Start=4)"
echo "  [SYSTEM]   UsoSvc disabled (Start=4)"
echo "  [SYSTEM]   WaaSMedicSvc disabled (Start=4)"
echo ""
echo "  Active ControlSet: $CS"
echo "  Backups saved to:  $BACKUP_DIR"
echo ""
echo "Next steps:"
echo "  1. Reboot into Windows:  reboot"
echo "  2. Wait ~2 minutes for Windows to boot"
echo "  3. Connect via RDP:      mstsc /v:<server-ip>"
echo "     Username: Administrator"
echo "     Password: (leave empty)"
echo ""
