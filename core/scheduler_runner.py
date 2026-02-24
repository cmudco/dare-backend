import os
import sys
import time
import logging
import django

# Configure Django
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from billing.scheduler import WalletTopupScheduler
from memory.scheduler import MemoryExtractionScheduler

def run_all_schedulers():
    logger.info("Starting all background schedulers...")

    wallet_scheduler = WalletTopupScheduler()
    wallet_scheduler.start()

    memory_scheduler = MemoryExtractionScheduler()
    memory_scheduler.start()

    logger.info("All schedulers started successfully.")

if __name__ == "__main__":
    run_all_schedulers()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutting down schedulers.")
