# app/services/tax.py
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from decimal import Decimal
from dataclasses import dataclass
import pandas as pd
from motor.motor_asyncio import AsyncIOMotorClient

class PerpetualTaxCalculator:
    def __init__(self, db: AsyncIOMotorClient):
        self.db = db

    async def calculate_tax_report(
        self,
        user_id: str,
        tax_year: int,
        include_unrealized: bool = False
    ) -> Dict:
        """Calculate tax implications for perpetual futures trading"""
        start_date = datetime(tax_year, 1, 1)
        end_date = datetime(tax_year + 1, 1, 1)
        
        # Get all closed trades for the tax year
        closed_trades = await self.db["trades"].find({
            "user_id": user_id,
            "closed_at": {"$gte": start_date, "$lt": end_date},
            "status": "closed"
        }).sort("closed_at", 1).to_list(None)

        # Process realized PnL
        realized_summary = {
            "total_realized_pnl": Decimal("0"),
            "total_funding_fees": Decimal("0"),
            "total_trading_fees": Decimal("0"),
            "trades_count": 0,
            "profitable_trades": 0,
            "losing_trades": 0,
            "by_symbol": {}
        }

        tax_events = []
        
        for trade in closed_trades:
            symbol = trade["symbol"]
            realized_pnl = Decimal(str(trade.get("pnl", 0)))
            trading_fees = Decimal(str(trade.get("fee", 0)))
            funding_fees = Decimal(str(trade.get("funding_fee", 0)))
            
            # Update symbol-specific metrics
            if symbol not in realized_summary["by_symbol"]:
                realized_summary["by_symbol"][symbol] = {
                    "realized_pnl": Decimal("0"),
                    "trading_fees": Decimal("0"),
                    "funding_fees": Decimal("0"),
                    "trades_count": 0
                }
            
            symbol_summary = realized_summary["by_symbol"][symbol]
            symbol_summary["realized_pnl"] += realized_pnl
            symbol_summary["trading_fees"] += trading_fees
            symbol_summary["funding_fees"] += funding_fees
            symbol_summary["trades_count"] += 1

            # Update overall metrics
            realized_summary["total_realized_pnl"] += realized_pnl
            realized_summary["total_trading_fees"] += trading_fees
            realized_summary["total_funding_fees"] += funding_fees
            realized_summary["trades_count"] += 1
            
            if realized_pnl > 0:
                realized_summary["profitable_trades"] += 1
            else:
                realized_summary["losing_trades"] += 1

            # Create tax event
            tax_events.append({
                "date": trade["closed_at"],
                "symbol": symbol,
                "side": trade["side"],
                "size": float(trade["size"]),
                "leverage": trade.get("leverage", 1),
                "entry_price": float(trade["entry_price"]),
                "close_price": float(trade["close_price"]),
                "realized_pnl": float(realized_pnl),
                "trading_fees": float(trading_fees),
                "funding_fees": float(funding_fees),
                "net_pnl": float(realized_pnl - trading_fees - funding_fees),
                "trade_duration": (trade["closed_at"] - trade["created_at"]).total_seconds() / 3600  # hours
            })

        # Get unrealized PnL if requested
        unrealized_summary = None
        if include_unrealized:
            unrealized_summary = await self._calculate_unrealized_pnl(
                user_id, end_date
            )

        # Calculate profit factor and other metrics
        profitable_sum = sum(t["realized_pnl"] for t in tax_events if t["realized_pnl"] > 0)
        losing_sum = abs(sum(t["realized_pnl"] for t in tax_events if t["realized_pnl"] < 0))
        
        metrics = {
            "profit_factor": float(profitable_sum / losing_sum) if losing_sum > 0 else None,
            "win_rate": (realized_summary["profitable_trades"] / realized_summary["trades_count"] * 100) 
                       if realized_summary["trades_count"] > 0 else 0,
            "average_trade_duration": sum(t["trade_duration"] for t in tax_events) / len(tax_events) 
                                    if tax_events else 0
        }

        return {
            "tax_year": tax_year,
            "realized_summary": {
                "total_realized_pnl": float(realized_summary["total_realized_pnl"]),
                "total_trading_fees": float(realized_summary["total_trading_fees"]),
                "total_funding_fees": float(realized_summary["total_funding_fees"]),
                "net_profit": float(realized_summary["total_realized_pnl"] - 
                                  realized_summary["total_trading_fees"] - 
                                  realized_summary["total_funding_fees"]),
                "trades_count": realized_summary["trades_count"],
                "profitable_trades": realized_summary["profitable_trades"],
                "losing_trades": realized_summary["losing_trades"],
                "by_symbol": {
                    symbol: {k: float(v) if isinstance(v, Decimal) else v 
                            for k, v in stats.items()}
                    for symbol, stats in realized_summary["by_symbol"].items()
                }
            },
            "unrealized_summary": unrealized_summary,
            "metrics": metrics,
            "tax_events": tax_events
        }

    async def _calculate_unrealized_pnl(
        self,
        user_id: str,
        date: datetime
    ) -> Dict:
        """Calculate unrealized PnL for open positions"""
        open_positions = await self.db["trades"].find({
            "user_id": user_id,
            "created_at": {"$lt": date},
            "status": "open"
        }).to_list(None)

        unrealized_pnl = Decimal("0")
        position_details = []

        for position in open_positions:
            # You would typically get the mark price from your exchange service here
            # For demonstration, we'll use the last known price
            mark_price = position.get("last_price", position["entry_price"])
            
            size = Decimal(str(position["size"]))
            entry_price = Decimal(str(position["entry_price"]))
            
            if position["side"] == "long":
                unrealized_position_pnl = (mark_price - entry_price) * size
            else:
                unrealized_position_pnl = (entry_price - mark_price) * size

            position_details.append({
                "symbol": position["symbol"],
                "side": position["side"],
                "size": float(size),
                "entry_price": float(entry_price),
                "mark_price": float(mark_price),
                "unrealized_pnl": float(unrealized_position_pnl)
            })

            unrealized_pnl += unrealized_position_pnl

        return {
            "total_unrealized_pnl": float(unrealized_pnl),
            "positions": position_details
        }

    async def get_monthly_summary(
        self,
        user_id: str,
        tax_year: int
    ) -> List[Dict]:
        """Get monthly breakdown of trading activity"""
        pipeline = [
            {
                "$match": {
                    "user_id": user_id,
                    "closed_at": {
                        "$gte": datetime(tax_year, 1, 1),
                        "$lt": datetime(tax_year + 1, 1, 1)
                    },
                    "status": "closed"
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": "$closed_at"},
                        "month": {"$month": "$closed_at"}
                    },
                    "realized_pnl": {"$sum": "$pnl"},
                    "trading_fees": {"$sum": "$fee"},
                    "funding_fees": {"$sum": "$funding_fee"},
                    "trades_count": {"$sum": 1},
                    "profitable_trades": {
                        "$sum": {"$cond": [{"$gt": ["$pnl", 0]}, 1, 0]}
                    }
                }
            },
            {"$sort": {"_id.year": 1, "_id.month": 1}}
        ]

        monthly_results = await self.db["trades"].aggregate(pipeline).to_list(None)
        
        return [{
            "year": r["_id"]["year"],
            "month": r["_id"]["month"],
            "realized_pnl": r["realized_pnl"],
            "trading_fees": r["trading_fees"],
            "funding_fees": r["funding_fees"],
            "net_pnl": r["realized_pnl"] - r["trading_fees"] - r["funding_fees"],
            "trades_count": r["trades_count"],
            "win_rate": (r["profitable_trades"] / r["trades_count"] * 100) 
                       if r["trades_count"] > 0 else 0
        } for r in monthly_results]
