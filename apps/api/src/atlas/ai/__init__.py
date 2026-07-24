"""Model routing and resilient execution (docs/20 §4-5, §8).

Framework-free by contract: sits beside atlas.rag between application
and domain in the import-linter layering. Adapters live in
infrastructure/providers; this package never imports a vendor SDK.
"""

from atlas.ai.breaker import BreakerBoard, CircuitBreaker
from atlas.ai.cost import DEFAULT_RATES, CostMeter, ModelRates
from atlas.ai.executor import ExecutionResult, ResilientExecutor
from atlas.ai.routing import ModelRef, ModelRouter, Profile, Role, RoutePlan

__all__ = [
    "DEFAULT_RATES",
    "BreakerBoard",
    "CircuitBreaker",
    "CostMeter",
    "ExecutionResult",
    "ModelRates",
    "ModelRef",
    "ModelRouter",
    "Profile",
    "ResilientExecutor",
    "Role",
    "RoutePlan",
]
