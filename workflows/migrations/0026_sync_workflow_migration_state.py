# Fix migration state to match actual database
# This updates Django's internal migration state without touching the database
# The fields were already removed by migration 0024's RunPython, but Django didn't track it

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0025_add_conditional_routes_and_human_validation'),
    ]

    operations = [
        # State-only operations - these don't run any SQL, they just update Django's migration state
        # to match what migration 0024's RunPython actually did to the database
        migrations.SeparateDatabaseAndState(
            # Database operations: NONE (fields were already removed by migration 0024)
            database_operations=[],
            
            # State operations: Tell Django these fields are gone
            state_operations=[
                migrations.RemoveField(
                    model_name='workflow',
                    name='title',
                ),
                migrations.RemoveField(
                    model_name='workflow',
                    name='description',
                ),
                migrations.RemoveField(
                    model_name='workflow',
                    name='mode',
                ),
                migrations.RemoveField(
                    model_name='workflow',
                    name='layout',
                ),
                migrations.RemoveField(
                    model_name='workflow',
                    name='viewport',
                ),
                migrations.RemoveField(
                    model_name='workflow',
                    name='steps',
                ),
            ],
        ),
    ]


