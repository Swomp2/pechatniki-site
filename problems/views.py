import time
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.middleware.csrf import get_token
from django.core.paginator import Paginator
from django.http import (
    Http404,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import ProblemForm
from .models import Problem, ProblemEvidenceFile, ProblemPhoto, ProblemVote
from .protected_media import (
    AttachmentAccessAudit,
    can_admin_download_evidence,
    can_admin_download_photo,
    can_admin_view_evidence,
    can_admin_view_photo,
    ensure_session_key,
    get_attachment_by_public_id,
    get_content_type,
    make_protected_file_response,
    make_public_photo_token,
    record_attachment_access,
    sanitize_download_name,
    verify_attachment_token,
    verify_public_photo_token,
)
from .voters import get_or_create_voter_identity, set_voter_cookie

ACTIVE_PROBLEM_STATUSES = [
    Problem.Status.SENT,
    Problem.Status.IN_PROGRESS,
]


def paginate_problems(request, queryset):
    # Публичные списки не должны расти бесконечно на одной странице:
    # это защищает и интерфейс, и количество загружаемых фотографий.
    paginator = Paginator(queryset, settings.PROBLEM_LIST_PAGE_SIZE)
    return paginator.get_page(request.GET.get("page"))


def attach_public_photo_tokens(request, page_obj):
    # Шаблоны не получают FieldFile.url, pk фотографии или путь в MEDIA_ROOT.
    # Токен привязан к текущей session, а view всё равно перепроверяет статус.
    for problem in page_obj.object_list:
        for photo in problem.photos.all():
            photo.public_access_token = make_public_photo_token(request, photo)


def get_vote_rate_limit_key(problem_id):
    return f"last_problem_vote_at:{problem_id}"


def get_vote_min_interval_ms():
    return int(settings.PROBLEM_VOTE_MIN_INTERVAL_SECONDS * 1000)


def get_vote_retry_after_seconds(request, problem_id):
    # Это лёгкий session-based throttle без внешних зависимостей.
    min_interval = settings.PROBLEM_VOTE_MIN_INTERVAL_SECONDS

    if min_interval <= 0:
        return 0

    now = time.time()
    last_vote_at = request.session.get(get_vote_rate_limit_key(problem_id))

    if last_vote_at is None:
        return 0

    return max(0, min_interval - (now - float(last_vote_at)))


def mark_vote_activity(request, problem_id):
    if settings.PROBLEM_VOTE_MIN_INTERVAL_SECONDS <= 0:
        return

    request.session[get_vote_rate_limit_key(problem_id)] = time.time()


def parse_desired_vote(value):
    if value is None:
        return None

    normalized_value = value.strip().lower()

    if normalized_value in {"1", "true", "yes", "on"}:
        return True

    if normalized_value in {"0", "false", "no", "off"}:
        return False

    return None


def wants_json_response(request):
    accept_header = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")

    return "application/json" in accept_header or requested_with in {
        "fetch",
        "XMLHttpRequest",
    }


def wants_page_transition_response(request):
    accept_header = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")

    return "text/html" in accept_header and requested_with in {
        "fetch",
        "XMLHttpRequest",
    }


def redirect_to_problem(problem_id):
    return redirect(f"{reverse('public_problems')}#problem-{problem_id}")


def legacy_redirect(route_name, *route_args):
    def view(request, *args, **kwargs):
        if request.method not in {"GET", "HEAD"}:
            return HttpResponseNotAllowed(["GET", "HEAD"])

        reverse_args = route_args or (
            [kwargs["problem_id"]] if "problem_id" in kwargs else None
        )
        url = reverse(route_name, args=reverse_args)
        query_string = request.META.get("QUERY_STRING")

        if query_string:
            url = f"{url}?{query_string}"

        return HttpResponsePermanentRedirect(url)

    return view


def build_vote_payload(
    problem,
    voter_hash,
    voted=None,
    operation="unchanged",
    retry_after_seconds=0,
):
    if voted is None:
        voted = ProblemVote.objects.filter(
            problem=problem,
            voter_hash=voter_hash,
        ).exists()

    return {
        "problem_id": problem.id,
        "votes_count": problem.votes_count,
        "voted": voted,
        "operation": operation,
        "min_interval_ms": get_vote_min_interval_ms(),
        "retry_after_ms": max(0, int(retry_after_seconds * 1000)),
    }


# Функция отображения главной страницы
def index(request):
    return render(request, "problems/index.html")


# Функция создания проблемы
def create_problem(request):
    if request.method == "POST":
        form = ProblemForm(request.POST, request.FILES)

        if form.is_valid():
            with transaction.atomic():
                # Сначала сохраняем заявку: фото и файлы обращений должны получить
                # внешний ключ на уже существующую Problem.
                problem = form.save()

                for photo in form.cleaned_data.get("photos", []):
                    ProblemPhoto.objects.create(
                        problem=problem,
                        image=photo,
                        content_type=getattr(photo, "content_type", ""),
                        file_size=getattr(photo, "size", 0),
                    )

                for evidence_file in form.cleaned_data.get("evidence_files", []):
                    ProblemEvidenceFile.objects.create(
                        problem=problem,
                        file=evidence_file,
                        original_name=getattr(
                            evidence_file,
                            "original_client_name",
                            evidence_file.name,
                        )[:255],
                        content_type=getattr(evidence_file, "content_type", ""),
                        file_size=getattr(evidence_file, "size", 0),
                    )

            success_url = reverse("problem_success")

            if wants_page_transition_response(request):
                response = render(
                    request,
                    "problems/problem_success.html",
                    status=201,
                )
                response["X-Redirect-URL"] = success_url

                return response

            if wants_json_response(request):
                return JsonResponse(
                    {"redirect_url": success_url},
                    status=201,
                )

            return redirect("problem_success")

        if wants_page_transition_response(request):
            response = render(
                request,
                "problems/create_problem.html",
                {"form": form},
                status=400,
            )
            response["X-Redirect-URL"] = request.get_full_path()

            return response

        if wants_json_response(request):
            return JsonResponse(
                {"errors": form.errors.get_json_data()},
                status=400,
            )
    else:
        form = ProblemForm()

    return render(request, "problems/create_problem.html", {"form": form})


# Функция отображения страницы говорящей о том, что проблема принята
def problem_success(request):
    return render(request, "problems/problem_success.html")


# Функция отображения решённых проблем
def resolved_problems(request):
    ensure_session_key(request)
    get_token(request)
    # Даже решённые заявки показываем только после явной публикации модератором.
    problems_queryset = (
        Problem.objects.filter(
            status=Problem.Status.RESOLVED,
            is_public=True,
        )
        .defer("contact_phone")
        .prefetch_related("photos")
    )
    page_obj = paginate_problems(request, problems_queryset)
    attach_public_photo_tokens(request, page_obj)

    return render(
        request,
        "problems/resolved_problems.html",
        {"problems": page_obj, "page_obj": page_obj},
    )


# Функция отображения публичных проблем
def public_problems(request):
    ensure_session_key(request)
    get_token(request)
    voter_identity = get_or_create_voter_identity(request)
    # Актуальный публичный список содержит только опубликованные рабочие статусы.
    # NEW/REJECTED/RESOLVED не должны попадать сюда даже по прямому запросу.
    problems_queryset = (
        Problem.objects.filter(is_public=True, status__in=ACTIVE_PROBLEM_STATUSES)
        .defer("contact_phone")
        .prefetch_related("photos")
        .order_by("-votes_count", "created_at")
    )
    page_obj = paginate_problems(request, problems_queryset)
    attach_public_photo_tokens(request, page_obj)

    page_problem_ids = [problem.id for problem in page_obj.object_list]
    # Список нужен только для отрисовки aria-pressed на кнопках голосования.
    voted_problem_ids = list(
        ProblemVote.objects.filter(
            voter_hash=voter_identity["hash"],
            problem_id__in=page_problem_ids,
        ).values_list("problem_id", flat=True)
    )

    response = render(
        request,
        "problems/public_problems.html",
        {
            "problems": page_obj,
            "page_obj": page_obj,
            "voted_problem_ids": voted_problem_ids,
        },
    )

    if voter_identity["should_set_cookie"]:
        set_voter_cookie(response, voter_identity["token"])

    return response


# Функция для показа отклонённых проблем
def rejected_problems(request):
    ensure_session_key(request)
    get_token(request)
    # Отклонённые заявки могут содержать неподходящий текст или персональные данные,
    # поэтому показываем только те, которые модератор явно сделал публичными.
    problems_queryset = (
        Problem.objects.filter(
            status=Problem.Status.REJECTED,
            is_public=True,
        )
        .defer("contact_phone")
        .exclude(rejection_reason="")  # Подстраховка, чтобы не отображать отклонённые
        # без объяснения проблемы
        .prefetch_related("photos")
        .order_by("-created_at")
    )
    page_obj = paginate_problems(request, problems_queryset)
    attach_public_photo_tokens(request, page_obj)

    return render(
        request,
        "problems/rejected_problems.html",
        {"problems": page_obj, "page_obj": page_obj},
    )


# Функция для обработки голосов за проблемы
def upvote_problem(request, problem_id):
    wants_json = wants_json_response(request)

    if request.method != "POST":
        if wants_json:
            return JsonResponse({"error": "method_not_allowed"}, status=405)

        return redirect("public_problems")

    # Сессия нужна для rate limit, а постоянный voter cookie — для сохранения
    # голоса между пересборками контейнеров. В БД попадает только HMAC cookie.
    ensure_session_key(request)
    voter_identity = get_or_create_voter_identity(request)
    voter_hash = voter_identity["hash"]

    def with_voter_cookie(response):
        if voter_identity["should_set_cookie"]:
            set_voter_cookie(response, voter_identity["token"])

        return response

    # Голосовать можно только за опубликованные активные заявки: это не даёт
    # накручивать скрытые, решённые, отклонённые или ещё не прошедшие модерацию.
    try:
        problem = get_object_or_404(
            Problem.objects.only("id", "status", "is_public", "votes_count"),
            id=problem_id,
            is_public=True,
            status__in=ACTIVE_PROBLEM_STATUSES,
        )
    except Http404:
        if wants_json:
            return with_voter_cookie(
                JsonResponse({"error": "problem_not_found"}, status=404)
            )

        raise

    desired_voted = parse_desired_vote(request.POST.get("desired_voted"))
    current_voted = None

    if desired_voted is not None:
        current_voted = ProblemVote.objects.filter(
            problem=problem,
            voter_hash=voter_hash,
        ).exists()

        if desired_voted == current_voted:
            problem.refresh_from_db(fields=["votes_count"])

            if wants_json:
                return with_voter_cookie(
                    JsonResponse(
                        build_vote_payload(
                            problem,
                            voter_hash,
                            voted=current_voted,
                            operation="unchanged",
                        )
                    )
                )

            return with_voter_cookie(redirect_to_problem(problem.id))

    retry_after_seconds = get_vote_retry_after_seconds(request, problem.id)

    if retry_after_seconds > 0:
        if wants_json:
            payload = build_vote_payload(
                problem,
                voter_hash,
                voted=current_voted,
                operation="unchanged",
                retry_after_seconds=retry_after_seconds,
            )
            payload["error"] = "rate_limited"

            return with_voter_cookie(JsonResponse(payload, status=429))

        return with_voter_cookie(redirect_to_problem(problem.id))

    mark_vote_activity(request, problem.id)

    # transaction.atomic() говорит django, что все действия внутри блока должны
    # выполниться как одна операция
    with transaction.atomic():
        session_key = request.session.session_key or ""

        # Один HMAC браузерного cookie даёт один голос на проблему. Уникальность
        # дополнительно защищена на уровне БД, поэтому быстрый дубль не создаст
        # две строки даже при гонке запросов.
        if desired_voted is None:
            deleted_count, _ = ProblemVote.objects.filter(
                problem=problem,
                voter_hash=voter_hash,
            ).delete()

            if deleted_count:
                voted = False
                operation = "removed"
            else:
                try:
                    ProblemVote.objects.create(
                        problem=problem,
                        voter_hash=voter_hash,
                        session_key=session_key[:40],
                    )
                    voted = True
                    operation = "added"
                except IntegrityError:
                    voted = True
                    operation = "unchanged"

        elif desired_voted:
            try:
                ProblemVote.objects.create(
                    problem=problem,
                    voter_hash=voter_hash,
                    session_key=session_key[:40],
                )
                created = True
            except IntegrityError:
                created = False

            voted = True
            operation = "added" if created else "unchanged"

        else:
            deleted_count, _ = ProblemVote.objects.filter(
                problem=problem,
                voter_hash=voter_hash,
            ).delete()

            voted = False
            operation = "removed" if deleted_count else "unchanged"

        actual_votes_count = ProblemVote.objects.filter(problem_id=problem.id).count()
        Problem.objects.filter(id=problem.id).update(votes_count=actual_votes_count)

    problem.refresh_from_db(fields=["votes_count"])

    if wants_json:
        return with_voter_cookie(
            JsonResponse(
                build_vote_payload(
                    problem,
                    voter_hash,
                    voted=voted,
                    operation=operation,
                )
            )
        )

    return with_voter_cookie(redirect_to_problem(problem.id))


def get_public_problem_photo_from_token(request, token):
    ensure_session_key(request)

    photo_public_id, problem_id = verify_public_photo_token(request, token)

    visible_problem_filter = Q(
        problem__status__in=ACTIVE_PROBLEM_STATUSES + [Problem.Status.RESOLVED]
    ) | Q(problem__status=Problem.Status.REJECTED, problem__rejection_reason__gt="")
    try:
        photo = ProblemPhoto.objects.select_related("problem").get(
            visible_problem_filter,
            public_id=photo_public_id,
            problem_id=problem_id,
            problem__is_public=True,
        )
    except ProblemPhoto.DoesNotExist as exc:
        raise Http404("Фото недоступно") from exc

    return photo


def make_public_problem_photo_response(photo):
    content_type = get_content_type(photo.image, photo.content_type)
    extension = Path(photo.image.name).suffix.lower() or ".jpg"

    return make_protected_file_response(
        photo.image,
        content_type,
        f"problem-photo-{photo.public_id}{extension}",
        inline=True,
    )


def public_problem_photo(request, token):
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD"])

    return make_public_problem_photo_response(
        get_public_problem_photo_from_token(request, token)
    )


def fetch_public_problem_photo(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    return make_public_problem_photo_response(
        get_public_problem_photo_from_token(
            request,
            request.POST.get("token", ""),
        )
    )


def attachment_access(request, public_id, action, token):
    if action not in {
        AttachmentAccessAudit.Action.VIEW,
        AttachmentAccessAudit.Action.DOWNLOAD,
    }:
        raise Http404("Вложение недоступно")

    attachment_type, attachment = get_attachment_by_public_id(public_id)
    user = request.user
    success = False

    try:
        verify_attachment_token(request, attachment, action, token)

        if attachment_type == AttachmentAccessAudit.AttachmentType.PHOTO:
            allowed = (
                can_admin_download_photo(user)
                if action == AttachmentAccessAudit.Action.DOWNLOAD
                else can_admin_view_photo(user)
            )
            file_field = attachment.image
            content_type = get_content_type(file_field, attachment.content_type)
            extension = Path(file_field.name).suffix.lower() or ".jpg"
            filename = f"problem-photo-{attachment.public_id}{extension}"
        else:
            allowed = (
                can_admin_download_evidence(user)
                if action == AttachmentAccessAudit.Action.DOWNLOAD
                else can_admin_view_evidence(user)
            )
            file_field = attachment.file
            content_type = get_content_type(file_field, attachment.content_type)
            filename = sanitize_download_name(
                attachment.original_name,
                f"problem-evidence-{attachment.public_id}",
            )

        if not allowed:
            return HttpResponseForbidden("Вложение недоступно")

        success = True

        return make_protected_file_response(
            file_field,
            content_type,
            filename,
            inline=action == AttachmentAccessAudit.Action.VIEW,
        )
    finally:
        if attachment_type != AttachmentAccessAudit.AttachmentType.PHOTO or (
            not attachment.problem.is_public
        ):
            record_attachment_access(user, attachment_type, attachment, action, success)
