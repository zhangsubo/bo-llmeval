"""顶层 API 路由聚合。"""

from fastapi import APIRouter

from evalplat.api.routes.admin import router as admin_router
from evalplat.api.routes.comparison_groups import router as comparison_groups_router
from evalplat.api.routes.comparisons import router as comparisons_router
from evalplat.api.routes.connections import router as connections_router
from evalplat.api.routes.datasets import router as datasets_router
from evalplat.api.routes.health import router as health_router
from evalplat.api.routes.model_deployments import router as model_deployments_router
from evalplat.api.routes.monitoring import router as monitoring_router
from evalplat.api.routes.prices import router as prices_router
from evalplat.api.routes.quality import router as quality_router
from evalplat.api.routes.reports import router as reports_router
from evalplat.api.routes.runs import router as runs_router
from evalplat.api.routes.security import router as security_router
from evalplat.api.routes.suppliers import router as suppliers_router
from evalplat.api.routes.tasks import router as tasks_router
from evalplat.api.routes.templates import router as templates_router
from evalplat.api.routes.tokenizers import router as tokenizers_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(suppliers_router)
api_router.include_router(connections_router)
api_router.include_router(model_deployments_router)
api_router.include_router(comparison_groups_router)
api_router.include_router(prices_router)
api_router.include_router(tokenizers_router)
api_router.include_router(datasets_router)
api_router.include_router(templates_router)
api_router.include_router(tasks_router)
api_router.include_router(runs_router)
api_router.include_router(quality_router)
api_router.include_router(monitoring_router)
api_router.include_router(comparisons_router)
api_router.include_router(reports_router)
api_router.include_router(security_router)
api_router.include_router(admin_router)
