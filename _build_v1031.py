"""Build v1.0.3.1 release package"""
import os
import shutil
import tarfile
import hashlib
import subprocess
import sys

ROOT = r'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck'
VERSION = '1.0.3.1'
ARCH = 'x86_64'
PKG_NAME = f'tdsql-sqlcheck-v{VERSION}-linux-{ARCH}'
STAGE_DIR = os.path.join(ROOT, 'dist', f'stage-{ARCH}')
PKG_DIR = os.path.join(STAGE_DIR, PKG_NAME)
DIST_DIR = os.path.join(ROOT, 'dist')

print('[1/4] 创建阶段目录...')
if os.path.exists(STAGE_DIR):
    shutil.rmtree(STAGE_DIR)
os.makedirs(PKG_DIR, exist_ok=True)
os.makedirs(DIST_DIR, exist_ok=True)

print('[2/4] 复制代码与部署脚本...')
shutil.copytree(os.path.join(ROOT, 'backend'), os.path.join(PKG_DIR, 'backend'))
shutil.copytree(os.path.join(ROOT, 'frontend'), os.path.join(PKG_DIR, 'frontend'))
shutil.copy2(os.path.join(ROOT, 'requirements.txt'), os.path.join(PKG_DIR, 'requirements.txt'))
shutil.copytree(os.path.join(ROOT, 'deploy'), os.path.join(PKG_DIR, 'deploy'))

# 复制文档
docs_src = os.path.join(ROOT, 'docs')
docs_dst = os.path.join(PKG_DIR, 'docs')
os.makedirs(docs_dst, exist_ok=True)
doc_files = [
    '部署手册-v1.0.2.md',
    '运维手册-v1.0.2.md',
    '上线检查清单-v1.0.2.md',
    '发布说明-v1.0.2.md',
    'V1.0.3变更清单与测试要点.md',
    'V1.0.3.1增量更新部署说明.md'
]
for doc in doc_files:
    src = os.path.join(docs_src, doc)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(docs_dst, doc))
        print(f'  复制文档: {doc}')

# VERSION文件
with open(os.path.join(PKG_DIR, 'VERSION'), 'w', encoding='utf-8', newline='\n') as f:
    f.write(VERSION)

# 清理__pycache__
for root, dirs, files in os.walk(PKG_DIR):
    for d in dirs:
        if d == '__pycache__':
            shutil.rmtree(os.path.join(root, d))

print('[3/4] 下载Python依赖包...')
wheels_dir = os.path.join(PKG_DIR, 'wheels')
os.makedirs(wheels_dir, exist_ok=True)
subprocess.run([
    sys.executable, '-m', 'pip', 'download',
    '-r', os.path.join(ROOT, 'requirements.txt'),
    '-d', wheels_dir,
    '--platform', 'manylinux2014_x86_64',
    '--platform', 'manylinux_2_17_x86_64',
    '--platform', 'any',
    '--python-version', '311',
    '--only-binary', ':all:'
], check=True, capture_output=True)

# 下载pip/setuptools/wheel
subprocess.run([
    sys.executable, '-m', 'pip', 'download',
    'pip', 'setuptools', 'wheel',
    '-d', wheels_dir,
    '--platform', 'any',
    '--python-version', '311',
    '--only-binary', ':all:'
], check=True, capture_output=True)

# 清理wheels目录中的CRLF
for root, dirs, files in os.walk(wheels_dir):
    for file in files:
        if file.endswith('.whl'):
            fpath = os.path.join(root, file)
            with open(fpath, 'rb') as f:
                content = f.read()
            if b'\r\n' in content:
                with open(fpath, 'wb') as f:
                    f.write(content.replace(b'\r\n', b'\n'))

print('[4/4] 创建tar.gz包...')
tarball = os.path.join(DIST_DIR, f'{PKG_NAME}.tar.gz')
with tarfile.open(tarball, 'w:gz') as tar:
    tar.add(PKG_DIR, arcname=PKG_NAME)

# 生成SHA256校验和
sha256_hash = hashlib.sha256()
with open(tarball, 'rb') as f:
    for chunk in iter(lambda: f.read(4096), b''):
        sha256_hash.update(chunk)
checksum = sha256_hash.hexdigest()

with open(tarball + '.sha256', 'w', encoding='utf-8') as f:
    f.write(f'{checksum}  {PKG_NAME}.tar.gz\n')

# 清理阶段目录
shutil.rmtree(STAGE_DIR)

print(f'\n发布包创建完成:')
print(f'  文件: {tarball}')
print(f'  大小: {os.path.getsize(tarball) / 1024 / 1024:.2f} MB')
print(f'  SHA256: {checksum}')
print(f'  校验和文件: {tarball}.sha256')
