"""The default technology-company template.

This module is the single source of truth for template content. The Alembic
migration seeds ``company_template_positions`` from :data:`TEMPLATE_POSITIONS`,
and the initialization service reads the table (not this module) so that a
deployed database always describes what it actually created. The migration test
asserts the two stay in sync.

The five roles come from the product baseline: CEO, 产品经理, 技术负责人/开发,
QA, DevOps. ``reports_to_code`` defines the initial management tree; it is
management only and grants no resource permission.
"""

from __future__ import annotations

from dataclasses import dataclass

TEMPLATE_KEY = "tech-company"
TEMPLATE_VERSION = 1

DEPARTMENT_CODE = "ENGINEERING"
DEPARTMENT_NAME = "研发部"
DEPARTMENT_PURPOSE = "负责产品定义、技术实现、质量保证与持续交付。"

CEO_CODE = "CEO"


@dataclass(frozen=True, slots=True)
class TemplatePosition:
    code: str
    title: str
    default_agent_name: str
    duty: str
    reports_to_code: str | None
    sort_order: int
    max_reports: int


TEMPLATE_POSITIONS: tuple[TemplatePosition, ...] = (
    TemplatePosition(
        code="CEO",
        title="CEO",
        default_agent_name="首席执行官",
        duty=(
            "设定公司目标与优先级，裁决跨部门冲突，对超出部门负责人权限的决策负责。"
            "不直接领取实现任务，除非明确说明原因。"
        ),
        reports_to_code=None,
        sort_order=10,
        max_reports=10,
    ),
    TemplatePosition(
        code="PRODUCT_MANAGER",
        title="产品经理",
        default_agent_name="产品经理",
        duty=(
            "把公司目标拆成可验收的产品需求，维护需求优先级与验收标准，"
            "对“做什么、为什么做”负责，不替代技术负责人做实现决策。"
        ),
        reports_to_code=CEO_CODE,
        sort_order=20,
        max_reports=0,
    ),
    TemplatePosition(
        code="TECH_LEAD",
        title="技术负责人/开发",
        default_agent_name="技术负责人",
        duty=(
            "负责技术方案、代码实现与技术评审，把需求转化为可测试的变更，"
            "并对实现质量与可维护性负责。"
        ),
        reports_to_code=CEO_CODE,
        sort_order=30,
        max_reports=10,
    ),
    TemplatePosition(
        code="QA",
        title="QA",
        default_agent_name="质量工程师",
        duty=(
            "独立验证交付是否满足验收标准，编写与运行测试，报告可复现的缺陷，"
            "不因为实现者自称完成而判定通过。"
        ),
        reports_to_code="TECH_LEAD",
        sort_order=40,
        max_reports=0,
    ),
    TemplatePosition(
        code="DEVOPS",
        title="DevOps",
        default_agent_name="DevOps 工程师",
        duty=(
            "维护构建、部署、可观测性与环境配置，保证变更可以安全发布与回滚，"
            "不获得超出部署需要的凭据。"
        ),
        reports_to_code="TECH_LEAD",
        sort_order=50,
        max_reports=0,
    ),
)

TEMPLATE_POSITION_CODES: frozenset[str] = frozenset(p.code for p in TEMPLATE_POSITIONS)


def expected_template_rows() -> list[dict[str, object]]:
    """Rows for the migration seed, in insert order."""
    return [
        {
            "template_key": TEMPLATE_KEY,
            "template_version": TEMPLATE_VERSION,
            "code": p.code,
            "title": p.title,
            "default_agent_name": p.default_agent_name,
            "duty": p.duty,
            "reports_to_code": p.reports_to_code,
            "sort_order": p.sort_order,
            "max_reports": p.max_reports,
        }
        for p in TEMPLATE_POSITIONS
    ]
