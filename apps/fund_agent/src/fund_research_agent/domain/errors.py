from __future__ import annotations


class AgentError(RuntimeError):
    code = "agent_error"
    retryable = False


class ValidationError(AgentError):
    code = "validation_error"


class DependencyUnavailable(AgentError):
    code = "dependency_unavailable"


class PolicyDenied(AgentError):
    code = "policy_denied"


class DataUnavailable(AgentError):
    code = "data_unavailable"


class StaleData(AgentError):
    code = "stale_data"


class ToolTimeout(AgentError):
    code = "timeout"
    retryable = True


class TransientToolError(AgentError):
    code = "transient_error"
    retryable = True


class ContextOverflow(AgentError):
    code = "context_overflow"


class WriteBoundaryViolation(PolicyDenied):
    code = "write_boundary_violation"

