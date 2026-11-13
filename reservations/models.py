from django.core.validators import MinValueValidator
from django.db import models


class Activity(models.Model):
    name = models.CharField(max_length=120)
    capacity = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0), name="activity_capacity_positive"
            )
        ]


class Reservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        CANCELLED = "cancelled"

    activity = models.ForeignKey(
        Activity, on_delete=models.PROTECT, related_name="reservations"
    )
    participant_name = models.CharField(max_length=120)
    idempotency_key = models.CharField(max_length=128)
    status = models.CharField(max_length=9, choices=Status, default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "idempotency_key"],
                name="reservation_unique_key_per_activity",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "cancelled"]),
                name="reservation_valid_status",
            ),
        ]
