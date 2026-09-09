"""
BEFORE UPDATE/DELETE (audit.0003) не покрывает TRUNCATE — это отдельный
тип операции в Postgres, row-level триггеры на него не срабатывают в
принципе. Нужен STATEMENT-level триггер на TRUNCATE.

Условие «блокировать, только если в таблице уже есть строки» — не
поблажка, а осознанный выбор: DELETE уже невозможен (0003), поэтому
единственный способ таблице стать пустой — если в неё ещё никогда не
писали. TRUNCATE пустой таблицы не теряет ни одной записи и ничем не
отличается от отсутствия данных, поэтому безопасен; как только появилась
хотя бы одна запись, обратного пути к пустой таблице уже нет — и
TRUNCATE блокируется навсегда. Без этого условия триггер конфликтует со
штатным flush Django (`TransactionTestCase` чистит тестовую БД через
TRUNCATE между тестами) — ломать тестовую инфраструктуру ради защиты
таблицы, в которой ещё нет ни одной строки, смысла нет.
"""
from django.db import migrations

CREATE_TRUNCATE_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION audit_auditlog_prevent_truncate() RETURNS trigger AS $$
BEGIN
    IF (SELECT count(*) FROM audit_auditlog) > 0 THEN
        RAISE EXCEPTION
            'Записи журнала аудита WORM неизменяемы: TRUNCATE запрещён на уровне БД, пока в таблице есть записи';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditlog_worm_guard_truncate
BEFORE TRUNCATE ON audit_auditlog
FOR EACH STATEMENT EXECUTE FUNCTION audit_auditlog_prevent_truncate();
"""

DROP_TRUNCATE_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS audit_auditlog_worm_guard_truncate ON audit_auditlog;
DROP FUNCTION IF EXISTS audit_auditlog_prevent_truncate();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0004_alter_auditlog_event_type"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_TRUNCATE_TRIGGER_SQL, reverse_sql=DROP_TRUNCATE_TRIGGER_SQL),
    ]
