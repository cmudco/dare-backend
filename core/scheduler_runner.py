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

from config.environment import config, features
from billing.scheduler import WalletTopupScheduler
from memory.scheduler import MemoryExtractionScheduler

def run_all_schedulers():
    logger.info(
        "Starting background schedulers... (environment: %s)",
        config.environment,
    )

    if features.enable_wallet_topup_scheduler:
        wallet_scheduler = WalletTopupScheduler()
        wallet_scheduler.start()
        logger.info("Wallet topup scheduler started.")
    else:
        logger.info("Wallet topup scheduler DISABLED for this environment.")

    if features.enable_memory_extraction_scheduler:
        memory_scheduler = MemoryExtractionScheduler()
        memory_scheduler.start()
        logger.info("Memory extraction scheduler started.")
    else:
        logger.info("Memory extraction scheduler DISABLED for this environment.")

    logger.info("Scheduler startup complete.")

if __name__ == "__main__":
    run_all_schedulers()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutting down schedulers.")
