"""
Enhanced test configuration with fixtures for:
- Database connections
- Authentication
- User roles
- Mock exchanges
- Test data generation
"""

import pytest
import asyncio
from typing import Dict, List, Generator, AsyncGenerator
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from fastapi.testclient import TestClient
from jose import jwt

# Import application components
from app.core.config import Settings
from app.core.security import create_access_token
from app.models.user import User, UserRole
from app.models.bot import Bot
from app.models.account import Account
from app.models.group import AccountGroup
from app.models.trade import Trade
from app.models.daily_performance import DailyPerformance
from app.models.position_history import PositionHistory
from app.models.symbol_info import SymbolInfo
from app.models.symbol_specs import SymbolSpecs
from app.db.db import Database
from app.main import app  # Import your FastAPI app

# Test settings
TEST_MONGODB_URL = "mongodb://localhost:27017"
TEST_DB_NAME = "test_trading_db"

@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Provide test settings with overridden values."""
    return Settings(
        MONGODB_URL=TEST_MONGODB_URL,
        MONGODB_DB_NAME=TEST_DB_NAME,
        SECRET_KEY="test-secret-key",
        ACCESS_TOKEN_EXPIRE_MINUTES=15,
        TRADINGVIEW_WEBHOOK_SECRET="test-webhook-secret",
        TELEGRAM_BOT_TOKEN="test-bot-token",
        TELEGRAM_CHAT_ID="test-chat-id",
        DEBUG_MODE=True,
        LOG_LEVEL="DEBUG"
    )

@pytest.fixture(scope="session")
async def test_db() -> AsyncGenerator[AsyncIOMotorClient, None]:
    """
    Create a test database and handle cleanup.
    Uses a separate database for testing to avoid conflicts.
    """
    try:
        client = AsyncIOMotorClient(TEST_MONGODB_URL)
        db = client[TEST_DB_NAME]

        # Initialize Beanie with all models
        await init_beanie(
            database=db,
            document_models=[
                User,
                Bot,
                Account,
                AccountGroup,
                Trade,
                SymbolInfo,
                SymbolSpecs,
                DailyPerformance,
                PositionHistory
            ]
        )

        yield db

        # Cleanup
        await client.drop_database(TEST_DB_NAME)
        client.close()

    except Exception as e:
        pytest.fail(f"Failed to setup test database: {str(e)}")

@pytest.fixture(autouse=True)
async def clean_db(test_db) -> None:
    """Clean all collections before each test."""
    collections = await test_db.list_collection_names()
    for collection in collections:
        await test_db[collection].delete_many({})

@pytest.fixture
def test_client() -> Generator:
    """Create a TestClient instance for API testing."""
    with TestClient(app) as client:
        yield client

@pytest.fixture
async def admin_user(test_db) -> User:
    """Create and return an admin user."""
    user = User(
        username="admin_test",
        hashed_password="$2b$12$test_hash",
        role=UserRole.ADMIN,
        is_active=True
    )
    await user.save()
    return user

@pytest.fixture
async def exporter_user(test_db) -> User:
    """Create and return an exporter user."""
    user = User(
        username="exporter_test",
        hashed_password="$2b$12$test_hash",
        role=UserRole.EXPORTER,
        is_active=True
    )
    await user.save()
    return user

@pytest.fixture
async def viewer_user(test_db) -> User:
    """Create and return a viewer user."""
    user = User(
        username="viewer_test",
        hashed_password="$2b$12$test_hash",
        role=UserRole.VIEWER,
        is_active=True
    )
    await user.save()
    return user

@pytest.fixture
async def admin_token(admin_user, test_settings) -> str:
    """Generate a valid JWT token for admin user."""
    return await create_access_token(
        subject=admin_user.username,
        role=UserRole.ADMIN
    )

@pytest.fixture
async def test_bot(test_db, admin_user) -> Bot:
    """Create a test bot instance."""
    bot = Bot(
        name="TestBot-1m",
        base_name="TestBot",
        timeframe="1m",
        status="STOPPED"
    )
    await bot.save()
    return bot

@pytest.fixture
async def test_account(test_db, admin_user, test_bot) -> Account:
    """Create a test trading account."""
    account = Account(
        user_id=str(admin_user.id),
        exchange="bybit",
        api_key="test_key",
        api_secret="test_secret",
        initial_balance=10000.0,
        initial_equity=10000.0,
        current_balance=10000.0,
        current_equity=10000.0,
        bot_id=str(test_bot.id),
        is_testnet=True
    )
    await account.save()
    return account

@pytest.fixture
async def test_group(test_db, admin_user) -> AccountGroup:
    """Create a test account group."""
    group = AccountGroup(
        name="Test Group",
        description="Test group for testing",
        created_by=str(admin_user.id)
    )
    await group.save()
    return group

class MockExchangeClient:
    """Mock exchange client for testing exchange operations."""
    
    async def get_balance(self) -> Dict:
        return {
            "total_equity": 10000.0,
            "available_balance": 10000.0
        }
    
    async def place_order(self, **kwargs) -> Dict:
        return {
            "order_id": "test_order_123",
            "status": "filled",
            "price": 100.0,
            "size": 1.0
        }

@pytest.fixture
def mock_exchange() -> MockExchangeClient:
    """Provide a mock exchange client."""
    return MockExchangeClient()

class MockWebSocket:
    """Mock WebSocket client for testing real-time data."""
    
    async def connect(self) -> None:
        pass
    
    async def disconnect(self) -> None:
        pass
    
    async def subscribe(self, **kwargs) -> None:
        pass

@pytest.fixture
def mock_websocket() -> MockWebSocket:
    """Provide a mock WebSocket client."""
    return MockWebSocket()

@pytest.fixture
def mock_telegram_bot():
    """Mock Telegram bot for testing notifications."""
    class MockBot:
        async def send_message(self, chat_id: str, text: str) -> None:
            pass
    return MockBot()

def generate_test_trades(account_id: str, count: int = 5) -> List[Dict]:
    """Generate test trade data."""
    trades = []
    base_time = datetime.utcnow()
    
    for i in range(count):
        trades.append({
            "account_id": account_id,
            "symbol": "BTCUSDT",
            "side": "buy" if i % 2 == 0 else "sell",
            "size": 1.0,
            "entry_price": 100.0 + i,
            "exit_price": 101.0 + i,
            "pnl": 1.0,
            "timestamp": base_time - timedelta(hours=i)
        })
    
    return trades

@pytest.fixture
def test_trades(test_account) -> List[Dict]:
    """Provide test trade data."""
    return generate_test_trades(str(test_account.id))