"""Сводные редакции (пункт меню 8.3 решения Заказчика).

ЧТО ЭТО ТАКОЕ — ДОПУЩЕНИЕ, А НЕ ЦИТАТА. В ТЗ пункт меню назван, но не
раскрыт. Здесь он трактуется так: сводная редакция документа — это сам
действующий документ ПЛЮС все действующие документы, вносящие в него
изменения, собранные в одном месте и в хронологическом порядке. Смысл
прикладной: чтобы узнать актуальное требование, работнику сейчас нужно
открыть базовый документ, увидеть на его карточке связи «Вносит изменения
в» и обойти их по одной; этот экран делает обход за него.

ЧЕГО ЭТОТ ЭКРАН НЕ ДЕЛАЕТ И НЕ МОЖЕТ ДЕЛАТЬ. Он не порождает единый
«сшитый» текст документа с применёнными правками. Система хранит сканы и
распознанный текст, но не структуру документа (пункты, абзацы, редакции
пунктов), поэтому машинно применить «пункт 4.2 изложить в редакции…» ей
не на чем. Экран собирает ссылки и описания затронутых пунктов из поля
`DocumentRelation.note` — сводку, а не сводный текст. Это существенная
граница: пользователь, ожидающий готовый консолидированный документ, её
должен видеть, поэтому она повторена и в шаблоне.

СОСТАВ СВОДКИ СЧИТАЕТСЯ ПО ГРАФУ, А НЕ ПО СТАТУСУ. Документ попадает в
перечень не потому, что у него статус «Действует с изм.», а потому, что
на него есть хотя бы одна действующая связь «Вносит изменения в». Статус
ставится человеком и может отстать от графа — например, когда
единственное изменение отменили, а базовый документ в «Действует» вернуть
забыли. Расхождение не прячется: `stale_amended_documents()` собирает
такие карточки отдельным списком, чтобы Контролёр/Юрист видел, где статус
пора вернуть.

ДСП-ГРАНИЦА. Наличие недоступного пользователю изменения само является
метаданными закрытого документа и не должно раскрываться через появление
базовой карточки или счётчик изменений. Поэтому пользовательская сводка
считается только по видимым изменениям. При этом проверка устаревшего
статуса использует полный граф: иначе базовый документ с единственным
ДСП-изменением попал бы без допуска в блок «статус разошёлся со связями»
и тем самым всё равно раскрыл бы факт скрытой связи.
"""
from __future__ import annotations

from django.db.models import Count, Q

from . import permissions
from .models import DocumentRelation, NormativeDocument


#: Статусы, в которых документ считается имеющим силу. Изменение, которое
#: само утратило силу или аннулировано, в сводную редакцию не входит: оно
#: больше ничего не меняет, и показывать его как действующее — вводить в
#: заблуждение. Черновик изменения тоже не входит — он ещё не издан.
IN_FORCE_STATUSES = (
    NormativeDocument.Status.ACTIVE,
    NormativeDocument.Status.ACTIVE_AMENDED,
)


def amendments_for(document, user):
    """Действующие изменения к документу, видимые пользователю.

    Порядок — хронологический по дате вступления в силу: читать сводку
    имеет смысл в том же порядке, в каком изменения накладывались.
    """
    amending_ids = DocumentRelation.objects.filter(
        to_document=document,
        relation_type=DocumentRelation.RelationType.AMENDS,
        from_document__status__in=IN_FORCE_STATUSES,
    ).values_list("from_document_id", flat=True)

    return (
        permissions.visible_documents(user)
        .filter(pk__in=amending_ids)
        .select_related("issuer_dept")
        .order_by("effective_date", "reg_date", "reg_number")
    )


def amendment_notes(document):
    """Описания затронутых пунктов, по документу-источнику.

    `note` заполняется при заведении связи и часто содержит единственное
    машинно недоступное знание — какие именно пункты затронуты.
    """
    return {
        relation.from_document_id: relation.note
        for relation in DocumentRelation.objects.filter(
            to_document=document,
            relation_type=DocumentRelation.RelationType.AMENDS,
        )
    }


def _visible_in_force_document_ids(user):
    """id действующих документов, которые пользователь вправе видеть."""
    return permissions.visible_documents(user).filter(
        status__in=IN_FORCE_STATUSES
    ).values_list("pk", flat=True)


def _amended_base_ids(user):
    """id базовых документов с хотя бы одним ВИДИМЫМ действующим изменением.

    Сам факт существования ДСП-документа закрыт тем же правилом, что его
    номер и содержимое. Поэтому скрытое изменение не должно ни добавлять
    базовую карточку в список, ни увеличивать счётчик изменений.
    """
    return DocumentRelation.objects.filter(
        relation_type=DocumentRelation.RelationType.AMENDS,
        from_document_id__in=_visible_in_force_document_ids(user),
    ).values_list("to_document_id", flat=True)


def _all_amended_base_ids():
    """id базовых документов с любым действующим изменением, независимо от доступа.

    Нужны только для внутренней проверки согласованности статуса с графом:
    пользователь без ДСП-допуска не должен увидеть публичную карточку в блоке
    «статус разошёлся со связями» лишь потому, что её единственное изменение
    ему скрыто.
    """
    return DocumentRelation.objects.filter(
        relation_type=DocumentRelation.RelationType.AMENDS,
        from_document__status__in=IN_FORCE_STATUSES,
    ).values_list("to_document_id", flat=True)


def consolidated_documents(user):
    """Документы, у которых есть что сводить, — с числом видимых изменений."""
    visible_in_force_ids = _visible_in_force_document_ids(user)
    return (
        permissions.visible_documents(user)
        .filter(pk__in=_amended_base_ids(user), status__in=IN_FORCE_STATUSES)
        .annotate(
            amendment_count=Count(
                "relations_to",
                filter=Q(
                    relations_to__relation_type=DocumentRelation.RelationType.AMENDS,
                    relations_to__from_document_id__in=visible_in_force_ids,
                ),
                distinct=True,
            )
        )
        .select_related("issuer_dept")
        .order_by("reg_number")
    )


def stale_amended_documents(user):
    """«Действует с изм.», у которых ни одного действующего изменения нет.

    Не ошибка данных, а нормальное следствие отмены изменения: переход
    «Действует с изм.» -> «Действует» выполняется человеком (см.
    apps/documents/transitions.py), и до него статус расходится с графом.
    Список нужен, чтобы это расхождение было видно, а не копилось молча.

    Здесь намеренно используется полный граф, а не только видимые изменения:
    иначе наличие скрытого ДСП-изменения раскрылось бы через ложное попадание
    базовой карточки в этот блок.
    """
    return (
        permissions.visible_documents(user)
        .filter(status=NormativeDocument.Status.ACTIVE_AMENDED)
        .exclude(pk__in=_all_amended_base_ids())
        .select_related("issuer_dept")
        .order_by("reg_number")
    )
