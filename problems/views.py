import time

from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.http import (
    Http404,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import ProblemForm
from .models import Problem, ProblemEvidenceFile, ProblemPhoto, ProblemVote

ACTIVE_PROBLEM_STATUSES = [
    Problem.Status.SENT,
    Problem.Status.IN_PROGRESS,
]


def paginate_problems(request, queryset):
    # Публичные списки не должны расти бесконечно на одной странице:
    # это защищает и интерфейс, и количество загружаемых фотографий.
    paginator = Paginator(queryset, settings.PROBLEM_LIST_PAGE_SIZE)
    return paginator.get_page(request.GET.get("page"))


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
    session_key,
    voted=None,
    operation="unchanged",
    retry_after_seconds=0,
):
    if voted is None:
        voted = ProblemVote.objects.filter(
            problem=problem,
            session_key=session_key,
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
                    )

                for evidence_file in form.cleaned_data.get("evidence_files", []):
                    ProblemEvidenceFile.objects.create(
                        problem=problem,
                        file=evidence_file,
                        original_name=evidence_file.name,
                    )

            return redirect("problem_success")
    else:
        form = ProblemForm()

    return render(request, "problems/create_problem.html", {"form": form})


# Функция отображения страницы говорящей о том, что проблема принята
def problem_success(request):
    return render(request, "problems/problem_success.html")


# Функция отображения решённых проблем
def resolved_problems(request):
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

    return render(
        request,
        "problems/resolved_problems.html",
        {"problems": page_obj, "page_obj": page_obj},
    )


# Функция отображения публичных проблем
def public_problems(request):
    # Актуальный публичный список содержит только опубликованные рабочие статусы.
    # NEW/REJECTED/RESOLVED не должны попадать сюда даже по прямому запросу.
    problems_queryset = (
        Problem.objects.filter(is_public=True, status__in=ACTIVE_PROBLEM_STATUSES)
        .defer("contact_phone")
        .prefetch_related("photos")
        .order_by("-votes_count", "created_at")
    )
    page_obj = paginate_problems(request, problems_queryset)

    if request.session.session_key:
        page_problem_ids = [problem.id for problem in page_obj.object_list]
        # Список нужен только для отрисовки aria-pressed на кнопках голосования.
        voted_problem_ids = list(
            ProblemVote.objects.filter(
                session_key=request.session.session_key,
                problem_id__in=page_problem_ids,
            ).values_list("problem_id", flat=True)
        )

    else:
        voted_problem_ids = []

    return render(
        request,
        "problems/public_problems.html",
        {
            "problems": page_obj,
            "page_obj": page_obj,
            "voted_problem_ids": voted_problem_ids,
        },
    )


# Функция для показа отклонённых проблем
def rejected_problems(request):
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

    # Если сессии не было, создаём, чтобы один браузер не мог голосовать несколько раз
    if not request.session.session_key:
        request.session.create()

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
            return JsonResponse({"error": "problem_not_found"}, status=404)

        raise

    desired_voted = parse_desired_vote(request.POST.get("desired_voted"))
    current_voted = None

    if desired_voted is not None:
        current_voted = ProblemVote.objects.filter(
            problem=problem,
            session_key=request.session.session_key,
        ).exists()

        if desired_voted == current_voted:
            problem.refresh_from_db(fields=["votes_count"])

            if wants_json:
                return JsonResponse(
                    build_vote_payload(
                        problem,
                        request.session.session_key,
                        voted=current_voted,
                        operation="unchanged",
                    )
                )

            return redirect_to_problem(problem.id)

    retry_after_seconds = get_vote_retry_after_seconds(request, problem.id)

    if retry_after_seconds > 0:
        if wants_json:
            payload = build_vote_payload(
                problem,
                request.session.session_key,
                voted=current_voted,
                operation="unchanged",
                retry_after_seconds=retry_after_seconds,
            )
            payload["error"] = "rate_limited"

            return JsonResponse(payload, status=429)

        return redirect_to_problem(problem.id)

    mark_vote_activity(request, problem.id)

    # transaction.atomic() говорит django, что все действия внутри блока должны
    # выполниться как одна операция
    with transaction.atomic():
        # Один session_key даёт один голос на одну проблему. Это удобная защита
        # от случайных повторов, но не полноценная защита от накрутки.
        if desired_voted is None:
            vote, created = ProblemVote.objects.get_or_create(
                problem=problem,
                session_key=request.session.session_key,
            )

            # Если не оставлял, то created будет True и выполнится этот блок
            if created:
                voted = True
                operation = "added"

            # Если голос уже был и пользователь нажал на + повторно,
            # то этот + удаляется.
            else:
                vote.delete()
                voted = False
                operation = "removed"

        elif desired_voted:
            _, created = ProblemVote.objects.get_or_create(
                problem=problem,
                session_key=request.session.session_key,
            )

            voted = True
            operation = "added" if created else "unchanged"

        else:
            deleted_count, _ = ProblemVote.objects.filter(
                problem=problem,
                session_key=request.session.session_key,
            ).delete()

            voted = False
            operation = "removed" if deleted_count else "unchanged"

        actual_votes_count = ProblemVote.objects.filter(problem_id=problem.id).count()
        Problem.objects.filter(id=problem.id).update(votes_count=actual_votes_count)

    problem.refresh_from_db(fields=["votes_count"])

    if wants_json:
        return JsonResponse(
            build_vote_payload(
                problem,
                request.session.session_key,
                voted=voted,
                operation=operation,
            )
        )

    return redirect_to_problem(problem.id)
