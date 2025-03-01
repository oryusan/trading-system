# Trading Application API Documentation

This document provides an overview of the REST API endpoints available in the Trading Application. The application uses a modular architecture and provides endpoints for authentication, bot management, account management, group management, trading operations, webhooks, and WebSocket communication.

All endpoints expect JSON payloads and return JSON responses. Authentication is handled via JWT tokens (sent in the `Authorization: Bearer <token>` header).

## Base URL

```
http://<your-domain>/api/v1
```

## Authentication API

Endpoints for user authentication, session management, and password reset.

### Endpoints

- **POST `/auth/login`**

  Authenticate a user and obtain access and refresh tokens.

  **Request Body:**
  ```json
  {
    "username": "string",
    "password": "string",
    "device_info": {
      "ip_address": "string",
      "user_agent": "string",
      "device_type": "string"
    }
  }
  ```

  **Response:**
  ```json
  {
    "access_token": "string",
    "refresh_token": "string",
    "token_type": "bearer",
    "expires_in": 86400,
    "user_context": {
      "user_id": "string",
      "username": "string",
      "role": "admin|exporter|viewer",
      "permissions": ["string"]
    }
  }
  ```

- **POST `/auth/logout`**

  End the current user session.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Successfully logged out",
    "data": {
      "timestamp": "2025-02-26T12:00:00Z"
    }
  }
  ```

- **POST `/auth/refresh`**

  Refresh an access token using a refresh token.

  **Request Body:**
  ```json
  {
    "refresh_token": "string"
  }
  ```

  **Response:** Same as login endpoint

- **GET `/auth/me`**

  Get information about the current authenticated user.

  **Response:**
  ```json
  {
    "user_id": "string",
    "username": "string",
    "role": "admin|exporter|viewer",
    "permissions": ["string"]
  }
  ```

- **POST `/auth/password/reset`**

  Initiate a password reset request.

  **Request Body:**
  ```json
  {
    "email": "string"
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "If an account exists with this email, reset instructions will be sent",
    "data": {
      "timestamp": "2025-02-26T12:00:00Z"
    }
  }
  ```

- **POST `/auth/password/reset/complete`**

  Complete the password reset process.

  **Request Body:**
  ```json
  {
    "token": "string",
    "new_password": "string"
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "Password reset successful",
    "data": {
      "timestamp": "2025-02-26T12:00:00Z"
    }
  }
  ```

- **GET `/auth/sessions`**

  List active sessions for the current user.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Active sessions retrieved",
    "data": {
      "sessions": [
        {
          "session_id": "string",
          "created_at": "2025-02-26T12:00:00Z",
          "device_info": {
            "ip_address": "string",
            "user_agent": "string"
          }
        }
      ],
      "timestamp": "2025-02-26T12:00:00Z"
    }
  }
  ```

- **POST `/auth/sessions/{session_id}/terminate`**

  Terminate a specific session.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Session terminated successfully",
    "data": {
      "timestamp": "2025-02-26T12:00:00Z"
    }
  }
  ```

- **GET `/auth/permissions`**

  Get the current user's permissions.

  **Response:**
  ```json
  {
    "success": true,
    "message": "User permissions retrieved",
    "data": {
      "permissions": ["string"],
      "role": "admin|exporter|viewer",
      "timestamp": "2025-02-26T12:00:00Z"
    }
  }
  ```

## Bots API

Endpoints for managing trading bots.

### Endpoints

- **POST `/bots/create`**

  Create a new bot (Admin only).

  **Query Parameters:**
  - `name`: string
  - `base_name`: string
  - `timeframe`: TimeFrame (1m, 5m, 15m, 1h, 6h, 1d, 3d)

  **Response:**
  ```json
  {
    "success": true,
    "bot_id": "string"
  }
  ```

- **GET `/bots/list`**

  List bots accessible to the current user.

  **Response:**
  ```json
  [
    {
      "id": "string",
      "name": "string",
      "base_name": "string",
      "timeframe": "string",
      "status": "string",
      "connected_accounts": ["string"]
    }
  ]
  ```

- **GET `/bots/{bot_id}`**

  Get detailed bot information.

  **Response:**
  ```json
  {
    "id": "string",
    "name": "string",
    "base_name": "string",
    "timeframe": "string",
    "status": "string",
    "connected_accounts": ["string"],
    "performance": {
      "metrics": {}
    }
  }
  ```

- **POST `/bots/{bot_id}/status`**

  Update bot status (Admin only).

  **Query Parameters:**
  - `status`: BotStatus (ACTIVE, PAUSED, STOPPED)

  **Response:**
  ```json
  {
    "success": true,
    "status": "string"
  }
  ```

- **POST `/bots/{bot_id}/connect-account`**

  Connect an account to a bot (Admin only).

  **Query Parameters:**
  - `account_id`: string

  **Response:**
  ```json
  {
    "success": true,
    "message": "Account connected"
  }
  ```

- **POST `/bots/{bot_id}/disconnect-account`**

  Disconnect an account from a bot (Admin only).

  **Query Parameters:**
  - `account_id`: string

  **Response:**
  ```json
  {
    "success": true,
    "message": "Account disconnected"
  }
  ```

- **POST `/bots/{bot_id}/terminate`**

  Terminate all positions and orders for bot accounts (Admin only).

  **Response:**
  ```json
  {
    "success": true,
    "terminated_accounts": 0,
    "errors": []
  }
  ```

- **GET `/bots/{bot_id}/accounts`**

  Get accounts connected to a bot.

  **Response:**
  ```json
  [
    {
      "id": "string",
      "name": "string",
      "exchange": "string",
      "balance": 0,
      "equity": 0,
      "is_active": true
    }
  ]
  ```

- **GET `/bots/{bot_id}/performance`**

  Get bot performance metrics.

  **Response:**
  ```json
  {
    "daily": {},
    "weekly": {},
    "monthly": {},
    "accounts": {}
  }
  ```

## Accounts API

Endpoints for managing trading accounts.

### Endpoints

- **POST `/accounts/create`**

  Create a new account (Admin only).

  **Request Body:**
  ```json
  {
    "exchange": "string",
    "api_key": "string",
    "api_secret": "string",
    "initial_balance": 0,
    "passphrase": "string",
    "is_testnet": false
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "account_id": "string",
    "message": "Account created successfully"
  }
  ```

- **GET `/accounts/list`**

  List accessible accounts.

  **Response:**
  ```json
  {
    "accounts": [
      {
        "account_info": {},
        "balances": {},
        "positions": {},
        "performance": {},
        "references": []
      }
    ],
    "pagination": {
      "total": 0,
      "skip": 0,
      "limit": 0
    }
  }
  ```

- **GET `/accounts/{account_id}`**

  Get detailed account information.

  **Response:**
  ```json
  {
    "account": {
      "account_info": {},
      "balances": {},
      "positions": {},
      "settings": {}
    },
    "performance": {},
    "references": [],
    "ws_status": {}
  }
  ```

- **POST `/accounts/{account_id}/update-credentials`**

  Update account credentials (Admin only).

  **Request Body:**
  ```json
  {
    "api_key": "string",
    "api_secret": "string",
    "passphrase": "string"
  }
  ```

  **Response:**
  ```json
  {
    "success": true
  }
  ```

- **POST `/accounts/{account_id}/sync-balance`**

  Sync account balance with exchange (Admin only).

  **Response:**
  ```json
  {
    "success": true,
    "balance": 0,
    "equity": 0
  }
  ```

- **GET `/accounts/{account_id}/performance`**

  Get account performance metrics.

  **Response:**
  ```json
  {
    "account_id": "string",
    "time_range": {
      "start_date": "2025-02-26T00:00:00Z",
      "end_date": "2025-02-26T23:59:59Z"
    },
    "metrics": {}
  }
  ```

- **DELETE `/accounts/{account_id}`**

  Delete an account (Admin only).

  **Response:**
  ```json
  {
    "success": true
  }
  ```

- **POST `/accounts/{account_id}/assign-group`**

  Assign an account to a group (Admin only).

  **Request Body:**
  ```json
  {
    "group_id": "string"
  }
  ```

  **Response:**
  ```json
  {
    "success": true
  }
  ```

## Groups API

Endpoints for managing account groups.

### Endpoints

- **GET `/groups/`**

  List accessible account groups.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Groups retrieved successfully",
    "data": {
      "groups": [
        {
          "id": "string",
          "name": "string",
          "description": "string",
          "accounts": ["string"]
        }
      ],
      "total": 0,
      "offset": 0,
      "limit": 50
    }
  }
  ```

- **GET `/groups/{group_id}`**

  Get detailed group information.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Group retrieved successfully",
    "data": {
      "group": {
        "id": "string",
        "name": "string",
        "description": "string",
        "accounts": ["string"],
        "performance": {},
        "websocket_status": {}
      }
    }
  }
  ```

- **POST `/groups/`**

  Create a new account group (Admin only).

  **Request Body:**
  ```json
  {
    "name": "string",
    "description": "string",
    "accounts": ["string"],
    "max_drawdown": 25.0,
    "target_monthly_roi": 5.0,
    "risk_limit": 5.0
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "Group created successfully",
    "data": {
      "group": {}
    }
  }
  ```

- **PATCH `/groups/{group_id}`**

  Update group settings (Admin only).

  **Request Body:**
  ```json
  {
    "description": "string",
    "accounts": ["string"],
    "max_drawdown": 25.0,
    "target_monthly_roi": 5.0,
    "risk_limit": 5.0
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "Group updated successfully",
    "data": {
      "group": {}
    }
  }
  ```

- **GET `/groups/{group_id}/performance`**

  Get group performance metrics.

  **Query Parameters:**
  - `start_date`: string (YYYY-MM-DD)
  - `end_date`: string (YYYY-MM-DD)

  **Response:**
  ```json
  {
    "success": true,
    "message": "Performance data retrieved",
    "data": {
      "performance": {}
    }
  }
  ```

- **GET `/groups/{group_id}/metrics`**

  Get current group metrics and health status.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Group metrics retrieved",
    "data": {
      "risk_metrics": {},
      "websocket_health": {},
      "balance_status": {}
    }
  }
  ```

- **GET `/groups/{group_id}/history`**

  Get historical group performance data.

  **Query Parameters:**
  - `start_date`: string (YYYY-MM-DD)
  - `end_date`: string (YYYY-MM-DD)
  - `interval`: string (day, week, month)

  **Response:**
  ```json
  {
    "success": true,
    "message": "Historical data retrieved",
    "data": {
      "history": [],
      "interval": "string",
      "period": {
        "start": "string",
        "end": "string"
      }
    }
  }
  ```

- **POST `/groups/{group_id}/accounts`**

  Add multiple accounts to a group (Admin only).

  **Request Body:**
  ```json
  {
    "account_ids": ["string"]
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "Bulk account addition completed",
    "data": {
      "results": {
        "success": ["string"],
        "failed": []
      },
      "group": {}
    }
  }
  ```

- **POST `/groups/{group_id}/accounts/{account_id}`**

  Add a single account to a group (Admin only).

  **Response:**
  ```json
  {
    "success": true,
    "message": "Account added to group",
    "data": {
      "group": {}
    }
  }
  ```

- **DELETE `/groups/{group_id}/accounts/{account_id}`**

  Remove an account from a group (Admin only).

  **Response:**
  ```json
  {
    "success": true,
    "message": "Account removed from group",
    "data": {
      "group": {}
    }
  }
  ```

- **DELETE `/groups/{group_id}`**

  Delete an account group (Admin only).

  **Response:**
  ```json
  {
    "success": true,
    "message": "Group deleted successfully",
    "data": {
      "group_id": "string"
    }
  }
  ```

## Trading API

Endpoints for executing trades and managing positions.

### Endpoints

- **POST `/trading/group/{group_id}/execute`**

  Execute a trade for all accounts in a group (Admin only).

  **Request Body:**
  ```json
  {
    "symbol": "string",
    "side": "string",
    "order_type": "string",
    "risk_percentage": 0,
    "leverage": 0,
    "take_profit": 0
  }
  ```

  **Response:**
  ```json
  {
    "success_count": 0,
    "error_count": 0,
    "results": []
  }
  ```

- **GET `/trading/positions/{account_id}`**

  Retrieve recent closed positions for an account.

  **Query Parameters:**
  - `limit`: integer (default: 50)

  **Response:**
  ```json
  [
    {
      "symbol": "string",
      "side": "string",
      "size": 0,
      "entry_price": 0,
      "exit_price": 0,
      "pnl": 0,
      "fee": 0
    }
  ]
  ```

- **POST `/trading/close-position/{account_id}`**

  Close a position for an account (Admin only).

  **Request Body:**
  ```json
  {
    "symbol": "string",
    "order_type": "string"
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "Position closed",
    "symbol": "string"
  }
  ```

- **POST `/trading/terminate/{account_id}`**

  Terminate all positions for an account (Admin only).

  **Response:**
  ```json
  {
    "success": true,
    "terminated_positions": 0,
    "errors": []
  }
  ```

## Webhook API

Endpoints for handling external webhooks (e.g., from TradingView).

### Endpoints

- **POST `/webhook/tradingview`**

  Handle TradingView webhook signals.

  **Headers:**
  - `X-TradingView-Signature`: string

  **Request Body:**
  ```json
  {
    "order_type": "string",
    "symbol": "string",
    "botname": "string",
    "side": "string",
    "size": "string",
    "leverage": "string",
    "takeprofit": "string"
  }
  ```

  **Response:**
  ```json
  {
    "success": true,
    "message": "Signal processed successfully",
    "data": {
      "correlation_id": "string",
      "success_count": 0,
      "error_count": 0,
      "results": []
    }
  }
  ```

- **GET `/webhook/tradingview/test`**

  Test webhook endpoint health.

  **Response:**
  ```json
  {
    "success": true,
    "message": "Webhook endpoint is operational",
    "data": {
      "timestamp": "2025-02-26T12:00:00Z",
      "correlation_id": "string"
    }
  }
  ```

## WebSocket API

WebSocket endpoints for real-time UI communication.

### Endpoints

- **WebSocket `/ws/{user_id}`**

  WebSocket endpoint for UI connections.

  **Authentication:**
  - Required: JWT token via WebSocket authentication.

  **Supported Messages:**
  ```json
  // Subscribe to bot updates
  {
    "type": "subscribe",
    "bot_id": "string"
  }

  // Unsubscribe from bot updates
  {
    "type": "unsubscribe",
    "bot_id": "string"
  }
  ```

  **Server Messages:**
  ```json
  // Bot update
  {
    "type": "bot_update",
    "bot_id": "string",
    "data": {},
    "timestamp": "2025-02-26T12:00:00Z"
  }

  // Ping message
  {
    "type": "ping"
  }
  ```

## API Health Endpoints

Endpoints for system health monitoring.

### Endpoints

- **GET `/health`**

  Check API health status.

  **Response:**
  ```json
  {
    "status": "ok|degraded",
    "version": "string",
    "environment": "string",
    "database": {
      "connected": true,
      "references": {}
    },
    "uptime": 0,
    "timestamp": "2025-02-26T12:00:00Z"
  }
  ```

- **GET `/metrics`**

  Get system metrics.

  **Response:**
  ```json
  {
    "circuit_breakers": {},
    "rate_limits": {},
    "timestamp": "2025-02-26T12:00:00Z"
  }
  ```

## Error Handling

The API uses consistent error handling with context-rich responses. Common status codes:

- `400` - Bad Request (validation errors)
- `401` - Unauthorized (authentication required)
- `403` - Forbidden (insufficient permissions)
- `404` - Not Found (resource doesn't exist)
- `422` - Unprocessable Entity (validation errors)
- `500` - Internal Server Error (server issues)

Error responses follow a standard format:

```json
{
  "success": false,
  "detail": "Error message",
  "errors": {
    "field_name": "Error details"
  },
  "request_id": "string",
  "timestamp": "2025-02-26T12:00:00Z"
}
```

For security-related errors, minimal information may be returned to prevent information leakage.