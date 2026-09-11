"""
Разделение файлового хранилища по типам объектов (STACK.md → «Разделение
политик хранения MinIO»).

`originals` — финальный WORM-бакет. В нём находятся сканы НРД
(`files_original`) и опубликованные версии бланков
(`Template.file_editable`, `Template.file_sample`). Эти FileField указывают
на `originals_storage`, но новые байты НЕ пишутся туда напрямую из
`FileField.pre_save`: `apps.core.staged_files` перехватывает uncommitted upload,
кладёт его в mutable `working/staging/worm/`, коммитит final key + durable
promotion intent в PostgreSQL и только после commit копирует объект в
`originals` с Object Lock headers. Поэтому rollback никогда не требует DELETE
из WORM.

`working` — mutable + Versioning. Помимо обычной редактируемой копии карточки
НРД (`files_editable`) он содержит короткоживущий staging-префикс для будущих
immutable объектов. Успешный promote удаляет staging сразу; MinIO lifecycle
страховочно очищает забытые/rollback staging-версии.

Storage передаётся в FileField как callable (а не готовый инстанс) — так путь
до функции сохраняется в миграциях как стабильная ссылка вместо попытки
сериализовать сам объект хранилища (см. документацию Django по
`FileField.storage`).
"""
from django.core.files.storage import storages


def originals_storage():
    return storages["originals"]


def working_storage():
    return storages["working"]
