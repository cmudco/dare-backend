"""
Move label from *NodeData models to WorkflowNode.

Label is a display concern that belongs on the graph layer (WorkflowNode),
not on the data/config layer (*NodeData). This migration:
1. Adds label to WorkflowNode
2. Copies label from each NodeData object to its parent WorkflowNode
3. Removes label from all 4 NodeData models
"""
from django.db import migrations, models


def copy_labels_to_workflow_node(apps, schema_editor):
    """Copy label from each *NodeData to the owning WorkflowNode."""
    WorkflowNode = apps.get_model("workflows", "WorkflowNode")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Map model names to their content types
    model_names = [
        "StepNodeData",
        "ChatOutputNodeData",
        "StructuredOutputNodeData",
        "FileNodeData",
    ]

    for model_name in model_names:
        Model = apps.get_model("workflows", model_name)
        ct = ContentType.objects.get_for_model(Model)

        # Bulk-read all labels for this model type
        label_map = dict(
            Model.objects.values_list("id", "label")
        )

        if not label_map:
            continue

        # Update WorkflowNodes that point to these data objects
        nodes = WorkflowNode.objects.filter(
            data_content_type=ct,
            data_object_id__in=label_map.keys(),
        )
        for node in nodes.iterator():
            label = label_map.get(node.data_object_id, "")
            if label:
                WorkflowNode.objects.filter(id=node.id).update(label=label)


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0062_add_workflow_root_start_node"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        # 1. Add label to WorkflowNode
        migrations.AddField(
            model_name="workflownode",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Display label for this node (e.g. 'Step 1', 'Research', 'Summarize')",
                max_length=255,
            ),
        ),
        # 2. Copy labels from NodeData → WorkflowNode
        migrations.RunPython(copy_labels_to_workflow_node, migrations.RunPython.noop),
        # 3. Remove label from all 4 NodeData models
        migrations.RemoveField(
            model_name="stepnodedata",
            name="label",
        ),
        migrations.RemoveField(
            model_name="chatoutputnodedata",
            name="label",
        ),
        migrations.RemoveField(
            model_name="structuredoutputnodedata",
            name="label",
        ),
        migrations.RemoveField(
            model_name="filenodedata",
            name="label",
        ),
    ]
