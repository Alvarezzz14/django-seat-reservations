from django.core.management.base import BaseCommand

from reservations.models import Activity


class Command(BaseCommand):
    help = "Create a two-seat demo activity without resetting existing reservations."

    def handle(self, *args, **options):
        activity, created = Activity.objects.get_or_create(
            name="Taller de PostgreSQL", defaults={"capacity": 2}
        )
        action = "Created" if created else "Kept"
        self.stdout.write(f"{action} activity {activity.pk}: {activity.name}")
