# Файл отвечает за отображение моделей в админ панели

from django.contrib import admin, messages  # Встроенная система админки
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html

from .admin_config import (
    PROBLEM_FIELDSETS,
    PROBLEM_LIST_DISPLAY,
    PROBLEM_LIST_FILTER,
    PROBLEM_READONLY_FIELDS,
    PROBLEM_SEARCH_FIELDS,
    ProblemAdminBadgesMixin,
    configure_admin_site,
)
from .models import (  # Модели из файла config/models.py
    AttachmentAccessAudit,
    Problem,
    ProblemEvidenceFile,
    ProblemPhoto,
)
from .protected_media import (
    can_admin_download_evidence,
    can_admin_download_photo,
    can_admin_view_evidence,
    can_admin_view_photo,
    make_attachment_token,
)

configure_admin_site()

CONTACT_PHONE_PERMISSION = "problems.view_problem_contact_phone"


def build_attachment_admin_links(request, obj, can_view, can_download):
    if not request:
        return "Недоступно"

    links = []

    if can_view(request.user):
        view_token = make_attachment_token(request, obj, "view")
        view_url = reverse(
            "attachment_access",
            args=[obj.public_id, "view", view_token],
        )
        links.append(
            format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">Открыть</a>',
                view_url,
            )
        )

    if can_download(request.user):
        download_token = make_attachment_token(request, obj, "download")
        download_url = reverse(
            "attachment_access",
            args=[obj.public_id, "download", download_token],
        )
        links.append(format_html('<a href="{}">Скачать</a>', download_url))

    if not links:
        return "Недостаточно прав"

    return format_html(" · ".join("{}" for _ in links), *links)


# Фото и файлы со старыми заявками доступны прямо в карточке обращения, чтобы модератор
# не ходил по отдельным разделам при проверке заявки.
class ProblemPhotoInline(admin.TabularInline):
    model = ProblemPhoto
    extra = 0
    fields = (
        "attachment_links",
        "uploaded_at",
    )
    readonly_fields = (
        "attachment_links",
        "uploaded_at",
    )
    show_change_link = True

    def get_formset(self, request, obj=None, **kwargs):
        self.request = request

        return super().get_formset(request, obj, **kwargs)

    @admin.display(description="Вложение")
    def attachment_links(self, obj):
        return build_attachment_admin_links(
            getattr(self, "request", None),
            obj,
            can_admin_view_photo,
            can_admin_download_photo,
        )


class ProblemEvidenceFileInline(admin.TabularInline):
    model = ProblemEvidenceFile
    extra = 0
    fields = (
        "attachment_links",
        "original_name",
        "uploaded_at",
    )

    readonly_fields = (
        "attachment_links",
        "original_name",
        "uploaded_at",
    )
    show_change_link = True

    def get_formset(self, request, obj=None, **kwargs):
        self.request = request

        return super().get_formset(request, obj, **kwargs)

    @admin.display(description="Вложение")
    def attachment_links(self, obj):
        return build_attachment_admin_links(
            getattr(self, "request", None),
            obj,
            can_admin_view_evidence,
            can_admin_download_evidence,
        )


# То, как проблемы будут отображаться в админке
@admin.register(Problem)
class ProblemAdmin(ProblemAdminBadgesMixin, admin.ModelAdmin):
    list_display = PROBLEM_LIST_DISPLAY
    list_filter = PROBLEM_LIST_FILTER
    search_fields = PROBLEM_SEARCH_FIELDS
    readonly_fields = PROBLEM_READONLY_FIELDS
    fieldsets = PROBLEM_FIELDSETS
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    actions = (
        "mark_public",
        "mark_hidden",
        "mark_in_progress",
        "mark_resolved",
    )

    inlines = [
        ProblemPhotoInline,
        ProblemEvidenceFileInline,
    ]  # Подключение фоток и файлов старых обращений прямо внутрь страницы обращения

    class Media:
        js = ("for_admin/js/problem_admin.js",)

    @admin.display(description="Телефон")
    def contact_phone_display(self, obj):
        return "Недостаточно прав"

    def has_contact_phone_permission(self, request):
        return request.user.has_perm(CONTACT_PHONE_PERMISSION)

    def get_queryset(self, request):
        return super().get_queryset(request).defer("contact_phone")

    def get_fieldsets(self, request, obj=None):
        if self.has_contact_phone_permission(request):
            return PROBLEM_FIELDSETS

        fieldsets = []

        for title, options in PROBLEM_FIELDSETS:
            options = options.copy()
            options["fields"] = tuple(
                "contact_phone_display" if field == "contact_phone" else field
                for field in options["fields"]
            )
            fieldsets.append((title, options))

        return tuple(fieldsets)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(PROBLEM_READONLY_FIELDS)

        if not self.has_contact_phone_permission(request):
            readonly_fields.append("contact_phone_display")

        return tuple(readonly_fields)

    @admin.action(description="Опубликовать выбранные обращения")
    def mark_public(self, request, queryset):
        # Массовая публикация не должна случайно выпускать сырые NEW-заявки.
        # Отклонённые публикуем только с заполненной причиной.
        allowed_statuses = (
            Problem.Status.SENT,
            Problem.Status.IN_PROGRESS,
            Problem.Status.RESOLVED,
        )
        eligible_queryset = queryset.filter(
            Q(status__in=allowed_statuses)
            | Q(status=Problem.Status.REJECTED, rejection_reason__gt="")
        )
        selected_count = queryset.count()
        published_count = eligible_queryset.update(is_public=True)
        skipped_count = selected_count - published_count

        if skipped_count:
            self.message_user(
                request,
                (
                    f"Опубликовано: {published_count}. "
                    f"Пропущено как непроверенные или без причины отклонения: "
                    f"{skipped_count}."
                ),
                level=messages.WARNING,
            )
        else:
            self.message_user(request, f"Опубликовано: {published_count}.")

    @admin.action(description="Скрыть выбранные обращения")
    def mark_hidden(self, request, queryset):
        queryset.update(is_public=False)

    @admin.action(description="Перевести выбранные обращения в работу")
    def mark_in_progress(self, request, queryset):
        queryset.update(status=Problem.Status.IN_PROGRESS)

    @admin.action(description="Отметить выбранные обращения решёнными")
    def mark_resolved(self, request, queryset):
        queryset.update(status=Problem.Status.RESOLVED)


# Отдельная страница фотографий в админке, для проверки, что фото реально сохранились
@admin.register(ProblemPhoto)
class ProblemPhotoAdmin(admin.ModelAdmin):
    list_display = (
        "problem",
        "attachment_links",
        "uploaded_at",
    )
    fields = (
        "problem",
        "attachment_links",
        "public_id",
        "content_type",
        "file_size",
        "uploaded_at",
    )
    readonly_fields = (
        "attachment_links",
        "public_id",
        "content_type",
        "file_size",
        "uploaded_at",
    )

    search_fields = (
        "problem__title",
        "problem__address",
    )

    list_filter = ("uploaded_at",)

    def get_queryset(self, request):
        self.request = request

        return super().get_queryset(request)

    @admin.display(description="Вложение")
    def attachment_links(self, obj):
        return build_attachment_admin_links(
            getattr(self, "request", None),
            obj,
            can_admin_view_photo,
            can_admin_download_photo,
        )


@admin.register(ProblemEvidenceFile)
class ProblemEvidenceFileAdmin(admin.ModelAdmin):
    list_display = (
        "problem",
        "original_name",
        "attachment_links",
        "uploaded_at",
    )
    fields = (
        "problem",
        "original_name",
        "attachment_links",
        "public_id",
        "content_type",
        "file_size",
        "uploaded_at",
    )

    search_fields = (
        "problem__title",
        "problem__address",
        "original_name",
    )

    list_filter = ("uploaded_at",)

    readonly_fields = (
        "original_name",
        "attachment_links",
        "public_id",
        "content_type",
        "file_size",
        "uploaded_at",
    )

    def get_queryset(self, request):
        self.request = request

        return super().get_queryset(request)

    @admin.display(description="Вложение")
    def attachment_links(self, obj):
        return build_attachment_admin_links(
            getattr(self, "request", None),
            obj,
            can_admin_view_evidence,
            can_admin_download_evidence,
        )


@admin.register(AttachmentAccessAudit)
class AttachmentAccessAuditAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "user",
        "attachment_type",
        "attachment_public_id",
        "problem_id",
        "action",
        "success",
    )
    list_filter = (
        "attachment_type",
        "action",
        "success",
        "created_at",
    )
    search_fields = (
        "attachment_public_id",
        "problem_id",
        "user__username",
    )
    readonly_fields = (
        "created_at",
        "user",
        "attachment_type",
        "attachment_public_id",
        "problem_id",
        "action",
        "success",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
