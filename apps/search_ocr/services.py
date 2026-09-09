"""
Импорт тезауруса Smart Search (ТЗ 4.4.1) — источник данных, формат и
бо́льшая часть правил валидации заданы самим файлом
docs/thesaurus/thesaurus_v0.9_draft.json (meta.schema, validation_rules
TH-01..TH-08, loading_notes: "upsert по id; при повторной загрузке
обновляются synonyms, canonical и weight" + "audit_event:
THESAURUS_UPDATED (WORM-журнал) с указанием id записей, оператора и
diff"). Какие TH-правила проверяются на уровне модели, а какие здесь —
см. docstring ThesaurusEntry (apps/search_ocr/models.py).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.iam.models import User

from .models import ThesaurusCategory, ThesaurusEntry, ThesaurusService, ThesaurusStatus

# Поля записи, которые реально копируются из JSON в модель — дословно
# meta.schema файла, без id (это PK, обрабатывается отдельно).
_ENTRY_FIELD_DEFAULTS = {
    "canonical": "",
    "category": None,
    "service": None,
    "short_forms": list,
    "synonyms": list,
    "synonyms_legacy": list,
    "weight": 1.0,
    "source": "",
    "ambiguous": False,
    "status": ThesaurusStatus.DRAFT,
}

MAX_THESAURUS_FILE_BYTES = 10 * 1024 * 1024
MAX_THESAURUS_ENTRIES = 10_000


@dataclass
class ThesaurusImportReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    # Существующая запись, для которой файл не принёс ни одного изменения —
    # намеренно не проходит ни в updated, ни в created (иначе повторный
    # запуск с тем же файлом писал бы в WORM-аудит шум без единого
    # реального изменения). Считается отдельно, чтобы total сходился с
    # реальным числом записей в файле.
    unchanged: list[str] = field(default_factory=list)
    # TH-02/TH-03/TH-08 — правила про файл/датасет целиком, не про одну
    # запись; не блокируют импорт, только сообщаются оператору.
    warnings: list[str] = field(default_factory=list)
    diffs: dict[str, dict] = field(default_factory=dict)

    @property
    def total(self):
        return len(self.created) + len(self.updated) + len(self.errors) + len(self.unchanged)


def _normalize(text: str) -> str:
    return text.strip().lower()


def _format_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    if isinstance(exc, IntegrityError):
        # Гонка параллельного импорта/ручного редактирования в админке —
        # то же рассуждение, что и в apps.iam.services._format_error.
        return "Конфликт уникальности (canonical или id) — запись изменена параллельно, повторите импорт."
    return str(exc)


def _check_file_level_rules(data: dict, report: ThesaurusImportReport) -> None:
    entries = data.get("entries", [])

    if len(entries) < 150:
        report.warnings.append(f"TH-08: в файле {len(entries)} записей — меньше рекомендуемых 150.")

    # TH-02: short_form не должен совпадать с canonical ДРУГОЙ записи, если
    # пара не зарегистрирована в ambiguity_registry. Файл не формализует
    # понятие "зарегистрирована" до уровня конкретных id-пар — здесь
    # проверяется совпадение нормализованной формы short_form с
    # нормализованной abbr из ambiguity_registry; когда её там нет —
    # предупреждение, а не отказ в импорте (см. модуль docstring).
    canonical_by_norm: dict[str, str] = {}
    for e in entries:
        canonical = e.get("canonical")
        entry_id = e.get("id")
        if canonical and entry_id:
            canonical_by_norm.setdefault(_normalize(canonical), entry_id)

    registered_abbrs = {_normalize(a["abbr"]) for a in data.get("ambiguity_registry", []) if a.get("abbr")}

    for e in entries:
        entry_id = e.get("id")
        for sf in e.get("short_forms", []) or []:
            norm = _normalize(sf)
            match_id = canonical_by_norm.get(norm)
            if match_id and match_id != entry_id and norm not in registered_abbrs:
                report.warnings.append(
                    f"TH-02: short_form {sf!r} записи {entry_id!r} совпадает с canonical "
                    f"записи {match_id!r} и не зарегистрирован в ambiguity_registry."
                )


def import_thesaurus(file_obj, *, actor: User | None = None) -> ThesaurusImportReport:
    """file_obj — путь (str/Path) или файлоподобный объект с JSON тезауруса."""
    if hasattr(file_obj, "read"):
        raw = file_obj.read(MAX_THESAURUS_FILE_BYTES + 1)
        if len(raw) > MAX_THESAURUS_FILE_BYTES:
            raise ValidationError("Файл тезауруса слишком большой (максимум 10 МБ).")
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    else:
        path = Path(file_obj)
        if path.stat().st_size > MAX_THESAURUS_FILE_BYTES:
            raise ValidationError("Файл тезауруса слишком большой (максимум 10 МБ).")
        text = path.read_text(encoding="utf-8")

    data = json.loads(text)
    entries = data.get("entries", [])
    if not entries:
        raise ValidationError("Файл не содержит записей (entries).")
    if len(entries) > MAX_THESAURUS_ENTRIES:
        raise ValidationError(f"В файле слишком много записей (максимум {MAX_THESAURUS_ENTRIES}).")

    report = ThesaurusImportReport()
    _check_file_level_rules(data, report)

    ids_seen: set[str] = set()

    for entry in entries:
        entry_id = entry.get("id")
        is_update = False
        changed: dict[str, list] = {}
        try:
            with transaction.atomic():
                if not entry_id:
                    raise ValueError("Запись без id.")
                if entry_id in ids_seen:
                    raise ValueError(f"Дублирующийся id внутри файла: {entry_id!r}.")
                ids_seen.add(entry_id)

                category = entry.get("category")
                if category not in ThesaurusCategory.values:
                    raise ValueError(f"Неизвестная категория {category!r} (TH-05).")

                service = entry.get("service")
                if service is not None and service not in ThesaurusService.values:
                    raise ValueError(f"Неизвестная служба {service!r} (TH-05).")

                existing = ThesaurusEntry.objects.filter(pk=entry_id).first()
                is_update = existing is not None
                obj = existing if existing is not None else ThesaurusEntry(id=entry_id)

                for field_name, default in _ENTRY_FIELD_DEFAULTS.items():
                    default_value = default() if callable(default) else default
                    new_value = entry.get(field_name, default_value)
                    if field_name in ("short_forms", "synonyms", "synonyms_legacy"):
                        new_value = list(new_value or [])
                    old_value = getattr(obj, field_name) if is_update else None
                    if is_update and old_value != new_value:
                        changed[field_name] = [old_value, new_value]
                    setattr(obj, field_name, new_value)

                obj.save()
        except (ValueError, ValidationError, IntegrityError) as exc:
            report.errors.append((entry_id or "?", _format_error(exc)))
            continue

        if is_update:
            if changed:
                report.updated.append(entry_id)
                report.diffs[entry_id] = changed
            else:
                report.unchanged.append(entry_id)
        else:
            report.created.append(entry_id)

    if report.created or report.updated:
        AuditLog.objects.create(
            event_type=AuditLog.EventType.THESAURUS_UPDATED,
            actor=actor,
            actor_personnel_number=actor.personnel_number if actor else "",
            object_type="ThesaurusEntry",
            object_id="bulk_import",
            details={
                "created": report.created,
                "updated": report.updated,
                "diffs": report.diffs,
            },
        )

    return report
