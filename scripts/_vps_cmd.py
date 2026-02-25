"""Helper to run commands on VPS via WinRM."""
import os
import winrm
import base64
import sys

VPS_IP = "88.99.142.89"
VPS_USER = "Administrator"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

VPS_PASS = require_env("GIFMAKE_VPS_WINRM_PASSWORD")

def run(cmd: str, ps: bool = True) -> str:
    """Run a command on the VPS using EncodedCommand to avoid pipe/quote issues."""
    s = winrm.Session(f'http://{VPS_IP}:5985/wsman', auth=(VPS_USER, VPS_PASS), transport='ntlm')
    if ps:
        encoded = base64.b64encode(cmd.encode('utf-16-le')).decode('ascii')
        r = s.run_cmd('powershell', ['-EncodedCommand', encoded])
    else:
        r = s.run_cmd(cmd)
    out = r.std_out.decode('utf-8', errors='replace').strip()
    err = r.std_err.decode('utf-8', errors='replace').strip()
    if err:
        return f"{out}\nSTDERR: {err}"
    return out

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        print(run(cmd))
    else:
        print("Usage: python _vps_cmd.py <powershell command>")
