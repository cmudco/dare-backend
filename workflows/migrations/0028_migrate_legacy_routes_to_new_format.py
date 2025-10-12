# Migration to convert legacy route_a/route_b fields to new routes JSON array format

from django.db import migrations


def migrate_legacy_routes_to_new_format(apps, schema_editor):
    """Convert all ConditionalNodeData from old route_a/route_b format to new routes array."""
    ConditionalNodeData = apps.get_model('workflows', 'ConditionalNodeData')
    
    updated_count = 0
    for conditional_node in ConditionalNodeData.objects.all():
        # Skip if already has routes data
        if conditional_node.routes and len(conditional_node.routes) > 0:
            continue
        
        # Build routes array from legacy fields
        routes = []
        
        if conditional_node.route_a_name:
            routes.append({
                'name': conditional_node.route_a_name,
                'description': conditional_node.route_a_description or ''
            })
        
        if conditional_node.route_b_name:
            routes.append({
                'name': conditional_node.route_b_name,
                'description': conditional_node.route_b_description or ''
            })
        
        # Only update if we found legacy routes
        if routes:
            conditional_node.routes = routes
            conditional_node.save(update_fields=['routes'])
            updated_count += 1
    
    print(f"Migrated {updated_count} conditional nodes from legacy format to new routes format")


def reverse_migration(apps, schema_editor):
    """Reverse migration - convert routes array back to route_a/route_b fields."""
    ConditionalNodeData = apps.get_model('workflows', 'ConditionalNodeData')
    
    for conditional_node in ConditionalNodeData.objects.all():
        if conditional_node.routes and len(conditional_node.routes) > 0:
            routes = conditional_node.routes
            
            # Set route A
            if len(routes) > 0:
                conditional_node.route_a_name = routes[0].get('name', '')
                conditional_node.route_a_description = routes[0].get('description', '')
            
            # Set route B
            if len(routes) > 1:
                conditional_node.route_b_name = routes[1].get('name', '')
                conditional_node.route_b_description = routes[1].get('description', '')
            
            conditional_node.save(update_fields=[
                'route_a_name', 'route_a_description',
                'route_b_name', 'route_b_description'
            ])


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0027_remove_all_legacy_models_from_state'),
    ]

    operations = [
        migrations.RunPython(
            migrate_legacy_routes_to_new_format,
            reverse_migration
        ),
    ]

