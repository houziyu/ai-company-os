"""Domain errors and their HTTP mapping.

The response envelope is always ``{code, message, request_id, field_errors}``
so the frontend can show a Chinese message and highlight fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FieldError:
    field: str
    message: str


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    field_errors: list[FieldError] = field(default_factory=list)
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class SetupAlreadyCompletedError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="SETUP_ALREADY_COMPLETED",
            message="所有者账号已初始化，不能重复创建。",
            status_code=409,
        )


class SetupRequiredError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="SETUP_REQUIRED",
            message="请先完成所有者账号初始化。",
            status_code=409,
        )


class InvalidCredentialsError(AppError):
    def __init__(self, locked_until_hint: str | None = None) -> None:
        details = {"locked_until": locked_until_hint} if locked_until_hint else None
        super().__init__(
            code="INVALID_CREDENTIALS",
            message="账号或密码不正确，或该账号暂时被锁定。",
            status_code=401,
            details=details,
        )


class LoginBlockedError(AppError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            code="LOGIN_BLOCKED",
            message="尝试次数过多，请稍后再试。",
            status_code=429,
            details={"retry_after_seconds": retry_after_seconds},
        )


class NotAuthenticatedError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="NOT_AUTHENTICATED",
            message="登录已失效，请重新登录。",
            status_code=401,
        )


class CsrfValidationError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="CSRF_VALIDATION_FAILED",
            message="请求来源校验失败，请刷新页面后重试。",
            status_code=403,
        )


class ForbiddenOriginError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="ORIGIN_NOT_ALLOWED",
            message="该请求来源未被允许。",
            status_code=403,
        )


class ValidationFailedError(AppError):
    def __init__(self, field_errors: list[FieldError]) -> None:
        super().__init__(
            code="VALIDATION_FAILED",
            message="提交的内容有误，请检查后重试。",
            status_code=422,
            field_errors=field_errors,
        )


class ServiceUnavailableError(AppError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="SERVICE_UNAVAILABLE",
            message=message,
            status_code=503,
            details=details,
        )


# ------------------------------------------------------------------ phase 02
class CompanyAlreadyExistsError(AppError):
    """A second company would break the single-company assumption."""

    def __init__(self) -> None:
        super().__init__(
            code="COMPANY_ALREADY_EXISTS",
            message="公司已创建，不能重复创建。",
            status_code=409,
        )


class CompanyNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="COMPANY_NOT_FOUND",
            message="公司尚未创建，请先完成公司初始化。",
            status_code=404,
        )


class PositionNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="POSITION_NOT_FOUND",
            message="岗位不存在或不属于当前公司。",
            status_code=404,
        )


class AgentNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="AGENT_NOT_FOUND",
            message="员工不存在或不属于当前公司。",
            status_code=404,
        )


class ProviderNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="PROVIDER_NOT_FOUND",
            message="供应商不存在。",
            status_code=404,
        )


class ModelNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="MODEL_NOT_FOUND",
            message="模型不存在或不属于当前供应商。",
            status_code=404,
        )


class ReportingCycleError(AppError):
    """A management cycle would make the chain ambiguous.

    Management relations never grant resource permissions, but an unbreakable
    cycle makes escalation routing undefined, so it is refused at write time
    with a field error the UI can highlight.
    """

    def __init__(self, message: str = "该汇报关系会形成循环，已被拒绝。") -> None:
        super().__init__(
            code="REPORTING_CYCLE",
            message=message,
            status_code=409,
            field_errors=[FieldError("managerId", message)],
        )


class SelfReportError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="REPORTING_SELF",
            message="员工不能向自己汇报。",
            status_code=409,
            field_errors=[FieldError("managerId", "员工不能向自己汇报。")],
        )


class CrossCompanyRelationError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="CROSS_COMPANY_RELATION",
            message="目标不属于当前公司，不能建立该关系。",
            status_code=409,
            field_errors=[FieldError("managerId", "目标不属于当前公司。")],
        )


class ConcurrentUpdateError(AppError):
    """Optimistic concurrency failed: the caller edited a stale copy."""

    def __init__(self, resource: str) -> None:
        super().__init__(
            code="CONCURRENT_UPDATE",
            message="该记录已被修改，请刷新后重试。",
            status_code=409,
            details={"resource": resource},
        )


class SecretNotConfiguredError(AppError):
    """The referenced environment variable is absent in this process."""

    def __init__(self, variable: str) -> None:
        super().__init__(
            code="SECRET_NOT_CONFIGURED",
            message=f"服务端未配置密钥引用 {variable}，请先在部署环境中设置。",
            status_code=409,
            field_errors=[FieldError("secretRef", "该密钥引用在服务端未配置。")],
        )


class ModelBindingUnavailableError(AppError):
    """Assignment is refused because the target model or provider is disabled."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            code="MODEL_BINDING_UNAVAILABLE",
            message=reason,
            status_code=409,
            field_errors=[FieldError("modelId", reason)],
        )


# ------------------------------------------------------------------ phase 03
class ProjectNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="PROJECT_NOT_FOUND",
            message="项目不存在。",
            status_code=404,
        )


class TaskNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="TASK_NOT_FOUND",
            message="任务不存在。",
            status_code=404,
        )


class ExecutionNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="EXECUTION_NOT_FOUND",
            message="执行记录不存在。",
            status_code=404,
        )


class InvalidTaskTransitionError(AppError):
    """A requested state change is not allowed from the task's current state.

    The server owns the state machine; the browser may only request named
    commands (cancel, retry) and never writes ``status`` directly.
    """

    def __init__(self, current: str, action: str) -> None:
        super().__init__(
            code="INVALID_TASK_TRANSITION",
            message=f"任务当前状态（{current}）不允许执行该操作（{action}）。",
            status_code=409,
            details={"currentStatus": current, "action": action},
        )


class IdempotencyConflictError(AppError):
    """Same idempotency key, different request body."""

    def __init__(self, resource: str) -> None:
        super().__init__(
            code="IDEMPOTENCY_CONFLICT",
            message="该请求编号已被不同的请求使用，请刷新后重新提交。",
            status_code=409,
            details={"resource": resource},
        )


class BudgetInsufficientError(AppError):
    """The requested allowance exceeds what is available at this scope."""

    def __init__(self, *, scope: str, requested: int, available: int) -> None:
        labels = {"COMPANY": "公司", "PROJECT": "项目", "TASK": "任务"}
        label = labels.get(scope, scope)
        super().__init__(
            code="BUDGET_INSUFFICIENT",
            message=(
                f"{label}可用额度不足：需要 {requested} Token，当前可用 {max(available, 0)} Token。"
            ),
            status_code=409,
            details={
                "scope": scope,
                "requestedTokens": requested,
                "availableTokens": max(available, 0),
            },
        )


class CompanyPausedError(AppError):
    """Company-wide pause blocks new paid calls and new task creation."""

    def __init__(self) -> None:
        super().__init__(
            code="COMPANY_PAUSED",
            message="公司处于暂停状态，已阻止新的模型调用与任务创建。恢复公司后可继续。",
            status_code=409,
        )


class RunModeUnavailableError(AppError):
    """A run mode has no implementation yet and is refused, never downgraded."""

    def __init__(self, mode: str) -> None:
        super().__init__(
            code="RUN_MODE_UNAVAILABLE",
            message=(
                f"运行模式 {mode} 尚未实现：本阶段只提供明确标识的演练处理器。"
                "系统不会把真实运行静默降级为演练。"
            ),
            status_code=409,
            details={"runMode": mode},
        )


class LeaseLostError(AppError):
    """A worker tried to write with a fencing token it no longer holds."""

    def __init__(self, execution_id: int) -> None:
        super().__init__(
            code="LEASE_LOST",
            message="该执行已失去租约，结果未被接受。",
            status_code=409,
            details={"executionId": execution_id},
        )


class ExecutionStoppedError(Exception):
    """A step must not start: a stop signal is in force.

    This is *not* an API error — no HTTP request returns it. It is raised inside
    the execution kernel so every path that would open a new paid call passes the
    same server-side gate (company pause, task/execution cancel, expired lease).
    The processor catches it and reports a stopped outcome; it never invents one.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class ExecutionAlreadySettledError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="EXECUTION_ALREADY_RESOLVED",
            message="该执行已有结果，不能再次结算。",
            status_code=409,
        )
