"""
Symbol specifications model with validation and error handling.

Core model for storing and validating exchange symbol specifications.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

from beanie import Document, Indexed
from pydantic import Field, field_validator, ConfigDict

from app.core.errors.base import ValidationError, DatabaseError
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType, ModelState

logger = get_logger(__name__)

# Define the verification interval as a constant (in hours)
VERIFICATION_INTERVAL_HOURS = 24


def get_next_verification_time() -> datetime:
    """Return the next verification time based on the current UTC time."""
    return datetime.utcnow() + timedelta(hours=VERIFICATION_INTERVAL_HOURS)


class SymbolSpecs(Document):
    """
    Exchange-specific symbol specifications model.

    Stores and validates critical trading parameters:
    - Tick size (minimum price increment)
    - Lot size (minimum order size increment)
    - Contract size (contract multiplier)
    """

    # Enable assignment validation for updates
    model_config = ConfigDict(validate_assignment=True)

    # Core specifications
    exchange: ExchangeType = Field(..., description="Exchange these specs apply to")
    symbol: Indexed(str) = Field(..., description="Exchange-normalized symbol identifier")

    # Trading specifications
    tick_size: Decimal = Field(..., description="Minimum price increment", gt=0)
    lot_size: Decimal = Field(..., description="Minimum order size increment", gt=0)
    contract_size: Decimal = Field(..., description="Contract size for derivatives", gt=0)

    # Verification tracking
    last_verified: datetime = Field(
        default_factory=datetime.utcnow, description="Last successful verification"
    )
    next_verification: datetime = Field(
        default_factory=get_next_verification_time, description="Next verification due"
    )
    is_active: bool = Field(True, description="Whether specs are valid")

    # Error tracking
    verification_failures: int = Field(
        0, description="Consecutive verification failures", ge=0
    )
    last_error: Optional[str] = Field(None, description="Last error message")

    class Settings:
        """Collection settings and indexes."""
        name = "symbol_specs"
        indexes = [
            [("exchange", 1), ("symbol", 1)],  # Compound unique index
            "last_verified",
            "next_verification",
            "is_active",
        ]

    @field_validator("tick_size", "lot_size", "contract_size")
    @classmethod
    def validate_positive_decimals(cls, v: Decimal) -> Decimal:
        """Ensure that specification values are positive."""
        if v <= 0:
            raise ValidationError(
                "Specification value must be positive", context={"value": str(v)}
            )
        return v

    @field_validator("next_verification")
    @classmethod
    def validate_next_verification(cls, v: datetime, info) -> datetime:
        """Ensure that next_verification is after last_verified."""
        last_verified = info.data.get("last_verified")
        if last_verified and v < last_verified:
            raise ValidationError(
                "Next verification must be after last verification",
                context={
                    "last_verified": last_verified.isoformat(),
                    "next_verification": v.isoformat(),
                },
            )
        return v

    def needs_verification(self) -> bool:
        """Check if specifications need re-verification."""
        return datetime.utcnow() >= self.next_verification

    async def mark_verification(self, success: bool, error: Optional[str] = None) -> None:
        """Update verification status based on the result of a verification attempt."""
        now = datetime.utcnow()
        self.last_verified = now
        self.next_verification = now + timedelta(hours=VERIFICATION_INTERVAL_HOURS)

        if success:
            self.verification_failures = 0
            self.last_error = None
            logger.info(
                "Symbol specs verified successfully",
                extra={
                    "symbol": self.symbol,
                    "exchange": self.exchange,
                    "next_verification": self.next_verification.isoformat(),
                },
            )
        else:
            self.verification_failures += 1
            self.last_error = error
            if self.verification_failures >= 3:
                self.is_active = False
                logger.warning(
                    "Symbol specs deactivated after consecutive verification failures",
                    extra={
                        "symbol": self.symbol,
                        "exchange": self.exchange,
                        "failures": self.verification_failures,
                        "error": error,
                    },
                )
            else:
                logger.warning(
                    "Symbol specs verification failed",
                    extra={
                        "symbol": self.symbol,
                        "exchange": self.exchange,
                        "failures": self.verification_failures,
                        "error": error,
                    },
                )

        try:
            await self.save()
        except Exception as e:
            raise DatabaseError(
                "Failed to update verification status",
                context={
                    "symbol": self.symbol,
                    "exchange": self.exchange,
                    "success": success,
                    "error": str(e),
                },
            )

    @classmethod
    async def get_or_create(
        cls,
        symbol: str,
        exchange: ExchangeType,
        tick_size: Decimal,
        lot_size: Decimal,
        contract_size: Decimal,
    ) -> "SymbolSpecs":
        """
        Retrieve existing specifications or create new ones for a given symbol and exchange.
        """
        try:
            now = datetime.utcnow()
            specs = await cls.find_one({"symbol": symbol.upper(), "exchange": exchange})

            if specs:
                # Update existing specifications
                specs.tick_size = tick_size
                specs.lot_size = lot_size
                specs.contract_size = contract_size
                specs.last_verified = now
                specs.next_verification = now + timedelta(hours=VERIFICATION_INTERVAL_HOURS)
                await specs.save()
                logger.info(
                    "Updated symbol specifications",
                    extra={
                        "symbol": symbol,
                        "exchange": exchange,
                        "tick_size": str(tick_size),
                    },
                )
            else:
                # Create new specifications
                specs = cls(
                    symbol=symbol.upper(),
                    exchange=exchange,
                    tick_size=tick_size,
                    lot_size=lot_size,
                    contract_size=contract_size,
                )
                await specs.insert()
                logger.info(
                    "Created new symbol specifications",
                    extra={
                        "symbol": symbol,
                        "exchange": exchange,
                        "tick_size": str(tick_size),
                    },
                )

            return specs

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get/create symbol specifications",
                context={"symbol": symbol, "exchange": exchange, "error": str(e)},
            )

    @classmethod
    async def get_active_specs(
        cls, exchange: Optional[ExchangeType] = None
    ) -> List["SymbolSpecs"]:
        """
        Retrieve all active symbol specifications, optionally filtered by exchange.
        """
        try:
            query: Dict[str, Any] = {"is_active": True}
            if exchange:
                query["exchange"] = exchange

            specs = await cls.find(query).to_list()
            logger.info(
                "Retrieved active symbol specifications",
                extra={
                    "exchange": exchange.value if exchange else "all",
                    "count": len(specs),
                },
            )
            return specs

        except Exception as e:
            raise DatabaseError(
                "Failed to get active specifications",
                context={
                    "exchange": exchange.value if exchange else "all",
                    "error": str(e),
                },
            )

    @classmethod
    async def get_symbol_specs(
        cls, symbol: str, exchange: ExchangeType
    ) -> Optional["SymbolSpecs"]:
        """
        Retrieve active specifications for a specific symbol and exchange.

        Args:
            symbol: Trading symbol.
            exchange: Exchange type.

        Returns:
            An instance of SymbolSpecs if found, otherwise None.
        """
        try:
            specs = await cls.find_one(
                {"symbol": symbol.upper(), "exchange": exchange, "is_active": True}
            )
            if specs:
                logger.debug(
                    "Retrieved symbol specifications",
                    extra={"symbol": symbol, "exchange": exchange},
                )
            return specs

        except Exception as e:
            raise DatabaseError(
                "Failed to get symbol specifications",
                context={"symbol": symbol, "exchange": exchange, "error": str(e)},
            )

    def to_dict(self) -> ModelState:
        """Convert symbol specifications to a dictionary representation."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange.value,
            "specifications": {
                "tick_size": str(self.tick_size),
                "lot_size": str(self.lot_size),
                "contract_size": str(self.contract_size),
            },
            "status": {
                "is_active": self.is_active,
                "last_verified": self.last_verified.isoformat(),
                "next_verification": self.next_verification.isoformat(),
                "verification_failures": self.verification_failures,
                "last_error": self.last_error,
            },
        }

    def __repr__(self) -> str:
        """Return a string representation of the symbol specifications."""
        return (
            f"SymbolSpecs({self.symbol} [{self.exchange}], tick={self.tick_size}, "
            f"lot={self.lot_size}, contract={self.contract_size}, active={self.is_active})"
        )
