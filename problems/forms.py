import time
from pathlib import Path

from django import forms
from django.conf import settings
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired

from .image_processing import build_safe_upload_name, optimize_uploaded_image
from .models import Problem
from .phone import normalize_russian_phone

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

EVIDENCE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
EVIDENCE_CONTENT_TYPES = IMAGE_CONTENT_TYPES | {"application/pdf"}

# started_at подписан Django signer-ом: пользователь не может просто
# подставить старую/быструю метку времени без знания SECRET_KEY.
FORM_STARTED_AT_FIELD = "started_at"
HONEYPOT_FIELD = "website"
FORM_SIGNING_SALT = "problems.problem_form.started_at"


def make_form_started_at_token(timestamp=None):
    return signing.dumps(timestamp or time.time(), salt=FORM_SIGNING_SALT)


def get_file_extension(uploaded_file):
    return Path(uploaded_file.name).suffix.lower()


def seek_to_start(uploaded_file):
    # Pillow и ручная проверка PDF читают поток файла. Возвращаем указатель
    # в начало, чтобы Django потом сохранил файл целиком.
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        return


def validate_client_content_type(uploaded_file, allowed_content_types):
    # content_type приходит от клиента и может быть подделан. Используем его
    # только как ранний фильтр, а содержимое проверяем отдельно.
    content_type = getattr(uploaded_file, "content_type", "").lower()

    if content_type and content_type != "application/octet-stream":
        if content_type not in allowed_content_types:
            raise forms.ValidationError("Один из файлов имеет неподдерживаемый тип.")


def validate_uploaded_image(uploaded_file, allowed_extensions):
    extension = get_file_extension(uploaded_file)

    if extension not in allowed_extensions:
        raise forms.ValidationError(
            "Можно загружать изображения только в форматах JPG, PNG или WebP."
        )

    validate_client_content_type(uploaded_file, IMAGE_CONTENT_TYPES)

    return optimize_uploaded_image(uploaded_file)


def validate_pdf(uploaded_file):
    validate_client_content_type(uploaded_file, {"application/pdf"})

    try:
        # Минимально проверяем сигнатуру PDF
        seek_to_start(uploaded_file)
        header = uploaded_file.read(5)
    finally:
        seek_to_start(uploaded_file)

    if header != b"%PDF-":
        raise forms.ValidationError(
            "Один из PDF-файлов повреждён или имеет неверный формат."
        )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        default_attrs = {
            "accept": "image/jpeg,image/png,image/webp",
        }

        if attrs:
            default_attrs.update(attrs)

        super().__init__(default_attrs)


class MultipleFileField(forms.FileField):
    # Django FileField по умолчанию чистит один файл. Этот wrapper возвращает
    # список, чтобы view мог одинаково обрабатывать 0, 1 или несколько файлов.
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean

        if not data:
            return []

        if isinstance(data, (list, tuple)):
            return [single_file_clean(file, initial) for file in data]

        return [single_file_clean(data, initial)]


class ProblemForm(forms.ModelForm):
    # Honeypot скрыт CSS-ом. Обычный пользователь его не заполняет,
    # а простые спам-боты часто отправляют все поля формы.
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "tabindex": "-1",
            }
        ),
    )
    started_at = forms.CharField(required=False, widget=forms.HiddenInput())

    photos = MultipleFileField(
        label="Фотографии",
        required=False,
    )

    evidence_files = MultipleFileField(
        label="Файлы с ответами, отказами и отписками",
        required=False,
        widget=MultipleFileInput(
            attrs={
                "accept": (
                    ".pdf,.jpg,.jpeg,.png,.webp,"
                    "application/pdf,"
                    "image/jpeg,image/png,image/webp"
                )
            }
        ),
    )

    class Meta:
        model = Problem
        fields = [
            "title",
            "description",
            "address",
            "category",
            "contact_phone",
            "has_prior_attempts",
        ]

        labels = {
            "contact_phone": "Телефон для обратной связи",
            "has_prior_attempts": (
                "Я уже пытался(ась) решить проблему через другие инстанции"
            )
        }

        widgets = {
            "contact_phone": forms.TextInput(
                attrs={
                    "autocomplete": "tel",
                    "inputmode": "tel",
                    "placeholder": "+7XXXXXXXXXX",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not self.is_bound:
            self.fields[FORM_STARTED_AT_FIELD].initial = make_form_started_at_token()

    def clean_website(self):
        value = self.cleaned_data.get(HONEYPOT_FIELD, "")

        if value:
            raise forms.ValidationError(
                "Не удалось отправить форму. Попробуйте ещё раз."
            )

        return value

    def clean_contact_phone(self):
        return normalize_russian_phone(self.cleaned_data.get("contact_phone", ""))

    def clean_photos(self):
        photos = self.cleaned_data.get("photos", [])

        # Лимиты берём из settings, чтобы их можно было согласовать с nginx
        # и менять через env без правки формы.
        max_files = settings.PROBLEM_PHOTO_MAX_FILES
        max_size = settings.PROBLEM_PHOTO_MAX_SIZE

        if len(photos) > max_files:
            raise forms.ValidationError(
                f"Можно загрузить не более {max_files} фотографий."
            )

        sanitized_photos = []

        for photo in photos:
            if photo.size > max_size:
                raise forms.ValidationError(
                    "Размер одной фотографии не должен превышать "
                    f"{max_size // 1024 // 1024} МБ."
                )

            sanitized_photos.append(validate_uploaded_image(photo, PHOTO_EXTENSIONS))

        return sanitized_photos

    def clean_evidence_files(self):
        evidence_files = self.cleaned_data.get("evidence_files", [])

        max_files_count = settings.PROBLEM_EVIDENCE_MAX_FILES
        max_file_size = settings.PROBLEM_EVIDENCE_MAX_SIZE

        if len(evidence_files) > max_files_count:
            raise forms.ValidationError(
                f"Можно загрузить не более {max_files_count} файлов."
            )

        sanitized_evidence_files = []

        for uploaded_file in evidence_files:
            extension = get_file_extension(uploaded_file)

            if extension not in EVIDENCE_EXTENSIONS:
                raise forms.ValidationError(
                    "Можно загружать только PDF, JPG, PNG и WebP."
                )

            validate_client_content_type(uploaded_file, EVIDENCE_CONTENT_TYPES)

            if uploaded_file.size > max_file_size:
                raise forms.ValidationError(
                    "Размер каждого файла не должен превышать "
                    f"{max_file_size // 1024 // 1024} МБ."
                )

            # Evidence files сознательно ограничены PDF и изображениями:
            # офисные документы без антивируса/конвертера рискованнее.
            if extension == ".pdf":
                validate_pdf(uploaded_file)
                uploaded_file.name = build_safe_upload_name(uploaded_file.name, ".pdf")
                sanitized_evidence_files.append(uploaded_file)
            else:
                sanitized_evidence_files.append(
                    validate_uploaded_image(uploaded_file, PHOTO_EXTENSIONS)
                )

        return sanitized_evidence_files

    def clean(self):
        cleaned_data = super().clean()

        has_prior_attempts = cleaned_data.get("has_prior_attempts")
        evidence_files = cleaned_data.get("evidence_files", [])

        # Если житель отмечает предыдущие обращения, модератору нужен файл
        # с ответом/отказом/скриншотом, иначе этот блок теряет смысл.
        if has_prior_attempts and not evidence_files:
            self.add_error(
                "evidence_files",
                "Приложите хотя бы один файл с ответом, отказом или отпиской.",
            )

        started_at = cleaned_data.get(FORM_STARTED_AT_FIELD)

        if not started_at:
            self.add_error(
                None, "Обновите страницу и попробуйте отправить форму ещё раз."
            )
            return cleaned_data

        try:
            form_started_at = signing.loads(
                started_at,
                salt=FORM_SIGNING_SALT,
                max_age=settings.PROBLEM_FORM_TOKEN_MAX_AGE_SECONDS,
            )
        except SignatureExpired:
            self.add_error(
                None, "Форма устарела. Обновите страницу и попробуйте ещё раз."
            )
            return cleaned_data
        except BadSignature:
            self.add_error(None, "Не удалось проверить форму. Обновите страницу.")
            return cleaned_data

        if (
            time.time() - float(form_started_at)
            < settings.PROBLEM_FORM_MIN_SUBMIT_SECONDS
        ):
            self.add_error(None, "Форма отправлена слишком быстро. Попробуйте ещё раз.")

        return cleaned_data
