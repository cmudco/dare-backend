"""
Backfill a versioned soul file for existing projects that don't have one yet.

Projects created before soul files existed get a v1 seeded from their chosen
standards template (this is the project's own config, not demo data).

Usage:
    python manage.py backfill_soul_files
"""

from django.core.management.base import BaseCommand

from research.constants import soul_template_content
from research.models import ResearchProject, SoulFile, SoulFileVersion


class Command(BaseCommand):
    help = "Create a v1 soul file for projects that don't have one."

    def handle(self, *args, **options):
        created = 0
        for project in ResearchProject.objects.all():
            if SoulFile.objects.filter(project=project).exists():
                continue
            content, origin = soul_template_content(project.standards_template)
            soul = SoulFile.objects.create(project=project)
            SoulFileVersion.objects.create(
                soul_file=soul,
                version=1,
                content=content,
                origin=origin,
                created_by=project.user,
            )
            created += 1
            self.stdout.write(f"  soul file created for project {project.id}")
        self.stdout.write(
            self.style.SUCCESS(f"Backfilled {created} soul file(s).")
        )
