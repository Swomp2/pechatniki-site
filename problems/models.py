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

    image = models.ImageField(
        "Фото",
        upload_to="problem_photos/",
    )

    uploaded_at = models.DateTimeField("Загружено", auto_now_add=True)

    class Meta:
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

    session_key = models.CharField("Ключ сессии", max_length=40)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        # По session_key быстро строим список уже отмеченных проблем для кнопок.
        indexes = [
            models.Index(fields=["session_key"], name="problem_vote_session_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "session_key"],
                name="unique_problem_vote_per_session",
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

    file = models.FileField(
        "Файл с ответом, отказом или отпиской",
        upload_to="problem_evidence/",
    )

    original_name = models.CharField(
        "Исходное имя файла",
        max_length=255,
        blank=True,
    )

    uploaded_at = models.DateTimeField(
        "Дата загрузки",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Файл по предыдущему обращению"
        verbose_name_plural = "Файлы по предыдущим обращениям"

    def __str__(self):
        return self.original_name or self.file.name
