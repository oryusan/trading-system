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
    account_id: Indexed(str) = Field(..., description="Account that executed this position")
    symbol: Indexed(str) = Field(..., description="Trading symbol")
    side: str = Field(..., description="Position side (long/short)")
    size: Decimal = Field(..., description="Position size")

    # Price information
    entry_price: Decimal = Field(..., description="Average entry price")
    exit_price: Decimal = Field(..., description="Average exit price")

    # Performance metrics
    raw_pnl: Decimal = Field(..., description="Raw profit/loss before fees")
    trading_fee: Decimal = Field(..., description="Trading fees (negative=paid)")
    funding_fee: Decimal = Field(..., description="Funding fees (negative=paid)")
    net_pnl: Decimal = Field(..., description="Net profit/loss after fees")
    pnl_ratio: Decimal = Field(..., description="ROI percentage")

    # Timestamps
    opened_at: Indexed(datetime) = Field(..., description="Position open timestamp")
    closed_at: Indexed(datetime) = Field(..., description="Position close timestamp")
    synced_at: datetime = Field(default_factory=datetime.utcnow, description="Last sync timestamp")

    class Settings:
        """Collection settings and indexes."""
        name = "position_history"
        indexes = [
            [("account_id", 1), ("closed_at", 1)],  # For account performance
            [("account_id", 1), ("symbol", 1)],       # For symbol lookups
            "closed_at",                             # For date range queries
            "synced_at"                              # For sync management
        ]

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        """Ensure position side is either 'long' or 'short'."""
        side_lower = v.lower()
        if side_lower not in {"long", "short"}:
            raise ValidationError(
                "Invalid position side",
                context={"side": v, "valid_values": ["long", "short"]}
            )
        return side_lower

    @field_validator("size", "entry_price", "exit_price")
    @classmethod
    def validate_positive_decimal(cls, v: Decimal) -> Decimal:
        """Ensure that the decimal value is positive."""
        if v <= 0:
            raise ValidationError("Value must be positive", context={"value": str(v)})
        return v

    @before_event([Replace, Insert])
    async def validate_references(self):
        """
        Validate the account reference and adjust decimal precisions before saving.

        Raises:
            ValidationError: If the account reference is invalid.
            DatabaseError: If reference checks fail.
        """
        try:
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
            precisions = await reference_manager.get_decimal_precisions()
            self.entry_price = round(self.entry_price, precisions["price"])
            self.exit_price = round(self.exit_price, precisions["price"])
            self.trading_fee = round(self.trading_fee, precisions["fee"])
            self.funding_fee = round(self.funding_fee, precisions["fee"])

            logger.debug(
                "Validated position references",
                extra={"position_id": str(self.id), "account_id": self.account_id}
            )
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Reference validation failed",
                context={"position_id": str(self.id), "account_id": self.account_id, "error": str(e)}
            )

    @classmethod
    def _parse_date(cls, date_str: str, field_name: str) -> datetime:
        """Helper to parse a date string in YYYY-MM-DD format."""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError as e:
            raise ValidationError(
                f"Invalid {field_name} format",
                context={field_name: date_str, "expected_format": "YYYY-MM-DD", "error": str(e)}
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
        Retrieve filtered position history for an account.

        Args:
            account_id: Account to retrieve positions for.
            start_date: Optional start date filter (YYYY-MM-DD).
            end_date: Optional end date filter (YYYY-MM-DD).
            symbol: Optional trading symbol filter.

        Returns:
            List of positions in dictionary format.
        """
        try:
            query: Dict[str, Any] = {"account_id": account_id}
            if start_date or end_date:
                date_filter: Dict[str, Any] = {}
                if start_date:
                    start = cls._parse_date(start_date, "start_date")
                    date_filter["$gte"] = start
                if end_date:
                    end = cls._parse_date(end_date, "end_date")
                    date_filter["$lte"] = end
                if start_date and end_date and start > end:
                    raise ValidationError(
                        "Start date must be before end date",
                        context={"start_date": start_date, "end_date": end_date}
                    )
                query["closed_at"] = date_filter

            if symbol:
                symbol = symbol.strip()
                if not symbol:
                    raise ValidationError("Symbol cannot be empty", context={"symbol": symbol})
                query["symbol"] = symbol.upper()

            positions = await cls.find(query).sort("-closed_at").to_list()

            logger.info("Retrieved account positions", extra={
                "account_id": account_id,
                "date_range": f"{start_date} to {end_date}",
                "symbol": symbol,
                "count": len(positions)
            })
            return [pos.dict() for pos in positions]
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to get account positions",
                context={"account_id": account_id, "date_range": f"{start_date} to {end_date}", "symbol": symbol, "error": str(e)}
            )

    @classmethod
    async def calculate_position_metrics(cls, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate aggregate metrics for a list of positions.

        Args:
            positions: List of position dictionaries.

        Returns:
            Aggregated metrics including win rate, total volume, and PnL stats.
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
                "total_volume": Decimal("0"),
                "total_pnl": Decimal("0"),
                "total_fees": Decimal("0"),
                "net_pnl": Decimal("0"),
                "by_symbol": {}
            }
            winning_pnl = []
            losing_pnl = []

            for pos in positions:
                required_fields = ["size", "exit_price", "net_pnl", "trading_fee", "funding_fee", "raw_pnl", "symbol"]
                missing = [field for field in required_fields if field not in pos]
                if missing:
                    raise ValidationError(
                        "Missing required position fields",
                        context={"missing_fields": missing, "position": pos}
                    )

                net_pnl = Decimal(str(pos["net_pnl"]))
                if net_pnl > 0:
                    metrics["winning_positions"] += 1
                    winning_pnl.append(float(net_pnl))
                else:
                    losing_pnl.append(float(net_pnl))

                volume = Decimal(str(pos["size"])) * Decimal(str(pos["exit_price"]))
                metrics["total_volume"] += volume
                metrics["total_pnl"] += Decimal(str(pos["raw_pnl"]))
                fees = Decimal(str(pos["trading_fee"])) + Decimal(str(pos["funding_fee"]))
                metrics["total_fees"] += fees
                metrics["net_pnl"] += net_pnl

                symbol = pos["symbol"]
                if symbol not in metrics["by_symbol"]:
                    metrics["by_symbol"][symbol] = {"positions": 0, "volume": Decimal("0"), "pnl": Decimal("0")}
                metrics["by_symbol"][symbol]["positions"] += 1
                metrics["by_symbol"][symbol]["volume"] += volume
                metrics["by_symbol"][symbol]["pnl"] += net_pnl

            total_positions = metrics["total_positions"]
            win_rate = round((metrics["winning_positions"] / total_positions * 100) if total_positions > 0 else 0, 2)
            avg_win = sum(winning_pnl) / len(winning_pnl) if winning_pnl else 0
            avg_loss = sum(losing_pnl) / len(losing_pnl) if losing_pnl else 0
            profit_factor = abs(sum(winning_pnl) / sum(losing_pnl)) if losing_pnl and sum(losing_pnl) != 0 else 0

            response = {
                "total_positions": total_positions,
                "winning_positions": metrics["winning_positions"],
                "total_volume": float(metrics["total_volume"]),
                "total_pnl": float(metrics["total_pnl"]),
                "total_fees": float(metrics["total_fees"]),
                "net_pnl": float(metrics["net_pnl"]),
                "win_rate": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
                "by_symbol": {
                    sym: {
                        "positions": data["positions"],
                        "volume": float(data["volume"]),
                        "pnl": float(data["pnl"])
                    }
                    for sym, data in metrics["by_symbol"].items()
                }
            }

            logger.info("Calculated position metrics", extra={
                "total_positions": total_positions,
                "win_rate": win_rate,
                "net_pnl": float(metrics["net_pnl"])
            })
            return response

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Position metrics calculation failed",
                context={"position_count": len(positions), "error": str(e)}
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the position to a dictionary format."""
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
        """Calculate the duration for which the position was held."""
        if not self.opened_at or not self.closed_at:
            raise ValidationError(
                "Missing timestamp data",
                context={"opened_at": self.opened_at, "closed_at": self.closed_at}
            )
        holding_time = self.closed_at - self.opened_at
        if holding_time.total_seconds() < 0:
            raise ValidationError(
                "Invalid holding time: negative duration",
                context={"opened_at": self.opened_at, "closed_at": self.closed_at, "duration": holding_time}
            )
        return holding_time

    async def update_from_trade(self, trade: Dict[str, Any]) -> None:
        """
        Update position details based on trade execution data.

        Args:
            trade: Dictionary containing trade details:
                   - price: Execution price
                   - size: Trade size
                   - fees: Associated fees
                   - timestamp: Unix timestamp of execution
                   - fee_type (optional): "funding" for funding fees
        """
        try:
            required_fields = ["price", "size", "fees", "timestamp"]
            missing = [field for field in required_fields if field not in trade]
            if missing:
                raise ValidationError(
                    "Missing required trade fields",
                    context={"missing_fields": missing, "trade_data": trade}
                )

            price = Decimal(str(trade["price"]))
            trade_size = Decimal(str(trade["size"]))
            fees = Decimal(str(trade["fees"]))
            trade_timestamp = datetime.fromtimestamp(trade["timestamp"])

            if not self.entry_price:
                self.entry_price = price
                self.opened_at = trade_timestamp
            else:
                self.exit_price = price
                self.closed_at = trade_timestamp

            if self.size < trade_size:
                self.size = trade_size

            if trade.get("fee_type") == "funding":
                self.funding_fee += fees
            else:
                self.trading_fee += fees

            self.synced_at = datetime.utcnow()
            await self.save()

            logger.info("Updated position from trade", extra={
                "position_id": str(self.id),
                "price": str(price),
                "size": str(trade_size),
                "fees": str(fees)
            })
        except ValidationError:
            raise
        except Exception as e:
            raise DatabaseError(
                "Failed to update from trade",
                context={"position_id": str(self.id), "trade": trade, "error": str(e)}
            )

    async def validate_position_data(self) -> Dict[str, Any]:
        """
        Validate the completeness and correctness of the position data.

        Checks include:
          - Presence of required fields
          - Valid timestamps
          - Accurate profit/loss calculations

        Returns:
            A dictionary with:
              - valid: bool indicating if the data is valid
              - errors: list of error messages (if any)
        """
        errors = []

        if not all([self.entry_price, self.exit_price, self.size]):
            errors.append("Missing required price or size data")

        if not self.opened_at or not self.closed_at:
            errors.append("Missing timestamp data")
        elif self.closed_at < self.opened_at:
            errors.append("Invalid timing: close before open")

        try:
            price_diff = self.exit_price - self.entry_price
            multiplier = Decimal("1") if self.side == "long" else Decimal("-1")
            expected_pnl = price_diff * self.size * multiplier
            if abs(self.raw_pnl - expected_pnl) > Decimal("0.0001"):
                errors.append("P/L calculation mismatch")
            total_fees = self.trading_fee + self.funding_fee
            if abs(self.net_pnl - (self.raw_pnl + total_fees)) > Decimal("0.0001"):
                errors.append("Net P/L calculation mismatch")
        except DecimalException as e:
            errors.append(f"Calculation validation failed: {str(e)}")

        return {"valid": not errors, "errors": errors}

    def get_position_summary(self) -> Dict[str, Any]:
        """
        Get a summarized view of the position data.

        Returns:
            Dictionary summarizing key details such as position, timing, prices, performance, and fees.
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
        """String representation of the PositionHistory instance."""
        return f"PositionHistory({self.symbol} {self.side}, size={self.size}, pnl={self.net_pnl})"


# Move imports to end to avoid circular dependencies
from app.core.errors.base import ValidationError, DatabaseError, NotFoundError
from app.core.logging.logger import get_logger
from app.services.reference.manager import reference_manager
from app.services.performance.service import performance_service

logger = get_logger(__name__)
