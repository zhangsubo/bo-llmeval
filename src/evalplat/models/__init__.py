"""ORM 模型聚合：供 Alembic autogenerate 发现全部表。"""

from evalplat.models.attempt import Attempt
from evalplat.models.audit_event import AuditEvent
from evalplat.models.comparison_group import ComparisonGroup
from evalplat.models.connection import Connection
from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.model_price import ModelPrice
from evalplat.models.note import Note
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.models.system_setting import SystemSetting
from evalplat.models.task import Task
from evalplat.models.test_template import TestTemplate, TestTemplateVersion
from evalplat.models.tokenizer import Tokenizer

__all__ = [
    "Attempt",
    "AuditEvent",
    "ComparisonGroup",
    "Connection",
    "Dataset",
    "DatasetVersion",
    "ModelDeployment",
    "ModelPrice",
    "Note",
    "Request",
    "Run",
    "Supplier",
    "SystemSetting",
    "Task",
    "TestTemplate",
    "TestTemplateVersion",
    "Tokenizer",
]
