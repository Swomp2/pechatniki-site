from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from problems.image_processing import optimize_image_path
from problems.models import ProblemEvidenceFile, ProblemPhoto


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class Command(BaseCommand):
    help = "Optimize already uploaded problem images without changing database links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be optimized without replacing files.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Process at most this many image files.",
        )
        parser.add_argument(
            "--path",
            help="Process only one filesystem path instead of all problem images.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        explicit_path = options["path"]
        stats = {
            "seen": 0,
            "processed": 0,
            "skipped": 0,
            "errors": 0,
            "before": 0,
            "after": 0,
            "saved": 0,
        }

        for path in self.iter_image_paths(explicit_path, limit):
            stats["seen"] += 1

            try:
                result = optimize_image_path(path, dry_run=dry_run)
            except Exception as exc:
                stats["errors"] += 1
                self.stderr.write(f"ERROR: failed to optimize image id {stats['seen']}: {exc}")
                continue

            stats["before"] += result["before"]
            stats["after"] += result["after"]
            stats["saved"] += result["saved"]

            if result["processed"]:
                stats["processed"] += 1
            else:
                stats["skipped"] += 1

        mode = "DRY RUN" if dry_run else "DONE"
        self.stdout.write(
            (
                f"{mode}: seen={stats['seen']} processed={stats['processed']} "
                f"skipped={stats['skipped']} errors={stats['errors']} "
                f"before={stats['before']} after={stats['after']} saved={stats['saved']}"
            )
        )

        if stats["errors"]:
            raise CommandError("Some images could not be optimized.")

    def iter_image_paths(self, explicit_path, limit):
        yielded = 0

        if explicit_path:
            path = Path(explicit_path)

            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                return

            yield path
            return

        querysets = [
            ProblemPhoto.objects.exclude(image="").only("image").iterator(),
            ProblemEvidenceFile.objects.exclude(file="").only("file").iterator(),
        ]

        for queryset in querysets:
            for instance in queryset:
                file_field = getattr(instance, "image", None) or getattr(
                    instance,
                    "file",
                    None,
                )

                if not file_field:
                    continue

                path = Path(file_field.path)

                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue

                yield path
                yielded += 1

                if limit is not None and yielded >= limit:
                    return
