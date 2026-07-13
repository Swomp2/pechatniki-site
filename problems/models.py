import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .encryption import EncryptedTextField
from .phone import validate_russian_phone


class Problem(models.Model):
    # NEW остаётся внутренним статусом модерации. Публично рабочими считаются
    # только SENT и IN_PROGRESS: именно за них разрешено голосовать.
    class Status(models.TextChoices):
        NEW = "new", "Новое"
        IN_PROGRESS = "in_progress", "В работе"
        SENT = "sent", "Отправлено"
        RESOLVED = "resolved", "Решено"
        REJECTED = "rejected", "Отклонено"

    class Category(models.TextChoices):
        YARD = "yard", "Двор"
        ROAD = "road", "Дорога"
        GARBAGE = "garbage", "Мусор"
        LIGHTING = "lighting", "Освещение"
        ENTRANCE = "entrance", "Подъезд"
        OTHER = "other", "Другое"

    title = models.CharField("Заголовок", max_length=200)
    description = models.TextField("Описание")
    address = models.CharField("Адрес или место", max_length=300, blank=True)

    category = models.CharField(
        "Категория",
        max_length=30,
        choices=Category.choices,
        default=Category.OTHER,
    )

    status = models.CharField(
        "Статус",
        max_length=30,
        choices=Status.choices,
        default=Status.NEW,
    )

    # Телефон нужен только модераторам в админке. В базе он хранится в
    # зашифрованном виде и не выводится в публичных шаблонах.
    contact_phone = EncryptedTextField(
        "Телефон",
        blank=True,
        validators=[validate_russian_phone],
    )

    is_public = models.BooleanField("Сделать публичным?", default=False)
    rejection_reason = models.TextField(
        "Причина отклонения",
        blank=True,
    )
    # Денормализованный счётчик нужен для быстрой сортировки публичного списка.
    # Источник правды для уникальности голоса — ProblemVote.
    votes_count = models.PositiveIntegerField("Важность", default=0)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    has_prior_attempts = models.BooleanField(
        "Житель уже пытался решить проблему через другие инстанции",
        default=False,
    )

    class Meta:
        ordering = ["-created_at"]
        # Индексы покрывают основные публичные списки: активные, решённые,
        # отклонённые и сортировку активных по важности.
        indexes = [
            models.Index(
                fields=["is_public", "status", "-created_at"],
                name="problem_public_status_idx",
            ),
            models.Index(
                fields=["is_public", "status", "-votes_count", "created_at"],
                name="problem_public_votes_idx",
            ),
        ]
        permissions = [
            (
                "view_problem_contact_phone",
                "Can view decrypted problem contact phone",
            ),
        ]
        verbose_name = "Обращение"
        verbose_name_plural = "Обращения"

    def __str__(self):
        return self.title

    def clean(self):
        if self.status == self.Status.REJECTED and not self.rejection_reason.strip():
            raise ValidationError(
                {
                    "rejection_reason": "Для отклонения заявки необходимо указать причину!!"
                }
            )


class ProblemPhoto(models.Model):
    # related_name="photos" даёт читаемый доступ problem.photos.all()
    # и используется в prefetch_related на публичных списках.
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Обращение",
    )

    # public_id отделяет внешний идентификатор вложения от pk и физического пути.
    # Сам по себе UUID не авторизует доступ: каждый запрос всё равно проверяет view.
    public_id = models.UUIDField(
        "Публичный идентификатор",
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    image = models.ImageField(
        "Фото",
        upload_to="problem_photos/",
    )

    content_type = models.CharField("MIME-тип", max_length=120, blank=True)
    file_size = models.PositiveBigIntegerField("Размер файла", default=0)
    uploaded_at = models.DateTimeField("Загружено", auto_now_add=True)

    class Meta:
        permissions = [
            (
                "view_private_problem_photo",
                "Can view private problem photos",
            ),
            (
                "download_problem_photo",
                "Can download problem photos",
            ),
        ]
        verbose_name = "Фото проблемы"
        verbose_name_plural = "Фото проблем"

    def __str__(self):
        return f"Фото для {self.problem}"


class ProblemVote(models.Model):
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name="votes",
        verbose_name="Важность",
    )

    # Исторически голос был привязан к Django session_key. Поле оставлено для
    # мягкой миграции старых записей, но новые проверки используют voter_hash.
    session_key = models.CharField("Ключ сессии", max_length=40, blank=True)
    voter_hash = models.CharField(
        "HMAC браузерного идентификатора",
        max_length=64,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        # По voter_hash быстро строим список уже отмеченных проблем для кнопок.
        # Открытый cookie-токен в базе не храним: только HMAC серверным ключом.
        indexes = [
            models.Index(fields=["session_key"], name="problem_vote_session_idx"),
            models.Index(fields=["voter_hash"], name="problem_vote_voter_hash_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "session_key"],
                name="unique_problem_vote_per_session",
                condition=models.Q(session_key__gt=""),
            ),
            models.UniqueConstraint(
                fields=["problem", "voter_hash"],
                name="unique_problem_vote_per_voter_hash",
                condition=models.Q(voter_hash__isnull=False),
            )
        ]

        verbose_name = "Голос за проблему"
        verbose_name_plural = "Голоса за проблему"

    def __str__(self):
        return f"+1 для {self.problem}"


class ProblemEvidenceFile(models.Model):
    # Файлы доказательств видны модераторам, но не выводятся в публичных списках:
    # там могут быть персональные данные из ответов ведомств.
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name="evidence_files",
        verbose_name="Проблема",
    )

    # UUID используется только как непрозрачный идентификатор записи.
    # Он не заменяет permission check и не раскрывает путь в MEDIA_ROOT.
    public_id = models.UUIDField(
        "Публичный идентификатор",
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    file = models.FileField(
        "Файл с ответом, отказом или отпиской",
        upload_to="problem_evidence/",
    )

    original_name = models.CharField(
        "Исходное имя файла",
        max_length=255,
        blank=True,
    )

    content_type = models.CharField("MIME-тип", max_length=120, blank=True)
    file_size = models.PositiveBigIntegerField("Размер файла", default=0)

    uploaded_at = models.DateTimeField(
        "Дата загрузки",
        auto_now_add=True,
    )

    class Meta:
        permissions = [
            (
                "view_problem_evidence_file",
                "Can view problem evidence files",
            ),
            (
                "download_problem_evidence_file",
                "Can download problem evidence files",
            ),
        ]
        verbose_name = "Файл по предыдущему обращению"
        verbose_name_plural = "Файлы по предыдущим обращениям"

    def __str__(self):
        return self.original_name or self.file.name


class AttachmentAccessAudit(models.Model):
    class AttachmentType(models.TextChoices):
        PHOTO = "photo", "Фото"
        EVIDENCE = "evidence", "Документ"

    class Action(models.TextChoices):
        VIEW = "view", "Просмотр"
        DOWNLOAD = "download", "Скачивание"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Пользователь",
    )
    attachment_type = models.CharField(
        "Тип вложения",
        max_length=20,
        choices=AttachmentType.choices,
    )
    attachment_public_id = models.UUIDField("Идентификатор вложения")
    problem_id = models.PositiveBigIntegerField("ID обращения")
    action = models.CharField("Действие", max_length=20, choices=Action.choices)
    success = models.BooleanField("Успешно", default=False)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            (
                "view_attachment_access_audit",
                "Can view attachment access audit",
            ),
        ]
        verbose_name = "Аудит доступа к вложению"
        verbose_name_plural = "Аудит доступа к вложениям"

    def __str__(self):
        return f"{self.get_action_display()} {self.attachment_public_id}"
