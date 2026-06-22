#!/usr/bin/env python
"""
TDSQL SQL审核工具 - CLI命令行工具 (V1.0)

用法:
  python -m backend.cli audit "SELECT * FROM users"
  python -m backend.cli audit-file script.sql
  python -m backend.cli rules
  python -m backend.cli gate --strategy strict
"""
import sys
import json
import click

from backend.engine.checker import RuleChecker
from backend.services.gate_service import GateService
from backend.engine.fingerprint import FingerprintEngine
from backend.engine.index_advisor import IndexAdvisor
from backend.engine.sql_rewriter import SQLRewriter


@click.group()
@click.version_option("1.0.0", prog_name="TDSQL-SQLCheck")
def cli():
    """TDSQL SQL审核工具 CLI"""
    pass


@cli.command()
@click.argument("sql")
@click.option("--project", "-p", default="default", help="项目ID")
@click.option("--gate", is_flag=True, help="启用质量门禁检查")
def audit(sql, project, gate):
    """审核单条SQL语句"""
    checker = RuleChecker()
    result = checker.audit_sql(sql)

    click.echo(f"\n{'='*60}")
    click.echo(f"SQL审核结果")
    click.echo(f"{'='*60}")
    click.echo(f"SQL类型: {result.sql_type}")
    click.echo(f"通过: {'✓' if result.passed else '✗'}")

    if result.violations:
        click.echo(f"\n违规详情 ({len(result.violations)} 条):")
        for v in result.violations:
            severity_icon = "🔴" if v.severity == "ERROR" else "🟡" if v.severity == "WARNING" else "🔵"
            click.echo(f"  {severity_icon} [{v.rule_id}] {v.severity}: {v.message}")
            if v.suggestion:
                click.echo(f"     建议: {v.suggestion}")

    if gate:
        gate_service = GateService()
        gate_result = gate_service.evaluate(result.violations)
        click.echo(f"\n门禁结果: {'通过 ✓' if gate_result.passed else '阻断 ✗'}")
        click.echo(f"  ERROR: {gate_result.error_count}, WARNING: {gate_result.warning_count}")
        if gate_result.blocked_by:
            click.echo(f"  阻断规则: {', '.join(gate_result.blocked_by)}")
        click.echo(f"  详情: {gate_result.detail}")

    click.echo(f"{'='*60}\n")

    if not result.passed:
        sys.exit(1)


@cli.command(name="audit-file")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--gate", is_flag=True, help="启用质量门禁检查")
def audit_file(file_path, gate):
    """审核SQL文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    checker = RuleChecker()
    results = checker.audit_file(content, file_path=file_path)
    summary = checker.compute_summary(results)

    click.echo(f"\n{'='*60}")
    click.echo(f"文件审核结果: {file_path}")
    click.echo(f"{'='*60}")
    click.echo(f"SQL总数: {summary.total_sql}")
    click.echo(f"通过: {summary.passed}, 失败: {summary.failed}")
    click.echo(f"ERROR: {summary.error_count}, WARNING: {summary.warning_count}")
    click.echo(f"通过率: {summary.pass_rate}%")

    for r in results:
        if not r.passed:
            click.echo(f"\n  [行{r.line_number or '?'}] {r.sql[:80]}...")
            for v in r.violations:
                click.echo(f"    [{v.rule_id}] {v.severity}: {v.message}")

    if gate:
        all_violations = [v for r in results for v in r.violations]
        gate_service = GateService()
        gate_result = gate_service.evaluate(all_violations)
        click.echo(f"\n门禁结果: {'通过 ✓' if gate_result.passed else '阻断 ✗'}")
        if not gate_result.passed:
            sys.exit(1)

    click.echo(f"{'='*60}\n")


@cli.command()
def rules():
    """列出所有审核规则"""
    checker = RuleChecker()
    rules_info = checker.get_rules_info()

    click.echo(f"\n共 {len(rules_info)} 条规则:\n")
    by_category = {}
    for r in rules_info:
        cat = r["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(r)

    for cat, rules_list in sorted(by_category.items()):
        click.echo(f"  [{cat}] ({len(rules_list)} 条)")
        for r in rules_list:
            status = "✓" if r["enabled"] else "✗"
            click.echo(f"    {status} {r['rule_id']}: [{r['severity']}] {r['description']}")
        click.echo()


@cli.command()
@click.argument("sql")
def fingerprint(sql):
    """生成SQL指纹"""
    engine = FingerprintEngine()
    fp = engine.fingerprint(sql)
    fp_hash = engine.fingerprint_hash(sql)
    click.echo(f"原始SQL: {sql}")
    click.echo(f"指纹: {fp}")
    click.echo(f"指纹哈希: {fp_hash}")


@cli.command()
@click.argument("sql")
def index_advise(sql):
    """索引推荐"""
    advisor = IndexAdvisor()
    recs = advisor.advise_from_sql(sql)
    if not recs:
        click.echo("无索引推荐")
        return
    for rec in recs:
        click.echo(f"\n类型: {rec.type}")
        click.echo(f"表: {rec.table}")
        click.echo(f"索引名: {rec.index_name}")
        click.echo(f"字段: {', '.join(rec.columns)}")
        click.echo(f"DDL: {rec.ddl}")
        click.echo(f"原因: {rec.reason}")


@cli.command()
@click.argument("sql")
def rewrite(sql):
    """SQL改写建议"""
    rewriter = SQLRewriter()
    suggestions = rewriter.rewrite(sql)
    if not suggestions:
        click.echo("无改写建议")
        return
    for s in suggestions:
        click.echo(f"\n类型: {s.type}")
        click.echo(f"原因: {s.reason}")
        click.echo(f"改写: {s.rewritten_sql}")
        click.echo(f"预期收益: {s.expected_benefit}")


@cli.command()
@click.argument("project_id")
@click.argument("strategy", type=click.Choice(["strict", "normal", "loose"]))
def gate(project_id, strategy):
    """设置门禁策略"""
    gate_service = GateService()
    if gate_service.apply_strategy(project_id, strategy):
        click.echo(f"门禁策略已设置: {project_id} -> {strategy}")
    else:
        click.echo(f"设置失败")
        sys.exit(1)


if __name__ == "__main__":
    cli()
