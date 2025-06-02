"""
Monthly Wallet Top-up Scheduler

This module handles the scheduling of automated monthly $5 wallet top-ups.
It uses RQ Scheduler to run the top-up process every 30 days.

Usage:
    from billing.scheduler import WalletTopupScheduler

    # Initialize and start the scheduler
    scheduler = WalletTopupScheduler()
    scheduler.start()

    # Check status
    scheduler.status()

    # Stop the scheduler
    scheduler.stop()
"""

import logging
from datetime import timedelta
from django.utils import timezone
from django_rq import get_scheduler
from .tasks import process_monthly_topup

logger = logging.getLogger(__name__)

class WalletTopupScheduler:
    """
    Handles scheduling of monthly wallet top-ups.
    """

    def __init__(self, queue_name='default'):
        """
        Initialize the scheduler.

        Args:
            queue_name (str): Name of the RQ queue to use
        """
        self.scheduler = get_scheduler(queue_name)
        self.job_id = 'monthly_wallet_topup'
        self.interval_seconds = 2592000  # 30 days in seconds

    def start(self):
        """
        Start the monthly top-up scheduler.

        Returns:
            dict: Status information about the scheduled job
        """
        try:
            # Cancel existing job if it exists
            self.stop()

            # Schedule the job to run every 30 days
            job = self.scheduler.schedule(
                scheduled_time=timezone.now(),
                func=process_monthly_topup,
                interval=self.interval_seconds,
                repeat=None,  # Repeat indefinitely
                id=self.job_id,
                description='Automated monthly $5 wallet top-up for all eligible users',
                meta={
                    'created_at': timezone.now().isoformat(),
                    'interval_days': 30,
                    'scheduler_version': '1.0'
                }
            )

            logger.info(f"Monthly wallet top-up scheduler started with job ID: {self.job_id}")

            return {
                'status': 'started',
                'job_id': self.job_id,
                'next_run': timezone.now().isoformat(),
                'interval_days': 30,
                'message': 'Monthly wallet top-up scheduler is now active'
            }

        except Exception as e:
            logger.error(f"Failed to start wallet top-up scheduler: {str(e)}")
            return {
                'status': 'error',
                'message': f'Failed to start scheduler: {str(e)}'
            }

    def stop(self):
        """
        Stop the monthly top-up scheduler.

        Returns:
            dict: Status information about the cancellation
        """
        try:
            self.scheduler.cancel(self.job_id)
            logger.info(f"Monthly wallet top-up scheduler stopped (job ID: {self.job_id})")

            return {
                'status': 'stopped',
                'job_id': self.job_id,
                'message': 'Monthly wallet top-up scheduler has been stopped'
            }

        except Exception as e:
            # Job might not exist, which is fine
            logger.info(f"No existing scheduler job found to cancel: {str(e)}")
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
                    'interval_days': target_job.meta.get('interval_days', 30),
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

    def schedule_immediate_run(self, delay_seconds=5):
        """
        Schedule an immediate test run of the top-up process.
        This creates a separate one-time job for testing.

        Args:
            delay_seconds (int): Delay before execution (default: 5 seconds)

        Returns:
            dict: Information about the scheduled test job
        """
        try:
            test_job_id = f"{self.job_id}_test_{timezone.now().strftime('%Y%m%d_%H%M%S')}"

            run_time = timezone.now() + timedelta(seconds=delay_seconds)

            job = self.scheduler.schedule(
                scheduled_time=run_time,
                func=process_monthly_topup,
                interval=None,  # One-time execution
                id=test_job_id,
                description='Test run of monthly wallet top-up process'
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
    """Start the monthly wallet top-up scheduler."""
    scheduler = WalletTopupScheduler()
    return scheduler.start()

def stop_scheduler():
    """Stop the monthly wallet top-up scheduler."""
    scheduler = WalletTopupScheduler()
    return scheduler.stop()

def get_scheduler_status():
    """Get the current scheduler status."""
    scheduler = WalletTopupScheduler()
    return scheduler.status()

def restart_scheduler():
    """Restart the scheduler."""
    scheduler = WalletTopupScheduler()
    return scheduler.restart()

def run_test_topup(delay_seconds=5):
    """Schedule an immediate test run."""
    scheduler = WalletTopupScheduler()
    return scheduler.schedule_immediate_run(delay_seconds)
