from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    # Публичные списки намеренно разделены по смыслу: актуальные, решённые
    # и отклонённые имеют разные фильтры видимости во views.
    path("проблемы/", views.public_problems, name="public_problems"),
    path("проблемы/отправить/", views.create_problem, name="create_problem"),
    path("проблемы/успешно/", views.problem_success, name="problem_success"),
    path("проблемы/решённые/", views.resolved_problems, name="resolved_problems"),
    path("проблемы/отклонённые/", views.rejected_problems, name="rejected_problems"),
    path(
        "проблемы/<int:problem_id>/оценить/",
        views.upvote_problem,
        name="upvote_problem",
    ),
    path(
        "вложения/фото/получить/",
        views.fetch_public_problem_photo,
        name="fetch_public_problem_photo",
    ),
    path(
        "вложения/<uuid:public_id>/<str:action>/<path:token>/",
        views.attachment_access,
        name="attachment_access",
    ),
    path("problems/", views.legacy_redirect("public_problems")),
    path("problems/send/", views.legacy_redirect("create_problem")),
    path("problems/success/", views.legacy_redirect("problem_success")),
    path("problems/resolved/", views.legacy_redirect("resolved_problems")),
    path("problems/rejected/", views.legacy_redirect("rejected_problems")),
    path(
        "problems/<int:problem_id>/upvote/",
        views.legacy_redirect("upvote_problem"),
    ),
]
