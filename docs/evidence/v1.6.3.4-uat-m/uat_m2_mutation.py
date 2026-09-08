"""变异测试：M01/M02 关键 1 行退化，断言测试变红；恢复后变绿。
Q 已在 commit 自证；M 复验一次。
"""
import subprocess, sys, os, shutil, time

ROOT = 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck'
os.chdir(ROOT)
env = os.environ.copy()
env['AUTH_ENABLED'] = 'false'
env['DATA_MASKING_ENABLED'] = 'false'
env['RAW_SLOWLOG_ENABLED'] = 'false'
env['SCHEDULER_ENABLED'] = 'false'
env['GITLAB_WEBHOOK_ALLOW_INSECURE'] = 'true'

def run(*args):
    """运行 pytest，返 (returncode, stdout)。"""
    res = subprocess.run([sys.executable, '-m', 'pytest', *args], capture_output=True, text=True, env=env, timeout=120)
    return res.returncode, res.stdout, res.stderr

def patch(src, old, new):
    """在文件中把 old 替换为 new（单处），无 old 则报失败。"""
    with open(src, encoding='utf-8') as f:
        s = f.read()
    if old not in s:
        raise RuntimeError(f'patch anchor not found in {src}')
    cnt = s.count(old)
    if cnt != 1:
        raise RuntimeError(f'patch anchor appears {cnt} times in {src}; need 1')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(s.replace(old, new, 1))
    return True

def unpatch(src, new, old):
    """回滚 patch。"""
    with open(src, encoding='utf-8') as f:
        s = f.read()
    s2 = s.replace(new, old, 1)
    with open(src, 'w', encoding='utf-8') as f:
        f.write(s2)

# ===== M01 变异：把 inject_context_into_html 的"替换已有块"逻辑退化 =====
M01_SRC = 'backend/services/report_context.py'
M01_OLD = '    if _CONTEXT_BLOCK_RE.search(html):\n        return _CONTEXT_BLOCK_RE.sub(lambda _m: context_html, html, count=1)'
M01_NEW = '    if _CONTEXT_BLOCK_RE.search(html):\n        return html  # M01 变异：禁用替换，期望 test_uat_m01_h08_header_no_duplicate 变红'

print('=== 变异测试 M01：禁用 inject_context_into_html 的"替换已有块"逻辑 ===\n')
try:
    patch(M01_SRC, M01_OLD, M01_NEW)
    rc, out, err = run('tests/test_v1634_report_context.py::test_uat_m01_h08_header_no_duplicate', '-v')
    print(f'  退化后 test_uat_m01_h08_header_no_duplicate: {"FAIL" if rc != 0 else "PASS"} (rc={rc})')
    if rc == 0:
        print('  !! 变异未生效，回归锁有问题！')
    # 提取 PASSED/FAILED
    for line in out.splitlines():
        if 'test_uat_m01_h08_header_no_duplicate' in line:
            print('  ', line.strip())
    # 恢复
    unpatch(M01_SRC, M01_NEW, M01_OLD)
    print('  恢复后文件已还原\n')

    # 恢复后跑一次
    rc2, out2, err2 = run('tests/test_v1634_report_context.py::test_uat_m01_h08_header_no_duplicate', '-v')
    print(f'  恢复后 test_uat_m01_h08_header_no_duplicate: {"FAIL" if rc2 != 0 else "PASS"} (rc={rc2})')
    for line in out2.splitlines():
        if 'test_uat_m01_h08_header_no_duplicate' in line:
            print('  ', line.strip())
except Exception as e:
    print('  ERROR:', e)
    if os.path.exists(M01_SRC):
        unpatch(M01_SRC, M01_NEW, M01_OLD)

# ===== M02 变异：把 _badge_offline 退化为返回原文（无徽标） =====
print('\n=== 变异测试 M02：让 _badge_offline 不再输出徽标样式 ===\n')
M02_OLD = '    return (\'<span style="background:#fff3cd;color:#856404;padding:1px 6px;\'\n            \'border-radius:3px;font-weight:600;">\' + escaped_text + \'</span>\')'
M02_NEW = '    return escaped_text  # M02 变异：徽标退化为原文，期望 M02 用例变红'
try:
    patch(M01_SRC, M02_OLD, M02_NEW)
    rc, out, err = run('tests/test_v1634_report_context.py::test_uat_m02_offline_badge_render', 'tests/test_v1634_report_context.py::test_uat_m02_legacy_missing_name_badged', '-v')
    print(f'  退化后 M02 用例: rc={rc}')
    for line in out.splitlines():
        if 'test_uat_m02' in line:
            print('  ', line.strip())
    if rc == 0:
        print('  !! 变异未生效，回归锁有问题！')
    unpatch(M01_SRC, M02_NEW, M02_OLD)
    print('  恢复后文件已还原\n')

    rc2, out2, err2 = run('tests/test_v1634_report_context.py::test_uat_m02_offline_badge_render', 'tests/test_v1634_report_context.py::test_uat_m02_legacy_missing_name_badged', '-v')
    print(f'  恢复后 M02 用例: rc={rc2}')
    for line in out2.splitlines():
        if 'test_uat_m02' in line:
            print('  ', line.strip())
except Exception as e:
    print('  ERROR:', e)
    if os.path.exists(M01_SRC):
        unpatch(M01_SRC, M02_NEW, M02_OLD)

print('\n=== 变异测试完成 ===')
