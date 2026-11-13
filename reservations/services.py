from django.db import transaction

from .models import Activity, Reservation


class ReservationConflict(Exception):
    pass


@transaction.atomic
def reserve_seat(
    *, activity_id: int, participant_name: str, idempotency_key: str
) -> tuple[Reservation, bool]:
    activity = Activity.objects.select_for_update().get(pk=activity_id)

    # Check replays before capacity: a successful retry must work even when full.
    existing = activity.reservations.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        if existing.participant_name != participant_name:
            raise ReservationConflict("Idempotency key was used with different data.")
        return existing, False

    active_count = activity.reservations.filter(status=Reservation.Status.ACTIVE).count()
    if active_count >= activity.capacity:
        raise ReservationConflict("Activity is full.")

    reservation = Reservation.objects.create(
        activity=activity,
        participant_name=participant_name,
        idempotency_key=idempotency_key,
    )
    return reservation, True


@transaction.atomic
def cancel_reservation(*, activity_id: int, reservation_id: int) -> Reservation:
    # Use the same lock and lock order as creation so cancellations serialize too.
    activity = Activity.objects.select_for_update().get(pk=activity_id)
    reservation = activity.reservations.get(pk=reservation_id)
    if reservation.status != Reservation.Status.CANCELLED:
        reservation.status = Reservation.Status.CANCELLED
        reservation.save(update_fields=["status"])
    return reservation
