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
