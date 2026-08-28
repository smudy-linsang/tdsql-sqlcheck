"""Independent O UAT oracle/differential probe. Does not execute audited SQL."""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import logging
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    repo = Path(args.repo).resolve()
    sys.path.insert(0, str(repo))
    from backend.engine.checker import RuleChecker
    from backend.engine.rules import ALL_RULE_CLASSES
    import sqlglot
    checker = RuleChecker()
    spec = importlib.util.spec_from_file_location("uat_verify", repo / "tests/rule_audit_materials/verify_rules.py")
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    cases = []
    for p in sorted((repo / "tests/rule_audit_materials").rglob("*")):
        if p.suffix not in (".sql", ".xml"):
            continue
        text = p.read_text(encoding="utf-8")
        parsed_cases = vr.split_cases(text) if p.suffix == ".sql" else vr.split_xml_cases(text, checker)
        for cid, expect, scope, sql in parsed_cases:
            for it in (["distributed", "centralized"] if scope == "all" else [scope]):
                wanted = expect["dist" if it == "distributed" else "cent"]
                if wanted is None:
                    wanted = expect["base"]
                cases.append((f"corpus:{p.name}:{cid}:{it}", sql, it, None, sorted(wanted), None))
    for p in sorted((repo / "tests/fixtures").glob("*.sql")):
        for it in ("distributed", "centralized"):
            cases.append((f"fixture:{p.name}:{it}", p.read_text(encoding="utf-8"), it, None, None, None))
    metadata = {"t_customer": {"shard_key": "cust_id", "is_shard_table": True,
                              "indexes": [{"name": "PRIMARY", "columns": ["cust_id"]}]}}
    meta_sqls = {
        "R048": "INSERT INTO t_customer (cust_name) VALUES ('synthetic')",
        "R055": "SELECT * FROM t_customer",
        "R056": "SELECT cust_id, cust_name FROM t_customer WHERE cust_id=1 ORDER BY create_time",
        "R057": "INSERT INTO t_customer (cust_name) VALUES ('synthetic')",
        "R058": "UPDATE t_customer SET cust_name='synthetic' WHERE cust_id=1",
        "R060": "SELECT cust_name FROM t_customer",
        "R064": "SELECT cust_name FROM t_customer WHERE cust_id=1",
    }
    for rid, sql in meta_sqls.items():
        cases.append(("metadata:" + rid, sql, "distributed", metadata, None, rid))
    # Deliberately mix newly recovered structures with unrelated rule triggers.
    columns = ["u VARCHAR(32) NOT NULL COMMENT 'u'", "u VARCHAR(32) UNIQUE COMMENT 'u'",
               "u TEXT COMMENT 'u'", "u DOUBLE COMMENT 'amount'", "u INT COMMENT 'u'"]
    pks = ["PRIMARY KEY(id,sk)", "PRIMARY KEY(id,sk) COMMENT 'pk'", "PRIMARY KEY(id)"]
    indexes = ["KEY idx_unique(u)", "KEY idx_u(u) COMMENT 'UNIQUE KEY uk_x(u)'",
               "UNIQUE KEY uk_u(u)", "UNIQUE KEY uk_u(u) COMMENT 'uk ) unique'",
               "UNIQUE INDEX uk_s(sk,u) COMMENT 'uk'", "UNIQUE(u)"]
    tails = ["shardkey=sk", "TDSQL_DISTRIBUTED BY HASH(sk)", "shardkey=noshardkey_allset", ""]
    for n, (col, pk, idx, tail, it) in enumerate(itertools.product(columns, pks, indexes, tails, ("distributed", "centralized"))):
        sql = f"CREATE TABLE t_o (id BIGINT NOT NULL COMMENT 'id',sk BIGINT NOT NULL COMMENT 'sk',{col},{pk},{idx}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='uat' {tail};"
        cases.append((f"generated:{n}", sql, it, None, None, None))
    # A parser fidelity failure must not be suppressed by quoted/comment payloads.
    kfns = ["CONSTRAINT uk_u UNIQUE(u)", "s SERIAL", "s INT SERIAL DEFAULT VALUE"]
    literals = ["plain", "CREATE VIEW", "CREATE PROCEDURE", "CREATE FUNCTION", "CREATE TRIGGER", "LOAD DATA", "LOAD XML"]
    for n, (kfn, literal) in enumerate(itertools.product(kfns, literals)):
        sql = f"CREATE TABLE t_guard (id BIGINT NOT NULL COMMENT 'id',sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',PRIMARY KEY(id,sk),{kfn}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='{literal}' shardkey=sk;"
        cases.append((f"kfn_literal:{n}", sql, "distributed", None, None, "E999_SYNTAX_ERROR"))
    for n, (kfn, literal, mode) in enumerate(itertools.product(kfns, literals[1:], ("block", "line", "quoted_block"))):
        base = f"CREATE TABLE t_guard (id BIGINT NOT NULL COMMENT 'id',sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created',update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'updated',is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT 'deleted',PRIMARY KEY(id,sk),{kfn}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='plain' shardkey=sk"
        if mode == "block":
            sql = base + f" /* {literal} */;"
        elif mode == "line":
            sql = base + f" -- {literal}\n;"
        else:
            sql = base.replace("COMMENT='plain'", f"COMMENT='/* {literal} */'") + ";"
        cases.append((f"kfn_comment:{n}:{mode}", sql, "distributed", None, None, "E999_SYNTAX_ERROR"))
    rows, coverage, failures = [], {}, []
    for cid, sql, it, meta, expected, must_have in cases:
        try:
            parsed = checker.parser.parse(sql)
            result = checker.audit_sql(sql, instance_type=it, table_metadata=meta)
            fired = sorted({v.rule_id for v in result.violations})
            violations = sorted([(v.rule_id, str(v.severity), v.message) for v in result.violations])
            row = {"id": cid, "sql": sql, "instance_type": it, "fired": fired,
                   "parse_error": parsed.parse_error, "sql_type": parsed.sql_type,
                   "passed": result.passed, "violations": violations}
            if expected is not None:
                actual_rules = set(fired) - {"E999_SYNTAX_ERROR"}
                if actual_rules != set(expected):
                    failures.append({"id": cid, "kind": "corpus_exact", "missing": sorted(set(expected)-actual_rules), "extra": sorted(actual_rules-set(expected))})
            if must_have and must_have not in fired:
                failures.append({"id": cid, "kind": "must_have", "missing": [must_have]})
            for rid in fired:
                coverage.setdefault(rid, []).append(cid)
        except Exception as exc:
            row = {"id": cid, "sql": sql, "exception": repr(exc)}
            failures.append({"id": cid, "kind": "exception", "error": repr(exc)})
        rows.append(row)
    info = {"repo": str(repo), "sqlglot": sqlglot.__version__, "rule_count": len(ALL_RULE_CLASSES),
            "rules": checker.get_rules_info(), "case_count": len(rows), "covered": sorted(coverage),
            "coverage": coverage, "failures": failures, "rows": rows}
    Path(args.out).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:info[k] for k in ("sqlglot", "rule_count", "case_count", "covered", "failures")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
