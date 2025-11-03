# Generated migration to backfill bot_id for existing SocraticBots conversations
# This migration reverse-engineers the bot_id from the deterministic conversation_id

from django.db import migrations
import hashlib
import logging

logger = logging.getLogger(__name__)


def backfill_bot_ids(apps, schema_editor):
    """
    Backfill bot_id for existing SocraticBots conversations.

    Old conversations used deterministic IDs:
    sha256(f"socratic_bot_{bot_id}_user_{user_id}")[:16]

    We'll try all possible bot_ids (1-100) and match against conversation_id.
    Note: user_id here is the DARE user_id, not Socratic Books local user_id!
    """
    Conversation = apps.get_model('conversations', 'Conversation')

    # Find all SocraticBots conversations without bot_id
    # Note: In migrations, we use the default manager which is .objects (not active_objects)
    orphaned_conversations = Conversation._default_manager.filter(
        source='SocraticBots',
        bot_id__isnull=True,
        is_active=True,
        is_deleted=False
    )

    total_orphaned = orphaned_conversations.count()
    logger.info(f"Found {total_orphaned} SocraticBots conversations without bot_id")
    print(f"\n{'='*70}")
    print(f"Backfilling bot_id for {total_orphaned} SocraticBots conversations")
    print(f"{'='*70}\n")

    if total_orphaned == 0:
        print("No orphaned conversations found. Migration complete.")
        return

    # Configuration: adjust these based on your production data
    MAX_BOT_ID = 20  # Max bot_id to try (adjust for production: 20-50)
    MAX_USER_ID = 200  # Max user_id to try (adjust for production: 200-500)

    matched_count = 0
    unmatched_conversations = []

    for conversation in orphaned_conversations:
        conversation_id = conversation.conversation_id
        conversation_user_id = conversation.user_id  # This is the DARE user_id

        found_match = False

        # Brute-force: Try ALL combinations of bot_id × user_id
        # Since SHA-256 is deterministic, we can reverse-engineer by trying all possibilities
        for bot_id in range(1, MAX_BOT_ID + 1):
            for user_id in range(1, MAX_USER_ID + 1):
                # Recreate the deterministic hash
                identifier = f"socratic_bot_{bot_id}_user_{user_id}"
                calculated_hash = hashlib.sha256(identifier.encode()).hexdigest()[:16]

                if calculated_hash == conversation_id:
                    # MATCH FOUND!
                    conversation.bot_id = bot_id
                    conversation.save(update_fields=['bot_id'])

                    matched_count += 1
                    found_match = True

                    if user_id != conversation_user_id:
                        print(f"✅ Matched {conversation_id[:8]}... → bot_id={bot_id}, user_id={user_id} (conversation.user_id={conversation_user_id} ⚠️  MISMATCH!)")
                    else:
                        print(f"✅ Matched {conversation_id[:8]}... → bot_id={bot_id}, user_id={user_id}")

                    logger.info(f"Matched conversation {conversation_id} to bot_id={bot_id}, user_id={user_id}")
                    break

            if found_match:
                break

        if not found_match:
            unmatched_conversations.append(conversation_id)
            print(f"❌ Could not match conversation {conversation_id[:8]}... (tried {MAX_BOT_ID}×{MAX_USER_ID} combinations)")
            logger.warning(f"Could not match conversation {conversation_id} to any bot_id/user_id combination")

    # Summary
    print(f"\n{'='*70}")
    print(f"Migration Summary:")
    print(f"  Total conversations processed: {total_orphaned}")
    print(f"  Successfully matched: {matched_count}")
    print(f"  Unmatched: {len(unmatched_conversations)}")
    print(f"{'='*70}\n")

    if unmatched_conversations:
        print("⚠️  Unmatched conversation IDs (may be test data or deleted bots):")
        for conv_id in unmatched_conversations:
            print(f"  - {conv_id}")
        print("\nThese conversations will remain with bot_id=NULL and won't be")
        print("accessible through the normal bot interface. Review them manually if needed.")

    if matched_count > 0:
        print(f"\n✅ Successfully backfilled {matched_count} conversations!")
        print("Users will now see their conversation history when opening bots.")


def reverse_backfill(apps, schema_editor):
    """
    Reverse migration: Set bot_id back to NULL for SocraticBots conversations
    """
    Conversation = apps.get_model('conversations', 'Conversation')

    updated = Conversation._default_manager.filter(
        source='SocraticBots',
        bot_id__isnull=False
    ).update(bot_id=None)

    print(f"Reversed: Set bot_id=NULL for {updated} SocraticBots conversations")


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0032_add_bot_id_to_conversation'),
    ]

    operations = [
        migrations.RunPython(backfill_bot_ids, reverse_backfill),
    ]
