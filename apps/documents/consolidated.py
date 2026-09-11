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
на него есть хотя бы одна действующая связь «Вносит изменения в», видимая
текущему пользователю. Это та же граница доступа, что и на карточке НРД:
ДСП-связь не должна раскрывать даже факт существования недоступного
документа через счётчик или сам факт наличия изменений. Статус ставится
человеком и может отстать от видимого графа — например, когда единственное
видимое изменение отменили. `stale_amended_documents()` использует тот же
пользовательский срез, чтобы список и детальная сводка не расходились.
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


def _visible_in_force_documents(user):
    """Действующие документы, видимые текущему пользователю."""
    return permissions.visible_documents(user).filter(status__in=IN_FORCE_STATUSES)


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
        _visible_in_force_documents(user)
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


def _amended_base_ids(user):
    """id документов с хотя бы одним видимым действующим изменением."""
    visible_amending_ids = _visible_in_force_documents(user).values("pk")
    return DocumentRelation.objects.filter(
        relation_type=DocumentRelation.RelationType.AMENDS,
        from_document_id__in=visible_amending_ids,
    ).values_list("to_document_id", flat=True)


def consolidated_documents(user):
    """Документы, у которых есть что сводить, — с числом видимых изменений."""
    visible_amending_ids = _visible_in_force_documents(user).values("pk")
    return (
        permissions.visible_documents(user)
        .filter(pk__in=_amended_base_ids(user), status__in=IN_FORCE_STATUSES)
        .annotate(
            amendment_count=Count(
                "relations_to",
                filter=Q(
                    relations_to__relation_type=DocumentRelation.RelationType.AMENDS,
                    relations_to__from_document_id__in=visible_amending_ids,
                ),
                distinct=True,
            )
        )
        .select_related("issuer_dept")
        .order_by("reg_number")
    )


def stale_amended_documents(user):
    """«Действует с изм.» без видимого действующего изменения.

    Переход «Действует с изм.» -> «Действует» выполняется человеком (см.
    apps/documents/transitions.py). Срез строится по тем же доступным
    пользователю связям, что и основной список, чтобы ДСП-метаданные не
    утекали через различия между двумя блоками экрана.
    """
    return (
        permissions.visible_documents(user)
        .filter(status=NormativeDocument.Status.ACTIVE_AMENDED)
        .exclude(pk__in=_amended_base_ids(user))
        .select_related("issuer_dept")
        .order_by("reg_number")
    )
