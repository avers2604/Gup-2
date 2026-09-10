"""
Доменная логика графа версионности, не сводимая к ограничениям на уровне
таблицы (ТЗ 4.2.2: «Алгоритм публикации проверяет граф связей на
ацикличность»).

CheckConstraint/UniqueConstraint на DocumentRelation (см. models.py)
ловят только самоссылку и точный дубль одной связи — по построению SQL
CHECK видит лишь вставляемую строку, а не весь граф. Цикл длиной больше
единицы (A → B → C → A) виден только обходом графа, поэтому это
рекурсивный запрос, а не ограничение таблицы.
"""
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction

from . import permissions, transitions


def relation_would_create_cycle(from_document_id, to_document_id) -> bool:
    """True, если ребро from_document -> to_document создаст цикл — то
    есть to_document уже может достичь from_document по существующим связям."""
    if from_document_id == to_document_id:
        return True

    # Модель через apps.get_model(), а не прямой импорт — models.py вызывает
    # эту функцию из DocumentRelation.clean(), прямой импорт дал бы цикл.
    table = apps.get_model("documents", "DocumentRelation")._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE reachable(id) AS (
                SELECT to_document_id FROM {table} WHERE from_document_id = %s
                UNION
                SELECT r.to_document_id
                FROM {table} r
                JOIN reachable ON r.from_document_id = reachable.id
            )
            SELECT 1 FROM reachable WHERE id = %s LIMIT 1
            """,
            [to_document_id, from_document_id],
        )
        return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Запись карточек НРД (ТЗ 4.1, 4.2) — партия 2 Этапа 2.
#
# DDD-граница, объявленная в STACK.md: `views.py` не трогает
# `Model.objects.*` и не содержит бизнес-логики. Здесь — единственное
# место, где карточка создаётся, правится и меняет статус, и куда
# приходят и Web GUI, и (когда появится) External API.
# ---------------------------------------------------------------------------
# Поля, которые full_clean() проверять не должен: `retention_mode` и
# `retention_until` объявлены editable=False и считаются в save() из
# категории. На момент full_clean() они ещё не заполнены, и обязательное
# retention_mode завалило бы создание карточки требованием заполнить
# поле, которого нет ни в одной форме.
_CLEAN_EXCLUDED_FIELDS = ["retention_mode", "retention_until"]


class StatusTransitionError(ValidationError):
    """Переход между статусами не разрешён графом (`transitions.py`).

    Наследует ValidationError, а не собственный базовый класс: для
    вызывающего HTTP-слоя это ошибка ввода — пользователь выбрал
    недопустимый вариант, — и она должна попадать в форму как обычная
    ошибка валидации, а не превращаться в 500.
    """


def _lock_document(document):
    """Перечитать карточку под блокировкой строки.

    Смена статуса — это чтение текущего статуса, проверка перехода и
    запись нового; без блокировки два одновременных перехода прочитали бы
    один и тот же исходный статус и оба сочли бы себя допустимыми.
    EXCLUDE USING gist на истории статусов поймал бы такую гонку, но
    ценой IntegrityError у одного из вызывающих — а это не «нельзя», это
    «попробуйте ещё раз», и разбираться в разнице пришлось бы каждому
    потребителю сервиса.
    """
    model = type(document)
    return model.objects.select_for_update().get(pk=document.pk)


def create_document(*, actor, form=None, **attrs):
    """Создать карточку. Всегда черновик — статус не выбирается при
    создании: придание документу силы это отдельное, юридически значимое
    действие с другим кругом полномочий (`permissions.can_change_status`).
    """
    if not permissions.can_edit_document(actor):
        raise PermissionDenied("Недостаточно прав для создания карточки НРД.")

    with transaction.atomic():
        if form is not None:
            document = form.save(commit=False)
        else:
            document = apps.get_model("documents", "NormativeDocument")(**attrs)
        document.status = document.Status.DRAFT
        # Транзитный атрибут, не поле модели — NormativeDocument.save()
        # читает его для аудита (та же конвенция, что в admin.save_model).
        document._audit_actor = actor
        document.full_clean(exclude=_CLEAN_EXCLUDED_FIELDS)
        document.save()
        if form is not None:
            form.save_m2m()
    return document


def update_document(*, actor, document, form=None, **attrs):
    """Правка карточки. Разрешена только черновику — документ, уже
    имеющий силу, изменяется новой редакцией со связью версионности
    (ТЗ 4.2.2), см. `permissions.can_edit_document`.
    """
    with transaction.atomic():
        locked = _lock_document(document)
        if not permissions.can_edit_document(actor, locked):
            raise PermissionDenied(
                "Правка доступна только черновику и только с соответствующими полномочиями."
            )
        if form is not None:
            document = form.save(commit=False)
        else:
            for field, value in attrs.items():
                setattr(document, field, value)
        document._audit_actor = actor
        document.full_clean(exclude=_CLEAN_EXCLUDED_FIELDS)
        document.save()
        if form is not None:
            form.save_m2m()
    return document


def change_document_status(*, actor, document, new_status, comment=""):
    """Сменить статус карточки с ведением SCD-2 (ТЗ 4.2.3).

    Сама история пишется в `NormativeDocument.save()` — здесь блокировка,
    права, проверка допустимости перехода и проверка графа связей при
    публикации.
    """
    with transaction.atomic():
        locked = _lock_document(document)

        if not permissions.can_change_status(actor, locked):
            raise PermissionDenied("Смена статуса документа доступна Контролёру/Юристу и Администратору.")

        if new_status == locked.status:
            raise StatusTransitionError("Документ уже находится в этом статусе.")

        if not transitions.is_allowed(locked.status, new_status):
            raise StatusTransitionError(
                f"Переход «{locked.get_status_display()}» → "
                f"«{dict(type(locked).Status.choices)[new_status]}» не предусмотрен."
            )

        # ТЗ 4.2.2: «Алгоритм публикации проверяет граф связей на
        # ацикличность». Каждое ребро проверяется ещё при создании
        # (DocumentRelation.clean()), поэтому в норме цикла быть не
        # может — но это проверка на входе, а требование ТЗ относится
        # к публикации. Пути в обход clean() существуют (сырой SQL,
        # миграции данных), и цена перепроверки — один рекурсивный
        # запрос на юридически значимое действие.
        if new_status in transitions.PUBLISHING_TRANSITIONS:
            cycle = find_cycle_through_document(locked.pk)
            if cycle is not None:
                raise StatusTransitionError(
                    "Публикация невозможна: граф связей версионности содержит цикл "
                    f"с участием этого документа ({cycle})."
                )

        previous_status = locked.status
        locked.status = new_status
        locked._audit_actor = actor
        locked._audit_comment = comment
        locked.save(update_fields=["status", "updated_at"])

    return locked, previous_status


def find_cycle_through_document(document_id):
    """Регистрационный номер документа, до которого от `document_id` есть
    путь по связям и обратно, либо None.

    Отдельно от `relation_would_create_cycle()`: та отвечает на вопрос
    «замкнёт ли цикл ещё не существующее ребро», эта — «есть ли цикл в
    уже сохранённом графе», и её ответ нужен человеку в сообщении об
    ошибке, а не булевым флагом.
    """
    table = apps.get_model("documents", "DocumentRelation")._meta.db_table
    document_table = apps.get_model("documents", "NormativeDocument")._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE reachable(id) AS (
                SELECT to_document_id FROM {table} WHERE from_document_id = %s
                UNION
                SELECT r.to_document_id
                FROM {table} r
                JOIN reachable ON r.from_document_id = reachable.id
            )
            SELECT d.reg_number
            FROM reachable
            JOIN {document_table} d ON d.id = reachable.id
            WHERE reachable.id = %s
            LIMIT 1
            """,
            [document_id, document_id],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def add_relation(*, actor, from_document, to_document, relation_type, note=""):
    """Завести ребро графа версионности (ТЗ 4.2.2).

    Ацикличность проверяет сам `DocumentRelation.clean()` (вызывается из
    его `save()`), самоссылку и точный дубль — ограничения БД. Здесь —
    права и, отдельно, видимость ЦЕЛИ: ссылку на документ, которого
    пользователь не видит, заводить нельзя, иначе гриф «ДСП» утекает уже
    через сам факт успешного создания связи.
    """
    model = apps.get_model("documents", "DocumentRelation")

    if not permissions.can_manage_relations(actor, from_document):
        raise PermissionDenied("Недостаточно прав для изменения графа связей версионности.")
    if not permissions.can_view_document(actor, to_document):
        raise PermissionDenied("Указанный документ недоступен.")

    with transaction.atomic():
        relation = model(
            from_document=from_document, to_document=to_document,
            relation_type=relation_type, note=note,
        )
        relation.save()
        _log_relation_event(
            actor=actor, relation=relation,
            event_name="DOCUMENT_RELATION_ADDED",
        )
    return relation


def remove_relation(*, actor, relation):
    """Снять ребро графа. Таблица связей не WORM — ошибочно заведённую
    связь надо уметь убрать, — но само снятие меняет граф, по которому
    документ считается отменённым или изменённым, поэтому пишется в
    журнал так же, как и добавление."""
    if not permissions.can_manage_relations(actor, relation.from_document):
        raise PermissionDenied("Недостаточно прав для изменения графа связей версионности.")
    if not permissions.can_view_document(actor, relation.to_document):
        # Связь на скрытый документ пользователь и не видел в карточке —
        # значит и снять её «случайно» не мог: это прямой запрос.
        raise PermissionDenied("Указанный документ недоступен.")

    with transaction.atomic():
        _log_relation_event(
            actor=actor, relation=relation,
            event_name="DOCUMENT_RELATION_REMOVED",
        )
        relation.delete()


def _log_relation_event(*, actor, relation, event_name):
    from apps.audit.models import AuditLog

    AuditLog.objects.create(
        event_type=getattr(AuditLog.EventType, event_name),
        actor=actor,
        actor_personnel_number=getattr(actor, "personnel_number", ""),
        object_type="NormativeDocument",
        # Объект события — документ, от которого идёт связь: именно в его
        # карточке она видна и именно его граф меняется.
        object_id=relation.from_document.reg_number,
        details={
            "relation_type": relation.relation_type,
            "to_document": relation.to_document.reg_number,
            "note": relation.note,
        },
    )
