"""Round-two independent exemption boundary probe; never executes audited SQL."""
import argparse
from collections import Counter
import json
import logging
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.repo).resolve()))
    from backend.engine.checker import RuleChecker
    import sqlglot
    logging.getLogger('sqlglot').setLevel(logging.ERROR)
    c = RuleChecker()
    cases = []
    kfns = ['CONSTRAINT uk_u UNIQUE(u)', 's SERIAL', 's INT SERIAL DEFAULT VALUE']
    indexes = ['', ",UNIQUE KEY uk_extra(u) COMMENT 'index note'", ",PRIMARY KEY(id,sk) COMMENT 'pk note'"]
    tails = ['ENGINE=InnoDB DEFAULT CHARSET=utf8mb4', 'ENGINE=123 DEFAULT CHARSET=utf8mb4', 'ENGINE=InnoDB DEFAULT', 'ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 PARTITION BY RANGE(id) (PARTITION p0 VALUES LESS THAN(10))']
    phrases = ['plain', 'CREATE VIEW', 'CREATE PROCEDURE', 'CREATE FUNCTION', 'CREATE TRIGGER', 'LOAD DATA', 'LOAD XML']
    for ki, kfn in enumerate(kfns):
        for ii, idx in enumerate(indexes):
            for ti, tail in enumerate(tails):
                for pi, phrase in enumerate(phrases):
                    pk = '' if ii == 2 else ',PRIMARY KEY(id,sk)'
                    sql = f"CREATE TABLE t_guard(id BIGINT NOT NULL COMMENT 'id',sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',create_time DATETIME COMMENT 'created',update_time DATETIME COMMENT 'updated',is_deleted INT COMMENT 'deleted'{pk},{kfn}{idx}) {tail} COMMENT='{phrase}' shardkey=sk;"
                    cases.append((f'kfn-path:{ki}:{ii}:{ti}:{pi}', sql, 'kfn'))
    # Genuine statement variants are compared with controls before judging legality.
    bodies = [
        'PROCEDURE p() BEGIN SELECT 1; END',
        'PROCEDURE p() BEGIN DECLARE x INT DEFAULT 0; SET x=1; SELECT x; END',
        'PROCEDURE p() BEGIN DECLARE x INT DEFAULT 0; WHILE x<3 DO SET x=x+1; END WHILE; SELECT x; END',
        'FUNCTION f() RETURNS INT DETERMINISTIC BEGIN DECLARE x INT DEFAULT 1; RETURN x; END',
        'TRIGGER tr BEFORE INSERT ON t FOR EACH ROW SET NEW.id = 1',
        'VIEW v AS SELECT 1 AS id',
    ]
    for bi, body in enumerate(bodies):
        for si, sep in enumerate([' ', '\n', '\t', '  ', ' /* ordinary */ ']):
            cases.append((f'real-object:{bi}:{si}', 'CREATE' + sep + body, 'real-object'))
    for pi, phrase in enumerate(phrases):
        for si, sql in enumerate([
            f"SELECT '{phrase}' FROM", f"SELECT 1 + '{phrase}' +", f"UPDATE t SET name='{phrase}' WHERE",
            f"CREATE TABLE t (id INT, u INT COMMENT '{phrase}',)",
            f"CREATE TABLE t (id INT, u INT COMMENT '{phrase}'",
        ]):
            cases.append((f'ordinary-error:{si}:{pi}', sql, 'ordinary-error'))
    for mi, marker in enumerate(['plain', 'KNOWN_FIDELITY_GAP', 'UNIQUE_SEMANTICS_INCOMPLETE']):
        cases.append((f'marker-literal:{mi}', f"CREATE PROCEDURE p() BEGIN DECLARE x VARCHAR(80) DEFAULT '{marker}'; WHILE x <> '' DO SET x=''; END WHILE; END", 'marker-literal'))
    for li, sql in enumerate(["LOAD DATA INFILE '/tmp/synthetic.csv' INTO TABLE t FIELDS TERMINATED BY ','", "LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t", "CREATE OR REPLACE VIEW v AS SELECT 1 AS id", "CREATE DEFINER='synthetic'@'localhost' PROCEDURE p() BEGIN SELECT 1; END"]):
        cases.append((f'special-control:{li}', sql, 'control'))
    rows = []
    disabled = {r.rule_id:{'enabled':False} for r in c.rules}
    for cid, sql, kind in cases:
        p = c.parser.parse(sql)
        result = c.audit_sql(sql, instance_type='distributed')
        fired = sorted({v.rule_id for v in result.violations})
        row = {'id':cid, 'kind':kind, 'sql':sql, 'parse_error':p.parse_error, 'known_fidelity_failures':p.known_fidelity_failures, 'ast':type(p.ast).__name__, 'sql_type':p.sql_type, 'has_load_data':p.has_load_data, 'fired':fired, 'passed':result.passed}
        if p.known_fidelity_failures:
            override = c.audit_sql(sql, instance_type='distributed', rule_overrides=disabled)
            row['all_business_rules_disabled'] = {'passed':override.passed,'fired':sorted({v.rule_id for v in override.violations})}
        rows.append(row)
    leaks = [r['id'] for r in rows if r['known_fidelity_failures'] and 'E999_SYNTAX_ERROR' not in r['fired']]
    result = {'sqlglot':sqlglot.__version__, 'count':len(rows), 'kfn_without_e999':leaks, 'groups':dict(Counter(r['kind'] for r in rows)), 'rows':rows}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
