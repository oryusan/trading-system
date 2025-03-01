"""
Exporter Service

This module provides functionality to export closed position data in CSV or Excel format.
It retrieves closed positions from the PositionHistory collection and writes them to a file
with the following fields:
  1. Type           - Hardcoded as "Perpetual" (for example)
  2. Symbol         - Trading symbol
  3. Opening Price  - Entry price of the trade
  4. Closing Price  - Exit price of the trade
  5. PnL            - Net profit/loss with currency (e.g., "5.12 USDT")
  6. Exchange       - Exchange where the trade was executed (if available)
  7. Closing Date   - The closing timestamp of the position

The module uses:
  - AsyncIOMotorClient to query MongoDB.
  - PositionHistory for fetching closed trades.
  - A centralized logger from get_logger.
  - The error_handler decorator for uniform error handling.
"""

from motor.motor_asyncio import AsyncIOMotorClient
from app.models.entities.position_history import PositionHistory
from app.core.logging.logger import get_logger
from app.core.errors.decorators import error_handler

import csv
from datetime import datetime
from typing import Dict, List, Any

try:
    import pandas as pd
except ImportError:
    pd = None

logger = get_logger(__name__)


class Exporter:
    """
    Exporter Service

    This class provides methods to export closed position data for a given account
    within a specified date range. It supports both CSV and Excel formats.
    """

    def __init__(self, db: AsyncIOMotorClient) -> None:
        """
        Initialize the Exporter with a MongoDB client.

        Args:
            db: An instance of AsyncIOMotorClient.
        """
        self.db = db
        self.logger = logger

    @error_handler(
        context_extractor=lambda account_id, start_date, end_date, output_file, export_format="csv": {
            "account_id": account_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "output_file": output_file,
            "export_format": export_format
        },
        log_message="Failed to export closed positions"
    )
    async def export_closed_positions(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
        output_file: str,
        export_format: str = "csv"
    ) -> None:
        """
        Export closed positions for a given account and date range.

        Args:
            account_id: The account identifier.
            start_date: Start date for exporting data.
            end_date: End date for exporting data.
            output_file: The file path to save the export.
            export_format: Format to export data ("csv" or "excel").

        Raises:
            ValueError: If an unsupported export format is specified.
        """
        # Convert dates to strings for querying
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        
        # Retrieve closed positions from the PositionHistory collection
        positions = await PositionHistory.find({
            "account_id": account_id,
            "date": {"$gte": start_str, "$lte": end_str}
        }).to_list()
        
        if not positions:
            self.logger.info("No closed positions found for export", extra={"account_id": account_id})
            return
        
        # Build export data (a list of dictionaries)
        export_data: List[Dict[str, Any]] = []
        for pos in positions:
            trade_type = "Perpetual"  # Hardcoded for this example
            symbol = pos.symbol
            opening_price = pos.entry_price
            closing_price = pos.exit_price
            pnl_value = pos.net_pnl
            currency = "USDT"  # Default currency; adjust if needed
            pnl_str = f"{pnl_value} {currency}"
            # Optionally, include exchange info if available (or leave empty)
            exchange = pos.exchange if hasattr(pos, "exchange") else ""
            closing_date = pos.closed_at.isoformat() if pos.closed_at else ""
            record = {
                "Type": trade_type,
                "Symbol": symbol,
                "Opening Price": opening_price,
                "Closing Price": closing_price,
                "PnL": pnl_str,
                "Exchange": exchange,
                "Closing Date": closing_date
            }
            export_data.append(record)
        
        # Export to the desired format
        if export_format.lower() == "csv":
            self._export_csv(export_data, output_file)
        elif export_format.lower() == "excel":
            self._export_excel(export_data, output_file)
        else:
            raise ValueError(f"Unsupported export format: {export_format}")
        
        self.logger.info(
            "Export completed",
            extra={"account_id": account_id, "output_file": output_file, "format": export_format}
        )

    @error_handler(
        context_extractor=lambda data, output_file: {"output_file": output_file, "record_count": len(data)},
        log_message="CSV export failed"
    )
    def _export_csv(self, data: List[Dict[str, Any]], output_file: str) -> None:
        """
        Export data to a CSV file.

        Args:
            data: List of records to export.
            output_file: The file path for the CSV file.
        """
        fieldnames = ["Type", "Symbol", "Opening Price", "Closing Price", "PnL", "Exchange", "Closing Date"]
        with open(output_file, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow(row)

    @error_handler(
        context_extractor=lambda data, output_file: {"output_file": output_file, "record_count": len(data)},
        log_message="Excel export failed"
    )
    def _export_excel(self, data: List[Dict[str, Any]], output_file: str) -> None:
        """
        Export data to an Excel file.

        Args:
            data: List of records to export.
            output_file: The file path for the Excel file.
        
        Raises:
            ImportError: If Pandas is not installed.
        """
        if pd is None:
            raise ImportError("Pandas is required for Excel export but is not installed.")
        df = pd.DataFrame(data)
        df.to_excel(output_file, index=False)


# Example call (in an async context):
# await export_performance_report(sample_data, "performance_report.csv", export_format="csv")
# await export_performance_report(sample_data, "performance_report.xlsx", export_format="excel")
# await export_performance_report(sample_data, "performance_report.json", export_format="json")
