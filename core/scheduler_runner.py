import time
import logging
from billing.scheduler import WalletTopupScheduler

logger = logging.getLogger(__name__)


def run_all_schedulers():
    logger.info("Starting all background schedulers...")

    wallet_scheduler = WalletTopupScheduler()
    wallet_scheduler.start()

    logger.info("All schedulers started successfully.")


if __name__ == "__main__":
    run_all_schedulers()

    # Keep the script alive
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutting down schedulers.")


