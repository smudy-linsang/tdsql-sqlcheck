"""UAT REQ-03 R043 验证：用附件原文 + 最小反例验证误报是否消除。"""
import urllib.request, json
import os

with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt') as f: token = f.read().strip()
base = 'http://127.0.0.1:8003'
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

def audit(sql, inst='distributed'):
    body = json.dumps({'sql': sql, 'instance_type': inst, 'project_id': '', 'connection_id': ''}).encode('utf-8')
    req = urllib.request.Request(base+'/api/v1/audit/sql', data=body, headers=headers, method='POST')
    r = urllib.request.urlopen(req, timeout=10)
    out = json.loads(r.read().decode())
    rule_ids = [v['rule_id'] for v in out.get('violations', [])]
    return rule_ids

print('=== REQ-03 R043 验证 ===\n')

# 测试 1: 附件 New 2.txt 同源建表（含 ON UPDATE CURRENT_TIMESTAMP + CHARACTER SET utf8mb4 + shardkey 二级分区 + MAXVALUE）
test1 = """CREATE TABLE t_test (
  id BIGINT NOT NULL,
  ts DATETIME ON UPDATE CURRENT_TIMESTAMP,
  c VARCHAR(20) CHARACTER SET utf8mb4,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 shardkey=id
PARTITION BY RANGE (id) (
  PARTITION p0 VALUES LESS THAN (100),
  PARTITION p1 VALUES LESS THAN (MAXVALUE)
);"""
rids = audit(test1)
print('T1 CREATE 含 ON UPDATE+CHARACTER SET+shardkey 二级分区+MAXVALUE:')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

# 测试 2: §5.1 最小反例
test2 = """CREATE TABLE t (
  ts DATETIME ON UPDATE CURRENT_TIMESTAMP,
  c VARCHAR(20) CHARACTER SET utf8mb4
);"""
rids = audit(test2)
print('T2 最小反例 (仅 ON UPDATE+CHARACTER SET):')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

# 测试 3: 真实联表 UPDATE（应报 R043）
test3 = "UPDATE a JOIN b ON a.id=b.id SET a.v=1 WHERE a.id=1;"
rids = audit(test3)
print('T3 真实联表 UPDATE (期望报 R043):')
print('  命中规则:', rids)
print('  R043 命中:', 'R043' in rids, '(设计期望: True)')

# 测试 4: 真实联表 DELETE（应报 R043）
test4 = "DELETE a FROM a JOIN b ON a.id=b.id;"
rids = audit(test4)
print('T4 真实联表 DELETE (期望报 R043):')
print('  命中规则:', rids)
print('  R043 命中:', 'R043' in rids, '(设计期望: True)')

# 测试 5: 单表 UPDATE 含子查询 JOIN（不应报 R043）
test5 = "UPDATE t SET v=(SELECT MAX(id) FROM t2 WHERE t2.id=t.id) WHERE id=1;"
rids = audit(test5)
print('T5 单表 UPDATE SET 子查询含 JOIN (期望 NOT R043):')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

# 测试 6: PARTITION(p0,p1) UPDATE
test6 = "UPDATE t PARTITION (p0,p1) SET v=1 WHERE id=1;"
rids = audit(test6)
print('T6 UPDATE PARTITION(p0,p1) (期望 NOT R043):')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

# 测试 7: ALTER ADD COLUMN 含 ON UPDATE
test7 = """ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4;"""
rids = audit(test7)
print('T7 ALTER ADD COLUMN 强触发版 (期望 NOT R043):')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

# 测试 8: LOCK TABLES（应 NOT R043，闭集命中）
test8 = "LOCK TABLES t WRITE;"
rids = audit(test8)
print('T8 LOCK TABLES (复合 token, 期望 NOT R043):')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

# 测试 9: 字符串伪造 'SELECT' AS x
test9 = "SELECT 'UPDATE a JOIN b SET x=1' AS c;"
rids = audit(test9)
print('T9 字符串伪造 (期望 NOT R043):')
print('  命中规则:', rids)
print('  R043 误报:', 'R043' in rids, '(设计期望: False)')

print('\nR043 UAT 验证完成。')
