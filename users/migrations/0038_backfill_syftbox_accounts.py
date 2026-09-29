"""
Move SyftBox credentials into the shared package's own table.

Tokens have lived in columns on ``users.User`` and ``core.DareConfig``. The
``syftbox-connect`` package now owns a ``SyftBoxAccount`` table instead, so a
project does not have to carry credential columns on its own models.

This is the expand half of an expand/contract migration: it *copies* rows
across and leaves the originals untouched. Nothing reads the new table
exclusively yet, and the package's resolver falls back to the old columns for
any identity it cannot find — so a row this misses still works, and unapplying
loses nothing.

Dropping the columns is a separate, later migration, once the new table has
been serving reads in production for a release.
"""

from django.db import migrations

# A datasite is addressed by email, so that is the key. These models hold that
# address under different names.
SOURCES = [
    ("users", "User", "email"),
    ("core", "DareConfig", "project_email"),
]


def has_credentials(row):
    return bool(
        (row.syftbox_access_token or "").strip()
        or (row.syftbox_refresh_token or "").strip()
    )


def forwards(apps, schema_editor):
    SyftBoxAccount = apps.get_model("syftbox_connect", "SyftBoxAccount")

    for app_label, model_name, email_field in SOURCES:
        try:
            Model = apps.get_model(app_label, model_name)
        except LookupError:
            # The model may not exist in every deployment's history.
            continue

        # _base_manager, not objects: DareConfig's default manager filters
        # out inactive rows, and a credential backfill must see every one.
        for row in Model._base_manager.all().iterator():
            email = (getattr(row, email_field, "") or "").strip()
            if not email or not has_credentials(row):
                continue

            # An identity may appear in both tables. First write wins; the
            # second would only overwrite it with the same credentials, and
            # skipping keeps the migration order-independent.
            if SyftBoxAccount.objects.filter(email=email).exists():
                continue

            SyftBoxAccount.objects.create(
                email=email,
                # Only a real user owns an account; DareConfig is a
                # project-level identity and stays unowned.
                owner_id=row.pk if model_name == "User" else None,
                syftbox_access_token=row.syftbox_access_token,
                syftbox_refresh_token=row.syftbox_refresh_token,
            )


def backwards(apps, schema_editor):
    """
    Remove only what this migration could have created.

    The originals were never touched, so nothing is lost -- but an account
    linked *after* this ran is real data and must survive an unapply.
    """
    SyftBoxAccount = apps.get_model("syftbox_connect", "SyftBoxAccount")
    emails = set()

    for app_label, model_name, email_field in SOURCES:
        try:
            Model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        # _base_manager, not objects: DareConfig's default manager filters
        # out inactive rows, and a credential backfill must see every one.
        for row in Model._base_manager.all().iterator():
            if has_credentials(row):
                email = (getattr(row, email_field, "") or "").strip()
                if email:
                    emails.add(email)

    SyftBoxAccount.objects.filter(email__in=emails).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0037_accesscodegroup_provisioned_by"),
        ("core", "0001_initial"),
        ("syftbox_connect", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
