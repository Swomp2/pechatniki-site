from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import ProblemEvidenceFile, ProblemPhoto


def schedule_file_delete(file_field):
    """
    Удаляет физический файл после успешного коммита транзакции.

    Важно удалять файл именно через transaction.on_commit():
    если БД-операция откатится, файл не должен исчезнуть с диска раньше времени.
    """
    if not file_field:
        return

    file_name = getattr(file_field, "name", "")

    if not file_name:
        return

    storage = file_field.storage

    def delete_file():
        if storage.exists(file_name):
            storage.delete(file_name)

    transaction.on_commit(delete_file)


def delete_replaced_file(instance, field_name):
    """
    Удаляет старый файл, если в админке его заменили новым.

    Без этого при замене фотографии/документа старая версия останется лежать
    в media/, хотя запись в БД уже будет ссылаться на новый файл.
    """
    if not instance.pk:
        return

    model = type(instance)

    try:
        old_instance = model.objects.only(field_name).get(pk=instance.pk)
    except model.DoesNotExist:
        return

    old_file = getattr(old_instance, field_name)
    new_file = getattr(instance, field_name)

    old_file_name = getattr(old_file, "name", "")
    new_file_name = getattr(new_file, "name", "")

    if old_file_name and old_file_name != new_file_name:
        schedule_file_delete(old_file)


@receiver(pre_save, sender=ProblemPhoto)
def delete_old_problem_photo_on_replace(sender, instance, **kwargs):
    delete_replaced_file(instance, "image")


@receiver(post_delete, sender=ProblemPhoto)
def delete_problem_photo_file(sender, instance, **kwargs):
    schedule_file_delete(instance.image)


@receiver(pre_save, sender=ProblemEvidenceFile)
def delete_old_evidence_file_on_replace(sender, instance, **kwargs):
    delete_replaced_file(instance, "file")


@receiver(post_delete, sender=ProblemEvidenceFile)
def delete_evidence_file(sender, instance, **kwargs):
    schedule_file_delete(instance.file)
