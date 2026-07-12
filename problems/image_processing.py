import os
import tempfile
import uuid
import warnings
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.text import get_valid_filename
from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_EXTENSION_BY_FORMAT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
IMAGE_CONTENT_TYPE_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def build_safe_upload_name(original_name, extension):
    safe_stem = get_valid_filename(Path(original_name).stem)[:80].strip("._-")

    if not safe_stem:
        safe_stem = "file"

    return f"{safe_stem}-{uuid.uuid4().hex}{extension}"


def get_allowed_image_formats():
    return {item.upper() for item in settings.PROBLEM_IMAGE_ALLOWED_FORMATS}


def get_resampling_filter():
    return getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)


def open_verified_image(file_object):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(file_object)
            image.load()
    except Image.DecompressionBombWarning as exc:
        raise ValidationError("Изображение слишком большое для безопасной обработки.") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("Один из файлов не является корректным изображением.") from exc

    image_format = image.format

    if image_format not in get_allowed_image_formats():
        raise ValidationError(
            "Можно загружать изображения только в форматах JPG, PNG или WebP."
        )

    if getattr(image, "is_animated", False):
        raise ValidationError("Анимированные изображения не поддерживаются.")

    width, height = image.size

    if width * height > settings.PROBLEM_IMAGE_MAX_PIXELS:
        raise ValidationError("Изображение слишком большое по разрешению.")

    return image, image_format


def resize_image(image):
    max_width = settings.PROBLEM_IMAGE_MAX_WIDTH
    max_height = settings.PROBLEM_IMAGE_MAX_HEIGHT
    width, height = image.size

    if width <= max_width and height <= max_height:
        return image

    image.thumbnail((max_width, max_height), get_resampling_filter())
    return image


def prepare_image_for_format(image, image_format):
    image = ImageOps.exif_transpose(image)
    image = resize_image(image.copy())

    if image_format == "JPEG":
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha_channel = image.getchannel("A")
            background.paste(image.convert("RGBA"), mask=alpha_channel)
            return background

        if image.mode not in {"RGB", "L"}:
            return image.convert("RGB")

    elif image_format == "PNG":
        if image.mode == "P":
            return image.convert("RGBA")

    elif image_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
        return image.convert("RGBA" if "A" in image.getbands() else "RGB")

    return image


def save_optimized_image(image, image_format, output):
    if image_format == "JPEG":
        image.save(
            output,
            format="JPEG",
            quality=settings.PROBLEM_IMAGE_JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
    elif image_format == "WEBP":
        image.save(
            output,
            format="WEBP",
            quality=settings.PROBLEM_IMAGE_WEBP_QUALITY,
            method=6,
        )
    else:
        image.save(
            output,
            format="PNG",
            optimize=True,
            compress_level=settings.PROBLEM_IMAGE_PNG_COMPRESS_LEVEL,
        )


def optimize_uploaded_image(uploaded_file):
    uploaded_file.seek(0)
    image, image_format = open_verified_image(uploaded_file)
    optimized_image = prepare_image_for_format(image, image_format)
    output = BytesIO()
    save_optimized_image(optimized_image, image_format, output)
    uploaded_file.seek(0)

    extension = IMAGE_EXTENSION_BY_FORMAT[image_format]
    content_type = IMAGE_CONTENT_TYPE_BY_FORMAT[image_format]

    return SimpleUploadedFile(
        build_safe_upload_name(uploaded_file.name, extension),
        output.getvalue(),
        content_type=content_type,
    )


def optimize_image_path(path, dry_run=False):
    path = Path(path)
    original_size = path.stat().st_size

    with path.open("rb") as source_file:
        image, image_format = open_verified_image(source_file)
        optimized_image = prepare_image_for_format(image, image_format)
        output = BytesIO()
        save_optimized_image(optimized_image, image_format, output)

    optimized_data = output.getvalue()
    optimized_size = len(optimized_data)
    min_saving = settings.PROBLEM_IMAGE_MIN_SAVING_BYTES

    if optimized_size >= original_size or original_size - optimized_size < min_saving:
        return {
            "processed": False,
            "before": original_size,
            "after": original_size,
            "saved": 0,
        }

    if not dry_run:
        temp_file = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(optimized_data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)

            os.replace(temp_path, path)
        finally:
            if temp_file is not None:
                temp_path = Path(temp_file.name)

                if temp_path.exists():
                    temp_path.unlink()

    return {
        "processed": True,
        "before": original_size,
        "after": optimized_size,
        "saved": original_size - optimized_size,
    }
