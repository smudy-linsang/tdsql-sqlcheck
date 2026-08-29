"""Prove whether a failed ALTER can still be recorded as an applied migration."""
import json
from pathlib import Path
from unittest.mock import patch
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from backend.schema.migrator import SchemaMigrator


class Cursor:
    def __init__(self):
        self.statements = []
        self.inserted = False

    def execute(self, statement, params=None):
        normalized = " ".join(statement.split())
        self.statements.append({"sql": normalized, "params": params})
        if normalized.startswith("SELECT version_key"):
            return
        if "ADD COLUMN related_index_name" in normalized:
            raise RuntimeError("synthetic ALTER failure")
        if normalized.startswith("INSERT INTO schema_migrations"):
            self.inserted = True

    def fetchall(self):
        return []


class Connection:
    def __init__(self):
        self.cursor_obj = Cursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def close(self):
        pass


connection = Connection()
schema_file = type("SchemaFile", (), {
    "version": 11,
    "sequence": 110,
    "name": "index_finding_structured",
    "sql": (
        "ALTER TABLE index_audit_finding ADD COLUMN related_index_name VARCHAR(128) DEFAULT '';\n"
        "ALTER TABLE index_audit_finding ADD COLUMN index_columns VARCHAR(512) DEFAULT '';"
    ),
})()

with patch("backend.schema.migrator._get_connection", return_value=connection), patch(
    "backend.schema.migrator.discover_schema_files", return_value=[schema_file]
):
    SchemaMigrator().run_migrations()

result = {
    "first_alter_failed": True,
    "second_alter_attempted": any(
        "ADD COLUMN index_columns" in row["sql"] for row in connection.cursor_obj.statements
    ),
    "migration_record_inserted_after_failure": connection.cursor_obj.inserted,
    "commits": connection.commits,
    "executed_statements": connection.cursor_obj.statements,
}
(HERE / "migration_fail_closed_probe.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
    newline="\n",
)
print(json.dumps(result, ensure_ascii=False))
