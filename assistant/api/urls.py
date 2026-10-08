from django.urls import path

from assistant.api.views import (
    ApplyProposalView,
    AssistantNewThreadView,
    AssistantThreadView,
    DiscardProposalView,
)

urlpatterns = [
    path("assistant/thread/", AssistantThreadView.as_view(), name="thread"),
    path("assistant/thread/new/", AssistantNewThreadView.as_view(), name="new-thread"),
    path(
        "assistant/proposals/<int:proposal_id>/apply/",
        ApplyProposalView.as_view(),
        name="apply-proposal",
    ),
    path(
        "assistant/proposals/<int:proposal_id>/discard/",
        DiscardProposalView.as_view(),
        name="discard-proposal",
    ),
]
