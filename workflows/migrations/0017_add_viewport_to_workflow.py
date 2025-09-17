from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0016_add_layout_to_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflow',
            name='viewport',
            field=models.JSONField(null=True, blank=True, help_text="Optional React Flow viewport data: { x: number, y: number, zoom: number }"),
        ),
    ]