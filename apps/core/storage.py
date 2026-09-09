"""
Разделение файлового хранилища по типам объектов (STACK.md → «Разделение
политик хранения MinIO»).

Оригиналы НРД (`files_original`) и бланки (`Template.file_editable`,
`Template.file_sample`) хранятся в защищённом от изменения бакете
`originals` (Object Locking, WORM) — по ТЗ 4.3.1 версия бланка
инкрементируется даже для минорной корректировки, то есть каждая строка
Template с самого начала является опубликованным, неизменяемым
артефактом (двухступенчатая модель, см. docstring apps.templates_bank.models.Template).
Редактируемая копия карточки НРД (`files_editable`) — рабочий,
неопубликованный файл, который правомерно заменяется без версионирования
и потому лежит в незаблокированном бакете `working`. Держать оба типа в
одном бакете с одной retention-политикой нельзя: строгий Object Lock на
общем бакете сломает замену рабочего файла.

Storage передаётся в FileField как callable (а не готовый инстанс) —
так путь до функции сохраняется в миграциях как стабильная ссылка вместо
попытки сериализовать сам объект хранилища (см. документацию Django по
`FileField.storage`).
"""
from django.core.files.storage import storages


def originals_storage():
    return storages["originals"]


def working_storage():
    return storages["working"]
