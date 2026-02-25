import os, glob

dirs = [
    '/mnt/win/Windows/System32/Tasks/Microsoft/Windows/WindowsUpdate',
    '/mnt/win/Windows/System32/Tasks/Microsoft/Windows/UpdateOrchestrator',
    '/mnt/win/Windows/System32/Tasks/Microsoft/Windows/WaaSMedic',
    '/mnt/win/Windows/System32/Tasks/Microsoft/Windows/Windows Defender',
    '/mnt/win/Windows/System32/Tasks/Microsoft/Windows/Defrag',
]

total = 0
for d in dirs:
    dirname = os.path.basename(d)
    if not os.path.isdir(d):
        print(f'  {dirname}: directory not found (skip)')
        continue
    count = 0
    for f in os.listdir(d):
        full = os.path.join(d, f)
        if os.path.isfile(full) and not f.endswith('.disabled'):
            os.rename(full, full + '.disabled')
            print(f'  Disabled: {dirname}/{f}')
            count += 1
            total += 1
    if count == 0:
        print(f'  {dirname}: no tasks to disable')
print(f'Total: {total} tasks disabled')
