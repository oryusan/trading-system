from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union
from datetime import datetime
from pydantic import BaseModel
from beanie import Document, PydanticObjectId
from beanie.operators import In

from app.core.logging.logger import get_logger
from app.core.errors.base import DatabaseError, ValidationError, NotFoundError

ModelType = TypeVar("ModelType", bound=Document)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    Base class for CRUD operations with enhanced error handling.

    Features:
    - Input validation
    - Rich error context
    - Proper error propagation
    - Operation logging
    """

    def __init__(self, model: Type[ModelType]):
        self.model = model
        self.logger = get_logger(f"crud_{model.__name__.lower()}")

    def _log_info(self, message: str, extra: Dict[str, Any]) -> None:
        """Helper to log messages with a standard timestamp and model name."""
        extra.setdefault("timestamp", datetime.utcnow().isoformat())
        extra.setdefault("model", self.model.__name__)
        self.logger.info(message, extra=extra)

    def _raise_db_error(self, operation: str, context: Dict[str, Any], error: Exception):
        """
        Helper to wrap exceptions (other than NotFound/Validation errors) into a DatabaseError.
        """
        context.setdefault("model", self.model.__name__)
        context.setdefault("timestamp", datetime.utcnow().isoformat())
        context["error"] = str(error)
        raise DatabaseError(f"Error {operation} {self.model.__name__}", context=context) from error

    async def get(self, id: PydanticObjectId) -> ModelType:
        """
        Retrieve a document by ID.
        """
        try:
            obj = await self.model.get(id)
            if not obj:
                raise NotFoundError(
                    f"{self.model.__name__} not found",
                    context={"id": str(id)}
                )
            return obj
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error("retrieving", {"id": str(id)}, e)

    async def get_by_ids(self, ids: List[PydanticObjectId]) -> List[ModelType]:
        """
        Retrieve multiple documents by IDs.
        """
        try:
            documents = await self.model.find(In(self.model.id, ids)).to_list()
            if not documents:
                raise NotFoundError(
                    f"No {self.model.__name__} documents found",
                    context={"ids": [str(_id) for _id in ids]}
                )
            return documents
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error("retrieving multiple", {"ids": [str(_id) for _id in ids]}, e)

    async def get_multi(
        self,
        skip: int = 0,
        limit: int = 100,
        query: Optional[Dict] = None,
        sort_by: Optional[str] = None,
        sort_desc: bool = False
    ) -> List[ModelType]:
        """
        Get multiple documents with pagination and sorting.
        """
        try:
            if skip < 0:
                raise ValidationError("Skip value must be non-negative", context={"skip": skip})
            if limit < 1:
                raise ValidationError("Limit value must be positive", context={"limit": limit})

            find_query = self.model.find(query) if query else self.model.find_all()
            if sort_by:
                sort_field = f"-{sort_by}" if sort_desc else sort_by
                find_query = find_query.sort(sort_field)
            documents = await find_query.skip(skip).limit(limit).to_list()

            self._log_info(
                f"Retrieved {len(documents)} {self.model.__name__} documents",
                {"skip": skip, "limit": limit, "sort_by": sort_by, "sort_desc": sort_desc, "query": query}
            )
            return documents
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error(
                "retrieving multiple",
                {"skip": skip, "limit": limit, "query": query, "sort_by": sort_by},
                e
            )

    async def create(self, obj_in: CreateSchemaType) -> ModelType:
        """
        Create a new document.
        """
        try:
            if not obj_in:
                raise ValidationError(
                    "Create data cannot be empty",
                    context={"model": self.model.__name__}
                )

            db_obj = self.model(**obj_in.model_dump())
            await db_obj.insert()

            self._log_info(
                f"Created new {self.model.__name__}",
                {"id": str(db_obj.id)}
            )
            return db_obj
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error("creating", {"data": obj_in.model_dump()}, e)

    async def update(
        self,
        id: PydanticObjectId,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        Update a document.
        """
        update_data: Dict[str, Any] = {}
        try:
            db_obj = await self.get(id)
            if not obj_in:
                raise ValidationError(
                    "Update data cannot be empty",
                    context={"id": str(id), "model": self.model.__name__}
                )
            update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(db_obj, field, value)
            await db_obj.save()

            self._log_info(
                f"Updated {self.model.__name__}",
                {"id": str(id), "fields": list(update_data.keys())}
            )
            return db_obj
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error("updating", {"id": str(id), "data": update_data}, e)

    async def delete(self, id: PydanticObjectId) -> bool:
        """
        Delete a document.
        """
        try:
            db_obj = await self.get(id)
            await db_obj.delete()

            self._log_info(
                f"Deleted {self.model.__name__}",
                {"id": str(id)}
            )
            return True
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error("deleting", {"id": str(id)}, e)

    async def bulk_create(self, objs_in: List[CreateSchemaType]) -> List[ModelType]:
        """
        Create multiple documents.
        """
        try:
            if not objs_in:
                raise ValidationError(
                    "Bulk create data cannot be empty",
                    context={"model": self.model.__name__}
                )
            db_objs = [self.model(**obj_in.model_dump()) for obj_in in objs_in]
            await self.model.insert_many(db_objs)

            self._log_info(
                f"Bulk created {self.model.__name__} documents",
                {"count": len(db_objs)}
            )
            return db_objs
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error("bulk creating", {"count": len(objs_in)}, e)

    async def bulk_update(
        self,
        ids: List[PydanticObjectId],
        update_data: Dict[str, Any]
    ) -> int:
        """
        Update multiple documents.
        """
        try:
            if not ids:
                raise ValidationError(
                    "Bulk update IDs cannot be empty",
                    context={"model": self.model.__name__}
                )
            if not update_data:
                raise ValidationError(
                    "Bulk update data cannot be empty",
                    context={"model": self.model.__name__}
                )

            result = await self.model.find({"_id": {"$in": ids}}).update({"$set": update_data})
            updated_count = result.modified_count

            self._log_info(
                f"Bulk updated {self.model.__name__} documents",
                {"count": updated_count, "fields": list(update_data.keys())}
            )
            return updated_count
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error(
                "bulk updating",
                {"ids": [str(_id) for _id in ids], "data": update_data},
                e
            )

    async def bulk_delete(self, ids: List[PydanticObjectId]) -> int:
        """
        Delete multiple documents.
        """
        try:
            if not ids:
                raise ValidationError(
                    "Bulk delete IDs cannot be empty",
                    context={"model": self.model.__name__}
                )

            result = await self.model.find({"_id": {"$in": ids}}).delete()
            deleted_count = result.deleted_count

            self._log_info(
                f"Bulk deleted {self.model.__name__} documents",
                {"count": deleted_count}
            )
            return deleted_count
        except Exception as e:
            if isinstance(e, (NotFoundError, ValidationError)):
                raise
            self._raise_db_error(
                "bulk deleting",
                {"ids": [str(_id) for _id in ids]},
                e
            )
