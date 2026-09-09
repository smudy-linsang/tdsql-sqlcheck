"""UAT v1.6.3.5 变异自证：退化工单 1 行验证 ALG-* 护锁有效。"""
import os, subprocess, sys
ROOT = 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck'
os.chdir(ROOT)
env = os.environ.copy()
env.update(AUTH_ENABLED='false', DATA_MASKING_ENABLED='false', RAW_SLOWLOG_ENABLED='false',
            SCHEDULER_ENABLED='false', GITLAB_WEBHOOK_ALLOW_INSECURE='true', PYTHONIOENCODING='utf-8')

def run(args):
    res = subprocess.run([sys.executable, '-m', 'pytest', *args], capture_output=True, text=True,
                          env=env, timeout=120, cwd=ROOT)
    return res.returncode, res.stdout, res.stderr

def patch(src, old, new):
    with open(src, encoding='utf-8') as f: s = f.read()
    if old not in s: raise RuntimeError(f'patch anchor not found in {src}')
    if s.count(old) != 1: raise RuntimeError(f'patch anchor appears {s.count(old)} times in {src}')
    with open(src, 'w', encoding='utf-8') as f: f.write(s.replace(old, new, 1))

def unpatch(src, new, old):
    with open(src, encoding='utf-8') as f: s = f.read()
    with open(src, 'w', encoding='utf-8') as f: f.write(s.replace(new, old, 1))

# M01 变异：让 R035PriorIndex 的 add 退化为空操作 → 应该某些 ALG 用例变红
print('=== 变异 M01：R035PriorIndex.add 退化（不追加历史）===')
SRC = 'backend/engine/r035_context.py'
OLD = '''        if self.anchor is None:
            self.anchor = ref
            return'''
NEW = '''        if self.anchor is None:
            self.anchor = ref
            return  # M01 mutation: drop return, fallthrough no-op
        if False:  # 关闭 history 追加
            self.anchor = ref
            return'''
try:
    patch(SRC, OLD, NEW)
    rc, out, _ = run(['tests/test_v1635_r035_streaming.py', '-q', '--no-header'])
    print(f'  退化后 rc={rc}')
    for line in out.splitlines():
        if 'passed' in line or 'failed' in line or 'ALG' in line:
            print(f'    {line.strip()}')
    unpatch(SRC, NEW, OLD)
    print('  恢复完成')
except Exception as e:
    print(f'  ERROR: {e}')
    if os.path.exists(SRC): unpatch(SRC, NEW, OLD)

# M02 变异：让 audit_file 不再走 iter_audit_file（破坏流式）
print('\n=== 变异 M02：audit_file 退回非流式（保留全量 parsed_items）===')
SRC2 = 'backend/engine/checker.py'
OLD2 = '        return list(self.iter_audit_file(content, file_path=file_path,\n                                         rule_overrides=rule_overrides,\n                                         instance_type=instance_type))'
NEW2 = '        # M02 mutation: 退回到 c0e5e25 之前的全量预解析\n        stmts = self._split_sqls(content, file_path)\n        return [self.audit_sql(s, file_path=fp, line_number=ln) for s, ln, fp in stmts]'
try:
    patch(SRC2, OLD2, NEW2)
    rc, out, _ = run(['tests/test_v1635_r035_streaming.py', '-q', '--no-header'])
    print(f'  退化后 rc={rc}')
    for line in out.splitlines():
        if 'passed' in line or 'failed' in line or 'ALG' in line:
            print(f'    {line.strip()}')
    unpatch(SRC2, NEW2, OLD2)
    print('  恢复完成')
except Exception as e:
    print(f'  ERROR: {e}')
    if os.path.exists(SRC2): unpatch(SRC2, NEW2, OLD2)

# 恢复后再跑一次确认全绿
print('\n=== 恢复后基线复测 ===')
rc, out, _ = run(['tests/test_v1635_r035_streaming.py', '-q', '--no-header'])
for line in out.splitlines():
    if 'passed' in line or 'failed' in line:
        print(f'  {line.strip()}')
