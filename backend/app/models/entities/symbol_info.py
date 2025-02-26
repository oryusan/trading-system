# ==== Begin File: backend/app/models/entities/symbol_info.py ====
"""
Symbol information model with exchange mapping and validation.

Features:
- Exchange-specific symbol mapping
- Scheduled validation using symbol_validator service
- Error handling with rich context
- Service integration with proper decoupling
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import asyncio

from beanie import Document, Indexed
from pydantic import Field, field_validator

from app.core.errors.base import (
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
            v: Next verification time.
            info: Context with current data.

        Returns:
            datetime: Validated time.

        Raises:
            ValidationError: If schedule is invalid.
        """
        last_verified = info.data.get("last_verified")
        if last_verified and v < last_verified:
            raise ValidationError(
                "Next verification must be after last verification",
                context={
                    "last_verified": last_verified.isoformat(),
                    "next_verification": v.isoformat()
                }
            )
        return v

    @classmethod
    async def get_active_mappings(
        cls, exchange: Optional[ExchangeType] = None
    ) -> List["SymbolInfo"]:
        """
        Retrieve all active symbol mappings, optionally filtered by exchange.

        Args:
            exchange: Optional exchange filter.

        Returns:
            List[SymbolInfo]: Active mappings.

        Raises:
            DatabaseError: If the query fails.
        """
        try:
            query: Dict[str, Any] = {"is_active": True}
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
        """Determine if the mapping requires re-verification."""
        return datetime.utcnow() >= self.next_verification

    async def mark_verification(self, success: bool, error: Optional[str] = None) -> None:
        """
        Update the verification status of the symbol mapping.

        Args:
            success: Outcome of the verification.
            error: Optional error message.

        Raises:
            DatabaseError: If the update fails.
        """
        try:
            now = datetime.utcnow()
            self.last_verified = now
            self.next_verification = now + timedelta(days=1)

            if success:
                self.verification_failures = 0
                self.last_error = None
                logger.info(
                    "Symbol mapping verified successfully",
                    extra={
                        "symbol": self.original,
                        "exchange": self.exchange.value
                        if hasattr(self.exchange, "value")
                        else self.exchange,
                        "next_verification": self.next_verification.isoformat(),
                    },
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
                            "exchange": self.exchange.value
                            if hasattr(self.exchange, "value")
                            else self.exchange,
                            "failures": self.verification_failures,
                            "error": error,
                        },
                    )
                else:
                    logger.warning(
                        "Symbol mapping verification failed",
                        extra={
                            "symbol": self.original,
                            "exchange": self.exchange.value
                            if hasattr(self.exchange, "value")
                            else self.exchange,
                            "failures": self.verification_failures,
                            "error": error,
                        },
                    )
            await self.save()
        except Exception as e:
            raise DatabaseError(
                "Failed to update verification status",
                context={
                    "symbol": self.original,
                    "exchange": self.exchange.value
                    if hasattr(self.exchange, "value")
                    else self.exchange,
                    "success": success,
                    "error": str(e),
                },
            )

    @classmethod
    async def get_or_create(cls, original: str, exchange: ExchangeType) -> "SymbolInfo":
        """
        Retrieve an existing symbol mapping or create a new one.

        Args:
            original: The generic symbol name.
            exchange: The target exchange type.

        Returns:
            SymbolInfo: The retrieved or newly created symbol mapping.

        Raises:
            ValidationError: If the provided symbol is invalid.
            DatabaseError: If the operation fails.
        """
        if not original.strip():
            raise ValidationError("Symbol string cannot be empty", context={"original": original})

        try:
            existing = await cls.find_one({"original": original.upper(), "exchange": exchange})
            if existing:
                return existing

            # Validate and obtain the normalized symbol
            validation_result = await symbol_validator.validate_symbol(
                symbol=original, exchange_type=exchange
            )

            symbol_info = cls(
                original=original.upper(),
                normalized_symbol=validation_result["normalized"],
                exchange=exchange,
            )
            await symbol_info.insert()

            logger.info(
                "Created new symbol mapping",
                extra={
                    "original": original,
                    "normalized": validation_result["normalized"],
                    "exchange": exchange.value if hasattr(exchange, "value") else exchange,
                },
            )
            return symbol_info
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get or create symbol mapping",
                context={
                    "original": original,
                    "exchange": exchange.value if hasattr(exchange, "value") else exchange,
                    "error": str(e),
                },
            )

    @classmethod
    async def verify_all(
        cls, exchange: Optional[ExchangeType] = None, force: bool = False
    ) -> None:
        """
        Verify all active symbol mappings using the symbol validator service.

        Args:
            exchange: Optional exchange filter.
            force: Force verification regardless of schedule.

        Raises:
            ExchangeError: If the verification process fails.
        """
        try:
            query: Dict[str, Any] = {"is_active": True}
            if exchange:
                query["exchange"] = exchange

            mappings = await cls.find(query).to_list()
            if not mappings:
                return

            async def verify_mapping(mapping: SymbolInfo) -> None:
                if not force and not mapping.needs_verification():
                    return
                try:
                    validation_result = await symbol_validator.validate_symbol(
                        symbol=mapping.original,
                        exchange_type=mapping.exchange,
                        force_validation=True,
                    )
                    is_valid = validation_result["normalized"] == mapping.normalized_symbol
                    await mapping.mark_verification(
                        success=is_valid,
                        error=None if is_valid else "Symbol mapping mismatch with exchange",
                    )
                except Exception as e:
                    await mapping.mark_verification(success=False, error=str(e))

            await asyncio.gather(*(verify_mapping(m) for m in mappings))

            logger.info(
                "Completed symbol mapping verification",
                extra={
                    "exchange": exchange.value if exchange else "all",
                    "verified_count": len(mappings),
                },
            )
        except Exception as e:
            raise ExchangeError(
                "Failed to verify symbol mappings",
                context={
                    "exchange": exchange.value if exchange else "all",
                    "error": str(e),
                },
            )

    @classmethod
    async def get_normalized_symbol(
        cls, original: str, exchange: ExchangeType
    ) -> Optional[str]:
        """
        Retrieve the normalized symbol for a given original symbol and exchange.

        Args:
            original: The original symbol.
            exchange: The target exchange.

        Returns:
            Optional[str]: The normalized symbol if available.

        Raises:
            DatabaseError: If the query fails.
        """
        try:
            mapping = await cls.find_one(
                {"original": original.upper(), "exchange": exchange, "is_active": True}
            )
            if mapping:
                return mapping.normalized_symbol

            mapping = await cls.get_or_create(original=original, exchange=exchange)
            return mapping.normalized_symbol
        except ValidationError:
            logger.debug(
                "Symbol validation failed",
                extra={
                    "original": original,
                    "exchange": exchange.value if hasattr(exchange, "value") else exchange,
                },
            )
            return None
        except Exception as e:
            raise DatabaseError(
                "Failed to get normalized symbol",
                context={
                    "original": original,
                    "exchange": exchange.value if hasattr(exchange, "value") else exchange,
                    "error": str(e),
                },
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the symbol mapping to a dictionary."""
        return {
            "symbol_info": {
                "original": self.original,
                "normalized": self.normalized_symbol,
                "exchange": self.exchange.value if hasattr(self.exchange, "value") else self.exchange,
            },
            "status": {
                "is_active": self.is_active,
                "last_verified": self.last_verified.isoformat(),
                "next_verification": self.next_verification.isoformat(),
            },
            "error_info": {
                "verification_failures": self.verification_failures,
                "last_error": self.last_error,
            },
        }

    def __repr__(self) -> str:
        """Return a string representation of the symbol mapping."""
        exchange_val = self.exchange.value if hasattr(self.exchange, "value") else self.exchange
        return f"SymbolInfo({self.original} -> {self.normalized_symbol} [{exchange_val}], active={self.is_active})"
