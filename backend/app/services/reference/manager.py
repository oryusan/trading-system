"""
Reference management service for handling model relationships.

Features:
- Relationship validation
- Reference integrity checking
- Cache management
- Circular dependency prevention
"""

from typing import Dict, List, Optional, Any, Type, Set, TypeVar, Generic
from datetime import datetime
import asyncio
from pydantic import BaseModel

from app.core.errors.base import ValidationError, DatabaseError
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T', bound=BaseModel)

class ReferenceManager(Generic[T]):
    """
    Manages model relationships and reference integrity.
    
    Features:
    - Validates model relationships without circular imports
    - Caches frequently accessed references
    - Handles forward references
    - Prevents circular dependencies
    """
    
    def __init__(self):
        """Initialize reference manager."""
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._reference_graph: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()
        self._validation_rules: Dict[str, List[str]] = {}

    async def validate_references(
        self,
        model: T,
        model_type: str,
        references: Dict[str, Any]
    ) -> None:
        """
        Validate model references without circular dependencies.
        
        Args:
            model: The model instance to validate
            model_type: Type of model being validated
            references: Dictionary of reference field names and values
            
        Raises:
            ValidationError: If references are invalid
            DatabaseError: If validation fails
        """
        try:
            async with self._lock:
                # Check for circular references
                if await self._would_create_cycle(model_type, references):
                    raise ValidationError(
                        "Circular reference detected",
                        context={
                            "model_type": model_type,
                            "references": references
                        }
                    )
                
                # Validate each reference
                for field_name, ref_value in references.items():
                    if not ref_value:
                        continue
                        
                    # Get validation rules for field
                    rules = self._validation_rules.get(f"{model_type}.{field_name}", [])
                    
                    # Apply validation rules
                    for rule in rules:
                        if not await self._apply_validation_rule(rule, ref_value):
                            raise ValidationError(
                                f"Validation rule '{rule}' failed",
                                context={
                                    "field": field_name,
                                    "value": ref_value,
                                    "rule": rule
                                }
                            )
                
                # Update reference graph
                self._update_reference_graph(model_type, references)

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "model_type": model_type,
                    "error": str(e)
                }
            )

    async def _would_create_cycle(
        self,
        model_type: str,
        references: Dict[str, Any]
    ) -> bool:
        """Check if adding references would create a cycle."""
        seen = {model_type}
        
        async def check_cycle(current_type: str) -> bool:
            if current_type in references:
                ref_type = self._get_reference_type(references[current_type])
                if ref_type in seen:
                    return True
                seen.add(ref_type)
                return await check_cycle(ref_type)
            return False
            
        return await check_cycle(model_type)

    def _update_reference_graph(
        self,
        model_type: str,
        references: Dict[str, Any]
    ) -> None:
        """Update the reference graph with new references."""
        if model_type not in self._reference_graph:
            self._reference_graph[model_type] = set()
            
        for ref_value in references.values():
            if ref_value:
                ref_type = self._get_reference_type(ref_value)
                self._reference_graph[model_type].add(ref_type)

    async def _apply_validation_rule(
        self,
        rule: str,
        value: Any
    ) -> bool:
        """Apply a validation rule to a reference value."""
        # Implement validation rules here
        if rule == "exists":
            return await self._validate_exists(value)
        elif rule == "active":
            return await self._validate_active(value)
        return True

    async def _validate_exists(self, reference: Any) -> bool:
        """Validate that a referenced entity exists."""
        # Implement existence validation
        return True

    async def _validate_active(self, reference: Any) -> bool:
        """Validate that a referenced entity is active."""
        # Implement active status validation
        return True

    def _get_reference_type(self, reference: Any) -> str:
        """Get the type name of a reference."""
        return reference.__class__.__name__

    async def add_validation_rule(
        self,
        model_type: str,
        field_name: str,
        rule: str
    ) -> None:
        """Add a validation rule for a model field."""
        key = f"{model_type}.{field_name}"
        if key not in self._validation_rules:
            self._validation_rules[key] = []
        self._validation_rules[key].append(rule)

    async def clear_cache(
        self,
        model_type: Optional[str] = None
    ) -> None:
        """Clear the reference cache."""
        async with self._lock:
            if model_type:
                self._cache.pop(model_type, None)
            else:
                self._cache.clear()

    async def get_dependents(
        self,
        model_type: str
    ) -> Set[str]:
        """Get types that depend on this model type."""
        dependents = set()
        for t, refs in self._reference_graph.items():
            if model_type in refs:
                dependents.add(t)
        return dependents

    async def get_dependencies(
        self,
        model_type: str
    ) -> Set[str]:
        """Get types that this model type depends on."""
        return self._reference_graph.get(model_type, set())

    async def validate_deletion(
        self,
        model: T,
        model_type: str
    ) -> None:
        """
        Validate that deleting a model won't break references.
        
        Args:
            model: The model to be deleted
            model_type: Type of model being deleted
            
        Raises:
            ValidationError: If deletion would break references
        """
        dependents = await self.get_dependents(model_type)
        if dependents:
            raise ValidationError(
                "Cannot delete: model has dependent references",
                context={
                    "model_type": model_type,
                    "dependents": list(dependents)
                }
            )

reference_manager = ReferenceManager()  # Global instance