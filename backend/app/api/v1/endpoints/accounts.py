# /api/v1/endpoints/accounts.py

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import io
import pandas as pd

# FastAPI imports for request validation
from fastapi import HTTPException, status

# Core imports
from app.core.errors import (
    ValidationError,
    AuthorizationError,
    NotFoundError,
    ExchangeError
)
from app.core.errors.handlers import handle_api_error
from app.core.logging.logger import get_logger
from app.core.references import ExchangeType, TimeRange, ModelState

# Model imports
from app.models.account import Account

# Dependencies 
from app.api.v1.deps import (
    get_admin_user,
    get_current_user,
    get_accessible_accounts,
    validate_date_range,
    validate_pagination
)

router = APIRouter()
logger = get_logger(__name__)

@router.post("/create")
async def create_account(
    request: Request,
    exchange: ExchangeType,
    api_key: str, 
    api_secret: str,
    initial_balance: float,
    passphrase: Optional[str] = None,
    is_testnet: bool = False,
    current_user = Depends(get_admin_user)
) -> Dict[str, Any]:
    """Create new exchange account with service integration."""
    try:
        # Create account
        account = Account(
            user_id=str(current_user["id"]),
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            initial_balance=initial_balance,
            current_balance=initial_balance,
            current_equity=initial_balance,
            is_testnet=is_testnet
        )

        # Validate API credentials using exchange service
        operations = await exchange_operations.create(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=is_testnet
        )

        # Test connection and get initial balance
        balance = await operations.get_balance()
        account.current_balance = balance["balance"]
        account.current_equity = balance["equity"]
        account.last_sync = datetime.utcnow()

        # Save account
        await account.save()

        logger.info(
            "Account created successfully",
            extra={
                "account_id": str(account.id),
                "exchange": exchange,
                "user_id": str(current_user["id"])
            }
        )

        return {
            "success": True,
            "account_id": str(account.id),
            "message": "Account created successfully"
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "exchange": exchange,
                "user_id": str(current_user["id"]),
                "is_testnet": is_testnet
            },
            log_message="Failed to create account"
        )

@router.get("/list")
async def list_accounts(
    request: Request,
    pagination: Dict = Depends(validate_pagination),
    current_user = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> Dict[str, Any]:
    """List accessible accounts with performance data."""
    try:
        # Get accounts with pagination
        accounts = await Account.find(
            {"_id": {"$in": allowed_accounts}}
        ).skip(pagination["skip"]).limit(pagination["limit"]).to_list()

        # Get account details using services
        enriched_accounts = []
        for account in accounts:
            # Get performance metrics and references
            metrics = await performance_service.get_account_metrics(
                account_id=str(account.id),
                time_range=TimeRange(
                    start_date=(datetime.utcnow() - timedelta(days=1)),
                    end_date=datetime.utcnow()
                )
            )
            refs = await reference_manager.get_references(
                source_type="Account",
                reference_id=str(account.id)
            )
            enriched_accounts.append({
                **account.to_dict(),
                "performance": metrics,
                "references": refs
            })

        total = await Account.find({"_id": {"$in": allowed_accounts}}).count()

        return {
            "accounts": enriched_accounts,
            "pagination": {
                "total": total,
                "skip": pagination["skip"],
                "limit": pagination["limit"]
            }
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "user_id": str(current_user["id"]),
                "pagination": pagination
            },
            log_message="Failed to list accounts"
        )

@router.get("/{account_id}")
async def get_account(
    account_id: str,
    current_user = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> Dict[str, Any]:
    """Get detailed account information."""
    try:
        if account_id not in allowed_accounts:
            raise AuthorizationError(
                "Not authorized to view this account",
                context={"account_id": account_id}
            )

        # Get account and enriched data
        account = await Account.get(account_id)
        if not account:
            raise NotFoundError("Account not found", context={"account_id": account_id})

        metrics = await performance_service.get_account_metrics(
            account_id=account_id,
            time_range=TimeRange(
                start_date=(datetime.utcnow() - timedelta(days=1)),
                end_date=datetime.utcnow()
            )
        )
        refs = await reference_manager.get_references(
            source_type="Account",
            reference_id=account_id
        )
        ws_status = await ws_manager.get_connection_status(account_id)

        return {
            "account": account.to_dict(),
            "performance": metrics,
            "references": refs,
            "ws_status": ws_status
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"account_id": account_id},
            log_message="Failed to get account details"
        )

@router.post("/{account_id}/update-credentials")
async def update_credentials(
    account_id: str,
    api_key: str,
    api_secret: str,
    passphrase: Optional[str] = None,
    current_user = Depends(get_admin_user)
) -> Dict[str, Any]:
    """Update account API credentials."""
    try:
        account = await Account.get(account_id)
        if not account:
            raise NotFoundError("Account not found", context={"account_id": account_id})

        # Validate new credentials
        operations = await exchange_operations.create(
            exchange=account.exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            is_testnet=account.is_testnet
        )
        await operations.get_balance()  # Test connection

        # Update account
        account.api_key = api_key
        account.api_secret = api_secret
        account.passphrase = passphrase
        account.modified_at = datetime.utcnow()
        await account.save()

        # Update WebSocket connection
        await ws_manager.reconnect(account_id)

        logger.info(
            "Updated account credentials",
            extra={"account_id": account_id}
        )

        return {"success": True}

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"account_id": account_id},
            log_message="Failed to update credentials"
        )

@router.post("/{account_id}/sync-balance")
async def sync_balance(
    account_id: str,
    current_user = Depends(get_admin_user)
) -> Dict[str, Any]:
    """Sync account balance with exchange."""
    try:
        operations = await exchange_operations.get_operations(account_id)
        balance = await operations.get_balance()

        # Update account and performance metrics
        await performance_service.update_daily_performance(
            account_id=account_id,
            date=datetime.utcnow(),
            metrics={
                "balance": balance["balance"],
                "equity": balance["equity"]
            }
        )

        return {
            "success": True,
            "balance": balance["balance"],
            "equity": balance["equity"]
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"account_id": account_id},
            log_message="Failed to sync balance"
        )

@router.get("/{account_id}/performance")
async def get_performance(
    account_id: str,
    time_range: TimeRange = Depends(validate_date_range),
    current_user = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> Dict[str, Any]:
    """Get account performance metrics."""
    try:
        if account_id not in allowed_accounts:
            raise AuthorizationError(
                "Not authorized to view this account",
                context={"account_id": account_id}
            )

        metrics = await performance_service.get_account_metrics(
            account_id=account_id,
            time_range=time_range
        )

        return {
            "account_id": account_id,
            "time_range": time_range.dict(),
            "metrics": metrics
        }

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "account_id": account_id,
                "time_range": time_range.dict()
            },
            log_message="Failed to get performance metrics"
        )

@router.delete("/{account_id}")
async def delete_account(
    account_id: str,
    current_user = Depends(get_admin_user)
) -> Dict[str, Any]:
    """Delete account with cleanup."""
    try:
        # Check positions
        operations = await exchange_operations.get_operations(account_id)
        positions = await operations.get_all_positions()
        if positions:
            raise ValidationError(
                "Cannot delete account with open positions",
                context={"position_count": len(positions)}
            )

        # Cleanup resources
        await ws_manager.close_connection(account_id)
        await reference_manager.remove_all_references(
            source_type="Account",
            source_id=account_id
        )
        await performance_service.archive_account_data(account_id)

        # Delete account
        await Account.find_one({"_id": account_id}).delete()

        logger.info(
            "Deleted account",
            extra={"account_id": account_id}
        )

        return {"success": True}

    except Exception as e:
        await handle_api_error(
            error=e,
            context={"account_id": account_id},
            log_message="Failed to delete account"
        )

@router.post("/{account_id}/assign-group")
async def assign_group(
    account_id: str,
    group_id: str,
    current_user = Depends(get_admin_user)
) -> Dict[str, Any]:
    """Assign account to group."""
    try:
        await reference_manager.create_reference(
            source_type="Account",
            target_type="Group",
            source_id=account_id,
            target_id=group_id
        )

        logger.info(
            "Assigned account to group",
            extra={
                "account_id": account_id,
                "group_id": group_id
            }
        )

        return {"success": True}

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "account_id": account_id,
                "group_id": group_id
            },
            log_message="Failed to assign group"
        )

@router.get("/{account_id}/export")
async def export_trades(
    account_id: str,
    time_range: TimeRange = Depends(validate_date_range),
    format: str = Query("csv", regex="^(csv|xlsx)$"),
    current_user = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> StreamingResponse:
    """Export account trade history."""
    try:
        if account_id not in allowed_accounts:
            raise AuthorizationError(
                "Not authorized to export this account data",
                context={"account_id": account_id}
            )

        # Get trade history
        trades = await performance_service.get_trade_history(
            account_id=account_id,
            time_range=time_range
        )

        # Create export buffer
        buffer = io.BytesIO()
        df = pd.DataFrame([trade.to_dict() for trade in trades])

        if format == "csv":
            df.to_csv(buffer, index=False)
            media_type = "text/csv"
            filename = f"trades_{account_id}_{time_range.start_date.date()}_{time_range.end_date.date()}.csv"
        else:
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Trades', index=False)
                metrics = await performance_service.get_account_metrics(
                    account_id=account_id,
                    time_range=time_range
                )
                pd.DataFrame([metrics]).to_excel(writer, sheet_name='Summary', index=False)

            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"trades_{account_id}_{time_range.start_date.date()}_{time_range.end_date.date()}.xlsx"

        buffer.seek(0)
        
        logger.info(
            "Exported trade history",
            extra={
                "account_id": account_id,
                "format": format,
                "trade_count": len(trades)
            }
        )

        return StreamingResponse(
            buffer,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        await handle_api_error(
            error=e,
            context={
                "account_id": account_id,
                "time_range": time_range.dict(),
                "format": format
            },
            log_message="Failed to export trades"
        )

# Service imports at bottom to avoid circular dependencies
from app.services.exchange.operations import exchange_operations
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager