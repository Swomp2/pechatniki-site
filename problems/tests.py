import logging
import json
import re
import time
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError, connection, transaction
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from PIL import Image

from config.logging_filters import SensitiveValueFilter

from .forms import ProblemForm, make_form_started_at_token
from .admin import (
    ProblemAdmin,
    ProblemEvidenceFileAdminForm,
    ProblemPhotoAdminForm,
)
from .models import (
    AttachmentAccessAudit,
    Problem,
    ProblemEvidenceFile,
    ProblemPhoto,
    ProblemVote,
)
from .protected_media import (
    ATTACHMENT_TOKEN_MAX_AGE,
    make_attachment_token,
    make_public_photo_token,
)

TEST_FIELD_ENCRYPTION_KEYS = [
    "test-key-1:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
]


def make_form_data(**overrides):
    # started_at ставим в прошлое, чтобы тесты не спотыкались о антиспам-таймер.
    data = {
        "title": "Сломанная лавочка",
        "description": "Во дворе сломана лавочка, нужна замена.",
        "address": "Печатники",
        "category": Problem.Category.YARD,
        "contact_phone": "",
        "started_at": make_form_started_at_token(time.time() - 10),
        "website": "",
    }
    data.update(overrides)
    return data


def make_image_upload(
    name="photo.jpg",
    image_format="JPEG",
    content_type="image/jpeg",
    size=(12, 12),
):
    image = Image.new("RGB", size, color=(71, 194, 192))
    content = BytesIO()
    image.save(content, format=image_format)
    return SimpleUploadedFile(name, content.getvalue(), content_type=content_type)


def make_problem(status, is_public=True, title=None):
    return Problem.objects.create(
        title=title or f"Problem {status} {is_public}",
        description="Описание проблемы",
        category=Problem.Category.OTHER,
        status=status,
        is_public=is_public,
        rejection_reason="Некорректная заявка"
        if status == Problem.Status.REJECTED
        else "",
    )


@override_settings(PROBLEM_FORM_MIN_SUBMIT_SECONDS=0)
class ProblemFormTests(TestCase):
    # Эти тесты закрывают upload-валидацию и базовую антиспам-защиту формы.
    def test_form_without_photos_is_valid(self):
        form = ProblemForm(data=make_form_data())

        self.assertTrue(form.is_valid(), form.errors.as_data())
        self.assertEqual(form.cleaned_data["photos"], [])

    def test_valid_jpg_png_and_webp_photos_pass(self):
        cases = [
            ("photo.jpg", "JPEG", "image/jpeg"),
            ("photo.png", "PNG", "image/png"),
            ("photo.webp", "WEBP", "image/webp"),
        ]

        for name, image_format, content_type in cases:
            with self.subTest(name=name):
                upload = make_image_upload(name, image_format, content_type)
                form = ProblemForm(
                    data=make_form_data(),
                    files=MultiValueDict({"photos": [upload]}),
                )

                self.assertTrue(form.is_valid(), form.errors.as_data())

    def test_fake_jpg_is_rejected(self):
        upload = SimpleUploadedFile(
            "fake.jpg",
            b"this is not an image",
            content_type="image/jpeg",
        )
        form = ProblemForm(
            data=make_form_data(),
            files=MultiValueDict({"photos": [upload]}),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)

    @override_settings(PROBLEM_PHOTO_MAX_FILES=1)
    def test_photo_count_limit_is_enforced(self):
        uploads = [
            make_image_upload("one.jpg"),
            make_image_upload("two.jpg"),
        ]
        form = ProblemForm(
            data=make_form_data(),
            files=MultiValueDict({"photos": uploads}),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)

    @override_settings(PROBLEM_PHOTO_MAX_SIZE=10)
    def test_photo_size_limit_is_enforced(self):
        upload = make_image_upload("large.jpg")
        form = ProblemForm(
            data=make_form_data(),
            files=MultiValueDict({"photos": [upload]}),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)

    def test_honeypot_blocks_form(self):
        form = ProblemForm(data=make_form_data(website="spam"))

        self.assertFalse(form.is_valid())
        self.assertIn("website", form.errors)

    def test_phone_is_normalized(self):
        form = ProblemForm(data=make_form_data(contact_phone="8 (999) 123-45-67"))

        self.assertTrue(form.is_valid(), form.errors.as_data())
        self.assertEqual(form.cleaned_data["contact_phone"], "+79991234567")

    def test_invalid_phone_is_rejected(self):
        form = ProblemForm(data=make_form_data(contact_phone="12345"))

        self.assertFalse(form.is_valid())
        self.assertIn("contact_phone", form.errors)

    @override_settings(FIELD_ENCRYPTION_KEYS=TEST_FIELD_ENCRYPTION_KEYS)
    def test_phone_is_encrypted_in_database(self):
        form = ProblemForm(data=make_form_data(contact_phone="+7 999 123-45-67"))

        self.assertTrue(form.is_valid(), form.errors.as_data())
        problem = form.save()

        self.assertEqual(problem.contact_phone, "+79991234567")

        with connection.cursor() as cursor:
            cursor.execute(
                "select contact_phone from problems_problem where id = %s",
                [problem.id],
            )
            raw_phone = cursor.fetchone()[0]

        self.assertTrue(raw_phone.startswith("fernet:test-key-1:"))
        self.assertNotIn("+79991234567", raw_phone)

        problem.refresh_from_db()
        self.assertEqual(problem.contact_phone, "+79991234567")


@override_settings(PROBLEM_VOTE_MIN_INTERVAL_SECONDS=0, PROBLEM_LIST_PAGE_SIZE=20)
class ProblemVisibilityAndVotingTests(TestCase):
    # Проверяем публичность отдельно: именно здесь проще всего случайно
    # показать скрытые или ещё не промодерированные заявки.
    def test_hidden_rejected_problem_is_not_public(self):
        public_problem = make_problem(
            Problem.Status.REJECTED,
            is_public=True,
            title="Публично отклонённая",
        )
        hidden_problem = make_problem(
            Problem.Status.REJECTED,
            is_public=False,
            title="Скрытая отклонённая",
        )

        response = self.client.get(reverse("rejected_problems"))

        self.assertContains(response, public_problem.title)
        self.assertNotContains(response, hidden_problem.title)

    def test_public_list_shows_only_active_public_statuses(self):
        sent = make_problem(Problem.Status.SENT, title="Отправленная")
        in_progress = make_problem(Problem.Status.IN_PROGRESS, title="В работе")
        resolved = make_problem(Problem.Status.RESOLVED, title="Решённая")
        rejected = make_problem(Problem.Status.REJECTED, title="Отклонённая")
        new = make_problem(Problem.Status.NEW, title="Новая")
        hidden = make_problem(Problem.Status.SENT, is_public=False, title="Скрытая")

        response = self.client.get(reverse("public_problems"))

        self.assertContains(response, sent.title)
        self.assertContains(response, in_progress.title)
        self.assertNotContains(response, resolved.title)
        self.assertNotContains(response, rejected.title)
        self.assertNotContains(response, new.title)
        self.assertNotContains(response, hidden.title)

    def test_vote_for_active_public_problem_toggles(self):
        problem = make_problem(Problem.Status.SENT)
        url = reverse("upvote_problem", args=[problem.id])
        redirect_url = f"{reverse('public_problems')}#problem-{problem.id}"

        response = self.client.post(url)
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], redirect_url)
        self.assertEqual(problem.votes_count, 1)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 1)

        response = self.client.post(url)
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], redirect_url)
        self.assertEqual(problem.votes_count, 0)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 0)

    def test_ajax_vote_returns_json_without_redirect(self):
        problem = make_problem(Problem.Status.SENT)
        url = reverse("upvote_problem", args=[problem.id])

        response = self.client.post(
            url,
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["problem_id"], problem.id)
        self.assertEqual(payload["votes_count"], 1)
        self.assertIs(payload["voted"], True)
        self.assertEqual(payload["operation"], "added")
        self.assertEqual(payload["min_interval_ms"], 0)
        self.assertEqual(payload["retry_after_ms"], 0)
        self.assertEqual(problem.votes_count, 1)
        self.assertIn(settings.PROBLEM_VOTER_COOKIE_NAME, response.cookies)

        response = self.client.post(
            url,
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["problem_id"], problem.id)
        self.assertEqual(payload["votes_count"], 0)
        self.assertIs(payload["voted"], False)
        self.assertEqual(payload["operation"], "removed")
        self.assertEqual(payload["min_interval_ms"], 0)
        self.assertEqual(payload["retry_after_ms"], 0)
        self.assertEqual(problem.votes_count, 0)

    def test_vote_persists_with_same_voter_cookie_in_new_client(self):
        problem = make_problem(Problem.Status.SENT)
        url = reverse("upvote_problem", args=[problem.id])

        response = self.client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        voter_cookie = response.cookies[settings.PROBLEM_VOTER_COOKIE_NAME].value
        vote = ProblemVote.objects.get(problem=problem)

        self.assertEqual(len(vote.voter_hash), 64)
        self.assertNotEqual(vote.voter_hash, voter_cookie)

        new_client = self.client_class()
        new_client.cookies[settings.PROBLEM_VOTER_COOKIE_NAME] = voter_cookie
        response = new_client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["operation"], "unchanged")
        self.assertEqual(problem.votes_count, 1)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 1)

        response = new_client.post(
            url,
            {"desired_voted": "0"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.json()["operation"], "removed")
        self.assertEqual(problem.votes_count, 0)

    def test_vote_cookie_deletion_creates_new_voter_identity(self):
        problem = make_problem(Problem.Status.SENT)
        url = reverse("upvote_problem", args=[problem.id])

        self.client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        new_client = self.client_class()
        new_client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(problem.votes_count, 2)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 2)

    def test_problem_vote_unique_voter_hash_constraint(self):
        problem = make_problem(Problem.Status.SENT)
        voter_hash = "a" * 64
        ProblemVote.objects.create(
            problem=problem,
            voter_hash=voter_hash,
            session_key="session-one",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProblemVote.objects.create(
                    problem=problem,
                    voter_hash=voter_hash,
                    session_key="session-two",
                )

    def test_ajax_vote_can_set_desired_state_idempotently(self):
        problem = make_problem(Problem.Status.SENT)
        url = reverse("upvote_problem", args=[problem.id])

        response = self.client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["voted"], True)
        self.assertEqual(response.json()["operation"], "added")
        self.assertEqual(problem.votes_count, 1)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 1)

        response = self.client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["voted"], True)
        self.assertEqual(response.json()["operation"], "unchanged")
        self.assertEqual(problem.votes_count, 1)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 1)

        response = self.client.post(
            url,
            {"desired_voted": "0"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["voted"], False)
        self.assertEqual(response.json()["operation"], "removed")
        self.assertEqual(problem.votes_count, 0)
        self.assertEqual(ProblemVote.objects.filter(problem=problem).count(), 0)

    @override_settings(PROBLEM_VOTE_MIN_INTERVAL_SECONDS=10)
    def test_ajax_rate_limit_reports_retry_for_state_changes_only(self):
        problem = make_problem(Problem.Status.SENT)
        url = reverse("upvote_problem", args=[problem.id])

        response = self.client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            url,
            {"desired_voted": "1"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["votes_count"], 1)

        response = self.client.post(
            url,
            {"desired_voted": "0"},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        problem.refresh_from_db()

        payload = response.json()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(payload["error"], "rate_limited")
        self.assertIs(payload["voted"], True)
        self.assertEqual(payload["votes_count"], 1)
        self.assertEqual(payload["min_interval_ms"], 10000)
        self.assertGreater(payload["retry_after_ms"], 0)
        self.assertEqual(problem.votes_count, 1)

    def test_vote_for_non_votable_problem_is_rejected(self):
        cases = [
            make_problem(Problem.Status.NEW),
            make_problem(Problem.Status.RESOLVED),
            make_problem(Problem.Status.REJECTED),
            make_problem(Problem.Status.SENT, is_public=False),
        ]

        for problem in cases:
            with self.subTest(status=problem.status, is_public=problem.is_public):
                response = self.client.post(
                    reverse("upvote_problem", args=[problem.id])
                )

                self.assertEqual(response.status_code, 404)
                problem.refresh_from_db()
                self.assertEqual(problem.votes_count, 0)

    def test_rejected_problem_requires_rejection_reason(self):
        problem = Problem(
            title="Неполная отклонённая заявка",
            description="Описание",
            category=Problem.Category.OTHER,
            status=Problem.Status.REJECTED,
            is_public=True,
        )

        with self.assertRaises(ValidationError) as context:
            problem.full_clean()

        self.assertIn("rejection_reason", context.exception.message_dict)


class PublicPageRenderTests(TestCase):
    def test_public_routes_use_cyrillic_paths(self):
        expected_paths = {
            "public_problems": "/проблемы/",
            "create_problem": "/проблемы/отправить/",
            "problem_success": "/проблемы/успешно/",
            "resolved_problems": "/проблемы/решённые/",
            "rejected_problems": "/проблемы/отклонённые/",
        }

        for url_name, path in expected_paths.items():
            with self.subTest(url_name=url_name):
                self.assertEqual(unquote(reverse(url_name)), path)

    def test_main_form_lists_and_success_pages_render(self):
        url_names = [
            "index",
            "create_problem",
            "problem_success",
            "public_problems",
            "resolved_problems",
            "rejected_problems",
            "health_check",
        ]

        for url_name in url_names:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))

                self.assertEqual(response.status_code, 200)

    def test_public_pages_include_browser_and_mobile_favicons(self):
        response = self.client.get(reverse("index"))

        self.assertContains(response, 'href="/favicon.ico"')
        self.assertContains(response, "favicon/favicon-32x32.png")
        self.assertContains(response, "favicon/favicon-16x16.png")
        self.assertContains(response, "favicon/apple-touch-icon.png")
        self.assertContains(response, 'href="/site.webmanifest"')
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, 'rel="shortcut icon"')

    def test_root_favicon_returns_real_multisize_ico_without_redirect(self):
        response = self.client.get("/favicon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/x-icon")
        self.assertEqual(response.content[:4], b"\x00\x00\x01\x00")
        self.assertNotIn("Location", response)
        self.assertIn("must-revalidate", response["Cache-Control"])

    def test_webmanifest_returns_required_icons_and_mime_type(self):
        response = self.client.get(reverse("site_webmanifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = json.loads(response.content)
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["display"], "browser")
        self.assertEqual(
            {icon["sizes"] for icon in manifest["icons"]},
            {"192x192", "512x512"},
        )
        self.assertTrue(
            all(icon["src"].startswith("/assets/") for icon in manifest["icons"])
        )

    def test_public_html_uses_logical_assets_and_revalidates(self):
        for url_name in ["index", "public_problems", "create_problem"]:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                body = response.content.decode()

                self.assertIn("/assets/", body)
                self.assertNotIn("/static/", body)
                self.assertNotIn("/staticfiles/", body)
                self.assertNotIn("/media/", body)
                self.assertIn("no-cache", response["Cache-Control"])
                self.assertNotIn("immutable", response["Cache-Control"])

    def test_public_listing_does_not_add_queries_per_problem(self):
        for index in range(settings.PROBLEM_LIST_PAGE_SIZE):
            problem = make_problem(
                Problem.Status.SENT,
                is_public=True,
                title=f"Query regression {index}",
            )
            ProblemPhoto.objects.create(
                problem=problem,
                image=f"problem_photos/query-{index}.jpg",
            )

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("public_problems"))

        self.assertEqual(response.status_code, 200)
        sql_queries = [query["sql"] for query in captured.captured_queries]
        problem_selects = [
            query
            for query in sql_queries
            if 'FROM "problems_problem"' in query
        ]
        photo_selects = [
            query
            for query in sql_queries
            if 'FROM "problems_problemphoto"' in query
        ]
        vote_selects = [
            query
            for query in sql_queries
            if 'FROM "problems_problemvote"' in query
        ]

        self.assertEqual(len(problem_selects), 2)
        self.assertEqual(len(photo_selects), 1)
        self.assertEqual(len(vote_selects), 1)

    def test_percent_encoded_cyrillic_url_renders(self):
        response = self.client.get(reverse("public_problems"))

        self.assertEqual(response.status_code, 200)

    def test_not_found_page_keeps_global_favicon_head(self):
        response = self.client.get("/missing-page-for-favicon/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            'href="/favicon.ico"',
            status_code=404,
        )
        self.assertContains(
            response,
            'href="/site.webmanifest"',
            status_code=404,
        )

    def test_legacy_latin_urls_redirect_permanently(self):
        cases = [
            ("/problems/?page=2", "/проблемы/?page=2"),
            ("/problems/send/", "/проблемы/отправить/"),
            ("/problems/success/", "/проблемы/успешно/"),
            ("/problems/resolved/", "/проблемы/решённые/"),
            ("/problems/rejected/", "/проблемы/отклонённые/"),
        ]

        for old_url, new_url in cases:
            with self.subTest(old_url=old_url):
                response = self.client.get(old_url)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(unquote(response["Location"]), new_url)

    def test_legacy_vote_post_is_not_redirected(self):
        problem = make_problem(Problem.Status.SENT)
        response = self.client.post(f"/problems/{problem.id}/upvote/")

        self.assertEqual(response.status_code, 405)

    @override_settings(PROBLEM_FORM_MIN_SUBMIT_SECONDS=0)
    def test_form_posts_to_cyrillic_url(self):
        response = self.client.post(
            reverse("create_problem"),
            make_form_data(title="Кириллический POST"),
        )

        self.assertRedirects(response, reverse("problem_success"))

    @override_settings(PROBLEM_FORM_MIN_SUBMIT_SECONDS=0)
    def test_ajax_form_post_returns_success_redirect_url(self):
        phone = "+79991234567"
        response = self.client.post(
            reverse("create_problem"),
            make_form_data(title="AJAX POST", contact_phone=phone),
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {"redirect_url": reverse("problem_success")},
        )
        self.assertTrue(Problem.objects.filter(title="AJAX POST").exists())
        self.assertNotIn(phone, response.content.decode())

    @override_settings(PROBLEM_FORM_MIN_SUBMIT_SECONDS=0)
    def test_page_transition_form_post_returns_success_html(self):
        phone = "+79991234567"
        response = self.client.post(
            reverse("create_problem"),
            make_form_data(title="HTML transition POST", contact_phone=phone),
            HTTP_ACCEPT="text/html",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["X-Redirect-URL"], reverse("problem_success"))
        self.assertContains(response, "Обращение отправлено", status_code=201)
        self.assertTrue(
            Problem.objects.filter(title="HTML transition POST").exists()
        )
        self.assertNotIn(phone, response.content.decode())

    def test_phone_is_not_rendered_on_public_pages(self):
        phone = "+79991234567"
        make_problem(
            Problem.Status.SENT,
            is_public=True,
            title="Публичная без телефона",
        )
        problem = Problem.objects.get(title="Публичная без телефона")
        problem.contact_phone = phone
        problem.save(update_fields=["contact_phone"])

        response = self.client.get(reverse("public_problems"))

        self.assertNotContains(response, phone)

    def test_phone_is_not_returned_by_vote_json(self):
        phone = "+79991234567"
        problem = make_problem(Problem.Status.SENT)
        problem.contact_phone = phone
        problem.save(update_fields=["contact_phone"])

        response = self.client.post(
            reverse("upvote_problem", args=[problem.id]),
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("phone", response.content.decode())
        self.assertNotIn(phone, response.content.decode())


class ProblemMediaCleanupTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def test_related_files_are_deleted_when_problem_is_deleted(self):
        problem = make_problem(Problem.Status.SENT)
        photo = ProblemPhoto.objects.create(
            problem=problem,
            image=make_image_upload("cleanup.jpg"),
        )
        evidence_file = ProblemEvidenceFile.objects.create(
            problem=problem,
            file=SimpleUploadedFile(
                "cleanup.pdf",
                b"%PDF-1.4\n%test\n",
                content_type="application/pdf",
            ),
            original_name="cleanup.pdf",
        )

        photo_path = Path(photo.image.path)
        evidence_path = Path(evidence_file.file.path)

        self.assertTrue(photo_path.exists())
        self.assertTrue(evidence_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            problem.delete()

        self.assertFalse(photo_path.exists())
        self.assertFalse(evidence_path.exists())


class ProtectedAttachmentAccessTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(
            MEDIA_ROOT=self.media_dir.name,
            PROTECTED_MEDIA_USE_X_ACCEL=False,
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def make_photo(self, problem):
        upload = make_image_upload("secret-family-name.jpg")

        return ProblemPhoto.objects.create(
            problem=problem,
            image=upload,
            content_type=upload.content_type,
            file_size=upload.size,
        )

    def make_evidence(self, problem):
        upload = SimpleUploadedFile(
            "resident-response.pdf",
            b"%PDF-1.4\n%test\n",
            content_type="application/pdf",
        )

        return ProblemEvidenceFile.objects.create(
            problem=problem,
            file=upload,
            original_name="resident-response.pdf",
            content_type=upload.content_type,
            file_size=upload.size,
        )

    def make_attachment_url(self, user, attachment, action):
        self.client.force_login(user)
        request = RequestFactory().get("/")
        request.user = user
        request.session = self.client.session
        token = make_attachment_token(request, attachment, action)

        return reverse("attachment_access", args=[attachment.public_id, action, token])

    def make_public_photo_token_for_client(self, photo):
        session = self.client.session

        if not session.session_key:
            session.create()

        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

        request = RequestFactory().post("/")
        request.session = session

        return make_public_photo_token(request, photo)

    def extract_public_photo_url(self, response):
        match = re.search(
            r'<img[^>]+src="([^"]*/%D0%B2%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F/%D1%84%D0%BE%D1%82%D0%BE/[^"]+)"',
            response.content.decode(),
        )

        self.assertIsNotNone(match)

        return match.group(1)

    def test_public_html_does_not_expose_media_path_name_or_uuid(self):
        problem = make_problem(
            Problem.Status.SENT,
            is_public=True,
            title="Проблема с фото",
        )
        photo = self.make_photo(problem)

        response = self.client.get(reverse("public_problems"))
        body = response.content.decode()

        self.assertContains(
            response,
            "/%D0%B2%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F/%D1%84%D0%BE%D1%82%D0%BE/",
        )
        self.assertNotContains(response, "data-protected-photo")
        self.assertNotContains(response, "data-photo-token")
        self.assertNotContains(response, "data-photo-endpoint")
        self.assertNotRegex(body, r"<img[^>]+data-problem-id")
        self.assertNotIn("data-photo-position", body)
        self.assertNotIn("/media/", body)
        self.assertNotIn(photo.image.name, body)
        self.assertNotIn(str(photo.public_id), body)
        self.assertNotIn("secret-family-name", body)

    def test_public_photo_is_fetched_through_checked_endpoint(self):
        problem = make_problem(Problem.Status.SENT, is_public=True)
        self.make_photo(problem)

        page_response = self.client.get(reverse("public_problems"))
        photo_url = self.extract_public_photo_url(page_response)
        response = self.client.get(photo_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(str(settings.MEDIA_ROOT), str(response.headers))

    def test_private_photo_is_not_available_to_public_photo_fetch(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        photo = self.make_photo(problem)
        token = self.make_public_photo_token_for_client(photo)

        response = self.client.get(reverse("public_problem_photo", args=[token]))

        self.assertEqual(response.status_code, 404)

    def test_public_photo_access_stops_after_problem_is_hidden(self):
        problem = make_problem(Problem.Status.SENT, is_public=True)
        self.make_photo(problem)
        page_response = self.client.get(reverse("public_problems"))
        photo_url = self.extract_public_photo_url(page_response)

        problem.is_public = False
        problem.save(update_fields=["is_public"])

        response = self.client.get(photo_url)

        self.assertEqual(response.status_code, 404)

    def test_public_photo_token_is_bound_to_session(self):
        problem = make_problem(Problem.Status.SENT, is_public=True)
        self.make_photo(problem)
        page_response = self.client.get(reverse("public_problems"))
        photo_url = self.extract_public_photo_url(page_response)
        copied_client = self.client_class()

        response = copied_client.get(photo_url)

        self.assertEqual(response.status_code, 404)

    def test_invalid_public_photo_token_is_rejected(self):
        response = self.client.get(
            reverse("public_problem_photo", args=["not-a-valid-token"])
        )

        self.assertEqual(response.status_code, 404)

    def test_direct_media_and_internal_routes_are_not_django_public_routes(self):
        problem = make_problem(Problem.Status.SENT, is_public=True)
        photo = self.make_photo(problem)

        for path in [
            f"/media/{photo.image.name}",
            f"/protected-media/{photo.image.name}",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 404)

    def test_superuser_can_view_evidence_with_session_bound_token(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        evidence = self.make_evidence(problem)
        user = get_user_model().objects.create_superuser(
            username="root-admin",
            email="root@example.com",
            password="password",
        )
        url = self.make_attachment_url(user, evidence, "view")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(
            AttachmentAccessAudit.objects.filter(
                attachment_public_id=evidence.public_id,
                success=True,
            ).exists()
        )

    def test_copied_admin_link_fails_without_original_session(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        evidence = self.make_evidence(problem)
        user = get_user_model().objects.create_superuser(
            username="copy-admin",
            email="copy@example.com",
            password="password",
        )
        url = self.make_attachment_url(user, evidence, "view")
        copied_client = self.client_class()
        copied_client.force_login(user)

        response = copied_client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_staff_without_attachment_permission_is_denied(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        evidence = self.make_evidence(problem)
        user = get_user_model().objects.create_user(
            username="limited-staff",
            password="password",
            is_staff=True,
        )
        user.user_permissions.add(Permission.objects.get(codename="view_problem"))
        url = self.make_attachment_url(user, evidence, "view")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            AttachmentAccessAudit.objects.filter(
                attachment_public_id=evidence.public_id,
                success=False,
            ).exists()
        )

    def test_staff_with_permission_can_download_evidence(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        evidence = self.make_evidence(problem)
        user = get_user_model().objects.create_user(
            username="evidence-staff",
            password="password",
            is_staff=True,
        )
        user.user_permissions.set(
            Permission.objects.filter(
                codename__in=[
                    "view_problem",
                    "download_problem_evidence_file",
                ]
            )
        )
        url = self.make_attachment_url(user, evidence, "download")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("resident-response.pdf", response["Content-Disposition"])

    def test_expired_attachment_token_is_rejected(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        evidence = self.make_evidence(problem)
        user = get_user_model().objects.create_superuser(
            username="expired-admin",
            email="expired@example.com",
            password="password",
        )
        url = self.make_attachment_url(user, evidence, "view")

        with patch(
            "django.core.signing.time.time",
            return_value=time.time() + ATTACHMENT_TOKEN_MAX_AGE + 5,
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    @override_settings(PROTECTED_MEDIA_USE_X_ACCEL=True)
    def test_authorized_attachment_uses_x_accel_redirect(self):
        problem = make_problem(Problem.Status.NEW, is_public=False)
        photo = self.make_photo(problem)
        user = get_user_model().objects.create_superuser(
            username="photo-admin",
            email="photo@example.com",
            password="password",
        )
        url = self.make_attachment_url(user, photo, "view")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["X-Accel-Redirect"].startswith("/protected-media/"))
        self.assertNotIn(str(settings.MEDIA_ROOT), response["X-Accel-Redirect"])
        self.assertNotIn("/media/", response["X-Accel-Redirect"])


class ProblemAdminTests(TestCase):
    # Регрессия на admin changelist: бейджи должны рендериться в шаблоне Django admin.
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def test_problem_changelist_renders_with_badges(self):
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        make_problem(Problem.Status.SENT, is_public=True)
        make_problem(Problem.Status.NEW, is_public=False)

        response = self.client.get(reverse("admin:problems_problem_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "for_admin/css/main.css")
        self.assertContains(response, 'href="/favicon.ico"')
        self.assertContains(response, "favicon/apple-touch-icon.png")
        self.assertContains(response, 'href="/site.webmanifest"')
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, "Публично")
        self.assertContains(response, "Скрыто")

    def test_staff_without_contact_permission_does_not_see_phone(self):
        phone = "+79991234567"
        problem = make_problem(Problem.Status.SENT)
        problem.contact_phone = phone
        problem.save(update_fields=["contact_phone"])
        user = get_user_model().objects.create_user(
            username="moderator",
            password="password",
            is_staff=True,
        )
        user.user_permissions.add(Permission.objects.get(codename="view_problem"))
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:problems_problem_change", args=[problem.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Недостаточно прав")
        self.assertNotContains(response, phone)

    def test_staff_with_contact_permission_sees_phone(self):
        phone = "+79991234567"
        problem = make_problem(Problem.Status.SENT)
        problem.contact_phone = phone
        problem.save(update_fields=["contact_phone"])
        user = get_user_model().objects.create_user(
            username="contact-moderator",
            password="password",
            is_staff=True,
        )
        permissions = Permission.objects.filter(
            codename__in=["view_problem", "view_problem_contact_phone"]
        )
        user.user_permissions.set(permissions)
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:problems_problem_change", args=[problem.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, phone)

    def test_mark_public_skips_new_and_rejected_without_reason(self):
        request = RequestFactory().post("/")
        SessionMiddleware(lambda inner_request: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        model_admin = ProblemAdmin(Problem, admin.site)
        new_problem = make_problem(
            Problem.Status.NEW,
            is_public=False,
            title="Сырая заявка",
        )
        rejected_without_reason = Problem.objects.create(
            title="Без причины",
            description="Описание",
            category=Problem.Category.OTHER,
            status=Problem.Status.REJECTED,
            is_public=False,
        )
        sent_problem = make_problem(
            Problem.Status.SENT,
            is_public=False,
            title="Проверенная заявка",
        )

        model_admin.mark_public(
            request,
            Problem.objects.filter(
                id__in=[
                    new_problem.id,
                    rejected_without_reason.id,
                    sent_problem.id,
                ]
            ),
        )

        new_problem.refresh_from_db()
        rejected_without_reason.refresh_from_db()
        sent_problem.refresh_from_db()

        self.assertFalse(new_problem.is_public)
        self.assertFalse(rejected_without_reason.is_public)
        self.assertTrue(sent_problem.is_public)

    def test_problem_change_inline_file_inputs_only_for_new_forms(self):
        problem = make_problem(Problem.Status.SENT)
        photo = ProblemPhoto.objects.create(
            problem=problem,
            image=make_image_upload("saved-photo.jpg"),
            content_type="image/jpeg",
            file_size=123,
        )
        evidence_file = ProblemEvidenceFile.objects.create(
            problem=problem,
            file=SimpleUploadedFile(
                "saved-document.pdf",
                b"%PDF-1.4\n%test\n",
                content_type="application/pdf",
            ),
            original_name="saved-document.pdf",
            content_type="application/pdf",
            file_size=19,
        )
        user = get_user_model().objects.create_superuser(
            username="inline-admin",
            email="inline@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:problems_problem_change", args=[problem.id])
        )
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertNotIn('name="photos-0-image"', body)
        self.assertNotIn('name="evidence_files-0-file"', body)
        self.assertIn('name="photos-__prefix__-image"', body)
        self.assertIn('name="evidence_files-__prefix__-file"', body)
        self.assertIn('type="file"', body)
        self.assertIn("Фотография уже сохранена", body)
        self.assertIn("Документ уже сохранён", body)
        self.assertIn("Открыть", body)
        self.assertIn("image/jpeg,image/png,image/webp", body)
        self.assertIn("application/pdf", body)
        self.assertNotIn(photo.image.name, body)
        self.assertNotIn(evidence_file.file.name, body)

    def test_admin_photo_upload_uses_safe_image_processing(self):
        problem = make_problem(Problem.Status.SENT)
        upload = make_image_upload("unsafe name.jpg", size=(256, 128))
        form = ProblemPhotoAdminForm(
            data={"problem": problem.id},
            files={"image": upload},
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())

        photo = form.save()

        self.assertEqual(photo.problem, problem)
        self.assertEqual(photo.content_type, "image/jpeg")
        self.assertGreater(photo.file_size, 0)
        self.assertTrue(photo.image.name.startswith("problem_photos/"))
        self.assertNotIn("unsafe name", photo.image.name)

    def test_admin_photo_form_does_not_replace_existing_file(self):
        problem = make_problem(Problem.Status.SENT)
        photo = ProblemPhoto.objects.create(
            problem=problem,
            image=make_image_upload("original.jpg"),
            content_type="image/jpeg",
            file_size=123,
        )
        original_name = photo.image.name
        form = ProblemPhotoAdminForm(
            data={"problem": problem.id},
            files={"image": make_image_upload("forged.jpg")},
            instance=photo,
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())

        form.save()
        photo.refresh_from_db()

        self.assertEqual(photo.image.name, original_name)

    def test_admin_photo_upload_rejects_dangerous_double_extension(self):
        problem = make_problem(Problem.Status.SENT)
        upload = make_image_upload("payload.svg.jpg")
        form = ProblemPhotoAdminForm(
            data={"problem": problem.id},
            files={"image": upload},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_admin_evidence_upload_uses_safe_document_processing(self):
        problem = make_problem(Problem.Status.SENT)
        upload = SimpleUploadedFile(
            "resident answer.pdf",
            b"%PDF-1.4\n%test\n",
            content_type="application/pdf",
        )
        form = ProblemEvidenceFileAdminForm(
            data={"problem": problem.id},
            files={"file": upload},
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())

        evidence_file = form.save()

        self.assertEqual(evidence_file.problem, problem)
        self.assertEqual(evidence_file.original_name, "resident answer.pdf")
        self.assertEqual(evidence_file.content_type, "application/pdf")
        self.assertGreater(evidence_file.file_size, 0)
        self.assertTrue(evidence_file.file.name.startswith("problem_evidence/"))
        self.assertNotIn("resident answer", evidence_file.file.name)

    def test_admin_evidence_form_does_not_replace_existing_file(self):
        problem = make_problem(Problem.Status.SENT)
        evidence_file = ProblemEvidenceFile.objects.create(
            problem=problem,
            file=SimpleUploadedFile(
                "original.pdf",
                b"%PDF-1.4\n%test\n",
                content_type="application/pdf",
            ),
            original_name="original.pdf",
            content_type="application/pdf",
            file_size=19,
        )
        original_name = evidence_file.file.name
        form = ProblemEvidenceFileAdminForm(
            data={"problem": problem.id},
            files={
                "file": SimpleUploadedFile(
                    "forged.pdf",
                    b"%PDF-1.4\n%test\n",
                    content_type="application/pdf",
                )
            },
            instance=evidence_file,
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())

        form.save()
        evidence_file.refresh_from_db()

        self.assertEqual(evidence_file.file.name, original_name)

    def test_admin_evidence_upload_rejects_dangerous_double_extension(self):
        problem = make_problem(Problem.Status.SENT)
        upload = SimpleUploadedFile(
            "payload.js.pdf",
            b"%PDF-1.4\n%test\n",
            content_type="application/pdf",
        )
        form = ProblemEvidenceFileAdminForm(
            data={"problem": problem.id},
            files={"file": upload},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("file", form.errors)

    def test_problem_admin_can_add_photo_and_document_once_then_hide_inputs(self):
        problem = make_problem(Problem.Status.SENT)
        user = get_user_model().objects.create_superuser(
            username="upload-admin",
            email="upload@example.com",
            password="password",
        )
        self.client.force_login(user)
        change_url = reverse("admin:problems_problem_change", args=[problem.id])
        data = {
            "title": problem.title,
            "description": problem.description,
            "address": problem.address,
            "category": problem.category,
            "status": problem.status,
            "is_public": "on",
            "rejection_reason": "",
            "contact_phone": "",
            "has_prior_attempts": "",
            "photos-TOTAL_FORMS": "1",
            "photos-INITIAL_FORMS": "0",
            "photos-MIN_NUM_FORMS": "0",
            "photos-MAX_NUM_FORMS": "1000",
            "photos-0-id": "",
            "photos-0-image": make_image_upload("admin-photo.jpg"),
            "evidence_files-TOTAL_FORMS": "1",
            "evidence_files-INITIAL_FORMS": "0",
            "evidence_files-MIN_NUM_FORMS": "0",
            "evidence_files-MAX_NUM_FORMS": "1000",
            "evidence_files-0-id": "",
            "evidence_files-0-file": SimpleUploadedFile(
                "admin-document.pdf",
                b"%PDF-1.4\n%test\n",
                content_type="application/pdf",
            ),
            "_save": "Сохранить",
        }

        response = self.client.post(change_url, data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(problem.photos.count(), 1)
        self.assertEqual(problem.evidence_files.count(), 1)

        response = self.client.get(change_url)
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('name="photos-0-image"', body)
        self.assertNotIn('name="evidence_files-0-file"', body)
        self.assertIn('name="photos-__prefix__-image"', body)
        self.assertIn('name="evidence_files-__prefix__-file"', body)


class ImageOptimizationTests(TestCase):
    @override_settings(
        PROBLEM_FORM_MIN_SUBMIT_SECONDS=0,
        PROBLEM_IMAGE_MAX_WIDTH=64,
        PROBLEM_IMAGE_MAX_HEIGHT=64,
        PROBLEM_IMAGE_MAX_PIXELS=100000,
    )
    def test_uploaded_image_is_resized(self):
        upload = make_image_upload("large.jpg", size=(256, 128))
        form = ProblemForm(
            data=make_form_data(),
            files=MultiValueDict({"photos": [upload]}),
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())

        optimized = form.cleaned_data["photos"][0]
        image = Image.open(BytesIO(optimized.read()))

        self.assertLessEqual(max(image.size), 64)

    @override_settings(PROBLEM_FORM_MIN_SUBMIT_SECONDS=0, PROBLEM_IMAGE_MAX_PIXELS=10)
    def test_too_many_pixels_image_is_rejected(self):
        upload = make_image_upload("too-large.jpg", size=(12, 12))
        form = ProblemForm(
            data=make_form_data(),
            files=MultiValueDict({"photos": [upload]}),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)


class DeploymentProtectionTests(TestCase):
    def test_django_does_not_serve_sensitive_paths(self):
        dangerous_paths = [
            "/db.sqlite3",
            "/db.sqlite3-wal",
            "/db.sqlite3-shm",
            "/.env",
            "/backups/np-site.tar.age",
            "/secrets/backup-age-identity.txt",
            "/%2e%2e/db.sqlite3",
            "/%252e%252e/db.sqlite3",
        ]

        for path in dangerous_paths:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertIn(response.status_code, {400, 404})
                self.assertNotContains(
                    response,
                    "SQLite format",
                    status_code=response.status_code,
                )

    def test_nginx_denies_dangerous_paths_and_has_no_database_volume(self):
        nginx_config = Path(
            "deploy/nginx/templates/production.conf.template"
        ).read_text()
        compose_config = Path("docker-compose.yml").read_text()
        dockerignore = Path(".dockerignore").read_text()
        nginx_http_preamble = nginx_config.split("upstream django_app", 1)[0]

        for pattern in [
            ".env",
            "docker-compose",
            "sqlite",
            "sqlite(?:3)?(?:-wal|-shm)?",
            "db(?:-wal|-shm)?",
            "backup",
            "scripts",
            "map",
        ]:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, nginx_config)

        nginx_service = compose_config.split("  nginx:", 1)[1].split("  certbot:", 1)[0]

        self.assertIn("server_tokens off", nginx_config)
        self.assertNotIn("sendfile on;", nginx_http_preamble)
        self.assertNotIn("keepalive_timeout", nginx_http_preamble)
        self.assertIn("autoindex off", nginx_config)
        self.assertIn("X-Content-Type-Options", nginx_config)
        self.assertIn("internal;", nginx_config)
        self.assertIn("disable_symlinks on from=/srv/protected-media", nginx_config)
        blocked_prefix_locations = [
            line
            for line in nginx_config.splitlines()
            if "location ~* ^/(?:app|srv|var|home" in line
        ]
        self.assertEqual(len(blocked_prefix_locations), 2)
        for location in blocked_prefix_locations:
            self.assertNotIn("|problems|", location)
        self.assertIn("/srv/compiled-static:ro", nginx_service)
        self.assertIn("/srv/protected-media:ro", nginx_service)
        self.assertNotIn("/app/data", nginx_service)
        self.assertNotIn("/app/media", nginx_service)
        self.assertNotIn("/app/staticfiles", nginx_service)
        self.assertIn("*.sqlite", dockerignore)
        self.assertIn("*.db-wal", dockerignore)

    def test_static_cache_and_privacy_policies_are_separate(self):
        nginx_config = Path(
            "deploy/nginx/templates/production.conf.template"
        ).read_text()

        self.assertIn("location /assets/", nginx_config)
        self.assertIn("location = /assets/", nginx_config)
        self.assertIn("max-age=31536000, immutable", nginx_config)
        self.assertIn("max-age=300, must-revalidate", nginx_config)
        self.assertIn('location = /favicon.ico', nginx_config)
        self.assertIn('location = /site.webmanifest', nginx_config)
        self.assertIn('application/manifest+json', nginx_config)
        self.assertIn('gzip_vary on', nginx_config)
        self.assertIn('application/manifest+json', nginx_config)
        self.assertIn('Cache-Control "private, no-store', nginx_config)
        self.assertIn('log_format privacy', nginx_config)
        self.assertNotIn('$request_uri $status', nginx_config)

    def test_acme_nginx_template_is_http_only_and_does_not_expose_the_app(self):
        acme_config = Path("deploy/nginx/templates/acme.conf.template").read_text()

        self.assertIn("listen 80;", acme_config)
        self.assertIn("listen [::]:80;", acme_config)
        self.assertIn("server_name ${NGINX_SERVER_NAME};", acme_config)
        self.assertIn(
            r"location ~ ^/\.well-known/acme-challenge/[A-Za-z0-9_-]+$",
            acme_config,
        )
        self.assertIn("root /var/www/certbot;", acme_config)
        self.assertIn("try_files $uri =404;", acme_config)
        self.assertIn("disable_symlinks on from=/var/www/certbot;", acme_config)
        self.assertIn("limit_except GET HEAD", acme_config)
        self.assertIn("server_tokens off;", acme_config)
        self.assertIn("autoindex off;", acme_config)
        self.assertIn("return 404;", acme_config)

        for forbidden_directive in [
            "listen 443",
            "ssl_certificate",
            "ssl_certificate_key",
            "proxy_pass",
            "django_app",
        ]:
            with self.subTest(forbidden_directive=forbidden_directive):
                self.assertNotIn(forbidden_directive, acme_config)

    def test_compose_selects_one_nginx_template_and_shares_only_acme_storage(self):
        compose_config = Path("docker-compose.yml").read_text()
        nginx_service = compose_config.split("  nginx:", 1)[1].split(
            "  certbot:", 1
        )[0]
        certbot_service = compose_config.split("  certbot:", 1)[1].split(
            "networks:", 1
        )[0]
        web_service = compose_config.split("  web:", 1)[1].split("  nginx:", 1)[0]

        self.assertIn(
            "./deploy/nginx/templates/${NGINX_TEMPLATE:-production.conf.template}"
            ":/etc/nginx/templates/default.conf.template:ro",
            nginx_service,
        )
        self.assertNotIn(
            "./deploy/nginx/templates:/etc/nginx/templates:ro",
            nginx_service,
        )
        self.assertIn(
            "${NP_SITE_ROOT:-/srv/np-site}/certbot/www:/var/www/certbot:ro",
            nginx_service,
        )
        self.assertIn(
            "${NP_SITE_ROOT:-/srv/np-site}/letsencrypt:/etc/letsencrypt:ro",
            nginx_service,
        )
        self.assertIn(
            "${NP_SITE_ROOT:-/srv/np-site}/certbot/www:/var/www/certbot",
            certbot_service,
        )
        self.assertIn(
            "${NP_SITE_ROOT:-/srv/np-site}/letsencrypt:/etc/letsencrypt",
            certbot_service,
        )
        self.assertNotIn("env_file:", certbot_service)
        self.assertNotIn("/app/data", certbot_service)
        self.assertNotIn("/var/www/certbot", web_service)
        self.assertNotIn("/etc/letsencrypt", web_service)
        self.assertIn("CERTBOT_CERTIFICATE_NAME", compose_config)

    def test_gunicorn_does_not_log_token_bearing_request_paths(self):
        dockerfile = Path("Dockerfile").read_text()
        compose_config = Path("docker-compose.yml").read_text()

        self.assertNotIn("--access-logfile", dockerfile)
        self.assertIn("--no-control-socket", dockerfile)
        self.assertIn("--no-control-socket", compose_config)
        self.assertNotIn("--access-logfile", compose_config)
        self.assertIn("--error-logfile", dockerfile)
        self.assertIn("--error-logfile", compose_config)

    def test_runtime_image_and_compose_are_hardened(self):
        dockerfile = Path("Dockerfile").read_text()
        compose_config = Path("docker-compose.yml").read_text()
        dockerignore = Path(".dockerignore").read_text()

        self.assertNotIn("COPY --chown=app:app . .", dockerfile)

        for copied_path in [
            "COPY --chown=app:app config ./config",
            "COPY --chown=app:app problems ./problems",
            "COPY --chown=app:app static ./static",
            "COPY --chown=app:app templates ./templates",
        ]:
            with self.subTest(copied_path=copied_path):
                self.assertIn(copied_path, dockerfile)

        for ignored_path in [
            ".codex/",
            ".agents/",
            "deploy/",
            "scripts/",
            "env.example",
            "problems/tests.py",
        ]:
            with self.subTest(ignored_path=ignored_path):
                self.assertIn(ignored_path, dockerignore)

        for service_name in ["web", "nginx", "certbot"]:
            service_match = re.search(
                rf"^  {service_name}:\n(?P<body>.*?)(?=^  [\w-]+:|^networks:|\Z)",
                compose_config,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(service_match)
            service_block = service_match.group("body")

            with self.subTest(service_name=service_name):
                self.assertIn("init: true", service_block)
                self.assertIn("no-new-privileges:true", service_block)
                self.assertIn("cap_drop:", service_block)
                self.assertIn("- ALL", service_block)
                self.assertIn("mem_limit:", service_block)
                self.assertIn("cpus:", service_block)

        certbot_block = compose_config.split("  certbot:", 1)[1].split("networks:", 1)[0]

        self.assertIn("read_only: true", certbot_block)
        self.assertIn("/var/lib/letsencrypt", certbot_block)
        self.assertIn("/var/log/letsencrypt", certbot_block)

    def test_backup_restore_rejects_unsafe_tar_members_before_extracting(self):
        verify_script = Path("scripts/verify-backup.sh").read_text()
        restore_script = Path("scripts/restore.sh").read_text()

        for script in [verify_script, restore_script]:
            with self.subTest(script=script[:30]):
                self.assertIn("validate_tar_archive", script)
                self.assertIn("member.isdev()", script)
                self.assertIn("member.issym() or member.islnk()", script)
                self.assertIn("posixpath.join(posixpath.dirname(normalized_name)", script)
                self.assertIn("name.startswith(\"/\")", script)
                self.assertIn("normalized_name.startswith(\"../\")", script)
                self.assertLess(
                    script.index("validate_tar_archive \"$TMP_DIR/backup.tar\""),
                    script.index("tar -C \"$TMP_DIR/payload\" -xf"),
                )

    def test_logging_filter_redacts_public_photo_tokens(self):
        for path_prefix in [
            "/вложения/фото/",
            (
                "/%D0%B2%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F"
                "/%D1%84%D0%BE%D1%82%D0%BE/"
            ),
            (
                "/\\u0432\\u043b\\u043e\\u0436\\u0435\\u043d\\u0438\\u044f"
                "/\\u0444\\u043e\\u0442\\u043e/"
            ),
        ]:
            with self.subTest(path_prefix=path_prefix):
                record = logging.LogRecord(
                    name="django.request",
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=1,
                    msg=(
                        f"Not Found: {path_prefix}"
                        "eyJwaG90byI6IjEyMyIsInNlc3Npb24iOiJhYmMifQ:1abc:def/"
                    ),
                    args=(),
                    exc_info=None,
                )

                self.assertTrue(SensitiveValueFilter().filter(record))

                message = record.getMessage()

                self.assertIn(f"{path_prefix}[photo token redacted]", message)
                self.assertNotIn("eyJwaG90by", message)

    def test_static_settings_use_manifest_storage_for_production(self):
        settings_source = Path("config/settings.py").read_text()

        self.assertIn("ManifestStaticFilesStorage", settings_source)
        self.assertIn("STATIC_ROOT", settings_source)
        self.assertIn('STATIC_URL = "/assets/"', settings_source)
        self.assertIn('PRAGMA journal_mode=WAL', settings_source)
        self.assertIn('PRAGMA synchronous=FULL', settings_source)


class WebSecurityRegressionTests(TestCase):
    def test_csrf_is_required_for_public_writes(self):
        client = Client(enforce_csrf_checks=True)
        problem = make_problem(Problem.Status.SENT, is_public=True)

        form_response = client.post(reverse("create_problem"), make_form_data())
        vote_response = client.post(
            reverse("upvote_problem", args=[problem.id]),
            {"desired_voted": "true"},
        )

        self.assertEqual(form_response.status_code, 403)
        self.assertEqual(vote_response.status_code, 403)

    @override_settings(ALLOWED_HOSTS=["allowed.test"])
    def test_unknown_host_is_rejected(self):
        response = self.client.get("/", HTTP_HOST="attacker.invalid")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("attacker.invalid", response.content.decode())

    def test_stored_user_text_is_escaped(self):
        payload = '<img src=x onerror="alert(1)">'
        make_problem(
            Problem.Status.SENT,
            is_public=True,
            title=payload,
        )

        response = self.client.get(reverse("public_problems"))
        body = response.content.decode()

        self.assertNotIn(payload, body)
        self.assertNotIn('src=x onerror="alert(1)"', body)
        self.assertIn("&lt;img", body)

    def test_legacy_redirect_cannot_become_external_redirect(self):
        response = self.client.get("/problems/?next=https://attacker.invalid/")

        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("/%D0%BF"))
        self.assertNotRegex(response["Location"], r"^https?://")

    def test_static_source_maps_and_physical_paths_are_not_present(self):
        forbidden_suffixes = {".map", ".bak", ".old", ".tmp"}
        forbidden_names = {".env", "db.sqlite3"}

        for path in Path("static").rglob("*"):
            if not path.is_file():
                continue

            with self.subTest(path=path):
                self.assertNotIn(path.suffix, forbidden_suffixes)
                self.assertNotIn(path.name, forbidden_names)


class FrontendSourceTests(TestCase):
    def test_project_css_disables_global_hyphenation(self):
        css_paths = [
            Path("static/for_users/css/main.css"),
            Path("static/for_users/css/main_page.css"),
            Path("static/for_admin/css/main.css"),
        ]
        css = "\n".join(path.read_text() for path in css_paths)

        self.assertNotIn("hyphens: auto", css)
        self.assertNotIn("word-break: break-all", css)
        self.assertIn("hyphens: none", css)
        self.assertIn("overflow-wrap: anywhere", css)

    def test_problem_nav_autoscroll_logic_is_initialized_once(self):
        html = Path("templates/base.html").read_text()
        css = Path("static/for_users/css/main_page.css").read_text()
        script = Path("static/for_users/js/main.js").read_text()

        self.assertIn("data-problem-nav", html)
        self.assertNotIn("data-problem-nav-track", html)
        self.assertIn("setupProblemNavAutoscroll", script)
        self.assertIn("runSetup(setupProblemNavAutoscroll)", script)
        self.assertIn("requestAnimationFrame", script)
        self.assertIn("prefers-reduced-motion: reduce", script)
        self.assertIn("scrollWidth - nav.clientWidth", script)
        self.assertIn("nav.scrollLeft = animatedScrollLeft", script)
        self.assertIn("overflow-x: auto", css)
        self.assertNotIn("track.style.transform", script)
        self.assertIn("visibilitychange", script)
        self.assertIn("site:page-loaded", script)
        self.assertIn("problemNavAutoscrollBound", script)
        self.assertIn("initialIdleMs", script)
        self.assertIn("interactionPauseMs = 6000", script)
        self.assertIn("edgePauseMs = 900", script)
        self.assertIn("animatedScrollLeft", script)
        self.assertIn("pauseAfterInteraction", script)
        self.assertIn("isProgrammaticScroll", script)
        self.assertIn("return isPointerDown;", script)
        self.assertIn("pointerdown", script)
        self.assertIn("pointerup", script)
        self.assertIn("pointercancel", script)
        self.assertIn("motionQuery.addListener", script)
        self.assertNotIn("mouseenter", script)
        self.assertNotIn("mouseleave", script)
        self.assertNotIn("focusin", script)
        self.assertNotIn("wheel", script)
        self.assertNotIn("IntersectionObserver", script)
        self.assertNotIn("new MutationObserver(queueMeasure)", script)
        self.assertNotIn("isInViewport", script)
        self.assertNotIn("pointerenter", script)

    def test_protected_photo_javascript_uses_token_not_media_path(self):
        script = Path("static/for_users/js/protected_media.js").read_text()

        self.assertNotIn("fetch(", script)
        self.assertNotIn("createObjectURL", script)
        self.assertNotIn("revokeObjectURL", script)
        self.assertNotIn("protectedMedia", script)

    def test_photo_lightbox_allows_browser_pinch_zoom(self):
        css = Path("static/for_users/css/photo_lightbox.css").read_text()
        script = Path("static/for_users/js/photo_lightbox.js").read_text()

        self.assertIn("pinch-zoom", css)
        self.assertIn("@media (pointer: coarse)", css)
        self.assertIn("touch-action: none", css)
        self.assertIn("animatePhotoSwitch", script)
        self.assertIn("resetZoom", script)
        self.assertIn("handleZoomTouchStart", script)
        self.assertIn("updatePinchZoom", script)
        self.assertIn("zoomAroundPoint", script)
        self.assertIn("handleZoomDoubleClick", script)
        self.assertIn("handleZoomMouseDown", script)
        self.assertIn("handleZoomMouseMove", script)
        self.assertIn("handleZoomMouseUp", script)
        self.assertIn("is-dragging", script)
        self.assertIn("cursor: grabbing", css)
        self.assertIn("generation !== animationGeneration", script)
        self.assertIn("clearTransformAnimations(activeImage)", script)
        self.assertIn("updateActiveImageSource(nextSourceImage)", script)
        self.assertIn("resetZoom();\n    activeIndex = nextIndex;", script)
        self.assertIn('sourceImage.closest(".problem-card")', script)
        self.assertIn("activeIndex", script)
        self.assertNotIn("prepareSourceImage", script)
        self.assertNotIn("openRequestGeneration", script)
        self.assertNotIn("showSwitchedPhotoImmediately", script)

    def test_photo_lightbox_switching_uses_single_stable_image(self):
        script = Path("static/for_users/js/photo_lightbox.js").read_text()
        switch_block = script[
            script.index("function switchToPhoto"):
            script.index("async function animateOpen")
        ]

        self.assertIn("let activeIndex = 0", script)
        self.assertIn(
            "activeIndex === galleryImages.length - 1 ? 0 : activeIndex + 1",
            script,
        )
        self.assertIn(
            "activeIndex === 0 ? galleryImages.length - 1 : activeIndex - 1",
            script,
        )
        self.assertIn("activeImage.src = sourceImage.currentSrc || sourceImage.src", script)
        self.assertIn("lightbox.addEventListener(\"dblclick\", handleZoomDoubleClick)", script)
        self.assertIn("lightbox.addEventListener(\"touchstart\", handleZoomTouchStart", script)
        self.assertIn("removeInactiveLightboxImages(activeImage)", switch_block)
        self.assertNotIn("lightbox.append", switch_block)
        self.assertNotIn("createLightboxImage", switch_block)
        self.assertNotIn("requestedIndex", script)
        self.assertNotIn("renderedIndex", script)
        self.assertEqual(script.count("createLightboxImage("), 2)
        self.assertEqual(script.count("lightbox.append(activeImage)"), 1)

        problem_card_position = script.index(
            'const problemCard = sourceImage.closest(".problem-card")'
        )
        gallery_position = script.index(
            'const gallery = sourceImage.closest("[data-lightbox-gallery]")'
        )
        self.assertLess(problem_card_position, gallery_position)

    def test_photo_lightbox_zoom_clears_switch_transform_animation(self):
        script = Path("static/for_users/js/photo_lightbox.js").read_text()
        switch_block = script[
            script.index("function switchToPhoto"):
            script.index("async function animateOpen")
        ]
        zoom_block = script[
            script.index("function handleZoomDoubleClick"):
            script.index("function clampZoomToViewport")
        ]

        self.assertIn("function clearTransformAnimations(image)", script)
        self.assertIn("hasOwnProperty.call(keyframe, \"transform\")", script)
        self.assertIn("animation.cancel();", script)
        self.assertIn("clearTransformAnimations(activeImage)", zoom_block)
        self.assertNotIn("clearTransformAnimations", switch_block)
        self.assertEqual(script.count("lightbox.addEventListener(\"dblclick\""), 1)
        self.assertEqual(script.count("handleZoomDoubleClick"), 2)

    def test_favicon_assets_exist_for_desktop_and_mobile_browsers(self):
        expected_sizes = {
            Path("static/favicon/favicon-16x16.png"): (16, 16),
            Path("static/favicon/favicon-32x32.png"): (32, 32),
            Path("static/favicon/apple-touch-icon.png"): (180, 180),
            Path("static/favicon/android-chrome-192x192.png"): (192, 192),
            Path("static/favicon/android-chrome-512x512.png"): (512, 512),
        }

        for path, expected_size in expected_sizes.items():
            with self.subTest(path=path):
                self.assertTrue(path.exists())
                with Image.open(path) as image:
                    self.assertEqual(image.size, expected_size)

        with Image.open("static/favicon/heart.ico") as icon:
            self.assertTrue({(16, 16), (32, 32), (48, 48)}.issubset(icon.ico.sizes()))

    def test_woff2_fonts_are_used_without_changing_font_families(self):
        user_css = Path("static/for_users/css/main.css").read_text()
        admin_css = Path("static/for_admin/css/main.css").read_text()

        for path in [
            Path("static/fonts/Golos/GolosText-VariableFont_wght.woff2"),
            Path("static/fonts/Unbounded/Unbounded-VariableFont_wght.woff2"),
        ]:
            with self.subTest(path=path):
                self.assertTrue(path.exists())

        for css in [user_css, admin_css]:
            self.assertIn('font-family: "Golos Text"', css)
            self.assertIn('font-family: "Unbounded"', css)
            self.assertIn('format("woff2")', css)
            self.assertNotIn('VariableFont_wght.ttf', css)
