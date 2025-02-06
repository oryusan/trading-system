from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union
from datetime import datetime
from pydantic import BaseModel
from beanie import Document, PydanticObjectId
from beanie.operators import In

from app.core.logging.logger import get_logger
from app.core.errors import (
    DatabaseError,
    ValidationError, 
    NotFoundError
)

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
        """Initialize CRUD instance with model type."""
        self.model = model
        self.logger = get_logger(f"crud_{model.__name__.lower()}")

    async def get(self, id: PydanticObjectId) -> ModelType:
        """
        Retrieve a document by ID with validation.
        
        Args:
            id: Document ID to retrieve
            
        Returns:
            ModelType: The found document
            
        Raises:
            NotFoundError: If document doesn't exist
            DatabaseError: If retrieval fails
        """
        try:
            obj = await self.model.get(id)
            if not obj:
                raise NotFoundError(
                    f"{self.model.__name__} not found",
                    context={"id": str(id)}
                )
            return obj
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error retrieving {self.model.__name__}",
                context={
                    "id": str(id),
                    "error": str(e),
                    "model": self.model.__name__
                }
            )

    async def get_by_ids(self, ids: List[PydanticObjectId]) -> List[ModelType]:
        """
        Retrieve multiple documents by IDs.
        
        Args:
            ids: List of document IDs
            
        Returns:
            List[ModelType]: The found documents
            
        Raises:
            NotFoundError: If no documents found
            DatabaseError: If retrieval fails
        """
        try:
            documents = await self.model.find(In(self.model.id, ids)).to_list()
            if not documents:
                raise NotFoundError(
                    f"No {self.model.__name__} documents found",
                    context={"ids": [str(id) for id in ids]}
                )
            return documents
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error retrieving multiple {self.model.__name__}s",
                context={
                    "ids": [str(id) for id in ids],
                    "error": str(e),
                    "model": self.model.__name__
                }
            )

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
        
        Args:
            skip: Number of documents to skip
            limit: Maximum documents to return
            query: Optional filter query
            sort_by: Optional field to sort by
            sort_desc: Sort descending if True
            
        Returns:
            List[ModelType]: Found documents
            
        Raises:
            ValidationError: If skip/limit invalid
            DatabaseError: If query fails
        """
        try:
            if skip < 0:
                raise ValidationError(
                    "Skip value must be non-negative",
                    context={"skip": skip}
                )
            if limit < 1:
                raise ValidationError(
                    "Limit value must be positive",
                    context={"limit": limit}
                )

            find_query = self.model.find(query) if query else self.model.find_all()
            
            if sort_by:
                sort_field = f"-{sort_by}" if sort_desc else sort_by
                find_query = find_query.sort(sort_field)
                
            documents = await find_query.skip(skip).limit(limit).to_list()
            
            self.logger.info(
                f"Retrieved {len(documents)} {self.model.__name__} documents",
                extra={
                    "skip": skip,
                    "limit": limit,
                    "sort_by": sort_by,
                    "sort_desc": sort_desc,
                    "query": query
                }
            )
            
            return documents
            
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error retrieving multiple {self.model.__name__} documents",
                context={
                    "skip": skip,
                    "limit": limit,
                    "query": query,
                    "sort_by": sort_by,
                    "error": str(e),
                    "model": self.model.__name__
                }
            )

    async def create(self, obj_in: CreateSchemaType) -> ModelType:
        """
        Create a new document.
        
        Args:
            obj_in: Document creation data
            
        Returns:
            ModelType: Created document
            
        Raises:
            ValidationError: If create data invalid
            DatabaseError: If creation fails
        """
        try:
            # Validate input data
            if not obj_in:
                raise ValidationError(
                    "Create data cannot be empty",
                    context={"model": self.model.__name__}
                )

            db_obj = self.model(**obj_in.model_dump())
            await db_obj.insert()
            
            self.logger.info(
                f"Created new {self.model.__name__}",
                extra={
                    "id": str(db_obj.id),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return db_obj
            
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error creating {self.model.__name__}",
                context={
                    "data": obj_in.model_dump(),
                    "error": str(e),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

    async def update(
        self,
        id: PydanticObjectId,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        Update a document.
        
        Args:
            id: Document ID to update
            obj_in: Update data
            
        Returns:
            ModelType: Updated document
            
        Raises:
            NotFoundError: If document not found
            ValidationError: If update data invalid
            DatabaseError: If update fails
        """
        try:
            db_obj = await self.get(id)  # This will raise NotFoundError if needed
            
            # Validate update data
            if not obj_in:
                raise ValidationError(
                    "Update data cannot be empty",
                    context={
                        "id": str(id),
                        "model": self.model.__name__
                    }
                )

            update_data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
            
            for field, value in update_data.items():
                setattr(db_obj, field, value)

            await db_obj.save()
            
            self.logger.info(
                f"Updated {self.model.__name__}",
                extra={
                    "id": str(id),
                    "model": self.model.__name__,
                    "fields": list(update_data.keys()),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return db_obj
            
        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error updating {self.model.__name__}",
                context={
                    "id": str(id),
                    "data": update_data if "update_data" in locals() else None,
                    "error": str(e),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

    async def delete(self, id: PydanticObjectId) -> bool:
        """
        Delete a document.
        
        Args:
            id: Document ID to delete
            
        Returns:
            bool: True if deleted
            
        Raises:
            NotFoundError: If document not found
            DatabaseError: If deletion fails
        """
        try:
            db_obj = await self.get(id)  # This will raise NotFoundError if needed
            
            await db_obj.delete()
            
            self.logger.info(
                f"Deleted {self.model.__name__}",
                extra={
                    "id": str(id),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return True
            
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error deleting {self.model.__name__}",
                context={
                    "id": str(id),
                    "error": str(e),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

    async def bulk_create(self, objs_in: List[CreateSchemaType]) -> List[ModelType]:
        """
        Create multiple documents.
        
        Args:
            objs_in: List of document creation data
            
        Returns:
            List[ModelType]: Created documents
            
        Raises:
            ValidationError: If input data invalid
            DatabaseError: If creation fails
        """
        try:
            if not objs_in:
                raise ValidationError(
                    "Bulk create data cannot be empty",
                    context={"model": self.model.__name__}
                )

            db_objs = [self.model(**obj_in.model_dump()) for obj_in in objs_in]
            await self.model.insert_many(db_objs)
            
            self.logger.info(
                f"Bulk created {self.model.__name__} documents",
                extra={
                    "count": len(db_objs),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return db_objs
            
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error in bulk create of {self.model.__name__}",
                context={
                    "count": len(objs_in),
                    "error": str(e),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

    async def bulk_update(
        self,
        ids: List[PydanticObjectId],
        update_data: Dict[str, Any]
    ) -> int:
        """
        Update multiple documents.
        
        Args:
            ids: List of document IDs to update
            update_data: Update data to apply
            
        Returns:
            int: Number of documents updated
            
        Raises:
            ValidationError: If input invalid
            DatabaseError: If update fails
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
            
            self.logger.info(
                f"Bulk updated {self.model.__name__} documents",
                extra={
                    "count": updated_count,
                    "model": self.model.__name__,
                    "fields": list(update_data.keys()),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return updated_count
            
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error in bulk update of {self.model.__name__}",
                context={
                    "ids": [str(id) for id in ids],
                    "data": update_data,
                    "error": str(e),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

    async def bulk_delete(self, ids: List[PydanticObjectId]) -> int:
        """
        Delete multiple documents.
        
        Args:
            ids: List of document IDs to delete
            
        Returns:
            int: Number of documents deleted
            
        Raises:
            ValidationError: If IDs empty
            DatabaseError: If deletion fails
        """
        try:
            if not ids:
                raise ValidationError(
                    "Bulk delete IDs cannot be empty",
                    context={"model": self.model.__name__}
                )

            result = await self.model.find({"_id": {"$in": ids}}).delete()
            deleted_count = result.deleted_count
            
            self.logger.info(
                f"Bulk deleted {self.model.__name__} documents",
                extra={
                    "count": deleted_count,
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return deleted_count
            
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                f"Error in bulk delete of {self.model.__name__}",
                context={
                    "ids": [str(id) for id in ids],
                    "error": str(e),
                    "model": self.model.__name__,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )