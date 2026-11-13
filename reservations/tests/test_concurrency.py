from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from reservations.models import Activity, Reservation
from reservations.services import reserve_seat


class ReservationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "These tests require real PostgreSQL.")
        self.activity = Activity.objects.create(name="Concurrent workshop", capacity=2)
        self.create_url = f"/api/activities/{self.activity.pk}/reservations/"

    def concurrent_posts(self, requests):
        """Release independent database connections together; propagate worker errors."""
        barrier = Barrier(len(requests), timeout=15)

        def worker(request):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                barrier.wait()
                url, body, key = request
                client = APIClient()
                response = client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
                return response.status_code, response.json(), backend_pid
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            futures = [executor.submit(worker, request) for request in requests]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual(len({pid for _, _, pid in results}), len(requests))
        return results

    def test_twenty_requests_compete_for_two_seats(self):
        results = self.concurrent_posts([
            (self.create_url, {"participant_name": f"Person {index}"}, f"request-{index}")
            for index in range(20)
        ])
        codes = [code for code, _, _ in results]
        self.assertEqual(codes.count(201), 2)
        self.assertEqual(codes.count(409), 18)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(Reservation.objects.filter(status=Reservation.Status.ACTIVE).count(), 2)

    def test_twenty_identical_keys_create_one_reservation(self):
        results = self.concurrent_posts([
            (self.create_url, {"participant_name": "Ana"}, "shared-key") for _ in range(20)
        ])
        codes = [code for code, _, _ in results]
        self.assertEqual(codes.count(201), 1)
        self.assertEqual(codes.count(200), 19)
        self.assertEqual(len({body["id"] for _, body, _ in results}), 1)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_concurrent_key_reuse_with_different_data_conflicts(self):
        results = self.concurrent_posts([
            (self.create_url, {"participant_name": name}, "shared-key") for name in ["Ana", "Luis"]
        ])
        self.assertEqual(sorted(code for code, _, _ in results), [201, 409])
        self.assertEqual(Reservation.objects.count(), 1)

    def test_concurrent_cancellations_release_only_one_seat(self):
        reservation, _ = reserve_seat(
            activity_id=self.activity.pk, participant_name="Ana", idempotency_key="original"
        )
        cancel_url = f"{self.create_url}{reservation.pk}/cancel/"
        results = self.concurrent_posts([(cancel_url, {}, "unused") for _ in range(20)])
        self.assertTrue(all(code == 200 for code, _, _ in results))
        self.assertTrue(all(body["status"] == "cancelled" for _, body, _ in results))
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(Reservation.objects.filter(status=Reservation.Status.ACTIVE).count(), 0)

    def test_cancellation_and_creation_compete_without_overbooking(self):
        self.activity.capacity = 1
        self.activity.save(update_fields=["capacity"])
        reservation, _ = reserve_seat(
            activity_id=self.activity.pk, participant_name="Ana", idempotency_key="original"
        )
        results = self.concurrent_posts([
            (f"{self.create_url}{reservation.pk}/cancel/", {}, "unused"),
            *[
                (self.create_url, {"participant_name": f"Person {index}"}, f"new-{index}")
                for index in range(19)
            ],
        ])
        self.assertEqual(results[0][0], 200)
        codes = [code for code, _, _ in results[1:]]
        self.assertTrue(all(code in [201, 409] for code in codes))
        self.assertLessEqual(codes.count(201), 1)
        active_count = Reservation.objects.filter(status=Reservation.Status.ACTIVE).count()
        self.assertEqual(active_count, codes.count(201))
        if active_count == 0:
            response = APIClient().post(
                self.create_url, {"participant_name": "After cancellation"},
                format="json", HTTP_IDEMPOTENCY_KEY="after-cancellation",
            )
            self.assertEqual(response.status_code, 201)
