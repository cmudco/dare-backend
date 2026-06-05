from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

# isort: off
from research.constants import RESEARCH_ROLE_POLICIES, SOUL_FILE_TEMPLATES
from research.hermes import HermesRunRequest, get_hermes_adapter
from research.models import (
    ResearchAgentOutputDestination,
    ResearchAgentRole,
    ResearchAgentRun,
    ResearchAgentRunStatus,
    ResearchAgentToolCall,
    ResearchKnowledgeItem,
    ResearchProject,
    ResearchReviewStatus,
    ResearchSoulFile,
    ResearchSoulFileTemplate,
    ResearchSoulFileVersion,
    ResearchStagingItem,
    default_run_selected_context,
)

# isort: on


def _template_for(template_key: str) -> dict[str, str]:
    for template in SOUL_FILE_TEMPLATES:
        if template["key"] == template_key:
            return template
    return SOUL_FILE_TEMPLATES[-1]


def _role_policy_for(role: str) -> dict:
    if role not in ResearchAgentRole.values:
        raise ValidationError({"role": "Unsupported research agent role."})
    return RESEARCH_ROLE_POLICIES[role]


def _normalized_selected_context(selected_context: dict | None) -> dict:
    normalized = default_run_selected_context()
    if not selected_context:
        return normalized

    for key in normalized:
        if key in selected_context:
            normalized[key] = selected_context[key]
    return normalized


def _is_terminal_run_status(status: str) -> bool:
    return status in [
        ResearchAgentRunStatus.SUCCEEDED,
        ResearchAgentRunStatus.FAILED,
        ResearchAgentRunStatus.CANCELLED,
    ]


class ResearchSoulFileService:
    @staticmethod
    @transaction.atomic
    def create_soul_file(
        *,
        user,
        title: str,
        description: str,
        template_key: str,
        body: str,
        is_default: bool,
    ) -> ResearchSoulFile:
        template = _template_for(template_key)
        final_title = title or template["name"]
        final_body = body if body else template["body"]

        if is_default:
            ResearchSoulFile.active_objects.filter(user=user, is_default=True).update(
                is_default=False
            )

        soul_file = ResearchSoulFile.objects.create(
            user=user,
            title=final_title,
            description=description,
            template_key=template_key,
            is_default=is_default,
        )
        ResearchSoulFileVersion.objects.create(
            soul_file=soul_file,
            user=user,
            version_number=1,
            title=final_title,
            description=description,
            template_key=template_key,
            body=final_body,
            created_by=user,
        )
        return soul_file

    @staticmethod
    @transaction.atomic
    def update_soul_file(
        *,
        soul_file: ResearchSoulFile,
        user,
        title: str,
        description: str,
        template_key: str,
        body: str,
        is_default: bool,
    ) -> ResearchSoulFile:
        template = _template_for(template_key)
        final_title = title or template["name"]
        final_body = body if body else template["body"]

        if is_default:
            ResearchSoulFile.active_objects.filter(user=user, is_default=True).exclude(
                id=soul_file.id
            ).update(is_default=False)

        soul_file.title = final_title
        soul_file.description = description
        soul_file.template_key = template_key
        soul_file.is_default = is_default
        soul_file.save(
            update_fields=[
                "title",
                "description",
                "template_key",
                "is_default",
                "updated_at",
            ]
        )

        latest_number = (
            ResearchSoulFileVersion.objects.filter(soul_file=soul_file).aggregate(
                latest=Max("version_number")
            )["latest"]
            or 0
        )
        ResearchSoulFileVersion.objects.create(
            soul_file=soul_file,
            user=user,
            version_number=latest_number + 1,
            title=final_title,
            description=description,
            template_key=template_key,
            body=final_body,
            created_by=user,
        )
        return soul_file

    @staticmethod
    def ensure_default_soul_file(user) -> ResearchSoulFile:
        default_soul_file = ResearchSoulFile.active_objects.filter(
            user=user,
            is_default=True,
        ).first()
        if default_soul_file is not None:
            return default_soul_file

        return ResearchSoulFileService.create_soul_file(
            user=user,
            title="Research Ethics",
            description="Default research standards for careful scholarship.",
            template_key=ResearchSoulFileTemplate.RESEARCH_ETHICS,
            body="",
            is_default=True,
        )

    @staticmethod
    @transaction.atomic
    def ensure_project_soul_file(project: ResearchProject, user) -> ResearchSoulFile:
        if project.active_soul_file_id is not None:
            return project.active_soul_file

        soul_file = ResearchSoulFileService.ensure_default_soul_file(user)
        project.active_soul_file = soul_file
        project.save(update_fields=["active_soul_file", "updated_at"])
        return soul_file

    @staticmethod
    def get_project_active_version(
        project: ResearchProject,
        user,
    ) -> ResearchSoulFileVersion:
        soul_file = ResearchSoulFileService.ensure_project_soul_file(project, user)
        current_version = soul_file.current_version
        if current_version is None:
            raise ValidationError("Selected soul file does not have a version.")
        return current_version


class ResearchAgentRunService:
    @staticmethod
    @transaction.atomic
    def create_run(
        *,
        user,
        project: ResearchProject,
        role: str,
        task: str,
        selected_context: dict | None,
        allowed_tools: list[str],
        output_destination: str | None,
    ) -> ResearchAgentRun:
        role_policy = _role_policy_for(role)
        allowed_role_tools = set(role_policy["allowed_tools"])
        requested_tools = set(allowed_tools)
        if not requested_tools.issubset(allowed_role_tools):
            raise ValidationError(
                {"allowed_tools": "One or more tools are not allowed for this role."}
            )

        destination = output_destination or role_policy["default_output_destination"]
        if destination not in role_policy["allowed_output_destinations"]:
            raise ValidationError(
                {
                    "output_destination": (
                        "This role cannot write to the selected output destination."
                    )
                }
            )

        soul_file_version = ResearchSoulFileService.get_project_active_version(
            project,
            user,
        )
        queued_at = timezone.now()
        run = ResearchAgentRun.objects.create(
            user=user,
            project=project,
            role=role,
            status=ResearchAgentRunStatus.QUEUED,
            task=task,
            selected_context=_normalized_selected_context(selected_context),
            allowed_tools=list(allowed_tools),
            capability_policy=role_policy["capability_policy"],
            output_destination=destination,
            soul_file_version=soul_file_version,
            queued_at=queued_at,
        )

        dispatch_result = get_hermes_adapter().dispatch(
            HermesRunRequest(
                run_id=str(run.run_id),
                user_id=user.id,
                project_id=project.id,
                role=role,
                task=task,
                selected_context=run.selected_context,
                soul_file_version_id=soul_file_version.id,
                allowed_tools=list(allowed_tools),
                capability_policy=role_policy["capability_policy"],
                output_destination=destination,
            )
        )
        run.status = dispatch_result.status
        run.status_message = dispatch_result.status_message
        run.error_message = dispatch_result.error_message
        run.external_run_id = dispatch_result.external_run_id
        if not dispatch_result.accepted or _is_terminal_run_status(run.status):
            run.completed_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "status_message",
                "error_message",
                "external_run_id",
                "completed_at",
                "updated_at",
            ]
        )
        return run

    @staticmethod
    @transaction.atomic
    def cancel_run(run: ResearchAgentRun) -> ResearchAgentRun:
        if _is_terminal_run_status(run.status):
            raise ValidationError("Only queued or running runs can be cancelled.")

        run.status = ResearchAgentRunStatus.CANCELLED
        run.status_message = "Run cancelled in DARE before completion."
        run.completed_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "status_message",
                "completed_at",
                "updated_at",
            ]
        )
        return run

    @staticmethod
    @transaction.atomic
    def apply_callback(
        *,
        run: ResearchAgentRun,
        status: str,
        status_message: str,
        error_message: str,
        external_run_id: str,
        cost_usd,
        tool_calls: list[dict],
    ) -> ResearchAgentRun:
        if run.status == ResearchAgentRunStatus.CANCELLED:
            raise ValidationError("Cancelled runs cannot be updated by Hermes.")

        now = timezone.now()
        run.status = status
        run.status_message = status_message
        run.error_message = error_message
        run.cost_usd = cost_usd
        if external_run_id:
            run.external_run_id = external_run_id
        if status == ResearchAgentRunStatus.RUNNING and run.started_at is None:
            run.started_at = now
        if _is_terminal_run_status(status):
            if run.started_at is None:
                run.started_at = run.queued_at
            run.completed_at = now
        run.save(
            update_fields=[
                "status",
                "status_message",
                "error_message",
                "cost_usd",
                "external_run_id",
                "started_at",
                "completed_at",
                "updated_at",
            ]
        )

        for tool_call in tool_calls:
            ResearchAgentToolCall.objects.create(
                run=run,
                project=run.project,
                user=run.user,
                tool_name=tool_call["tool_name"],
                status=tool_call["status"],
                input_summary=tool_call.get("input_summary", ""),
                output_summary=tool_call.get("output_summary", ""),
                error_message=tool_call.get("error_message", ""),
                cost_usd=tool_call.get("cost_usd", 0),
                started_at=tool_call.get("started_at"),
                completed_at=tool_call.get("completed_at"),
            )
        return run


class ResearchReviewService:
    @staticmethod
    @transaction.atomic
    def approve(
        staging_item: ResearchStagingItem,
        reviewer,
    ) -> ResearchKnowledgeItem:
        if staging_item.status not in [
            ResearchReviewStatus.PENDING,
            ResearchReviewStatus.APPROVED,
        ]:
            raise ValidationError(
                "Only pending staging items can be approved. Restore this item first."
            )

        now = timezone.now()
        staging_item.status = ResearchReviewStatus.APPROVED
        staging_item.rejection_reason = ""
        staging_item.later_reason = ""
        staging_item.reviewed_by = reviewer
        staging_item.reviewed_at = now
        staging_item.save(
            update_fields=[
                "status",
                "rejection_reason",
                "later_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

        knowledge_item, _created = ResearchKnowledgeItem.objects.get_or_create(
            staging_item=staging_item,
            defaults={
                "project": staging_item.project,
                "user": staging_item.user,
                "source": staging_item.source,
                "title": staging_item.title,
                "authors": staging_item.authors,
                "venue": staging_item.venue,
                "year": staging_item.year,
                "url": staging_item.url,
                "doi": staging_item.doi,
                "content": staging_item.content,
                "rationale": staging_item.rationale,
                "confidence": staging_item.confidence,
                "confidence_rationale": staging_item.confidence_rationale,
                "evidence_label": staging_item.evidence_label,
                "citation_context": staging_item.citation_context,
                "provenance": staging_item.provenance,
                "soul_file_version": staging_item.soul_file_version,
                "approved_by": reviewer,
                "approved_at": now,
            },
        )
        return knowledge_item

    @staticmethod
    def reject(
        staging_item: ResearchStagingItem,
        reviewer,
        reason: str,
    ) -> ResearchStagingItem:
        if staging_item.status == ResearchReviewStatus.APPROVED:
            raise ValidationError("Approved staging items cannot be rejected.")

        staging_item.status = ResearchReviewStatus.REJECTED
        staging_item.rejection_reason = reason
        staging_item.later_reason = ""
        staging_item.reviewed_by = reviewer
        staging_item.reviewed_at = timezone.now()
        staging_item.save(
            update_fields=[
                "status",
                "rejection_reason",
                "later_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        return staging_item

    @staticmethod
    def mark_later(
        staging_item: ResearchStagingItem,
        reviewer,
        reason: str,
    ) -> ResearchStagingItem:
        if staging_item.status == ResearchReviewStatus.APPROVED:
            raise ValidationError("Approved staging items cannot be moved later.")

        staging_item.status = ResearchReviewStatus.LATER
        staging_item.rejection_reason = ""
        staging_item.later_reason = reason
        staging_item.reviewed_by = reviewer
        staging_item.reviewed_at = timezone.now()
        staging_item.save(
            update_fields=[
                "status",
                "rejection_reason",
                "later_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        return staging_item

    @staticmethod
    def restore(staging_item: ResearchStagingItem) -> ResearchStagingItem:
        if staging_item.status == ResearchReviewStatus.APPROVED:
            raise ValidationError("Approved staging items cannot be restored.")

        staging_item.status = ResearchReviewStatus.PENDING
        staging_item.rejection_reason = ""
        staging_item.later_reason = ""
        staging_item.reviewed_by = None
        staging_item.reviewed_at = None
        staging_item.save(
            update_fields=[
                "status",
                "rejection_reason",
                "later_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        return staging_item


__all__ = [
    "ResearchAgentRunService",
    "ResearchReviewService",
    "ResearchSoulFileService",
]
