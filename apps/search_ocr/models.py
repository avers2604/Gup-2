from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TimeStampedModel

from .normalization import normalize_term


class ThesaurusCategory(models.TextChoices):
    """Дословно meta.categories из docs/thesaurus/thesaurus_v0.9_draft.json.

    Продублировано в Python, а не читается из файла на лету, чтобы
    ThesaurusEntry.category оставалось обычным полем с валидацией на
    уровне Django (choices), а не завязывалось на чтение JSON при каждом
    импорте модели. Расплата за это — риск дрейфа, если файл когда-нибудь
    получит новую категорию без обновления этого класса; от него защищает
    ThesaurusFileIntegrityTests.test_python_categories_match_json_meta
    (тест сравнивает этот список с meta.categories файла построчно)."""

    ORG_L1 = "org.l1", "Аппарат управления (уровень 1)"
    ORG_L2 = "org.l2", "Отраслевые службы (уровень 2)"
    ORG_L3_DEPOT = "org.l3.depot", "Обособленные парки (уровень 3)"
    ORG_L3_DISTRICT = "org.l3.district", "Районы и дистанции (уровень 3)"
    ORG_L4 = "org.l4", "Линейные подразделения (уровень 4)"
    DOC_TYPE = "doc.type", "Типы нормативно-распорядительных документов"
    DOC_STATUS = "doc.status", "Статусы жизненного цикла документа"
    DOC_FORM = "doc.form", "Бланки, формы актов и протоколов"
    TECH_VEHICLE = "tech.vehicle", "Подвижной состав и изготовители"
    TECH_ENERGY = "tech.energy", "Энергохозяйство и контактная сеть"
    TECH_TRACK = "tech.track", "Путевое хозяйство"
    INCIDENT = "incident", "Происшествия и инциденты"
    HSE = "hse", "Охрана труда, промышленная и пожарная безопасность, БДД"
    ROLE_PERSON = "role.person", "Должности линейного и инженерного персонала"
    ROLE_AIS = "role.ais", "Роли в АИС «БЗ ГЭТ»"
    PROCESS = "process", "Производственные процессы"
    AIS_SYSTEM = "ais.system", "Термины самой АИС"
    REG_EXTERNAL = "reg.external", "Внешние нормативные акты"


class ThesaurusService(models.TextChoices):
    """Дословно meta.services файла (без ключа null — он означает
    «общепредприятийский термин» и выражается в ThesaurusEntry как
    service=None, а не отдельным кодом справочника)."""

    SD = "SD", "Служба движения"
    SPS = "SPS", "Служба подвижного состава"
    EKH = "EKH", "Энергохозяйство (Служба ЭХ)"
    SPUT = "SPUT", "Служба пути"
    ASIT = "ASIT", "Служба АСиИТ"
    OTPB = "OTPB", "Служба ОТ, ПБ и БДД"
    IB = "IB", "Служба информационной безопасности"
    APPARAT = "APPARAT", "Аппарат управления"
    YUR = "YUR", "Юридический отдел"


class ThesaurusStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    VERIFIED = "verified", "Подтверждено"
    REJECTED = "rejected", "Отклонено"


class ThesaurusEntry(TimeStampedModel):
    """Словарная статья Smart Search (ТЗ 4.4.1) — импортируется из
    docs/thesaurus/thesaurus_v0.9_draft.json management-командой
    import_thesaurus (apps/search_ocr/management/commands/).

    Поля и большая часть правил валидации (validation_rules файла,
    коды TH-01..TH-08) взяты дословно из самого файла — он специфицирует
    и данные, и контракт их проверки. Что из TH-правил реализовано и где:

    - TH-01 (canonical уникален без учёта регистра) — UniqueConstraint по
      Lower(canonical) на уровне БД.
    - TH-04 (хотя бы один short_form или synonym) — clean().
    - TH-05 (category/service из допустимого перечня) — Django choices;
      двойная проверка на актуальность самого перечня — см. docstring
      ThesaurusCategory/ThesaurusService.
    - TH-06 (weight в [0.0, 1.0]) — валидаторы поля.
    - TH-02/TH-03 (конфликты аббревиатур с ambiguity_registry) и TH-08
      (минимум 150 записей) — правила про файл/датасет ЦЕЛИКОМ, а не про
      одну запись; проверяются в import_thesaurus.py на уровне всего
      импорта, а не здесь (см. команду).
    - TH-07 (verified только после подтверждения уполномоченной ролью —
      исторически «куратором», роль упразднена, функционал унаследован
      Контролёром/Юристом, см. apps/iam/models.py) — организационное
      правило, не выражаемое в проверке одной модели без контекста
      «кто сейчас выполняет запрос» (нет вызывающего пользователя на
      уровне save()); реальное ограничение по ролям — задача Этапа 2 (admin
      permissions/API), здесь НЕ проверяется — оставлено как честно
      не реализованное, а не тихо проигнорированное."""

    id = models.SlugField(primary_key=True, max_length=100, verbose_name="Идентификатор записи")
    canonical = models.CharField(max_length=500, verbose_name="Каноническая форма термина")
    category = models.CharField(max_length=32, choices=ThesaurusCategory.choices, verbose_name="Категория")
    service = models.CharField(
        max_length=16, choices=ThesaurusService.choices, null=True, blank=True,
        verbose_name="Служба-владелец (null = общепредприятийский термин)",
    )
    short_forms = ArrayField(
        models.CharField(max_length=100), blank=True, default=list, verbose_name="Аббревиатуры и шифры",
    )
    synonyms = ArrayField(
        models.CharField(max_length=200), blank=True, default=list, verbose_name="Синонимы",
    )
    synonyms_legacy = ArrayField(
        models.CharField(max_length=200), blank=True, default=list,
        verbose_name="Устаревшие обозначения (вес 0.3, только для поиска по историческому фонду)",
    )
    weight = models.FloatField(
        default=1.0, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        verbose_name="Вес расширения запроса",
    )
    source = models.CharField(max_length=255, blank=True, verbose_name="Источник термина")
    ambiguous = models.BooleanField(
        default=False, verbose_name="Аббревиатура конфликтует с другой записью (см. ambiguity_registry файла)",
    )
    status = models.CharField(max_length=16, choices=ThesaurusStatus.choices, default=ThesaurusStatus.DRAFT)

    class Meta:
        verbose_name = "Словарная статья тезауруса"
        verbose_name_plural = "Тезаурус (Smart Search)"
        ordering = ["canonical"]
        constraints = [
            models.UniqueConstraint(Lower("canonical"), name="unique_thesaurus_canonical_ci"),
        ]
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["service"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.canonical

    def clean(self):
        super().clean()
        if not self.short_forms and not self.synonyms:
            raise ValidationError(
                "Запись должна содержать хотя бы одну аббревиатуру (short_forms) "
                "или синоним (synonyms) — TH-04."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ThesaurusAmbiguity(TimeStampedModel):
    """Персистентный реестр неоднозначных аббревиатур — ambiguity_registry
    файла тезауруса (docs/thesaurus/thesaurus_v0.9_draft.json), например
    «ТП» -> тяговая подстанция (вес 1.0) / транспортное происшествие
    (0.6) / трамвайный парк, legacy (0.25). Наполняется import_thesaurus()
    (upsert по abbr_normalized), читается apps.search_ocr.search при
    разрешении неоднозначности в расширении запроса.

    Раньше (до модуля поиска) реестр использовался только транзитно при
    импорте — для предупреждений TH-02/TH-03, сам не сохранялся (см.
    STACK.md, раздел про тезаурус: «когда появится модуль поиска, реестр
    нужно будет либо читать из того же JSON при индексации, либо
    перенести в отдельную модель — это решение стоит принимать вместе с
    дизайном самого поискового модуля»). Этот момент настал — решение:
    отдельная модель, а не чтение JSON-файла на каждый поисковый запрос
    (тот же файл и так уже целиком читается один раз при импорте).

    `disambiguation` — человекочитаемое правило файла (например «при
    фильтре service=EKH → тяговая подстанция»), хранится как есть для
    отображения/аудита, но НЕ разбирается программно (произвольная
    русская проза, не формализованный DSL) — реальное разрешение
    неоднозначности в search.py использует структурированную часть
    (`candidates[].weight` + сопоставление факультативных фасетов
    category/service с полями самих ThesaurusEntry-кандидатов), а не
    парсинг этого текста. Честная граница, а не недосмотр."""

    abbr = models.CharField(max_length=100, verbose_name="Аббревиатура (как в файле)")
    abbr_normalized = models.CharField(
        max_length=100, unique=True, editable=False,
        verbose_name="Аббревиатура (нормализованная — ключ upsert/поиска)",
    )
    candidates = models.JSONField(
        default=list, verbose_name="Кандидаты расшифровки: [{id, weight, reason}, ...]",
    )
    disambiguation = models.TextField(
        blank=True, verbose_name="Правило разрешения неоднозначности (свободный текст файла)",
    )

    class Meta:
        verbose_name = "Неоднозначная аббревиатура"
        verbose_name_plural = "Реестр неоднозначностей тезауруса"
        ordering = ["abbr"]

    def __str__(self):
        return self.abbr

    def save(self, *args, **kwargs):
        self.abbr_normalized = normalize_term(self.abbr)
        super().save(*args, **kwargs)
