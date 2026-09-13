from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django_rq import get_connection
from rq import Queue
from rq.executions import Execution
from rq.job import Job


class ActiveJobsDashboardTests(TestCase):
    def test_active_execution_renders_with_rq_22(self):
        user = get_user_model().objects.create_user(
            email="queue-admin@example.com", password="test", is_staff=True
        )
        self.client.force_login(user)
        connection = get_connection()
        queue = Queue(f"dashboard-test-{uuid4().hex}", connection=connection)
        job = Job.create(sum, args=([1, 2],), connection=connection, origin=queue.name)
        job.set_status("started")
        job.save()
        with connection.pipeline() as pipeline:
            execution = Execution.create(job, ttl=60, pipeline=pipeline)
            pipeline.execute()
        try:
            # RQ 2.2 returns plain job IDs here, not composite execution keys.
            self.assertEqual(queue.started_job_registry.get_job_ids(), [job.id])
            with patch("django_rq.views.get_queue_by_index", return_value=queue):
                response = self.client.get("/django-rq/queues/0/started/")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, execution.id)
            self.assertContains(response, "sum")
        finally:
            with connection.pipeline() as pipeline:
                execution.delete(job, pipeline)
                pipeline.execute()
            job.delete()
            connection.delete(queue.started_job_registry.key)
