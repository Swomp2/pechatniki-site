import mimetypes
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse
from django.utils.http import content_disposition_header
from django.utils.text import get_valid_filename

from .models import AttachmentAccessAudit, ProblemEvidenceFile, ProblemPhoto


ATTACHMENT_TOKEN_SALT = "problems.protected_media.attachment"  # nosec B105
PUBLIC_PHOTO_TOKEN_SALT = "problems.protected_media.public_photo"  # nosec B105
ATTACHMENT_TOKEN_MAX_AGE = 60

PHOTO_VIEW_PERMISSION = "problems.view_private_problem_photo"
PHOTO_DOWNLOAD_PERMISSION = "problems.download_problem_photo"
EVIDENCE_VIEW_PERMISSION = "problems.view_problem_evidence_file"
EVIDENCE_DOWNLOAD_PERMISSION = "problems.download_problem_evidence_file"

INLINE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}


def ensure_session_key(request):
    # Ссылки на вложения привязаны к серверной session. Анонимный посетитель
    # публичной страницы получает session cookie, но не получает постоянный URL.
    if not request.session.session_key:
        request.session.create()

    return request.session.session_key


def make_attachment_token(request, attachment, action):
    session_key = ensure_session_key(request)
    user = request.user if request.user.is_authenticated else None

    return signing.dumps(
        {
            "attachment": str(attachment.public_id),
            "action": action,
            "session": session_key,
            "user": user.pk if user else None,
        },
        salt=ATTACHMENT_TOKEN_SALT,
    )


def make_public_photo_token(request, photo):
    session_key = ensure_session_key(request)

    # Публичная страница получает только подписанный логический идентификатор.
    # Физический путь остаётся в БД/storage и проверяется заново при каждом запросе.
    return signing.dumps(
        {
            "photo": str(photo.public_id),
            "problem": photo.problem_id,
            "session": session_key,
        },
        salt=PUBLIC_PHOTO_TOKEN_SALT,
    )


def verify_public_photo_token(request, token):
    try:
        payload = signing.loads(
            token,
            salt=PUBLIC_PHOTO_TOKEN_SALT,
            max_age=settings.PUBLIC_PHOTO_TOKEN_MAX_AGE,
        )
    except signing.BadSignature as exc:
        raise Http404("Фото недоступно") from exc

    if payload.get("session") != request.session.session_key:
        raise Http404("Фото недоступно")

    try:
        photo_public_id = uuid.UUID(str(payload.get("photo")))
        problem_id = int(payload.get("problem"))
    except (TypeError, ValueError) as exc:
        raise Http404("Фото недоступно") from exc

    return photo_public_id, problem_id


def verify_attachment_token(request, attachment, action, token):
    try:
        payload = signing.loads(
            token,
            salt=ATTACHMENT_TOKEN_SALT,
            max_age=ATTACHMENT_TOKEN_MAX_AGE,
        )
    except signing.BadSignature as exc:
        raise Http404("Вложение недоступно") from exc

    session_key = request.session.session_key
    user_id = request.user.pk if request.user.is_authenticated else None

    if (
        payload.get("attachment") != str(attachment.public_id)
        or payload.get("action") != action
        or payload.get("session") != session_key
        or payload.get("user") != user_id
    ):
        raise Http404("Вложение недоступно")


def sanitize_download_name(value, fallback):
    # Исходное имя документа хранится только для админки и заголовка скачивания.
    # Перед отправкой убираем управляющие символы, путь и слишком длинный хвост.
    candidate = "".join(
        character if character.isprintable() and character not in {"\\", "/"} else "_"
        for character in (value or "")
    ).strip(" ._-")
    candidate = get_valid_filename(candidate)[:120].strip(" ._-")

    return candidate or fallback


def get_content_type(file_field, stored_content_type=""):
    if stored_content_type:
        return stored_content_type

    guessed_type, _ = mimetypes.guess_type(file_field.name)

    return guessed_type or "application/octet-stream"


def get_safe_media_path(file_field):
    relative_name = str(getattr(file_field, "name", ""))

    if not relative_name:
        raise Http404("Файл недоступен")

    relative_path = PurePosixPath(relative_name)

    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or "\\" in relative_name
        or "\x00" in relative_name
    ):
        raise Http404("Файл недоступен")

    media_root = Path(settings.MEDIA_ROOT).resolve()
    absolute_path = (media_root / Path(*relative_path.parts)).resolve()

    try:
        absolute_path.relative_to(media_root)
    except ValueError as exc:
        raise Http404("Файл недоступен") from exc

    if has_symlink_component(media_root, absolute_path):
        raise Http404("Файл недоступен")

    if not absolute_path.is_file():
        raise Http404("Файл недоступен")

    return media_root, absolute_path


def has_symlink_component(root, path):
    current = root

    for part in path.relative_to(root).parts:
        current = current / part

        if current.is_symlink():
            return True

    return False


def build_internal_media_uri(media_root, absolute_path):
    relative_path = absolute_path.relative_to(media_root)
    encoded_parts = [quote(part) for part in relative_path.parts]

    return f"{settings.PROTECTED_MEDIA_URL}{'/'.join(encoded_parts)}"


def add_attachment_security_headers(response):
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    response["Referrer-Policy"] = "no-referrer"

    return response


def make_protected_file_response(file_field, content_type, filename, inline=False):
    media_root, absolute_path = get_safe_media_path(file_field)
    as_attachment = not inline or content_type not in INLINE_CONTENT_TYPES

    if settings.PROTECTED_MEDIA_USE_X_ACCEL:
        response = HttpResponse(status=200)
        # X-Accel-Redirect содержит только внутренний URI Nginx. Он строится из
        # доверенной записи БД и не попадает в публичный HTML.
        response["X-Accel-Redirect"] = build_internal_media_uri(media_root, absolute_path)
    else:
        response = FileResponse(
            absolute_path.open("rb"),
            as_attachment=as_attachment,
            filename=filename,
        )

    response["Content-Type"] = content_type
    response["Content-Disposition"] = content_disposition_header(
        as_attachment,
        filename,
    )

    return add_attachment_security_headers(response)


def can_admin_view_photo(user):
    return user.is_authenticated and (
        user.is_superuser or user.has_perm(PHOTO_VIEW_PERMISSION)
    )


def can_admin_download_photo(user):
    return user.is_authenticated and (
        user.is_superuser or user.has_perm(PHOTO_DOWNLOAD_PERMISSION)
    )


def can_admin_view_evidence(user):
    return user.is_authenticated and (
        user.is_superuser or user.has_perm(EVIDENCE_VIEW_PERMISSION)
    )


def can_admin_download_evidence(user):
    return user.is_authenticated and (
        user.is_superuser or user.has_perm(EVIDENCE_DOWNLOAD_PERMISSION)
    )


def get_attachment_by_public_id(public_id):
    try:
        return (
            AttachmentAccessAudit.AttachmentType.PHOTO,
            ProblemPhoto.objects.select_related("problem").get(public_id=public_id),
        )
    except ProblemPhoto.DoesNotExist:
        pass

    try:
        return (
            AttachmentAccessAudit.AttachmentType.EVIDENCE,
            ProblemEvidenceFile.objects.select_related("problem").get(
                public_id=public_id
            ),
        )
    except ProblemEvidenceFile.DoesNotExist as exc:
        raise Http404("Вложение недоступно") from exc


def record_attachment_access(user, attachment_type, attachment, action, success):
    # Audit хранит только логические идентификаторы. Токены, пути, телефоны и
    # содержимое файлов сюда не попадают.
    AttachmentAccessAudit.objects.create(
        user=user if user.is_authenticated else None,
        attachment_type=attachment_type,
        attachment_public_id=attachment.public_id,
        problem_id=attachment.problem_id,
        action=action,
        success=success,
    )
