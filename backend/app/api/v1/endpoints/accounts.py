"""
Account management endpoints.

Handles:
- Account creation
- Listing accounts with enrichment (performance and reference data)
- Retrieving individual account details
- Updating account credentials
- Synchronizing account balance
- Retrieving performance metrics
- Deleting an account
- Assigning an account to a group
"""

from datetime import datetime, timedelta
import io
import asyncio
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel

from app.core.errors.base import AuthorizationError, NotFoundError
from app.core.logging.logger import get_logger
from app.models.entities.account import Account
from app.api.v1.deps import get_admin_user, get_current_user, get_accessible_accounts
from app.services.exchange.operations import ExchangeOperations
from app.services.performance.service import performance_service
from app.services.reference.manager import reference_manager
from app.services.websocket.manager import ws_manager

router = APIRouter()
logger = get_logger(__name__)


# --- Pydantic models for request bodies ---
class AccountCreateRequest(BaseModel):
    exchange: str
    api_key: str
    api_secret: str
    initial_balance: float
    passphrase: Optional[str] = None
    is_testnet: bool = False

class AccountUpdateCredentialsRequest(BaseModel):
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None

class AccountAssignGroupRequest(BaseModel):
    group_id: str


# --- Helper function ---
def check_account_access(account_id: str, allowed_accounts: List[str]) -> None:
    if account_id not in allowed_accounts:
        raise AuthorizationError("Not authorized to access this account", context={"account_id": account_id})


# --- Endpoints ---

@router.post("/create")
async def create_account(
    request: Request,
    account_data: AccountCreateRequest,
    current_user: dict = Depends(get_admin_user)
) -> Dict[str, Any]:
    now = datetime.utcnow()
    account = Account(
        user_id=str(current_user["id"]),
        exchange=account_data.exchange,
        api_key=account_data.api_key,
        api_secret=account_data.api_secret,
        passphrase=account_data.passphrase,
        initial_balance=account_data.initial_balance,
        current_balance=account_data.initial_balance,
        current_equity=account_data.initial_balance,
        is_testnet=account_data.is_testnet
    )
    # Create exchange operations instance and fetch balance.
    operations = await ExchangeOperations.create(
        exchange=account_data.exchange,
        api_key=account_data.api_key,
        api_secret=account_data.api_secret,
        passphrase=account_data.passphrase,
        is_testnet=account_data.is_testnet
    )
    balance = await operations.get_balance()
    account.current_balance = balance["balance"]
    account.current_equity = balance["equity"]
    account.last_sync = now
    await account.save()
    logger.info("Account created successfully", extra={
        "account_id": str(account.id),
        "exchange": account_data.exchange,
        "user_id": str(current_user["id"])
    })
    return {
        "success": True,
        "account_id": str(account.id),
        "message": "Account created successfully"
    }

@router.get("/list")
async def list_accounts(
    request: Request,
    pagination: Dict = Depends(lambda: {"skip": 0, "limit": 50}),  # Replace with a proper pagination dependency if needed.
    current_user: dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> Dict[str, Any]:
    now = datetime.utcnow()
    query = {"_id": {"$in": allowed_accounts}}
    accounts = await Account.find(query).skip(pagination["skip"]).limit(pagination["limit"]).to_list()

    async def enrich_account(account):
        metrics = await performance_service.get_account_metrics(
            account_id=str(account.id),
            time_range={"start_date": now - timedelta(days=1), "end_date": now}
        )
        refs = await reference_manager.get_references(
            source_type="Account",
            reference_id=str(account.id)
        )
        return {**account.to_dict(), "performance": metrics, "references": refs}

    enriched_accounts = await asyncio.gather(*(enrich_account(acc) for acc in accounts))
    total = await Account.find(query).count()

    return {
        "accounts": enriched_accounts,
        "pagination": {
            "total": total,
            "skip": pagination["skip"],
            "limit": pagination["limit"]
        }
    }

@router.get("/{account_id}")
async def get_account(
    account_id: str,
    current_user: dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> Dict[str, Any]:
    check_account_access(account_id, allowed_accounts)
    account = await Account.get(account_id)
    if not account:
        raise NotFoundError("Account not found", context={"account_id": account_id})
    now = datetime.utcnow()
    metrics = await performance_service.get_account_metrics(
        account_id=account_id,
        time_range={"start_date": now - timedelta(days=1), "end_date": now}
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

@router.post("/{account_id}/update-credentials")
async def update_credentials(
    account_id: str,
    credentials: AccountUpdateCredentialsRequest,
    current_user: dict = Depends(get_admin_user)
) -> Dict[str, Any]:
    account = await Account.get(account_id)
    if not account:
        raise NotFoundError("Account not found", context={"account_id": account_id})
    operations = await ExchangeOperations.create(
        exchange=account.exchange,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        passphrase=credentials.passphrase,
        is_testnet=account.is_testnet
    )
    # Test new credentials by retrieving balance.
    await operations.get_balance()
    account.api_key = credentials.api_key
    account.api_secret = credentials.api_secret
    account.passphrase = credentials.passphrase
    account.modified_at = datetime.utcnow()
    await account.save()
    await ws_manager.reconnect(account_id)
    logger.info("Updated account credentials", extra={"account_id": account_id})
    return {"success": True}

@router.post("/{account_id}/sync-balance")
async def sync_balance(
    account_id: str,
    current_user: dict = Depends(get_admin_user)
) -> Dict[str, Any]:
    operations = await ExchangeOperations.get_operations(account_id)
    balance = await operations.get_balance()
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

@router.get("/{account_id}/performance")
async def get_performance(
    account_id: str,
    time_range: Dict = Depends(lambda: {"start_date": datetime.utcnow() - timedelta(days=1), "end_date": datetime.utcnow()}),
    current_user: dict = Depends(get_current_user),
    allowed_accounts: List[str] = Depends(get_accessible_accounts)
) -> Dict[str, Any]:
    check_account_access(account_id, allowed_accounts)
    metrics = await performance_service.get_account_metrics(
        account_id=account_id,
        time_range=time_range
    )
    return {
        "account_id": account_id,
        "time_range": time_range,
        "metrics": metrics
    }

@router.delete("/{account_id}")
async def delete_account(
    account_id: str,
    current_user: dict = Depends(get_admin_user)
) -> Dict[str, Any]:
    operations = await ExchangeOperations.get_operations(account_id)
    positions = await operations.get_all_positions()
    if positions:
        raise Exception("Cannot delete account with open positions")
    await ws_manager.close_connection(account_id)
    await reference_manager.remove_all_references(
        source_type="Account",
        source_id=account_id
    )
    await performance_service.archive_account_data(account_id)
    await Account.find_one({"_id": account_id}).delete()
    logger.info("Deleted account", extra={"account_id": account_id})
    return {"success": True}

@router.post("/{account_id}/assign-group")
async def assign_group(
    account_id: str,
    assignment: AccountAssignGroupRequest,
    current_user: dict = Depends(get_admin_user)
) -> Dict[str, Any]:
    await reference_manager.create_reference(
        source_type="Account",
        target_type="Group",
        source_id=account_id,
        target_id=assignment.group_id
    )
    logger.info("Assigned account to group", extra={"account_id": account_id, "group_id": assignment.group_id})
    return {"success": True}
