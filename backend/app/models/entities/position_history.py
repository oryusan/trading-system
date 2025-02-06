"""
Position history model with enhanced service integration and error handling.

Features:
- Reference manager integration
- Enhanced error handling 
- Proper logging
- Performance tracking
- Service integration 
"""

from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from typing import Optional, Dict, List, Any
from beanie import Document, before_event, Replace, Insert, Indexed
from pydantic import Field, field_validator

class PositionHistory(Document):
    """
    Historical record of closed trading positions.
    
    Features:
    - Full lifecycle data
    - Performance metrics
    - Fee accounting  
    - Reference validation
    """
    
    # Core fields 
    account_id: Indexed(str) = Field(
        ...,
        description="Account that executed this position"
    )
    symbol: Indexed(str) = Field(
        ...,
        description="Trading symbol"  
    )
    side: str = Field(
        ...,
        description="Position side (long/short)"
    )
    size: Decimal = Field(
        ...,
        description="Position size"
    )
    
    # Price information
    entry_price: Decimal = Field(
        ...,
        description="Average entry price"
    )
    exit_price: Decimal = Field(
        ...,
        description="Average exit price" 
    )

    # Performance metrics
    raw_pnl: Decimal = Field(
        ..., 
        description="Raw profit/loss before fees"
    )
    trading_fee: Decimal = Field(
        ...,
        description="Trading fees (negative=paid)"
    )
    funding_fee: Decimal = Field(
        ...,
        description="Funding fees (negative=paid)"
    )
    net_pnl: Decimal = Field(
        ...,
        description="Net profit/loss after fees"
    )
    pnl_ratio: Decimal = Field(
        ...,
        description="ROI percentage"
    )

    # Timestamps
    opened_at: Indexed(datetime) = Field(
        ...,
        description="Position open timestamp"
    )
    closed_at: Indexed(datetime) = Field(
        ...,
        description="Position close timestamp"
    )
    synced_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last sync timestamp"
    )

    class Settings:
        """Collection settings and indexes."""
        name = "position_history"
        indexes = [
            [("account_id", 1), ("closed_at", 1)],  # For account performance
            [("account_id", 1), ("symbol", 1)],     # For symbol lookups
            "closed_at",                            # For date range queries
            "synced_at"                             # For sync management
        ]

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        """Validate position side is either 'long' or 'short'."""
        if v.lower() not in ["long", "short"]:
            raise ValidationError(
                "Invalid position side",
                context={
                    "side": v,
                    "valid_values": ["long", "short"]
                }
            )
        return v.lower()

    @field_validator("size", "entry_price", "exit_price")
    @classmethod
    def validate_positive_decimal(cls, v: Decimal) -> Decimal:
        """Ensure decimal values are positive."""
        try:
            if v <= 0:
                raise ValidationError(
                    "Value must be positive",
                    context={"value": str(v)}
                )
            return v
        except DecimalException as e:
            raise ValidationError(
                "Invalid decimal value",
                context={
                    "value": str(v),
                    "error": str(e)
                }
            )

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate account reference before saving.
        
        Uses reference manager to verify:
        - Account exists and is active
        - Price and quantity precision valid
        
        Raises:
            ValidationError: If validation fails
            DatabaseError: If reference checks fail
        """
        try:
            # Validate account exists and is active
            valid = await reference_manager.validate_reference(
                source_type="PositionHistory",
                target_type="Account",
                reference_id=self.account_id
            )
            
            if not valid:
                raise ValidationError(
                    "Referenced account not found or inactive",
                    context={"account_id": self.account_id}
                )
                
            # Get decimal precisions from reference manager
            precisions = await reference_manager.get_decimal_precisions()
            
            # Validate and round decimal values
            self.entry_price = round(self.entry_price, precisions["price"])
            self.exit_price = round(self.exit_price, precisions["price"])
            self.trading_fee = round(self.trading_fee, precisions["fee"])
            self.funding_fee = round(self.funding_fee, precisions["fee"])

            logger.debug(
                "Validated position references",
                extra={
                    "position_id": str(self.id),
                    "account_id": self.account_id
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={
                    "position_id": str(self.id),
                    "account_id": self.account_id,
                    "error": str(e)
                }
            )

    async def update_daily_performance(self) -> None:
        """
        Update daily performance metrics.
        
        Uses performance service to:
        - Update daily metrics
        - Track PnL and fees
        - Handle statistics
        
        Raises:
            DatabaseError: If update fails
        """
        try:
            metrics = {
                "pnl": float(self.net_pnl),
                "trading_fees": float(self.trading_fee),
                "funding_fees": float(self.funding_fee),
                "raw_pnl": float(self.raw_pnl),
                "size": float(self.size),
                "is_successful": self.net_pnl > 0,
                "holding_time": self.closed_at - self.opened_at
            }

            await performance_service.update_daily_performance(
                account_id=self.account_id,
                date=self.closed_at.date(),
                metrics=metrics
            )

            logger.info(
                "Updated daily performance",
                extra={
                    "position_id": str(self.id),
                    "account_id": self.account_id,
                    "closed_at": self.closed_at.isoformat(),
                    "net_pnl": str(self.net_pnl)
                }
            )

        except Exception as e:
            raise DatabaseError(
                "Failed to update daily performance",
                context={
                    "position_id": str(self.id),
                    "account_id": self.account_id,
                    "closed_at": self.closed_at.isoformat(),
                    "error": str(e)
                }
            )

    @classmethod
    async def get_account_positions(
        cls,
        account_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get position history for an account with filtering.
        
        Args:
            account_id: Account to get positions for
            start_date: Optional start date filter (YYYY-MM-DD)
            end_date: Optional end date filter (YYYY-MM-DD)
            symbol: Optional symbol filter
            
        Returns:
            List[Dict]: Filtered position history
            
        Raises:
            ValidationError: If filters invalid
            DatabaseError: If query fails
        """
        try:
            query = {"account_id": account_id}
            
            # Build date filter
            if start_date or end_date:
                date_filter = {}
                if start_date:
                    try:
                        start = datetime.strptime(start_date, "%Y-%m-%d")
                        date_filter["$gte"] = start
                    except ValueError as e:
                        raise ValidationError(
                            "Invalid start date format",
                            context={
                                "start_date": start_date,
                                "expected_format": "YYYY-MM-DD",
                                "error": str(e)
                            }
                        )
                        
                if end_date:
                    try:
                        end = datetime.strptime(end_date, "%Y-%m-%d")
                        date_filter["$lte"] = end
                    except ValueError as e:
                        raise ValidationError(
                            "Invalid end date format",
                            context={
                                "end_date": end_date,
                                "expected_format": "YYYY-MM-DD",
                                "error": str(e)
                            }
                        )
                
                if start_date and end_date and start > end:
                    raise ValidationError(
                        "Start date must be before end date",
                        context={
                            "start_date": start_date,
                            "end_date": end_date
                        }
                    )
                        
                query["closed_at"] = date_filter
                
            if symbol:
                # Validate symbol format
                if not symbol.strip():
                    raise ValidationError(
                        "Symbol cannot be empty",
                        context={"symbol": symbol}
                    )
                query["symbol"] = symbol.upper()

            # Execute query with sorting
            positions = await cls.find(query).sort("-closed_at").to_list()
            
            logger.info(
                "Retrieved account positions",
                extra={
                    "account_id": account_id,
                    "date_range": f"{start_date} to {end_date}",
                    "symbol": symbol,
                    "count": len(positions)
                }
            )

            return [pos.dict() for pos in positions]

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get account positions",
                context={
                    "account_id": account_id,
                    "date_range": f"{start_date} to {end_date}",
                    "symbol": symbol,
                    "error": str(e)
                }
            )

    @classmethod
    async def calculate_position_metrics(
        cls, 
        positions: List[Dict[str, Any]]  
    ) -> Dict[str, Any]:
        """
        Calculate aggregate metrics for positions.
        
        Args:
            positions: List of positions to analyze
            
        Returns:
            Dict with aggregated metrics:
            - Total positions
            - Win rate
            - Volume
            - PnL stats
            - Symbol breakdowns
            
        Raises:
            ValidationError: If calculations fail
        """
        try:
            if not positions:
                return {
                    "total_positions": 0,
                    "winning_positions": 0,
                    "total_volume": 0,
                    "total_pnl": 0,
                    "total_fees": 0,
                    "net_pnl": 0,
                    "win_rate": 0,
                    "avg_win": 0,
                    "avg_loss": 0,
                    "profit_factor": 0,
                    "by_symbol": {}
                }

            metrics = {
                "total_positions": len(positions),
                "winning_positions": 0,
                "total_volume": Decimal('0'),
                "total_pnl": Decimal('0'),
                "total_fees": Decimal('0'),
                "net_pnl": Decimal('0'),
                "by_symbol": {}
            }
            
            winning_pnl = []
            losing_pnl = []
            
            for pos in positions:
                # Validate required fields
                required = ["size", "exit_price", "net_pnl", "trading_fee", "funding_fee"]
                missing = [f for f in required if f not in pos]
                if missing:
                    raise ValidationError(
                        "Missing required position fields",
                        context={
                            "missing_fields": missing,
                            "position": pos
                        }
                    )

                # Track wins/losses
                net_pnl = Decimal(str(pos["net_pnl"]))
                if net_pnl > 0:
                    metrics["winning_positions"] += 1
                    winning_pnl.append(float(net_pnl))
                else:
                    losing_pnl.append(float(net_pnl))

                # Accumulate totals
                metrics["total_volume"] += Decimal(str(pos["size"])) * Decimal(str(pos["exit_price"]))
                metrics["total_pnl"] += Decimal(str(pos["raw_pnl"]))
                metrics["total_fees"] += (
                    Decimal(str(pos["trading_fee"])) + 
                    Decimal(str(pos["funding_fee"]))
                )
                metrics["net_pnl"] += net_pnl
                
                # Track by symbol
                symbol = pos["symbol"]
                if symbol not in metrics["by_symbol"]:
                    metrics["by_symbol"][symbol] = {
                        "positions": 0,
                        "volume": Decimal('0'),
                        "pnl": Decimal('0')
                    }
                    
                symbol_metrics = metrics["by_symbol"][symbol]
                symbol_metrics["positions"] += 1
                symbol_metrics["volume"] += Decimal(str(pos["size"])) * Decimal(str(pos["exit_price"]))
                symbol_metrics["pnl"] += net_pnl

            # Calculate ratios and convert for response
            response = {
                "total_positions": metrics["total_positions"],
                "winning_positions": metrics["winning_positions"],
                "total_volume": float(metrics["total_volume"]),
                "total_pnl": float(metrics["total_pnl"]),
                "total_fees": float(metrics["total_fees"]),
                "net_pnl": float(metrics["net_pnl"]),
                "win_rate": round(
                    (metrics["winning_positions"] / metrics["total_positions"] * 100)
                    if metrics["total_positions"] > 0 else 0,
                    2
                ),
                "avg_win": sum(winning_pnl) / len(winning_pnl) if winning_pnl else 0,
                "avg_loss": sum(losing_pnl) / len(losing_pnl) if losing_pnl else 0,
                "profit_factor": (
                    abs(sum(winning_pnl) / sum(losing_pnl))
                    if losing_pnl and sum(losing_pnl) != 0 else 0
                ),
                "by_symbol": {
                    symbol: {
                        "positions": data["positions"],
                        "volume": float(data["volume"]),
                        "pnl": float(data["pnl"])
                    }
                    for symbol, data in metrics["by_symbol"].items()
                }
            }

            logger.info(
                "Calculated position metrics",
                extra={
                    "total_positions": metrics["total_positions"],
                    "win_rate": response["win_rate"],
                    "net_pnl": float(metrics["net_pnl"])
                }
            )

            return response

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Position metrics calculation failed",
                context={
                    "position_count": len(positions),
                    "error": str(e)
                }
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary format."""
        return {
            "position": {
                "id": str(self.id),
                "account_id": self.account_id,
                "symbol": self.symbol,
                "side": self.side,
                "size": str(self.size),
                "entry_price": str(self.entry_price),
                "exit_price": str(self.exit_price)
            },
            "performance": {
                "raw_pnl": str(self.raw_pnl),
                "trading_fee": str(self.trading_fee),
                "funding_fee": str(self.funding_fee),
                "net_pnl": str(self.net_pnl),
                "pnl_ratio": str(self.pnl_ratio)
            },
            "timing": {
                "opened_at": self.opened_at.isoformat(),
                "closed_at": self.closed_at.isoformat(),
                "synced_at": self.synced_at.isoformat(),
                "holding_time": str(self.closed_at - self.opened_at)
            }
        }

    def calculate_holding_time(self) -> timedelta:
        """Calculate position holding duration."""
        if not self.opened_at or not self.closed_at:
            raise ValidationError(
                "Missing timestamp data",
                context={
                    "opened_at": self.opened_at,
                    "closed_at": self.closed_at
                }
            )
        
        holding_time = self.closed_at - self.opened_at
        if holding_time.total_seconds() < 0:
            raise ValidationError(
                "Invalid holding time: negative duration",
                context={
                    "opened_at": self.opened_at,
                    "closed_at": self.closed_at,
                    "duration": holding_time
                }
            )
            
        return holding_time

    async def update_from_trade(self, trade: Dict[str, Any]) -> None:
        """
        Update position from trade execution.
        
        Args:
            trade: Trade execution details
                - price: Execution price
                - size: Trade size  
                - fees: Trading fees
                - timestamp: Unix timestamp
                
        Raises:
            ValidationError: If trade data invalid
            DatabaseError: If update fails
        """
        try:
            # Validate trade data
            required = ["price", "size", "fees", "timestamp"]
            missing = [f for f in required if f not in trade]
            if missing:
                raise ValidationError(
                    "Missing required trade fields", 
                    context={
                        "missing_fields": missing,
                        "trade_data": trade
                    }
                )

            # Update based on trade direction
            if not self.entry_price:
                self.entry_price = Decimal(str(trade["price"]))
                self.opened_at = datetime.fromtimestamp(trade["timestamp"])
            else:
                self.exit_price = Decimal(str(trade["price"]))
                self.closed_at = datetime.fromtimestamp(trade["timestamp"])

            # Update size if partial fill
            filled_size = Decimal(str(trade["size"]))
            if self.size < filled_size:
                self.size = filled_size

            # Track fees
            fees = Decimal(str(trade["fees"]))
            if "fee_type" in trade and trade["fee_type"] == "funding":
                self.funding_fee += fees
            else:
                self.trading_fee += fees

            self.synced_at = datetime.utcnow()
            await self.save()

            logger.info(
                "Updated position from trade",
                extra={
                    "position_id": str(self.id),
                    "price": str(trade["price"]),
                    "size": str(trade["size"]),
                    "fees": str(fees)
                }
            )

        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update from trade",
                context={
                    "position_id": str(self.id),
                    "trade": trade,
                    "error": str(e)
                }
            )

    async def validate_position_data(self) -> ValidationResult:
        """
        Validate position data completeness and correctness.
        
        Checks:
        - Required fields present
        - Valid timestamps
        - P/L calculations accurate
        
        Returns:
            ValidationResult containing validation status and errors
        """
        errors = []
        
        # Check required fields
        if not all([self.entry_price, self.exit_price, self.size]):
            errors.append("Missing required price or size data")

        # Validate timing
        if not self.opened_at or not self.closed_at:
            errors.append("Missing timestamp data")
        elif self.closed_at < self.opened_at:
            errors.append("Invalid timing: close before open")

        # Validate calculations
        try:
            # Recalculate P/L
            price_diff = self.exit_price - self.entry_price
            multiplier = Decimal("1") if self.side == "long" else Decimal("-1")
            expected_pnl = price_diff * self.size * multiplier
            
            if abs(self.raw_pnl - expected_pnl) > Decimal("0.0001"):
                errors.append("P/L calculation mismatch")
                
            # Verify net P/L
            total_fees = self.trading_fee + self.funding_fee
            if abs(self.net_pnl - (self.raw_pnl + total_fees)) > Decimal("0.0001"):
                errors.append("Net P/L calculation mismatch")
            
        except DecimalException as e:
            errors.append(f"Calculation validation failed: {str(e)}")

        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    def get_position_summary(self) -> Dict[str, Any]:
        """
        Get summarized position information.
        
        Returns:
            Dict containing:
            - Position details (symbol, side, size)
            - Timing information
            - Price data
            - Performance metrics
            - Fee breakdown
        """
        return {
            "position": {
                "id": str(self.id),
                "symbol": self.symbol,
                "side": self.side,
                "size": str(self.size)
            },
            "timing": {
                "opened_at": self.opened_at.isoformat(),
                "closed_at": self.closed_at.isoformat(),
                "holding_time": str(self.closed_at - self.opened_at)
            },
            "prices": {
                "entry": str(self.entry_price),
                "exit": str(self.exit_price)  
            },
            "performance": {
                "raw_pnl": str(self.raw_pnl),
                "net_pnl": str(self.net_pnl),
                "pnl_ratio": str(self.pnl_ratio)
            },
            "fees": {
                "trading": str(self.trading_fee),
                "funding": str(self.funding_fee),
                "total": str(self.trading_fee + self.funding_fee)
            }
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"PositionHistory({self.symbol} {self.side}, "
            f"size={self.size}, pnl={self.net_pnl})"
        )

# Move imports to end to avoid circular dependencies
from app.core.errors import ValidationError, DatabaseError, NotFoundError
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service

logger = get_logger(__name__)