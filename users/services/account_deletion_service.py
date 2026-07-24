"""Permanent account deletion (expunge).

Erases a user's DARE identity and every trace of their data: database rows
(explicit sweeps plus FK cascade), MemU memories, the vector-store namespace,
uploaded file blobs, the avatar, email logs, and the linked SocraticBooks
account.

External stores are purged best-effort: a failure there is recorded as a
warning and does not abort the expunge, because the account must not survive
a transient outage of a secondary store. The database portion is atomic.
"""

import logging
from dataclasses import dataclass, field

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from agents.models import Agent, AgentNodeData, TemplateAgentNodeData
from billing.models import LiteLLMKey
from core.config.vector_db import get_user_namespace
from core.services.sb_client import SocraticBooksClient
from core.services.vector_service import get_vector_service
from email_logs.models import EmailLog
from files.models import File
from memory.services import get_memu_service
from users.services.avatar_service import AvatarService
from workflows.models import WorkflowNode

logger = logging.getLogger(__name__)


class AccountDeletionBlocked(Exception):
    """The account cannot be deleted until an administrative conflict is resolved."""


@dataclass
class AccountDeletionReport:
    """What happened during an expunge, including non-fatal degradations."""

    user_id: int
    warnings: list[str] = field(default_factory=list)


class AccountDeletionService:
    """Orchestrate the permanent, unrecoverable deletion of a user account."""

    def expunge(self, user) -> AccountDeletionReport:
        user_id = user.id
        email = user.email
        report = AccountDeletionReport(user_id=user_id)

        # Fail before touching any external store: keys this user administers
        # for other users are PROTECTed and would abort the DB delete midway.
        self._ensure_not_blocked(user)

        self._purge_memories(user_id, report)
        self._purge_vector_namespace(user_id, report)
        self._delete_file_blobs(user, report)
        self._remove_avatar(user, report)
        self._delete_socratic_account(user_id, report)

        with transaction.atomic():
            # PROTECT chains abort a cascade even when the protecting row is
            # itself part of it, so protected owners go first, in dependency
            # order: node data -> agents -> LiteLLM keys -> the user cascade.
            self._delete_workflow_node_data(user)
            Agent._base_manager.filter(user=user).delete()
            LiteLLMKey._base_manager.filter(
                Q(owner_user=user) | Q(assigned_user=user)
            ).delete()
            EmailLog._base_manager.filter(recipient__iexact=email).delete()
            try:
                user.delete()
            except ProtectedError as exc:
                raise AccountDeletionBlocked(
                    "Some records tied to this account are protected and could "
                    "not be removed. Nothing was deleted from the database."
                ) from exc

        logger.info("Expunged account %s (%s): %s", user_id, email, report)
        return report

    def _ensure_not_blocked(self, user) -> None:
        administers_foreign_keys = (
            LiteLLMKey._base_manager.filter(created_by=user)
            .exclude(owner_user=user)
            .exclude(assigned_user=user)
            .exists()
        )
        if administers_foreign_keys:
            raise AccountDeletionBlocked(
                "This account administers LiteLLM keys that belong to other "
                "users. Reassign or remove those keys before deleting it."
            )

    def _purge_memories(self, user_id: int, report: AccountDeletionReport) -> None:
        try:
            async_to_sync(get_memu_service().clear_all)(str(user_id))
        except Exception as exc:
            logger.warning("Memory purge failed for user %s: %s", user_id, exc)
            report.warnings.append("Stored memories could not be reached for deletion.")

    def _purge_vector_namespace(
        self, user_id: int, report: AccountDeletionReport
    ) -> None:
        try:
            get_vector_service(user_id).delete_namespace(get_user_namespace(user_id))
        except Exception as exc:
            logger.warning("Vector purge failed for user %s: %s", user_id, exc)
            report.warnings.append("Vectorized data could not be reached for deletion.")

    def _delete_file_blobs(self, user, report: AccountDeletionReport) -> None:
        """Remove stored file contents; row deletion alone leaves blobs behind."""
        failed = 0
        for stored_file in File._base_manager.filter(user=user).iterator():
            try:
                if stored_file.file:
                    stored_file.file.delete(save=False)
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Blob delete failed for file %s (user %s): %s",
                    stored_file.id,
                    user.id,
                    exc,
                )
        if failed:
            report.warnings.append(
                f"{failed} uploaded file(s) could not be removed from storage."
            )

    def _remove_avatar(self, user, report: AccountDeletionReport) -> None:
        try:
            AvatarService.remove_avatar(user)
        except Exception as exc:
            logger.warning("Avatar removal failed for user %s: %s", user.id, exc)
            report.warnings.append("Avatar image could not be removed from storage.")

    def _delete_socratic_account(
        self, user_id: int, report: AccountDeletionReport
    ) -> None:
        deleted = SocraticBooksClient.delete_user(user_id)
        if not deleted:
            report.warnings.append(
                "The linked SocraticBooks account could not be deleted."
            )

    def _delete_workflow_node_data(self, user) -> None:
        """Delete node-data rows before the FK cascade runs.

        WorkflowNode reaches its data through a GenericForeignKey, so the
        cascade removes nodes but strands their data rows — and stranded
        Agent/TemplateAgent node data PROTECTs the user's agents and prompts,
        which would abort ``user.delete()``.
        """
        nodes = WorkflowNode._base_manager.filter(workflow__user=user).select_related(
            "data_content_type"
        )
        for node in nodes.iterator():
            data_object = node.data_object
            if data_object is not None:
                data_object.delete()

        AgentNodeData._base_manager.filter(
            Q(agent__user=user) | Q(prompt__user=user)
        ).delete()
        TemplateAgentNodeData._base_manager.filter(agent__user=user).delete()
