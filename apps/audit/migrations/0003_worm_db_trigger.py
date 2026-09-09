"""
DB-level защита WORM (ТЗ 4.3.1, 4.7: «неизменяемая таблица БД режима
Write Once, Read Many»).

Переопределённые save()/delete() и WORMQuerySet (миграции 0001-0002,
apps/audit/models.py) защищают только путь через Django ORM. Прямой SQL
из psql или другого клиента, а также код, обходящий модель (например,
будущий экспорт/интеграционный скрипт), их не видит. Триггер на уровне
Postgres работает независимо от того, кто и как отправляет запрос.

Честная граница этой защиты: роль-владелец таблицы (тот, кем применялись
миграции) или суперпользователь БД технически может выполнить
`ALTER TABLE ... DISABLE TRIGGER` или удалить функцию/триггер — это
не отменяемо средствами самой таблицы. Настоящая защита от суперпользователя
требует разделения ролей (отдельная непривилегированная роль приложения
без прав ALTER на эту таблицу, миграции — от имени другой роли) — это
организационная/инфраструктурная задача Этапа 3 (ИБ), не решается одной
миграцией Django и здесь сознательно не эмулируется.
"""
from django.db import migrations

CREATE_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION audit_auditlog_prevent_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Записи журнала аудита WORM неизменяемы: % запрещён на уровне БД', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditlog_worm_guard
BEFORE UPDATE OR DELETE ON audit_auditlog
FOR EACH ROW EXECUTE FUNCTION audit_auditlog_prevent_mutation();
"""

DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS audit_auditlog_worm_guard ON audit_auditlog;
DROP FUNCTION IF EXISTS audit_auditlog_prevent_mutation();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_TRIGGER_SQL, reverse_sql=DROP_TRIGGER_SQL),
    ]
