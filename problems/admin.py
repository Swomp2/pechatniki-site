# Файл отвечает за отображение моделей в админ панели

from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages  # Встроенная система админки
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
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
from .forms import (
    EVIDENCE_EXTENSIONS,
    PHOTO_EXTENSIONS,
    get_file_extension,
    validate_pdf,
    validate_uploaded_image,
)
from .image_processing import build_safe_upload_name
from .protected_media import (
    can_admin_download_evidence,
    can_admin_download_photo,
    can_admin_view_evidence,
    can_admin_view_photo,
    make_attachment_token,
)

configure_admin_site()

CONTACT_PHONE_PERMISSION = "problems.view_problem_contact_phone"
PHOTO_ADMIN_ACCEPT = "image/jpeg,image/png,image/webp"
EVIDENCE_ADMIN_ACCEPT = (
    ".pdf,.jpg,.jpeg,.png,.webp,"
    "application/pdf,image/jpeg,image/png,image/webp"
)
DANGEROUS_DOUBLE_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".html",
    ".htm",
    ".js",
    ".mjs",
    ".php",
    ".ps1",
    ".sh",
    ".svg",
}


def is_new_upload(value):
    return bool(
        value
        and hasattr(value, "content_type")
        and hasattr(value, "size")
        and hasattr(value, "name")
    )


def has_dangerous_double_extension(filename):
    suffixes = Path(filename).suffixes

    if len(suffixes) < 2:
        return False

    return any(
        suffix.lower() in DANGEROUS_DOUBLE_EXTENSIONS
        for suffix in suffixes[:-1]
    )


def format_file_size(value):
    if not value:
        return "Неизвестно"

    units = ("Б", "КБ", "МБ", "ГБ")
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"

        size /= 1024

    return f"{int(value)} Б"


class ExistingAttachmentWidget(forms.Widget):
    # Существующее вложение нельзя подменить через inline-строку: для него
    # рендерится только текст, а настоящий input остаётся только у новых форм.
    def __init__(self, label):
        super().__init__()
        self.label = label

    def render(self, name, value, attrs=None, renderer=None):
        return format_html('<span class="readonly">{}</span>', self.label)

    def value_from_datadict(self, data, files, name):
        return None

    def use_required_attribute(self, initial):
        return False


class AttachmentInlineFormSet(BaseInlineFormSet):
    # File input есть в empty_form, которую Django admin клонирует кнопкой
    # "Добавить ещё", поэтому весь change form должен оставаться multipart.
    def is_multipart(self):
        return True


class ProblemPhotoAdminForm(forms.ModelForm):
    class Meta:
        model = ProblemPhoto
        fields = (
            "problem",
            "image",
        )
        widgets = {
            "image": forms.FileInput(attrs={"accept": PHOTO_ADMIN_ACCEPT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["image"].disabled = True
            self.fields["image"].required = False
            self.fields["image"].widget = ExistingAttachmentWidget(
                "Фотография уже сохранена"
            )

    def clean_image(self):
        if self.instance and self.instance.pk:
            return self.instance.image

        uploaded_file = self.cleaned_data.get("image")

        if not is_new_upload(uploaded_file):
            return uploaded_file

        if has_dangerous_double_extension(uploaded_file.name):
            raise forms.ValidationError("Имя файла содержит опасное двойное расширение.")

        if uploaded_file.size > settings.PROBLEM_PHOTO_MAX_SIZE:
            raise forms.ValidationError(
                "Размер фотографии не должен превышать "
                f"{settings.PROBLEM_PHOTO_MAX_SIZE // 1024 // 1024} МБ."
            )

        return validate_uploaded_image(uploaded_file, PHOTO_EXTENSIONS)

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded_file = self.cleaned_data.get("image")

        if is_new_upload(uploaded_file):
            instance.content_type = uploaded_file.content_type
            instance.file_size = uploaded_file.size

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class ProblemEvidenceFileAdminForm(forms.ModelForm):
    class Meta:
        model = ProblemEvidenceFile
        fields = (
            "problem",
            "file",
        )
        widgets = {
            "file": forms.FileInput(attrs={"accept": EVIDENCE_ADMIN_ACCEPT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["file"].disabled = True
            self.fields["file"].required = False
            self.fields["file"].widget = ExistingAttachmentWidget(
                "Документ уже сохранён"
            )

    def clean_file(self):
        if self.instance and self.instance.pk:
            return self.instance.file

        uploaded_file = self.cleaned_data.get("file")

        if not is_new_upload(uploaded_file):
            return uploaded_file

        if has_dangerous_double_extension(uploaded_file.name):
            raise forms.ValidationError("Имя файла содержит опасное двойное расширение.")

        if uploaded_file.size > settings.PROBLEM_EVIDENCE_MAX_SIZE:
            raise forms.ValidationError(
                "Размер файла не должен превышать "
                f"{settings.PROBLEM_EVIDENCE_MAX_SIZE // 1024 // 1024} МБ."
            )

        original_client_name = Path(uploaded_file.name).name[:255]
        extension = get_file_extension(uploaded_file)

        if extension not in EVIDENCE_EXTENSIONS:
            raise forms.ValidationError("Можно загружать только PDF, JPG, PNG и WebP.")

        if extension == ".pdf":
            validate_pdf(uploaded_file)
            uploaded_file.name = build_safe_upload_name(uploaded_file.name, ".pdf")
            uploaded_file.original_client_name = original_client_name
            return uploaded_file

        sanitized_file = validate_uploaded_image(uploaded_file, PHOTO_EXTENSIONS)
        sanitized_file.original_client_name = original_client_name

        return sanitized_file

    def save(self, commit=True):
        instance = super().save(commit=False)
        uploaded_file = self.cleaned_data.get("file")

        if is_new_upload(uploaded_file):
            instance.original_name = getattr(
                uploaded_file,
                "original_client_name",
                Path(uploaded_file.name).name,
            )[:255]
            instance.content_type = uploaded_file.content_type
            instance.file_size = uploaded_file.size

        if commit:
            instance.save()
            self.save_m2m()

        return instance


def build_attachment_admin_url(request, obj, action):
    token = make_attachment_token(request, obj, action)

    return reverse(
        "attachment_access",
        args=[obj.public_id, action, token],
    )


def build_attachment_admin_links(request, obj, can_view, can_download):
    if not obj or not getattr(obj, "pk", None):
        return "Будет доступно после сохранения"

    if not request:
        return "Недоступно"

    links = []

    if can_view(request.user):
        view_url = build_attachment_admin_url(request, obj, "view")
        links.append(
            format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">Открыть</a>',
                view_url,
            )
        )

    if can_download(request.user):
        download_url = build_attachment_admin_url(request, obj, "download")
        links.append(format_html('<a href="{}">Скачать</a>', download_url))

    if not links:
        return "Недостаточно прав"

    return format_html(" · ".join("{}" for _ in links), *links)


# Фото и файлы со старыми заявками доступны прямо в карточке обращения, чтобы модератор
# не ходил по отдельным разделам при проверке заявки.
class ProblemPhotoInline(admin.TabularInline):
    model = ProblemPhoto
    form = ProblemPhotoAdminForm
    formset = AttachmentInlineFormSet
    extra = 0
    fields = (
        "attachment_preview",
        "attachment_links",
        "image",
        "file_size_display",
        "uploaded_at",
    )
    readonly_fields = (
        "attachment_preview",
        "attachment_links",
        "file_size_display",
        "uploaded_at",
    )
    show_change_link = True

    def get_formset(self, request, obj=None, **kwargs):
        self.request = request

        return super().get_formset(request, obj, **kwargs)

    @admin.display(description="Миниатюра")
    def attachment_preview(self, obj):
        request = getattr(self, "request", None)

        if not obj or not getattr(obj, "pk", None):
            return "Появится после сохранения"

        if not request or not can_admin_view_photo(request.user):
            return "Недостаточно прав"

        view_url = build_attachment_admin_url(request, obj, "view")

        return format_html(
            (
                '<a href="{}" target="_blank" rel="noopener noreferrer">'
                '<img src="{}" alt="Миниатюра фотографии" loading="lazy" '
                'style="width: 96px; height: 64px; object-fit: cover; '
                'border-radius: 10px;" /></a>'
            ),
            view_url,
            view_url,
        )

    @admin.display(description="Вложение")
    def attachment_links(self, obj):
        return build_attachment_admin_links(
            getattr(self, "request", None),
            obj,
            can_admin_view_photo,
            can_admin_download_photo,
        )

    @admin.display(description="Размер")
    def file_size_display(self, obj):
        return format_file_size(getattr(obj, "file_size", 0))


class ProblemEvidenceFileInline(admin.TabularInline):
    model = ProblemEvidenceFile
    form = ProblemEvidenceFileAdminForm
    formset = AttachmentInlineFormSet
    extra = 0
    fields = (
        "attachment_links",
        "file",
        "original_name",
        "file_size_display",
        "uploaded_at",
    )

    readonly_fields = (
        "attachment_links",
        "original_name",
        "file_size_display",
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

    @admin.display(description="Размер")
    def file_size_display(self, obj):
        return format_file_size(getattr(obj, "file_size", 0))


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
    form = ProblemPhotoAdminForm
    list_display = (
        "problem",
        "attachment_links",
        "uploaded_at",
    )
    fields = (
        "problem",
        "image",
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
    form = ProblemEvidenceFileAdminForm
    list_display = (
        "problem",
        "original_name",
        "attachment_links",
        "uploaded_at",
    )
    fields = (
        "problem",
        "file",
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
