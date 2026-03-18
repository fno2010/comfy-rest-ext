"""
Task registry for named task handlers.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("comfy-rest-ext.tasks")


class TaskRegistry:
    """
    Registry of named task handlers.

    Allows registering handler functions/callables that can be
    looked up by name and invoked to create new tasks.
    """

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, name: str, handler: Callable) -> None:
        """
        Register a task handler.

        Args:
            name: Unique identifier for the task type
            handler: Callable that creates and returns a coroutine
        """
        if name in self._handlers:
            logger.warning(f"Overwriting existing handler for task type: {name}")
        self._handlers[name] = handler
        logger.debug(f"Registered task handler: {name}")

    def get(self, name: str) -> Optional[Callable]:
        """Get a handler by name."""
        return self._handlers.get(name)

    def list_handlers(self) -> Dict[str, Callable]:
        """List all registered handlers."""
        return dict(self._handlers)

    def unregister(self, name: str) -> bool:
        """
        Unregister a handler.

        Returns True if the handler was found and removed.
        """
        if name in self._handlers:
            del self._handlers[name]
            logger.debug(f"Unregistered task handler: {name}")
            return True
        return False


# Global registry instance
_task_registry: Optional[TaskRegistry] = None


def get_task_registry() -> TaskRegistry:
    """Get the global task registry instance."""
    global _task_registry
    if _task_registry is None:
        _task_registry = TaskRegistry()
    return _task_registry