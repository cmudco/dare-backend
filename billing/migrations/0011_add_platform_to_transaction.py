# Generated manually for platform separation

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0010_transaction_billing_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='platform',
            field=models.CharField(
                choices=[('DARE', 'DARE'), ('SocraticBots', 'SocraticBots')],
                default='DARE',
                help_text='Platform where this transaction originated: DARE or SocraticBots',
                max_length=50,
                verbose_name='Platform'
            ),
        ),
    ]
