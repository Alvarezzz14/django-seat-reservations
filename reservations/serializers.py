from rest_framework import serializers

from .models import Reservation


class ReservationInputSerializer(serializers.Serializer):
    participant_name = serializers.CharField(max_length=120, trim_whitespace=True)


class IdempotencyKeySerializer(serializers.Serializer):
    key = serializers.RegexField(regex=r"\A[A-Za-z0-9._:-]{1,128}\Z")


class ReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reservation
        fields = ["id", "activity_id", "participant_name", "status"]
