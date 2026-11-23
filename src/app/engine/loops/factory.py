from __future__ import annotations

from app.engine.loops.base import BaseLoopEngine
from app.engine.loops.plan_execute import PlanAndExecuteEngine
from app.engine.loops.react import ReActEngine
from app.engine.loops.reflexion import ReflexionEngine


class LoopEngineFactory:
    """Strategy registry allowing runtime switching between reasoning loop engines."""

    _registry: dict[str, type[BaseLoopEngine]] = {
        "react": ReActEngine,
        "plan_execute": PlanAndExecuteEngine,
        "reflexion": ReflexionEngine,
    }

    @classmethod
    def register(cls, name: str, engine_cls: type[BaseLoopEngine]) -> None:
        cls._registry[name.lower()] = engine_cls

    @classmethod
    def list_engines(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseLoopEngine:
        key = name.lower()
        engine_cls = cls._registry.get(key)
        if engine_cls is None:
            available = ", ".join(cls.list_engines())
            raise ValueError(f"Unknown loop engine '{name}'. Available: {available}")
        return engine_cls(**kwargs)


def create_loop_engine(name: str = "react", **kwargs) -> BaseLoopEngine:
    return LoopEngineFactory.create(name, **kwargs)
