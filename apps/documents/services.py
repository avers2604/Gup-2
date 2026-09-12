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
import logging

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction

from apps.core.write_retry import retry_on_read_only_primary

from . import permissions, transitions

logger = logging.getLogger(__name__)

# Почему ретрай стоит не на всех операциях записи.
#
# `retry_on_read_only_primary` повторяет вызов целиком. Это безопасно для
# операций, которые при откате не оставляют следов вне БД. Для `create_document`
# и `update_document` это не так: они принимают уже прочитанный upload и пишут
# файл в staging ДО INSERT, поэтому повтор потребовал бы заново перемотать и
# перезалить файл. Их повторяет пользователь (форма остаётся заполненной), а не
# сервис молча. Смена статуса и правка графа связей файлов не трогают — там
# ретрай честен.

# Один transaction-scoped advisory lock на весь граф версионности. Пара
# int32 выбрана как стабильная ASCII-подпись "BZGETDAG" и не зависит от
# Python hash randomization. Лок нужен не для производительности, а для
# корректности check-then-insert: две транзакции с непересекающимися концами
# рёбер иначе могут обе проверить старый DAG и вместе закоммитить цикл.
_VERSION_GRAPH_LOCK_KEY = (0x425A4745, 0x54444147)


def _lock_version_graph() -> None:
    """Сериализовать проверки/изменения, от которых зависит ацикличность.

    `pg_advisory_xact_lock` живёт до commit/rollback текущей транзакции.
    `DocumentRelation.save()` вызывает проверку цикла внутри atomic(), поэтому
    после возврата из `relation_would_create_cycle()` lock остаётся удержанным
    и защищает последующий INSERT. Публикационная перепроверка использует тот
    же lock, чтобы не принять решение на снимке, который тут же изменит другая
    транзакция добавления связи.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            list(_VERSION_GRAPH_LOCK_KEY),
        )


def relation_would_create_cycle(from_document_id, to_document_id) -> bool:
    """True, если ребро from_document -> to_document создаст цикл — то
    есть to_document уже может достичь from_document по существующим связям."""
    if from_document_id == to_document_id:
        return True

    # Блокировка должна браться ДО чтения графа. В штатном save() она остаётся
    # до INSERT/commit благодаря внешнему transaction.atomic().
    _lock_version_graph()

    # Модель через apps.get_model(), а не прямой импорт — models.py вызывает
    # эту функцию из DocumentRelation.clean(), прямой импорт дал бы цикл.
    table = apps.get_model("documents", "DocumentRelation")._meta.db_table
    with connection.cursor() as cursor:
        # Подавление B608 ниже: в f-строку подставляется ТОЛЬКО имя таблицы
        # из _meta.db_table (его знает сам Django, пользовательский ввод
        # туда не попадает), оба значения переданы параметрами через %s.
        # Имя таблицы нельзя передать параметром — это идентификатор, а не
        # значение, и подстановка здесь неизбежна.
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
            """,  # nosec B608
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


def _cleanup_failed_file_writes(document, previous_mutable_names):
    """Компенсировать только те файлы, которые физически можно удалять.

    `files_original` больше НИКОГДА не пишется прямо в WORM до коммита БД:
    apps.core.staged_files кладёт новый оригинал в mutable `working/staging`
    и лишь после коммита копирует его в `originals` с Object Lock. Поэтому
    rollback очищает staging, а не вызывает DELETE в WORM-бакете.

    `files_editable` остаётся обычным mutable FileField и пишется синхронно;
    его новую версию при последующем rollback безопасно удалить. Удаляем
    только `_committed=True`: если save упал до FileField.pre_save(), имя уже
    могло быть назначено, но физического объекта ещё нет — удалять такое имя
    опасно, оно теоретически может совпасть с чужим существующим объектом.
    """
    from apps.core.staged_files import discard_staged_uploads

    discard_staged_uploads(document)
    for field_name, previous_name in previous_mutable_names.items():
        field_file = getattr(document, field_name)
        if (
            not field_file
            or field_file.name == previous_name
            or not getattr(field_file, "_committed", True)
        ):
            continue
        try:
            field_file.delete(save=False)
        except Exception:
            logger.exception(
                "Не удалось удалить mutable-файл %s=%r после отката транзакции (id=%s)",
                field_name, field_file.name, getattr(document, "pk", None),
            )


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
        # Проверяем не только роль, но и доступ к РЕЗУЛЬТИРУЮЩЕЙ карточке:
        # иначе редактор без dsp_access может сразу создать скрытый от себя
        # документ с грифом ДСП.
        if not permissions.can_edit_document(actor, document):
            raise PermissionDenied(
                "Создание документа ДСП доступно только пользователю с соответствующим допуском."
            )
        # Транзитный атрибут, не поле модели — NormativeDocument.save()
        # читает его для аудита (та же конвенция, что в admin.save_model).
        document._audit_actor = actor
        document.full_clean(exclude=_CLEAN_EXCLUDED_FIELDS)
        previous_file_names = {"files_editable": ""}
        try:
            document.save()
        except Exception:
            _cleanup_failed_file_writes(document, previous_file_names)
            raise
        if form is not None:
            try:
                form.save_m2m()
            except Exception:
                _cleanup_failed_file_writes(document, previous_file_names)
                raise
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
        expected = form.cleaned_data["revision"] if form is not None else document.edit_version
        if locked.edit_version != expected:
            raise ValidationError("Документ уже изменён другим пользователем. Обновите страницу и повторите правку.")
        previous_file_names = {
            "files_editable": locked.files_editable.name if locked.files_editable else "",
        }
        from .forms import DocumentForm
        fields = DocumentForm._meta.fields
        if form is not None:
            source = form.save(commit=False)
            for name in fields:
                field = locked._meta.get_field(name)
                if not field.many_to_many:
                    setattr(locked, name, getattr(source, name))
        else:
            for name, value in attrs.items():
                if name not in fields or locked._meta.get_field(name).many_to_many:
                    raise ValidationError(f"Поле {name} нельзя изменять через этот сервис.")
                setattr(locked, name, value)
        # Поля уже применены к locked, поэтому повторная проверка ловит
        # GENERAL -> RESTRICTED до любого model save / staging side effect.
        if not permissions.can_edit_document(actor, locked):
            raise PermissionDenied(
                "Недостаточно прав для сохранения выбранного уровня доступа документа."
            )
        locked._audit_actor = actor
        locked.full_clean(exclude=_CLEAN_EXCLUDED_FIELDS)
        try:
            locked.save()
        except Exception:
            _cleanup_failed_file_writes(locked, previous_file_names)
            raise
        if form is not None:
            form.instance = locked
            try:
                form.save_m2m()
            except Exception:
                _cleanup_failed_file_writes(locked, previous_file_names)
                raise
        document.refresh_from_db()
    return locked


@retry_on_read_only_primary
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

        # Исправление ошибки публикации — за Администратором, а не за тем,
        # кто публиковал (см. transitions.ADMINISTRATOR_ONLY_TARGETS).
        if not permissions.can_change_status(actor, locked, new_status):
            raise PermissionDenied(
                "Откат публикации и аннулирование доступны только Администратору."
            )

        if transitions.requires_reason(new_status) and not comment.strip():
            # Проверяется и здесь, а не только в форме: форму можно
            # обойти, а запись «публикация отменена» без объяснения
            # бесполезна и проверяющему, и подразделению через полгода.
            raise StatusTransitionError(
                "Для отката публикации и аннулирования обязательно основание."
            )

        # ТЗ 4.2.2: «Алгоритм публикации проверяет граф связей на
        # ацикличность». Каждое ребро проверяется ещё при создании
        # (DocumentRelation.clean()), поэтому в норме цикла быть не
        # может — но это проверка на входе, а требование ТЗ относится
        # к публикации. Пути в обход clean() существуют (сырой SQL,
        # миграции данных), и цена перепроверки — один рекурсивный
        # запрос на юридически значимое действие. find_cycle... берёт тот же
        # transaction advisory lock, что и добавление ребра, поэтому решение
        # о публикации нельзя принять на устаревшем снимке графа.
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
    _lock_version_graph()
    table = apps.get_model("documents", "DocumentRelation")._meta.db_table
    document_table = apps.get_model("documents", "NormativeDocument")._meta.db_table
    with connection.cursor() as cursor:
        # Подавление B608 ниже: подставляются только имена таблиц из
        # _meta.db_table, см. пояснение в relation_would_create_cycle() выше.
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
            """,  # nosec B608
            [document_id, document_id],
        )
        row = cursor.fetchone()
    return row[0] if row else None


@retry_on_read_only_primary
def add_relation(*, actor, from_document, to_document, relation_type, note=""):
    """Завести ребро графа версионности (ТЗ 4.2.2).

    Права и видимость проверяются по актуальным строкам документов уже после
    `select_for_update()`: переданные экземпляры могут устареть между чтением
    формы и записью, в том числе получить гриф ДСП. Блокировки берутся в
    стабильном порядке `pk`, совпадающем с `DocumentRelation.save()`, чтобы не
    вводить новый порядок блокировок и не создавать взаимных дедлоков.
    """
    relation_model = apps.get_model("documents", "DocumentRelation")
    document_model = type(from_document)
    from_pk = from_document.pk
    to_pk = to_document.pk
    if from_pk is None or to_pk is None:
        raise ValidationError(
            "Один из документов был удалён до сохранения связи. Обновите страницу и повторите."
        )

    with transaction.atomic():
        current_documents = {
            item.pk: item
            for item in (
                document_model.objects.select_for_update()
                .filter(pk__in={from_pk, to_pk})
                .order_by("pk")
            )
        }
        if from_pk not in current_documents or to_pk not in current_documents:
            raise ValidationError(
                "Один из документов был удалён до сохранения связи. Обновите страницу и повторите."
            )
        current_from = current_documents[from_pk]
        current_to = current_documents[to_pk]

        if not permissions.can_manage_relations(actor, current_from):
            raise PermissionDenied("Недостаточно прав для изменения графа связей версионности.")
        if not permissions.can_view_document(actor, current_to):
            raise PermissionDenied("Указанный документ недоступен.")

        relation = relation_model(
            from_document=current_from,
            to_document=current_to,
            relation_type=relation_type,
            note=note,
        )
        relation.save()
        _log_relation_event(
            actor=actor, relation=relation,
            event_name="DOCUMENT_RELATION_ADDED",
        )
    return relation


@retry_on_read_only_primary
def remove_relation(*, actor, relation):
    """Снять ребро графа ровно один раз даже при конкурентных запросах.

    WORM-событие должно означать состоявшееся удаление, а не попытку удалить
    уже исчезнувшую строку. Поэтому relation перечитывается под row lock внутри
    той же транзакции, где пишется audit и выполняется DELETE. Второй конкурент
    после ожидания видит отсутствие строки и становится идемпотентным no-op.
    """
    model = type(relation)

    with transaction.atomic():
        locked = model.objects.select_for_update().filter(pk=relation.pk).first()
        if locked is None:
            return False

        # Права проверяем на актуальной строке, а не на stale-экземпляре,
        # переданном вызывающим кодом до ожидания row lock.
        if not permissions.can_manage_relations(actor, locked.from_document):
            raise PermissionDenied("Недостаточно прав для изменения графа связей версионности.")
        if not permissions.can_view_document(actor, locked.to_document):
            # Связь на скрытый документ пользователь и не видел в карточке —
            # значит и снять её «случайно» не мог: это прямой запрос.
            raise PermissionDenied("Указанный документ недоступен.")

        _log_relation_event(
            actor=actor, relation=locked,
            event_name="DOCUMENT_RELATION_REMOVED",
        )
        locked.delete()
        return True


def _log_relation_event(*, actor, relation, event_name):
    from apps.audit.models import AuditLog

    AuditLog.objects.create(
        event_type=getattr(AuditLog.EventType, event_name),
        actor=actor,
        actor_personnel_number=getattr(actor, "personnel_number", ""),
        object_type="NormativeDocument",
        # Объект события — документ, от которого идёт связь: именно в его
        # карточке она видна и именно его граф меняется.
        object_id=str(relation.from_document.pk),
        details={
            "reg_number": relation.from_document.reg_number,
            "relation_type": relation.relation_type,
            "to_document": relation.to_document.reg_number,
            "note": relation.note,
        },
    )


@retry_on_read_only_primary
def apply_ocr_review(*, actor, document, corrected_text):
    """Сохранить вычитанный человеком текст скана и снять документ с очереди.

    Что именно меняется: `ocr_body` (поисковый материал) и `ocr_status`. Файл
    оригинала не трогается вовсе — он лежит в WORM-бакете, и вычитка не имеет
    к нему отношения.

    `ocr_confidence` намеренно НЕ переписывается на 100: это измерение машины,
    показывающее качество исходного распознавания, и подменять его оценкой
    «человек проверил» значит потерять статистику, по которой настраиваются
    пороги (`apps/documents/ocr_thresholds.py`). Факт проверки человеком несёт
    `ocr_status`, а не уверенность распознавания.

    Право проверяется ПОСЛЕ `select_for_update()`: переданный view/клиентом
    экземпляр может устареть между чтением и записью (например, карточку
    перевели в ДСП). Решение о записи должно приниматься по актуальной строке,
    которую эта же транзакция уже заблокировала.
    """
    from apps.audit.models import AuditLog
    from apps.core.business_metrics import sync_ocr_review_queue

    corrected_text = (corrected_text or "").strip()

    with transaction.atomic():
        locked = _lock_document(document)
        if not permissions.can_review_ocr(actor, locked):
            raise PermissionDenied("Недостаточно прав для вычитки распознанного текста.")

        previous_length = len(locked.ocr_body or "")
        locked.ocr_body = corrected_text
        locked.ocr_status = locked.OcrStatus.INDEXED
        # update_fields ограничен OCR-полями: правка текста не должна
        # выглядеть как редакторское изменение карточки и не должна
        # инвалидировать чужие открытые формы правки (edit_version).
        locked.save(update_fields=["ocr_body", "ocr_status", "updated_at"])

        sync_ocr_review_queue(locked.pk, needs_review=False)

        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_OCR_REVIEWED,
            actor=actor,
            actor_personnel_number=getattr(actor, "personnel_number", ""),
            object_type="NormativeDocument",
            object_id=str(locked.pk),
            details={
                "reg_number": locked.reg_number,
                # Сам текст в журнал не пишется: он может быть на сотни
                # килобайт и содержать ДСП-содержимое, а WORM-журнал читают
                # шире, чем сам документ. Для разбора достаточно факта,
                # автора и масштаба правки.
                "previous_length": previous_length,
                "new_length": len(corrected_text),
                "ocr_confidence": locked.ocr_confidence,
            },
        )

    document.refresh_from_db()
    return locked
