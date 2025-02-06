"""
Symbol information model with exchange mapping and validation.

Features:
- Exchange-specific symbol mapping
- Scheduled validation using symbol_validator service
- Error handling with rich context
- Service integration with proper decoupling
"""

from datetime import datetime, timedelta
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

from app.core.errors import (
    ValidationError,
    DatabaseError,
    NotFoundError,
    ExchangeError
)
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType
from app.services.exchange.factory import symbol_validator

logger = get_logger(__name__)

class SymbolInfo(Document):
    """
    Represents exchange-specific symbol mappings.
    
    Features:
    - Maps generic symbols to exchange-specific formats
    - Regular validation scheduling
    - Error tracking and recovery
    """
    
    # Core fields
    original: Indexed(str) = Field(
        ...,
        description="Original symbol identifier (e.g., 'BTC')"
    )
    normalized_symbol: str = Field(
        ...,
        description="Exchange-specific normalized symbol (e.g., 'BTC-USDT-SWAP')"
    )
    exchange: ExchangeType = Field(
        ...,
        description="Exchange this mapping applies to"
    )

    # Verification tracking
    last_verified: datetime = Field(
        default_factory=datetime.utcnow,
        description="When mapping was last verified"
    )
    next_verification: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(days=1),
        description="When mapping needs verification"
    )
    is_active: bool = Field(
        True,
        description="Whether mapping is valid"
    )

    # Error tracking
    verification_failures: int = Field(
        0,
        description="Consecutive verification failures",
        ge=0
    )
    last_error: Optional[str] = Field(
        None,
        description="Last error message"
    )

    class Settings:
        """Collection settings and indexes."""
        name = "symbol_info"
        indexes = [
            [("original", 1), ("exchange", 1)],  # Compound unique index
            "last_verified",
            "next_verification",
            "is_active"
        ]

    @field_validator("next_verification")
    @classmethod
    def validate_next_verification(cls, v: datetime, info) -> datetime:
        """
        Ensure next verification is after last verified.
        
        Args:
            v: Next verification time
            info: Context with current data
            
        Returns:
            datetime: Validated time
            
        Raises:
            ValidationError: If schedule invalid
        """
        try:
            last_verified = info.data.get('last_verified')
            if last_verified and v < last_verified:
                raise ValidationError(
                    "Next verification must be after last verification",
                    context={
                        "last_verified": last_verified.isoformat(),
                        "next_verification": v.isoformat()
                    }
                )
            return v
        except Exception as e:
            raise ValidationError(
                "Invalid verification schedule",
                context={
                    "next_verification": v.isoformat() if v else None,
                    "error": str(e)
                }
            )

    @classmethod
    async def get_active_mappings(
        cls,
        exchange: Optional[ExchangeType] = None
    ) -> List["SymbolInfo"]:
        """
        Get all active symbol mappings.
        
        Args:
            exchange: Optional exchange filter
            
        Returns:
            List[SymbolInfo]: Active mappings
            
        Raises:
            DatabaseError: If query fails
        """
        try:
            query = {"is_active": True}
            if exchange:
                query["exchange"] = exchange
                
            mappings = await cls.find(query).to_list()
            
            logger.info(
                "Retrieved active symbol mappings",
                extra={
                    "exchange": exchange.value if exchange else "all",
                    "count": len(mappings)
                }
            )
            
            return mappings
            
        except Exception as e:
            raise DatabaseError(
                "Failed to get active mappings",
                context={
                    "exchange": exchange.value if exchange else "all",
                    "error": str(e)
                }
            )

    def needs_verification(self) -> bool:
        """Check if mapping needs re-verification."""
        return datetime.utcnow() >= self.next_verification

    async def mark_verification(
        self,
        success: bool,
        error: Optional[str] = None
    ) -> None:
        """
        Update verification status.
        
        Args:
            success: Whether verification succeeded
            error: Optional error message
            
        Raises:
            DatabaseError: If updates fail
        """
        try:
            self.last_verified = datetime.utcnow()
            self.next_verification = datetime.utcnow() + timedelta(days=1)
            
            if success:
                self.verification_failures = 0
                self.last_error = None
                
                logger.info(
                    "Symbol mapping verified successfully",
                    extra={
                        "symbol": self.original,
                        "exchange": self.exchange,
                        "next_verification": self.next_verification.isoformat()
                    }
                )
            else:
                self.verification_failures += 1
                self.last_error = error
                
                if self.verification_failures >= 3:
                    self.is_active = False
                    logger.warning(
                        "Symbol mapping deactivated",
                        extra={
                            "symbol": self.original,
                            "exchange": self.exchange,
                            "failures": self.verification_failures,
                            "error": error
                        }
                    )
                else:
                    logger.warning(
                        "Symbol mapping verification failed",
                        extra={
                            "symbol": self.original,
                            "exchange": self.exchange,
                            "failures": self.verification_failures,
                            "error": error
                        }
                    )
            
            await self.save()
            
        except Exception as e:
            raise DatabaseError(
                "Failed to update verification status",
                context={
                    "symbol": self.original,
                    "exchange": self.exchange,
                    "success": success,
                    "error": str(e)
                }
            )

    @classmethod
    async def get_or_create(
        cls,
        original: str,
        exchange: ExchangeType
    ) -> "SymbolInfo":
        """
        Get existing mapping or create new one.
        
        Args:
            original: Generic symbol name
            exchange: Exchange type
            
        Returns:
            SymbolInfo: Retrieved or created mapping
            
        Raises:
            ValidationError: If parameters invalid
            DatabaseError: If operation fails
        """
        try:
            # Validate inputs
            if not original.strip():
                raise ValidationError(
                    "Symbol string cannot be empty",
                    context={"original": original}
                )

            # Get normalized symbol from validator service
            validation_result = await symbol_validator.validate_symbol(
                symbol=original,
                exchange_type=exchange
            )
                
            # Try to find existing
            symbol_info = await cls.find_one({
                "original": original.upper(),
                "exchange": exchange
            })
            
            if symbol_info:
                return symbol_info

            # Create new
            try:
                symbol_info = cls(
                    original=original.upper(),
                    normalized_symbol=validation_result["normalized"],
                    exchange=exchange
                )
                await symbol_info.insert()
                
                logger.info(
                    "Created new symbol mapping",
                    extra={
                        "original": original,
                        "normalized": validation_result["normalized"],
                        "exchange": exchange
                    }
                )
                
                return symbol_info
                
            except Exception as e:
                raise DatabaseError(
                    "Failed to create symbol mapping",
                    context={
                        "original": original,
                        "exchange": exchange,
                        "error": str(e)
                    }
                )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get/create symbol mapping",
                context={
                    "original": original,
                    "exchange": exchange,
                    "error": str(e)
                }
            )

    @classmethod
    async def verify_all(
        cls,
        exchange: Optional[ExchangeType] = None,
        force: bool = False
    ) -> None:
        """
        Verify all symbol mappings using symbol validator service.
        
        Args:
            exchange: Optional exchange filter
            force: Force verification regardless of schedule
            
        Raises:
            ExchangeError: If verification fails
        """
        try:
            # Build query
            query = {"is_active": True}
            if exchange:
                query["exchange"] = exchange

            # Get mappings to verify
            mappings = await cls.find(query).to_list()
            if not mappings:
                return

            # Verify each mapping
            for mapping in mappings:
                if not force and not mapping.needs_verification():
                    continue

                try:
                    # Use symbol validator service
                    validation_result = await symbol_validator.validate_symbol(
                        symbol=mapping.original,
                        exchange_type=mapping.exchange,
                        force_validation=True
                    )
                    
                    is_valid = validation_result["normalized"] == mapping.normalized_symbol
                    
                    await mapping.mark_verification(
                        success=is_valid,
                        error=None if is_valid else "Symbol mapping mismatch with exchange"
                    )
                    
                except Exception as e:
                    await mapping.mark_verification(
                        success=False,
                        error=str(e)
                    )

            logger.info(
                "Completed symbol mapping verification",
                extra={
                    "exchange": exchange.value if exchange else "all",
                    "verified_count": len(mappings)
                }
            )

        except Exception as e:
            raise ExchangeError(
                "Failed to verify symbol mappings",
                context={
                    "exchange": exchange.value if exchange else "all",
                    "error": str(e)
                }
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "symbol_info": {
                "original": self.original,
                "normalized": self.normalized_symbol,
                "exchange": self.exchange.value
            },
            "status": {
                "is_active": self.is_active,
                "last_verified": self.last_verified.isoformat(),
                "next_verification": self.next_verification.isoformat()
            },
            "error_info": {
                "verification_failures": self.verification_failures,
                "last_error": self.last_error
            }
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"SymbolInfo({self.original} -> {self.normalized_symbol} "
            f"[{self.exchange}], active={self.is_active})"
        )

    @classmethod
    async def get_normalized_symbol(
        cls,
        original: str,
        exchange: ExchangeType
    ) -> Optional[str]:
        """
        Get normalized symbol for original symbol.
        
        Args:
            original: Original symbol to normalize
            exchange: Target exchange
            
        Returns:
            Optional[str]: Normalized symbol if found
            
        Raises:
            DatabaseError: If query fails
        """
        try:
            # Try to get from existing mapping first
            mapping = await cls.find_one({
                "original": original.upper(),
                "exchange": exchange,
                "is_active": True
            })
            
            if mapping:
                return mapping.normalized_symbol

            # If no existing mapping, try to validate and create new mapping
            try:
                validation_result = await symbol_validator.validate_symbol(
                    symbol=original,
                    exchange_type=exchange
                )
                
                # Create and store new mapping
                mapping = await cls.get_or_create(
                    original=original,
                    exchange=exchange
                )
                
                return mapping.normalized_symbol

            except ValidationError:
                logger.debug(
                    "Symbol validation failed",
                    extra={
                        "original": original,
                        "exchange": exchange
                    }
                )
                return None

        except Exception as e:
            raise DatabaseError(
                "Failed to get normalized symbol",
                context={
                    "original": original,
                    "exchange": exchange,
                    "error": str(e)
                }
            )