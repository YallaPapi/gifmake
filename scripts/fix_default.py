import hivex
import struct

h = hivex.Hivex('/mnt/win/Windows/System32/config/DEFAULT', write=True)

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

sm = find_or_create_key(h, root, ['Software', 'Microsoft', 'ServerManager'])
sd(h, sm, 'DoNotOpenServerManagerAtLogon', 1)

desk = find_or_create_key(h, root, ['Control Panel', 'Desktop'])
ss(h, desk, 'ScreenSaveActive', '0')
ss(h, desk, 'ScreenSaverIsSecure', '0')
ss(h, desk, 'ScreenSaveTimeOut', '0')

upe = find_or_create_key(h, root, ['Software', 'Microsoft', 'Windows', 'CurrentVersion', 'UserProfileEngagement'])
sd(h, upe, 'ScoobeSystemSettingEnabled', 0)

print('  DEFAULT hive: ServerManager, screensaver, OOBE nag disabled')

h.commit(None)
print('DEFAULT hive committed OK')
