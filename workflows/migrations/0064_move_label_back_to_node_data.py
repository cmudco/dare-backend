"""
Move label from WorkflowNode back to *NodeData models.

Label belongs inside node data to match React Flow's data structure exactly.
The nodes & edges tables mirror what React Flow accepts, and label is part
of each node's data payload — not a graph-layer concern.

This migration:
1. Adds label to each of the 4 NodeData models
2. Copies label from WorkflowNode to the appropriate NodeData object
3. Removes label from WorkflowNode
"""
from django.db import migrations, models


def copy_labels_to_node_data(apps, schema_editor):
    """Copy label from each WorkflowNode to its owned *NodeData object."""
    WorkflowNode = apps.get_model("workflows", "WorkflowNode")
    ContentType = apps.get_model("contenttypes", "ContentType")

    model_names = [
        "StepNodeData",
        "ChatOutputNodeData",
        "StructuredOutputNodeData",
        "FileNodeData",
    ]

    for model_name in model_names:
        Model = apps.get_model("workflows", model_name)
        ct = ContentType.objects.get_for_model(Model)

        # Get all WorkflowNodes pointing to this data type that have a label
        nodes_with_labels = WorkflowNode.objects.filter(
            data_content_type=ct,
        ).exclude(label='').values_list("data_object_id", "label")

        for data_object_id, label in nodes_with_labels:
            Model.objects.filter(id=data_object_id).update(label=label)


def copy_labels_to_workflow_node(apps, schema_editor):
    """Reverse: copy label from *NodeData back to WorkflowNode."""
    WorkflowNode = apps.get_model("workflows", "WorkflowNode")
    ContentType = apps.get_model("contenttypes", "ContentType")

    model_names = [
        "StepNodeData",
        "ChatOutputNodeData",
        "StructuredOutputNodeData",
        "FileNodeData",
    ]

    for model_name in model_names:
        Model = apps.get_model("workflows", model_name)
        ct = ContentType.objects.get_for_model(Model)

        label_map = dict(
            Model.objects.exclude(label='').values_list("id", "label")
        )

        if not label_map:
            continue

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
        ("workflows", "0063_move_label_to_workflow_node"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        # 1. Add label to all 4 NodeData models
        migrations.AddField(
            model_name="stepnodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Display label (e.g. 'Step 1', 'Research')",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="chatoutputnodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Display label (e.g. 'Step 1 Output')",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="structuredoutputnodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Display label (e.g. 'Router 1')",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="filenodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Display label (e.g. 'File 1')",
                max_length=255,
            ),
        ),
        # 2. Copy labels from WorkflowNode → NodeData
        migrations.RunPython(copy_labels_to_node_data, copy_labels_to_workflow_node),
        # 3. Remove label from WorkflowNode
        migrations.RemoveField(
            model_name="workflownode",
            name="label",
        ),
    ]
