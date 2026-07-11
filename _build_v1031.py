"""Build v1.0.3.1 release package"""
import os, shutil, tarfile, hashlib, subprocess, glob

ROOT = r'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck'
VERSION = '1.0.3.1'
ARCH = 'x86_64'
PKG = f'tdsql-sqlcheck-v{VERSION}-linux-{ARCH}'
STAGE = os.path.join(ROOT, 'dist', 'stage')
PKG_DIR = os.path.join(STAGE, PKG)
DIST = os.path.join(ROOT, 'dist')

if os.path.exists(STAGE):
    shutil.rmtree(STAGE)
os.makedirs(PKG_DIR, exist_ok=True)

print('[1/5] Copying code...')
shutil.copytree(os.path.join(ROOT, 'backend'), os.path.join(PKG_DIR, 'backend'))
shutil.copytree(os.path.join(ROOT, 'frontend'), os.path.join(PKG_DIR, 'frontend'))
shutil.copy2(os.path.join(ROOT, 'requirements.txt'), os.path.join(PKG_DIR, 'requirements.txt'))

print('[2/5] Copying deploy scripts...')
deploy_dir = os.path.join(PKG_DIR, 'deploy')
os.makedirs(deploy_dir, exist_ok=True)
for f in ['install.sh','make_release.sh','make_release.ps1','preflight_check.sh',
          'rollback.sh','verify_deploy.sh','tdsql-sqlcheck.service',
          'env.template','nginx-sqlcheck.conf','README.md']:
    src = os.path.join(ROOT, 'deploy', f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(deploy_dir, f))

print('[3/5] Copying docs...')
docs_dir = os.path.join(PKG_DIR, 'docs')
os.makedirs(docs_dir, exist_ok=True)
for f in os.listdir(os.path.join(ROOT, 'docs')):
    if f.endswith('.md'):
        shutil.copy2(os.path.join(ROOT, 'docs', f), os.path.join(docs_dir, f))

with open(os.path.join(PKG_DIR, 'VERSION'), 'w', newline='\n') as f:
    f.write(VERSION)

for dp, dn, fn in os.walk(PKG_DIR):
    for d in list(dn):
        if d == '__pycache__':
            shutil.rmtree(os.path.join(dp, d))
            dn.remove(d)

print('[4/5] Downloading wheels...')
wheels_dir = os.path.join(PKG_DIR, 'wheels')
os.makedirs(wheels_dir, exist_ok=True)
subprocess.run([
    'python', '-m', 'pip', 'download',
    '-r', os.path.join(ROOT, 'requirements.txt'),
    '-d', wheels_dir,
    '--platform', 'manylinux2014_x86_64',
    '--platform', 'manylinux_2_17_x86_64',
    '--platform', 'any',
    '--python-version', '311',
    '--implementation', 'cp',
    '--abi', 'cp311', '--abi', 'none', '--abi', 'abi3',
    '--only-binary=:all:'
], check=True)
subprocess.run([
    'python', '-m', 'pip', 'download',
    'pip', 'setuptools', 'wheel',
    '-d', wheels_dir,
    '--platform', 'any', '--python-version', '311',
    '--only-binary=:all:'
], check=True, capture_output=True)

# BOM/CRLF cleanup
text_exts = {'.html','.js','.css','.sh','.conf','.template','.service','.md','.txt'}
for fpath in glob.glob(os.path.join(PKG_DIR, '**', '*'), recursive=True):
    if not os.path.isfile(fpath):
        continue
    ext = os.path.splitext(fpath)[1].lower()
    if ext in text_exts or os.path.basename(fpath) == 'VERSION':
        with open(fpath, 'rb') as f:
            data = f.read()
        changed = False
        if data[:3] == b'\xef\xbb\xbf':
            data = data[3:]
            changed = True
        if b'\r\n' in data:
            data = data.replace(b'\r\n', b'\n')
            changed = True
        if changed:
            with open(fpath, 'wb') as f:
                f.write(data)

print('[5/5] Creating tar.gz...')
whl_count = len([f for f in os.listdir(wheels_dir) if f.endswith('.whl')])
tarball = os.path.join(DIST, f'{PKG}.tar.gz')
with tarfile.open(tarball, 'w:gz') as tar:
    tar.add(PKG_DIR, arcname=PKG)

sha = hashlib.sha256()
with open(tarball, 'rb') as f:
    for chunk in iter(lambda: f.read(8192), b''):
        sha.update(chunk)
digest = sha.hexdigest()
with open(tarball + '.sha256', 'w') as f:
    f.write(f'{digest}  {PKG}.tar.gz\n')

shutil.rmtree(STAGE)

size_mb = os.path.getsize(tarball) / (1024*1024)
print(f'\nPackage: {PKG}.tar.gz ({size_mb:.1f} MB)')
print(f'SHA256: {digest}')
print(f'Wheels: {whl_count}')
print('Done!')
