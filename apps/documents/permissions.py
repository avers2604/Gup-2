"""Права доступа к карточкам НРД — единый источник для всех контуров.

До появления Web GUI правила жили внутри `NormativeDocumentAdmin`
(`_can_access`) и `TemplateAdmin._can_manage`: пока единственным
интерфейсом была админка, этого хватало. С появлением рабочих мест
(ТЗ 4.1) те же правила понадобились второму потребителю, и дублировать
их означало гарантированно разъехаться — поэтому они вынесены сюда, а
admin вызывает эти же функции.

ОТКУДА ВЗЯТА МАТРИЦА (важно для ревью Заказчиком). Полномочия ролей по
операциям с НРД в переданных материалах ТЗ поимённо не расписаны, поэтому
матрица ниже **выведена** из трёх источников, а не придумана:

1. Ограничение по ДСП — дословно из прежнего
   `NormativeDocumentAdmin._can_access`: документ уровня «ДСП» доступен
   только при `dsp_access` или суперпользователю. Поведение сохранено
   без изменений, включая фильтрацию списка.
2. Кто правит и публикует — из `TemplateAdmin._can_manage`
   (`CONTROLLER_LAWYER` + `ADMINISTRATOR`) и из STACK.md, где решение о
   юридически значимом действии над документом закреплено за
   Контролёром/Юристом.
3. Методист подразделения — из `ROLE_PRIVILEGE_ORDER`: роль заведена
   между Читателем и Контролёром/Юристом, то есть содержательно это
   «готовит документы, но не придаёт им силу». Отсюда право вести
   черновик без права публикации.

**Требует подтверждения Заказчиком** — это интерпретация, а не цитата из
ТЗ; спорные места, если они появятся, меняются здесь, в одном месте, без
правки вьюх и админки.
"""
from __future__ import annotations

from .models import NormativeDocument

def _editor_roles():
    """Роли читаются лениво (импорт apps.iam.models внутри функции), чтобы
    этот модуль можно было импортировать из admin/вьюх на любом этапе
    загрузки приложений — apps.documents.models уже импортирован выше,
    а вот apps.iam на момент импорта admin.py готов не всегда."""
    from apps.iam.models import User

    return {User.Role.METHODIST, User.Role.CONTROLLER_LAWYER, User.Role.ADMINISTRATOR}


def _publisher_roles():
    from apps.iam.models import User

    return {User.Role.CONTROLLER_LAWYER, User.Role.ADMINISTRATOR}


def _administrator_roles():
    from apps.iam.models import User

    return {User.Role.ADMINISTRATOR}


def _has_role(user, allowed):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.role in allowed


def can_view_dsp(user) -> bool:
    """Допуск к документам «ДСП» (ТЗ 4.2.1, гриф доступа).

    Не роль, а отдельный признак учётной записи (`User.dsp_access`):
    допуск оформляется независимо от должности, поэтому Читатель с
    допуском видит ДСП, а Методист без допуска — нет.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_superuser or user.dsp_access)


def can_view_document(user, document) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if document.access_level == NormativeDocument.AccessLevel.GENERAL:
        return True
    return can_view_dsp(user)


def can_edit_document(user, document=None) -> bool:
    """Право вести карточку. Документ, уже имеющий силу, правится не
    редактированием, а новой редакцией со связью версионности (ТЗ 4.2.2)
    — поэтому редактирование разрешено только черновику."""
    if not _has_role(user, _editor_roles()):
        return False
    if document is not None:
        if not can_view_document(user, document):
            return False
        if document.status != NormativeDocument.Status.DRAFT:
            return False
    return True


def can_change_status(user, document=None, new_status=None) -> bool:
    """Публикация, отмена, перевод в архив — юридически значимое действие
    (см. STACK.md: решение остаётся за Контролёром/Юристом).

    Без `new_status` отвечает на вопрос «может ли пользователь менять
    статус в принципе» — этого достаточно, чтобы решить, показывать ли
    кнопку. С указанным `new_status` проверяется конкретный переход:
    исправление ошибки публикации (откат в черновик и аннулирование)
    оставлено Администратору, см. `transitions.ADMINISTRATOR_ONLY_TARGETS`.
    """
    if not _has_role(user, _publisher_roles()):
        return False
    if document is not None and not can_view_document(user, document):
        return False
    if new_status is not None:
        from . import transitions

        if transitions.requires_administrator(new_status):
            return _has_role(user, _administrator_roles())
    return True


def can_manage_relations(user, document=None) -> bool:
    """Право заводить и снимать рёбра графа версионности (ТЗ 4.2.2).

    ОТКУДА ВЗЯТО (как и вся матрица выше — вывод, не цитата). Круг ролей
    тот же, что у `can_edit_document`: связь версионности — часть
    подготовки документа, а не придание ему силы.

    Но, в отличие от правки полей, **статус документа здесь не
    ограничивает**. Связь описывает отношение между двумя карточками, а
    не содержание одной: «Отменяет 87-р» заводится на новом документе,
    который в этот момент ещё черновик, — а вот забытую ссылку на
    смежный НРД приходится дописывать и к уже действующему. Запрет по
    статусу означал бы, что единственный способ исправить граф после
    публикации — админка, то есть ровно тот обход рабочего места, от
    которого Этап 2 и уходит.
    """
    if not _has_role(user, _editor_roles()):
        return False
    return document is None or can_view_document(user, document)


def visible_documents(user, queryset=None):
    """Базовый queryset карточек, доступных пользователю.

    Единая точка для списка, карточки и админки: скрывать ДСП фильтром в
    одном месте и проверять правом в другом — верный способ разойтись.
    Неаутентифицированный пользователь не получает ничего (вьюхи и так
    закрыты LoginRequiredMixin, но пустой queryset — более безопасный
    ответ, чем исключение, если функцию вызовут откуда-то ещё).
    """
    queryset = NormativeDocument.objects.all() if queryset is None else queryset
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if can_view_dsp(user):
        return queryset
    return queryset.filter(access_level=NormativeDocument.AccessLevel.GENERAL)
