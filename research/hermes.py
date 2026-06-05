from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class HermesRunRequest:
    run_id: str
    user_id: int
    project_id: int
    role: str
    task: str
    selected_context: dict[str, object]
    soul_file_version_id: int | None
    allowed_tools: list[str]
    capability_policy: dict[str, bool]
    output_destination: str


@dataclass(frozen=True)
class HermesDispatchResult:
    accepted: bool
    status: str
    status_message: str
    error_message: str = ""
    external_run_id: str = ""


class BaseHermesAdapter(ABC):
    @abstractmethod
    def dispatch(self, request: HermesRunRequest) -> HermesDispatchResult:
        pass


class UnavailableHermesAdapter(BaseHermesAdapter):
    def dispatch(self, request: HermesRunRequest) -> HermesDispatchResult:
        return HermesDispatchResult(
            accepted=False,
            status="failed",
            status_message="Hermes runtime is not configured for this environment.",
            error_message="Hermes runtime is not configured.",
        )


class FakeHermesAdapter(BaseHermesAdapter):
    def dispatch(self, request: HermesRunRequest) -> HermesDispatchResult:
        return HermesDispatchResult(
            accepted=True,
            status="queued",
            status_message="Run accepted by the local Hermes fake adapter.",
            external_run_id=f"fake-{request.run_id}",
        )


def get_hermes_adapter() -> BaseHermesAdapter:
    adapter_key = getattr(settings, "HERMES_ADAPTER", "unavailable")
    if adapter_key == "fake":
        return FakeHermesAdapter()
    return UnavailableHermesAdapter()


__all__ = [
    "BaseHermesAdapter",
    "FakeHermesAdapter",
    "HermesDispatchResult",
    "HermesRunRequest",
    "UnavailableHermesAdapter",
    "get_hermes_adapter",
]
