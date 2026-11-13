from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from .models import Activity, Reservation
from .serializers import (
    IdempotencyKeySerializer,
    ReservationInputSerializer,
    ReservationSerializer,
)
from .services import ReservationConflict, cancel_reservation, reserve_seat


@api_view(["GET"])
def activity_detail(request, activity_id):
    # A single query returns the count and capacity from one database snapshot.
    activities = Activity.objects.annotate(
        active_count=Count(
            "reservations", filter=Q(reservations__status=Reservation.Status.ACTIVE)
        )
    )
    activity = get_object_or_404(activities, pk=activity_id)
    return Response(
        {
            "id": activity.pk,
            "name": activity.name,
            "capacity": activity.capacity,
            "active_reservations": activity.active_count,
            "available_seats": activity.capacity - activity.active_count,
        }
    )


@api_view(["POST"])
def reservation_create(request, activity_id):
    payload = ReservationInputSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    header = IdempotencyKeySerializer(
        data={"key": request.headers.get("Idempotency-Key", "")}
    )
    header.is_valid(raise_exception=True)

    try:
        reservation, created = reserve_seat(
            activity_id=activity_id,
            participant_name=payload.validated_data["participant_name"],
            idempotency_key=header.validated_data["key"],
        )
    except Activity.DoesNotExist:
        raise NotFound("Activity not found.") from None
    except ReservationConflict as error:
        return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)

    return Response(
        ReservationSerializer(reservation).data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["POST"])
def reservation_cancel(request, activity_id, reservation_id):
    try:
        reservation = cancel_reservation(
            activity_id=activity_id, reservation_id=reservation_id
        )
    except (Activity.DoesNotExist, Reservation.DoesNotExist):
        raise NotFound("Activity or reservation not found.") from None
    return Response(ReservationSerializer(reservation).data)
