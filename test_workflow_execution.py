#!/usr/bin/env python
"""
Test script for workflow execution using the new node handler system.

This script sets up Django environment and tests workflow execution
with real database access.
"""
import os
import sys
import django
import asyncio
from django.conf import settings

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
django.setup()

# Now import Django models and services
from django.contrib.auth import get_user_model
from channels.db import database_sync_to_async
from workflows.models import Workflow, WorkflowRun
from core.services.workflow_execution_service import WorkflowExecutionService

User = get_user_model()


async def test_workflow_execution():
    """Test workflow execution with existing workflow in database."""
    print("🚀 Testing Workflow Execution with Node Handlers")
    print("=" * 50)

    try:
        # Get the first available workflow
        workflow = await database_sync_to_async(
            lambda: Workflow.objects.filter(is_active=True, is_deleted=False).first()
        )()

        if not workflow:
            print("❌ No active workflows found in database")
            print("Please create a workflow through the UI first")
            return

        workflow_title = await database_sync_to_async(lambda: workflow.title)()
        workflow_user = await database_sync_to_async(lambda: workflow.user.username if workflow.user else 'None')()

        print(f"📋 Found workflow: {workflow_title}")
        print(f"👤 Owner: {workflow_user}")
        print(f"🆔 Workflow ID: {workflow.id}")

        # Get workflow nodes
        nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()
        print(f"🔗 Total nodes: {len(nodes)}")

        for node in nodes:
            print(f"   - {node.node_type} node: {node.node_id}")
            # Skip data_object access for now to avoid async issues

        # Get the workflow user or create a test user
        if workflow.user:
            user = workflow.user
        else:
            user = await database_sync_to_async(
                lambda: User.objects.get_or_create(username='test_user')[0]
            )()

        # Create a workflow run
        workflow_run = await database_sync_to_async(
            lambda: WorkflowRun.objects.create(workflow=workflow, user=user)
        )()

        print(f"\n🏃 Created WorkflowRun #{workflow_run.id}")

        # Initialize and execute workflow
        print("\n🎯 Initializing WorkflowExecutionService...")
        service = WorkflowExecutionService()

        print("⚡ Starting workflow execution...")
        result = await service.execute_workflow(workflow_run)

        # Display results
        print("\n📊 Execution Results:")
        print("=" * 30)
        print(f"✅ Success: {result['success']}")
        print(f"📈 Total nodes: {result['total_nodes']}")
        print(f"✔️  Completed: {result['completed_nodes']}")
        print(f"❌ Failed: {result['failed_nodes']}")

        if result.get('error'):
            print(f"💥 Error: {result['error']}")

        print("\n🔍 Individual Node Results:")
        print("-" * 25)

        for node_id, node_result in result['results'].items():
            status = "✅" if node_result['success'] else "❌"
            print(f"{status} Node {node_id}:")
            if node_result['output']:
                # Truncate long outputs
                output = node_result['output'][:200]
                if len(node_result['output']) > 200:
                    output += "..."
                print(f"    Output: {output}")

            if node_result['error']:
                print(f"    Error: {node_result['error']}")

            if node_result['token_usage']:
                usage = node_result['token_usage']
                print(f"    Tokens: {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out")
            print()

        # Check workflow run status in database
        await database_sync_to_async(workflow_run.refresh_from_db)()
        print(f"💾 Final WorkflowRun status: {'Completed' if workflow_run.ended_at else 'Running'}")
        if workflow_run.ended_at:
            print(f"⏰ Ended at: {workflow_run.ended_at}")

        print("\n🎉 Test completed successfully!")

    except Exception as e:
        print(f"💥 Test failed with error: {str(e)}")
        import traceback
        traceback.print_exc()


def list_available_workflows():
    """List all available workflows for testing."""
    print("📋 Available Workflows:")
    print("=" * 30)

    workflows = Workflow.objects.filter(is_active=True, is_deleted=False)

    if not workflows.exists():
        print("❌ No workflows found")
        return

    for workflow in workflows:
        print(f"🔗 {workflow.title}")
        print(f"   ID: {workflow.id}")
        print(f"   Owner: {workflow.user.username}")
        print(f"   Nodes: {workflow.nodes.count()}")
        print(f"   Created: {workflow.created_at}")
        print()


def main():
    """Main test function."""
    print("🧪 DARE Workflow Execution Test")
    print("Using Node Handler System")
    print("=" * 40)

    # Check Django setup
    print(f"📦 Django settings: {settings.SETTINGS_MODULE}")
    print(f"🗄️  Database: {settings.DATABASES['default']['NAME']}")

    # Run async tests
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # First list available workflows
        list_available_workflows()
        print()

        # Then test execution
        loop.run_until_complete(test_workflow_execution())

    finally:
        loop.close()


if __name__ == "__main__":
    main()