import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchProject",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("question", models.TextField()),
                ("field", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("archived", "Archived")],
                        default="active",
                        max_length=24,
                    ),
                ),
                ("enabled_tools", models.JSONField(blank=True, default=list)),
                ("standards_template", models.CharField(blank=True, max_length=64)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="research_projects",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
        migrations.CreateModel(
            name="ResearchSource",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("file", "File"),
                            ("url", "URL"),
                            ("doi", "DOI"),
                            ("manual", "Manual"),
                        ],
                        default="manual",
                        max_length=24,
                    ),
                ),
                ("title", models.CharField(max_length=500)),
                ("citation", models.TextField(blank=True)),
                ("url", models.URLField(blank=True)),
                ("doi", models.CharField(blank=True, max_length=255)),
                ("authors", models.TextField(blank=True)),
                ("venue", models.CharField(blank=True, max_length=255)),
                ("year", models.PositiveIntegerField(blank=True, null=True)),
                ("abstract", models.TextField(blank=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sources",
                        to="research.researchproject",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="research_sources",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="researchproject",
            index=models.Index(
                fields=["user", "status"], name="research_re_user_id_4353f8_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="researchproject",
            index=models.Index(
                fields=["user", "is_deleted"], name="research_re_user_id_49db24_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="researchsource",
            index=models.Index(
                fields=["project", "is_deleted"], name="research_re_project_eea6ec_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="researchsource",
            index=models.Index(
                fields=["user", "is_deleted"], name="research_re_user_id_0c37e1_idx"
            ),
        ),
    ]
