# Файл отвечает за конфигурацию панели админа

from django.contrib import admin
from django.utils.html import format_html


def configure_admin_site():
    admin.site.site_header = "Новые Печатники - админ панель"
    admin.site.site_title = "Новые Печатники"
    admin.site.index_title = "Админ панель"


PROBLEM_LIST_DISPLAY = (
    "title",
    "category",
    "status_badge",
    "publication_badge",
    "address",
    "votes_count",
    "created_at",
    "rejection_reason",
    "has_prior_attempts",
)

PROBLEM_LIST_FILTER = (
    "status",
    "category",
    "is_public",
    "has_prior_attempts",
    "created_at",
)

PROBLEM_SEARCH_FIELDS = (
    "title",
    "description",
    "address",
    "rejection_reason",
)

# votes_count меняется только через голосование, иначе публичная сортировка
# может разъехаться с таблицей ProblemVote.
PROBLEM_READONLY_FIELDS = (
    "votes_count",
    "created_at",
    "updated_at",
)

PROBLEM_FIELDSETS = (
    (
        "Содержание обращения",
        {
            "fields": (
                "title",
                "description",
                "address",
                "category",
            ),
        },
    ),
    (
        "Модерация и публикация",
        {
            "fields": (
                "status",
                "is_public",
                "rejection_reason",
                "votes_count",
                "has_prior_attempts",
            ),
            "description": ("Здесь меняется публичность, статус и причина отклонения."),
        },
    ),
    (
        "Контакты жителя",
        {
            "classes": ("collapse",),
            "fields": (
                "contact_phone",
            ),
        },
    ),
    (
        "Служебная информация",
        {
            "classes": ("collapse",),
            "fields": (
                "created_at",
                "updated_at",
            ),
        },
    ),
)


class ProblemAdminBadgesMixin:
    # Бейджи в list_display быстрее читаются модератором, чем сырые значения
    # вроде in_progress/true/false.
    @admin.display(description="Статус", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="nl-admin-badge nl-admin-badge--{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    @admin.display(description="Публикация", ordering="is_public")
    def publication_badge(self, obj):
        if obj.is_public:
            return format_html(
                '<span class="nl-admin-badge nl-admin-badge--public">{}</span>',
                "Публично",
            )

        return format_html(
            '<span class="nl-admin-badge nl-admin-badge--private">{}</span>',
            "Скрыто",
        )
