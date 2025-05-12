from django.db import models

APP_NAME = "billing"

class TransactionTypeChoice(models.IntegerChoices):
    DEBIT = 1, ("Debit")
    CREDIT = 2, ("Credit")