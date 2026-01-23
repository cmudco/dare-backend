"""
Memory Extraction Scheduler

Handles scheduling of automatic memory extraction from idle conversations.
Uses RQ Scheduler to run extraction every 10 minutes.

Usage:
    from memory.scheduler import MemoryExtractionScheduler

    # Initialize and start the scheduler
    scheduler = MemoryExtractionScheduler()
    scheduler.start()

    # Check status
    scheduler.status()

    # Stop the scheduler
    scheduler.stop()
"""

import logging
from django.utils import timezone
from django_rq import get_scheduler
from .tasks import process_memory_extraction

logger = logging.getLogger(__name__)


class MemoryExtractionScheduler:
    """
    Handles scheduling of automatic memory extraction.
    """

    def __init__(self, queue_name='scheduler'):
        """
        Initialize the scheduler.

        Args:
            queue_name (str): Name of the RQ queue to use
        """
        self.scheduler = get_scheduler(queue_name)
        self.job_id = 'memory_extraction'
        self.interval_seconds = 600  # 10 minutes

    def start(self):
        """
        Start the memory extraction scheduler.
        Runs every 10 minutes to check for idle conversations.

        Returns:
            dict: Status information about the scheduled job
        """
        try:
            # Cancel existing job if it exists
            self.stop()

            # Schedule the job to run every 10 minutes
            job = self.scheduler.schedule(
                scheduled_time=timezone.now(),
                func=process_memory_extraction,
                interval=self.interval_seconds,
                repeat=None,  # Repeat indefinitely
                id=self.job_id,
                description='Extract memories from idle conversations',
                meta={
                    'created_at': timezone.now().isoformat(),
                    'interval_minutes': 10,
                    'scheduler_version': '1.0'
                }
            )

            logger.info(f"Memory extraction scheduler started with job ID: {self.job_id}")

            return {
                'status': 'started',
                'job_id': self.job_id,
                'next_run': timezone.now().isoformat(),
                'interval_minutes': 10,
                'message': 'Memory extraction scheduler is now active'
            }

        except Exception as e:
            logger.error(f"Failed to start memory extraction scheduler: {str(e)}")
            return {
                'status': 'error',
                'message': f'Failed to start scheduler: {str(e)}'
            }

    def stop(self):
        """
        Stop the memory extraction scheduler.

        Returns:
            dict: Status information about the cancellation
        """
        try:
            self.scheduler.cancel(self.job_id)
            logger.info(f"Memory extraction scheduler stopped (job ID: {self.job_id})")

            return {
                'status': 'stopped',
                'job_id': self.job_id,
                'message': 'Memory extraction scheduler has been stopped'
            }

        except Exception as e:
            # Job might not exist, which is fine
            logger.debug(f"No existing scheduler job found to cancel: {str(e)}")
            return {
                'status': 'not_found',
                'message': 'No active scheduler job found to stop'
            }

    def status(self):
        """
        Get the current status of the scheduler.

        Returns:
            dict: Detailed status information
        """
        try:
            scheduled_jobs = list(self.scheduler.get_jobs())

            # Find our specific job
            target_job = None
            for job in scheduled_jobs:
                if job.id == self.job_id:
                    target_job = job
                    break

            if target_job:
                return {
                    'status': 'active',
                    'job_id': target_job.id,
                    'description': target_job.description,
                    'next_run': getattr(target_job, 'scheduled_for', 'Unknown'),
                    'created_at': target_job.meta.get('created_at', 'Unknown'),
                    'interval_minutes': target_job.meta.get('interval_minutes', 10),
                    'total_scheduled_jobs': len(scheduled_jobs),
                    'message': 'Scheduler is active and running'
                }
            else:
                return {
                    'status': 'inactive',
                    'job_id': self.job_id,
                    'total_scheduled_jobs': len(scheduled_jobs),
                    'message': 'No active scheduler job found'
                }

        except Exception as e:
            logger.error(f"Failed to get scheduler status: {str(e)}")
            return {
                'status': 'error',
                'message': f'Failed to get status: {str(e)}'
            }

    def restart(self):
        """
        Restart the scheduler (stop and start).

        Returns:
            dict: Status information about the restart
        """
        stop_result = self.stop()
        start_result = self.start()

        return {
            'status': 'restarted',
            'stop_result': stop_result,
            'start_result': start_result,
            'message': 'Scheduler has been restarted'
        }

    def run_now(self, delay_seconds=5):
        """
        Schedule an immediate run of the extraction process.
        Creates a separate one-time job for testing.

        Args:
            delay_seconds (int): Delay before execution (default: 5 seconds)

        Returns:
            dict: Information about the scheduled test job
        """
        from datetime import timedelta
        
        try:
            test_job_id = f"{self.job_id}_test_{timezone.now().strftime('%Y%m%d_%H%M%S')}"

            run_time = timezone.now() + timedelta(seconds=delay_seconds)

            job = self.scheduler.schedule(
                scheduled_time=run_time,
                func=process_memory_extraction,
                interval=None,  # One-time execution
                id=test_job_id,
                description='Test run of memory extraction process'
            )

            logger.info(f"Scheduled immediate test run with job ID: {test_job_id}")

            return {
                'status': 'scheduled',
                'test_job_id': test_job_id,
                'scheduled_for': run_time.isoformat(),
                'delay_seconds': delay_seconds,
                'message': f'Test run scheduled to execute in {delay_seconds} seconds'
            }

        except Exception as e:
            logger.error(f"Failed to schedule immediate run: {str(e)}")
            return {
                'status': 'error',
                'message': f'Failed to schedule test run: {str(e)}'
            }


# Convenience functions for easy access
def start_scheduler():
    """Start the memory extraction scheduler."""
    scheduler = MemoryExtractionScheduler()
    return scheduler.start()

def stop_scheduler():
    """Stop the memory extraction scheduler."""
    scheduler = MemoryExtractionScheduler()
    return scheduler.stop()

def get_scheduler_status():
    """Get the current scheduler status."""
    scheduler = MemoryExtractionScheduler()
    return scheduler.status()

def restart_scheduler():
    """Restart the scheduler."""
    scheduler = MemoryExtractionScheduler()
    return scheduler.restart()

def run_extraction_now(delay_seconds=5):
    """Schedule an immediate test run."""
    scheduler = MemoryExtractionScheduler()
    return scheduler.run_now(delay_seconds)
