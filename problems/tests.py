import time
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from PIL import Image

from .forms import ProblemForm, make_form_started_at_token
from .admin import ProblemAdmin
from .models import Problem, ProblemEvidenceFile, ProblemPhoto, ProblemVote

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

    def test_percent_encoded_cyrillic_url_renders(self):
        response = self.client.get(reverse("public_problems"))

        self.assertEqual(response.status_code, 200)

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


class ProblemAdminTests(TestCase):
    # Регрессия на admin changelist: бейджи должны рендериться в шаблоне Django admin.
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
        nginx_config = Path("deploy/nginx/templates/default.conf.template").read_text()
        compose_config = Path("docker-compose.yml").read_text()
        dockerignore = Path(".dockerignore").read_text()

        for pattern in [
            ".env",
            "docker-compose",
            "sqlite",
            "db-wal",
            "db-shm",
            "backup",
            "scripts",
        ]:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, nginx_config)

        nginx_service = compose_config.split("  nginx:", 1)[1].split("  certbot:", 1)[0]

        self.assertNotIn("/app/data", nginx_service)
        self.assertIn("*.sqlite", dockerignore)
        self.assertIn("*.db-wal", dockerignore)
