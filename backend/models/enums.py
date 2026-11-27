"""Enumerations used across the application."""

from __future__ import annotations

from enum import Enum


class DeploymentMode(str, Enum):
    DEMO = "demo"
    SUBDOMAIN = "subdomain"
    PROD = "prod"
    CLOUDRUN = "cloudrun"


class DeploymentStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class PipelineStep(str, Enum):
    EXTRACT = "EXTRACT"
    AI_INSPECT = "AI_INSPECT"
    AI_FIX = "AI_FIX"
    BUILD = "BUILD"
    VERIFY = "VERIFY"
    DOMAIN_REGISTER = "DOMAIN_REGISTER"
    INFRA = "INFRA"
    UPLOAD = "UPLOAD"
    NOTIFY = "NOTIFY"


#: The order the orchestrator runs the steps in.
PIPELINE_STEPS = [
    PipelineStep.EXTRACT,
    PipelineStep.AI_INSPECT,
    PipelineStep.AI_FIX,
    PipelineStep.BUILD,
    PipelineStep.VERIFY,
    PipelineStep.DOMAIN_REGISTER,
    PipelineStep.INFRA,
    PipelineStep.UPLOAD,
    PipelineStep.NOTIFY,
]

#: Steps that act on uploaded sources. A deployment that links an existing
#: Cloud Run service has none — the container is already built and running —
#: so its pipeline is DOMAIN_REGISTER → INFRA → NOTIFY.
SOURCE_STEPS = (
    PipelineStep.EXTRACT,
    PipelineStep.AI_INSPECT,
    PipelineStep.AI_FIX,
    PipelineStep.BUILD,
    PipelineStep.VERIFY,
    PipelineStep.UPLOAD,
)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class LogLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class UserRole(str, Enum):
    ADMIN = "admin"
    SIMPLE_USER = "simple_user"
    SUPER_USER = "super_user"


class UserStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
