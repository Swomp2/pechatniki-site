from django.core.management.base import BaseCommand

from problems.models import Problem


class Command(BaseCommand):
    help = "Re-encrypt sensitive model fields with the active FIELD_ENCRYPTION_KEYS key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count rows that would be re-encrypted without saving changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        queryset = Problem.objects.exclude(contact_phone="")
        processed_count = 0

        for problem in queryset.iterator():
            processed_count += 1

            if dry_run:
                continue

            # Assigning the decrypted value back to the encrypted field makes
            # get_prep_value() store it with the current active key.
            problem.contact_phone = problem.contact_phone
            problem.save(update_fields=["contact_phone"])

        action = "would be re-encrypted" if dry_run else "re-encrypted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{processed_count} problem contact phone value(s) {action}."
            )
        )
