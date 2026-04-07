from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0013_file_storage_backend_alter_file_file'),
        ('workflows', '0064_move_label_back_to_node_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='stepnodedata',
            name='tags',
            field=models.ManyToManyField(blank=True, help_text='Tags to filter files for this step', related_name='step_nodes', to='files.tag'),
        ),
    ]
