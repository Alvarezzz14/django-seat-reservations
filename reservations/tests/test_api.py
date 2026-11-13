from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from reservations.models import Activity, Reservation


class ReservationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.activity = Activity.objects.create(name="Taller", capacity=2)
        self.detail_url = f"/api/activities/{self.activity.pk}/"
        self.create_url = f"{self.detail_url}reservations/"

    def reserve(self, key="request-1", name="Ana", activity_id=None):
        url = self.create_url
        if activity_id is not None:
            url = f"/api/activities/{activity_id}/reservations/"
        return self.client.post(
            url,
            {"participant_name": name},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def cancel(self, reservation_id, activity_id=None):
        activity_id = self.activity.pk if activity_id is None else activity_id
        return self.client.post(
            f"/api/activities/{activity_id}/reservations/{reservation_id}/cancel/",
            {},
            format="json",
        )

    def test_activity_shows_capacity_and_available_seats(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": self.activity.pk,
                "name": "Taller",
                "capacity": 2,
                "active_reservations": 0,
                "available_seats": 2,
            },
        )

    def test_creating_a_reservation_consumes_one_seat(self):
        response = self.reserve()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "active")
        self.assertEqual(response.json()["activity_id"], self.activity.pk)
        self.assertEqual(response.json()["participant_name"], "Ana")
        self.assertNotIn("idempotency_key", response.json())
        detail = self.client.get(self.detail_url).json()
        self.assertEqual(detail["active_reservations"], 1)
        self.assertEqual(detail["available_seats"], 1)

    def test_full_activity_rejects_new_key_without_creating_a_record(self):
        self.reserve("first")
        self.reserve("second")
        response = self.reserve("third")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(self.client.get(self.detail_url).json()["available_seats"], 0)

    def test_replay_returns_the_original_reservation_even_when_full(self):
        first = self.reserve("first")
        self.reserve("second")
        replay = self.reserve("first")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(Reservation.objects.count(), 2)

    def test_key_with_different_data_conflicts(self):
        original = self.reserve(name="Ana")
        response = self.reserve(name="Luis")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Reservation.objects.get().participant_name, "Ana")
        self.assertEqual(original.status_code, 201)

    def test_participant_name_is_trimmed_before_idempotency_comparison(self):
        first = self.reserve(name=" Ana ")
        replay = self.reserve(name="Ana")
        self.assertEqual(first.json()["participant_name"], "Ana")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), first.json())

    def test_same_key_is_independent_between_activities(self):
        other = Activity.objects.create(name="Otro taller", capacity=1)
        first = self.reserve()
        second = self.reserve(name="Luis", activity_id=other.pk)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["id"], second.json()["id"])

    def test_cancellation_frees_a_seat_once_and_keeps_the_record(self):
        reservation = self.reserve().json()
        first = self.cancel(reservation["id"])
        second = self.cancel(reservation["id"])
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(first.json()["status"], "cancelled")
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(self.client.get(self.detail_url).json()["available_seats"], 2)

    def test_cancelled_key_replays_cancelled_reservation_and_new_key_can_book(self):
        first = self.reserve().json()
        self.cancel(first["id"])
        replay = self.reserve()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["id"], first["id"])
        self.assertEqual(replay.json()["status"], "cancelled")
        fresh = self.reserve("fresh")
        self.assertEqual(fresh.status_code, 201)
        self.assertEqual(fresh.json()["status"], "active")

    def test_rejected_full_request_does_not_consume_its_idempotency_key(self):
        first = self.reserve("first").json()
        self.reserve("second")
        self.assertEqual(self.reserve("waiting").status_code, 409)
        self.cancel(first["id"])
        self.assertEqual(self.reserve("waiting").status_code, 201)

    def test_missing_activity_returns_404_for_all_operations(self):
        self.assertEqual(self.client.get("/api/activities/99999/").status_code, 404)
        self.assertEqual(self.reserve(activity_id=99999).status_code, 404)
        self.assertEqual(self.cancel(1, activity_id=99999).status_code, 404)

    def test_missing_reservation_returns_404(self):
        self.assertEqual(self.cancel(99999).status_code, 404)

    def test_reservation_cannot_be_cancelled_through_another_activity(self):
        other = Activity.objects.create(name="Otro taller", capacity=1)
        reservation = self.reserve().json()
        self.assertEqual(self.cancel(reservation["id"], activity_id=other.pk).status_code, 404)
        self.assertEqual(Reservation.objects.get().status, Reservation.Status.ACTIVE)

    def test_invalid_names_return_400_without_writing(self):
        for name in ["", "   ", "A" * 121, None, [], {}]:
            with self.subTest(name=name):
                self.assertEqual(self.reserve(name=name).status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_missing_name_and_non_object_body_return_400(self):
        for body in [{}, [], "Ana"]:
            with self.subTest(body=body):
                response = self.client.post(
                    self.create_url, body, format="json", HTTP_IDEMPOTENCY_KEY="valid"
                )
                self.assertEqual(response.status_code, 400)

    def test_invalid_or_missing_idempotency_key_returns_400(self):
        for key in ["", " ", "has space", "á", "a" * 129, "line\nbreak"]:
            with self.subTest(key=key):
                self.assertEqual(self.reserve(key=key).status_code, 400)
        missing = self.client.post(self.create_url, {"participant_name": "Ana"}, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_max_length_idempotency_key_is_accepted(self):
        self.assertEqual(self.reserve(key="a" * 128).status_code, 201)

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            self.create_url, '{"participant_name":', content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_posting_to_read_only_endpoint_returns_405(self):
        self.assertEqual(self.client.post(self.detail_url, {}, format="json").status_code, 405)


class DatabaseConstraintTests(TestCase):
    def test_capacity_must_be_positive_even_when_bypassing_the_api(self):
        for capacity in [0, -1]:
            with self.subTest(capacity=capacity):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Activity.objects.create(name="Invalid", capacity=capacity)

    def test_duplicate_key_is_rejected_by_postgresql(self):
        activity = Activity.objects.create(name="Taller", capacity=2)
        Reservation.objects.create(activity=activity, participant_name="Ana", idempotency_key="same")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(activity=activity, participant_name="Luis", idempotency_key="same")

    def test_unknown_status_is_rejected_by_postgresql(self):
        activity = Activity.objects.create(name="Taller", capacity=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(
                activity=activity, participant_name="Ana", idempotency_key="key", status="invalid"
            )
