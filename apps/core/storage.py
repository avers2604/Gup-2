"""
Разделение файлового хранилища по типам объектов (STACK.md → «Разделение
политик хранения MinIO»).

Оригиналы НРД (`files_original`) должны быть защищены от изменения —
бакет `originals` создаётся с Object Locking (WORM). Редактируемые копии
и бланки (`files_editable`, `Template.file_editable`, `Template.file_sample`)
по ТЗ 4.3.1 правомерно заменяются при минорной корректировке — бакет
`working` без блокировки. Держать оба типа в одном бакете с одной
retention-политикой нельзя: строгий Object Lock на общем бакете сломает
замену файла бланка при корректировке.

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
