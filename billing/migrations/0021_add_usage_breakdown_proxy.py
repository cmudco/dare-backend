from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0020_delete_byokeyfeatureflag'),
    ]

    operations = [
        migrations.CreateModel(
            name='UsageBreakdown',
            fields=[
            ],
            options={
                'verbose_name': 'Usage Breakdown',
                'verbose_name_plural': 'Usage Breakdown',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('billing.transaction',),
        ),
    ]
