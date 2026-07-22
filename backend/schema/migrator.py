"""
Schema 自动迁移引擎：计算 SHA256 Checksum 并进行增量 DDL 迁移
"""
import hashlib
import logging
from backend.services.database import _get_connection
from backend.schema.loader import discover_schema_files

logger = logging.getLogger("tdsql.schema.migrator")


class SchemaMigrator:
    def ensure_migration_table(self, conn):
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version_key VARCHAR(128) PRIMARY KEY,
                checksum VARCHAR(64) NOT NULL,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()

    def run_migrations(self):
        conn = _get_connection()
        try:
            self.ensure_migration_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT version_key, checksum FROM schema_migrations")
            rows = cursor.fetchall()
            applied = {}
            for r in rows:
                if isinstance(r, dict):
                    applied[r["version_key"]] = r["checksum"]
                else:
                    applied[r[0]] = r[1]

            schema_files = discover_schema_files()
            for sf in schema_files:
                key = f"v{sf.version}_{sf.sequence:03d}_{sf.name}"
                checksum = hashlib.sha256(sf.sql.encode("utf-8")).hexdigest()
                if key in applied:
                    if applied[key] != checksum:
                        logger.warning(f"Schema 文件 {key} 的 Checksum 发生变动（可能手工修改过）")
                    continue

                logger.info(f"应用增量数据库 Schema 迁移: {key}")
                # 剔除注释行并分割执行
                clean_lines = []
                for line in sf.sql.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("--"):
                        clean_lines.append(line)
                clean_sql = "\n".join(clean_lines)

                statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
                for stmt in statements:
                    try:
                        cursor.execute(stmt)
                    except Exception as e:
                        logger.warning(f"迁移语句执行告警 [{key}]: {e}")
                cursor.execute("""
                    INSERT INTO schema_migrations (version_key, checksum)
                    VALUES (%s, %s)
                """, (key, checksum))
                conn.commit()
        except Exception as e:
            logger.error(f"Schema 迁移失败: {e}")
        finally:
            conn.close()


migrator = SchemaMigrator()
