"""
Reference management service for handling model relationships.

Features:
- Relationship validation
- Reference integrity checking
- Cache management
- Circular dependency prevention
"""

import asyncio
from typing import Any, Dict, Optional, Set, TypeVar, Generic, List

from pydantic import BaseModel
from app.core.errors.base import ValidationError
from app.core.config.settings import settings
from app.core.logging.logger import get_logger
from app.core.errors.decorators import error_handler
from app.db.db import db

logger = get_logger(__name__)

T = TypeVar('T', bound=BaseModel)


class ReferenceManager(Generic[T]):
    """
    Manages model relationships and reference integrity.

    Features:
    - Validates model relationships without circular dependencies
    - Caches frequently accessed references
    - Prevents circular dependencies
    """

    def __init__(self):
        """Initialize reference manager."""
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._reference_graph: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()
        self._validation_rules: Dict[str, Set[str]] = {}

    async def start(self) -> None:
        """
        Start or initialize the Reference Manager.
        
        This method clears any existing cache and reference graph,
        and logs that the Reference Manager has been started. 
        Extend this method with any additional initialization logic if needed.
        """
        async with self._lock:
            self._cache.clear()
            self._reference_graph.clear()
        logger.info("Reference Manager started successfully, cache and reference graph cleared.")

    @error_handler(log_message="Error occurred during reference validation")
    async def validate_references(self, model: T, model_type: str, references: Dict[str, Any]) -> None:
        """
        Validate model references, preventing circular dependencies.
        """
        async with self._lock:
            if self._would_create_cycle(model_type, references):
                raise ValidationError(
                    "Circular reference detected", context={"model_type": model_type, "references": references}
                )

            # Apply validation rules for each reference
            for field_name, ref_value in references.items():
                if ref_value and not await self._apply_validation_rules(model_type, field_name, ref_value):
                    raise ValidationError(
                        f"Validation failed for field '{field_name}'", context={"field": field_name}
                    )

            # Update reference graph after successful validation
            self._update_reference_graph(model_type, references)

    async def get_reference_counts(self) -> Dict[str, int]:
        """
        Retrieve counts of documents in key collections for monitoring and health-checks.
        """
        # Retrieve the actual database instance using your Database settings.
        database = db.client[settings.database.MONGODB_DB_NAME]
        counts = {}
        counts["User"] = await database.get_collection("users").count_documents({})
        counts["Bot"] = await database.get_collection("bots").count_documents({})
        counts["Account"] = await database.get_collection("accounts").count_documents({})
        counts["AccountGroup"] = await database.get_collection("account_groups").count_documents({})
        counts["Trade"] = await database.get_collection("trades").count_documents({})
        counts["SymbolData"] = await database.get_collection("symbol_data").count_documents({})
        counts["PositionHistory"] = await database.get_collection("position_history").count_documents({})
        return counts

    def _would_create_cycle(self, model_type: str, references: Dict[str, Any]) -> bool:
        """
        Determine whether adding the given references for the model_type would create a circular dependency.
        """
        new_refs = {self._get_reference_type(ref) for ref in references.values() if ref}
        return any(self._has_path(ref, model_type) for ref in new_refs)

    def _has_path(self, start: str, target: str) -> bool:
        """
        Check if there is a path from start to target in the reference graph.
        """
        visited = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node not in visited:
                visited.add(node)
                stack.extend(self._reference_graph.get(node, []))
        return False

    def _update_reference_graph(self, model_type: str, references: Dict[str, Any]) -> None:
        """
        Update the internal reference graph with the provided references.
        """
        if model_type not in self._reference_graph:
            self._reference_graph[model_type] = set()
        for ref_value in references.values():
            if ref_value:
                self._reference_graph[model_type].add(self._get_reference_type(ref_value))

    async def _apply_validation_rules(self, model_type: str, field_name: str, ref_value: Any) -> bool:
        """
        Apply all validation rules for a specific field.
        """
        rules = self._validation_rules.get(f"{model_type}.{field_name}", [])
        for rule in rules:
            if not await self._apply_validation_rule(rule, ref_value):
                return False
        return True

    @error_handler(
        log_message="Error applying validation rule",
        context_extractor=lambda self, rule, value: {"rule": rule, "value": repr(value)}
    )
    async def _apply_validation_rule(self, rule: str, value: Any) -> bool:
        """
        Apply a specific validation rule to the given value.
        """
        if rule == "exists":
            return await self._validate_exists(value)
        elif rule == "active":
            return await self._validate_active(value)
        return True

    @error_handler(
        log_message="Error validating existence",
        context_extractor=lambda self, reference: {"reference": repr(reference)}
    )
    async def _validate_exists(self, reference: Any) -> bool:
        """
        Validate that a reference exists.
        Placeholder for actual existence-checking logic.
        """
        return True

    @error_handler(
        log_message="Error validating active status",
        context_extractor=lambda self, reference: {"reference": repr(reference)}
    )
    async def _validate_active(self, reference: Any) -> bool:
        """
        Validate that a reference is active.
        Placeholder for actual active-status-checking logic.
        """
        return True

    def _get_reference_type(self, reference: Any) -> str:
        """
        Get the type name of the reference.
        """
        return reference.__class__.__name__

    def add_validation_rule(self, model_type: str, field_name: str, rule: str) -> None:
        """
        Add a validation rule for a model's field.
        """
        key = f"{model_type}.{field_name}"
        self._validation_rules.setdefault(key, set()).add(rule)

    async def clear_cache(self, model_type: Optional[str] = None) -> None:
        """
        Clear the internal reference cache.
        """
        async with self._lock:
            if model_type:
                self._cache.pop(model_type, None)
            else:
                self._cache.clear()

    def get_dependents(self, model_type: str) -> Set[str]:
        """
        Get dependent model types for a given model type.
        """
        return {t for t, refs in self._reference_graph.items() if model_type in refs}

    def get_dependencies(self, model_type: str) -> Set[str]:
        """
        Get model types that the specified model type depends on.
        """
        return self._reference_graph.get(model_type, set())

    def validate_deletion(self, model: T, model_type: str) -> None:
        """
        Validate that deleting a model won't break references.
        """
        dependents = self.get_dependents(model_type)
        if dependents:
            raise ValidationError(
                "Cannot delete: model has dependent references",
                context={"model_type": model_type, "dependents": list(dependents)}
            )


# Global instance of the reference service.
reference_manager = ReferenceManager()
