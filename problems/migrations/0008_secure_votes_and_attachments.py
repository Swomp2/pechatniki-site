import hashlib
import hmac
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def fill_attachment_public_ids(apps, schema_editor):
    for model_name in ("ProblemPhoto", "ProblemEvidenceFile"):
        model = apps.get_model("problems", model_name)

        for attachment in model.objects.filter(public_id__isnull=True):
            attachment.public_id = uuid.uuid4()
            attachment.save(update_fields=["public_id"])


def fill_vote_hashes(apps, schema_editor):
    problem_vote = apps.get_model("problems", "ProblemVote")
    key = settings.PROBLEM_VOTER_HMAC_KEY.encode("utf-8")

    for vote in problem_vote.objects.filter(
        voter_hash__isnull=True,
        session_key__gt="",
    ):
        vote.voter_hash = hmac.new(
            key,
            vote.session_key.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        vote.save(update_fields=["voter_hash"])


class Migration(migrations.Migration):
    dependencies = [
        ("problems", "0007_problem_contact_phone_permission"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="problemevidencefile",
            name="public_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Публичный идентификатор",
            ),
        ),
        migrations.AddField(
            model_name="problemphoto",
            name="public_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Публичный идентификатор",
            ),
        ),
        migrations.RunPython(fill_attachment_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="problemevidencefile",
            name="public_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="Публичный идентификатор",
            ),
        ),
        migrations.AlterField(
            model_name="problemphoto",
            name="public_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="Публичный идентификатор",
            ),
        ),
        migrations.AddField(
            model_name="problemevidencefile",
            name="content_type",
            field=models.CharField(blank=True, max_length=120, verbose_name="MIME-тип"),
        ),
        migrations.AddField(
            model_name="problemevidencefile",
            name="file_size",
            field=models.PositiveBigIntegerField(default=0, verbose_name="Размер файла"),
        ),
        migrations.AddField(
            model_name="problemphoto",
            name="content_type",
            field=models.CharField(blank=True, max_length=120, verbose_name="MIME-тип"),
        ),
        migrations.AddField(
            model_name="problemphoto",
            name="file_size",
            field=models.PositiveBigIntegerField(default=0, verbose_name="Размер файла"),
        ),
        migrations.AddField(
            model_name="problemvote",
            name="voter_hash",
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                verbose_name="HMAC браузерного идентификатора",
            ),
        ),
        migrations.AlterField(
            model_name="problemvote",
            name="session_key",
            field=models.CharField(blank=True, max_length=40, verbose_name="Ключ сессии"),
        ),
        migrations.RunPython(fill_vote_hashes, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="problemvote",
            name="unique_problem_vote_per_session",
        ),
        migrations.AddIndex(
            model_name="problemvote",
            index=models.Index(
                fields=["voter_hash"],
                name="problem_vote_voter_hash_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="problemvote",
            constraint=models.UniqueConstraint(
                condition=models.Q(session_key__gt=""),
                fields=("problem", "session_key"),
                name="unique_problem_vote_per_session",
            ),
        ),
        migrations.AddConstraint(
            model_name="problemvote",
            constraint=models.UniqueConstraint(
                condition=models.Q(voter_hash__isnull=False),
                fields=("problem", "voter_hash"),
                name="unique_problem_vote_per_voter_hash",
            ),
        ),
        migrations.CreateModel(
            name="AttachmentAccessAudit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "attachment_type",
                    models.CharField(
                        choices=[
                            ("photo", "Фото"),
                            ("evidence", "Документ"),
                        ],
                        max_length=20,
                        verbose_name="Тип вложения",
                    ),
                ),
                (
                    "attachment_public_id",
                    models.UUIDField(verbose_name="Идентификатор вложения"),
                ),
                (
                    "problem_id",
                    models.PositiveBigIntegerField(verbose_name="ID обращения"),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("view", "Просмотр"),
                            ("download", "Скачивание"),
                        ],
                        max_length=20,
                        verbose_name="Действие",
                    ),
                ),
                (
                    "success",
                    models.BooleanField(default=False, verbose_name="Успешно"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Дата"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Аудит доступа к вложению",
                "verbose_name_plural": "Аудит доступа к вложениям",
                "ordering": ["-created_at"],
                "permissions": [
                    (
                        "view_attachment_access_audit",
                        "Can view attachment access audit",
                    ),
                ],
            },
        ),
        migrations.AlterModelOptions(
            name="problemevidencefile",
            options={
                "verbose_name": "Файл по предыдущему обращению",
                "verbose_name_plural": "Файлы по предыдущим обращениям",
                "permissions": [
                    (
                        "view_problem_evidence_file",
                        "Can view problem evidence files",
                    ),
                    (
                        "download_problem_evidence_file",
                        "Can download problem evidence files",
                    ),
                ],
            },
        ),
        migrations.AlterModelOptions(
            name="problemphoto",
            options={
                "verbose_name": "Фото проблемы",
                "verbose_name_plural": "Фото проблем",
                "permissions": [
                    (
                        "view_private_problem_photo",
                        "Can view private problem photos",
                    ),
                    (
                        "download_problem_photo",
                        "Can download problem photos",
                    ),
                ],
            },
        ),
    ]
