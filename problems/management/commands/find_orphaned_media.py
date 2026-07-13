from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from problems.models import ProblemEvidenceFile, ProblemPhoto


class Command(BaseCommand):
    help = "Find or delete media files that are not referenced by problem attachments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show orphaned files. This is the default safe mode.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete orphaned files after checking that a backup exists.",
        )
        parser.add_argument(
            "--confirm-backup",
            action="store_true",
            help="Required with --delete to confirm that a fresh backup was created.",
        )

    def handle(self, *args, **options):
        if options["delete"] and not options["confirm_backup"]:
            raise CommandError("--delete requires --confirm-backup.")

        media_root = Path(settings.MEDIA_ROOT).resolve()

        if not media_root.exists():
            self.stdout.write("MEDIA_ROOT does not exist.")
            return

        referenced_files = set()

        for name in ProblemPhoto.objects.exclude(image="").values_list(
            "image",
            flat=True,
        ):
            referenced_files.add(Path(name).as_posix())

        for name in ProblemEvidenceFile.objects.exclude(file="").values_list(
            "file",
            flat=True,
        ):
            referenced_files.add(Path(name).as_posix())

        orphaned_files = []

        for path in media_root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue

            relative_name = path.relative_to(media_root).as_posix()

            if relative_name not in referenced_files:
                orphaned_files.append(path)

        if not orphaned_files:
            self.stdout.write("No orphaned media files found.")
            return

        for path in orphaned_files:
            relative_name = path.relative_to(media_root).as_posix()
            self.stdout.write(relative_name)

            if options["delete"]:
                path.unlink(missing_ok=True)

        if options["delete"]:
            self.stdout.write(f"Deleted orphaned files: {len(orphaned_files)}")
        else:
            self.stdout.write(
                f"Found orphaned files: {len(orphaned_files)}. "
                "Run with --delete --confirm-backup after creating a backup."
            )
