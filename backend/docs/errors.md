# Error References

This document provides an overview of the error classes used throughout the application. These error types, defined primarily in `app/core/errors/base.py`, are used to standardize error handling, provide rich contextual information, and help with debugging. Below is a summary of each error type and its intended usage.

---

## DatabaseError

**Description:**  
Wraps exceptions that occur during database operations such as creating, reading, updating, or deleting documents.

**Usage:**  
- Thrown by CRUD operations when unexpected errors occur in the database layer.
- Provides context (e.g., model name, operation type, timestamp) to aid in diagnosing database issues.

---

## ValidationError

**Description:**  
Indicates that an input validation rule has failed. This error is raised when data does not meet expected criteria.

**Usage:**  
- Used extensively in Pydantic validators and custom validation functions.
- Thrown in scenarios where incoming data is empty, improperly formatted, or does not meet business rules.
- Commonly seen during user creation, updating account information, and verifying API credentials.

---

## NotFoundError

**Description:**  
Raised when a requested resource (e.g., User, Bot, Account) is not found.

**Usage:**  
- Typically thrown in CRUD operations when a document is not found in the database.
- Used by the reference manager to indicate missing references.

---

## AuthorizationError

**Description:**  
Indicates that a user does not have the necessary permissions or credentials to perform a specific action.

**Usage:**  
- Thrown during authentication failures (e.g., wrong password).
- Used to enforce role‑based access control within the system.

---

## ExchangeError

**Description:**  
Represents errors that occur during interactions with external exchanges. This includes issues with API credentials, order execution, and data retrieval.

**Usage:**  
- Thrown by exchange service operations when an error occurs during order placement, balance retrieval, or market data requests.
- Provides context regarding the specific exchange, endpoint, and parameters involved.

---

## RateLimitError

**Description:**  
Raised when the application exceeds the allowed number of requests in a given time period.

**Usage:**  
- Used in both HTTP and WebSocket operations to enforce throttling.
- Indicates that a request should be retried after a delay.

---

## ServiceError

**Description:**  
A general error type used within service layers to indicate failures in processing business logic or aggregating data.

**Usage:**  
- Thrown by high-level services when operations such as performance calculations or trading operations fail unexpectedly.

---

## WebSocketError

**Description:**  
Thrown when errors occur in WebSocket operations such as connection failures, disconnections, or message processing errors.

**Usage:**  
- Utilized in the WebSocket client implementations and the connection manager.
- Provides detailed context on connection state, subscription channels, and reconnection attempts.

---

## RequestException

**Description:**  
Specific to HTTP request failures. This error is thrown when an API request to an external exchange returns an error or unexpected response.

**Usage:**  
- Commonly used in the exchange service implementations.
- Wraps error responses from HTTP requests to provide additional context.

---

## BotMonitorError

**Description:**  
A specialized error used within the bot monitoring service.

**Usage:**  
- Thrown when monitoring tasks (e.g., verifying WebSocket connections, bot lifecycle management) fail.
- Indicates issues that may require immediate attention, such as repeated reconnection failures.

---

## ConfigurationError

**Description:**  
Raised when required configuration parameters (e.g., database URLs, API keys, environment variables) are missing or invalid.

**Usage:**  
- Used during application startup and in factory methods to ensure that all required configuration values are provided.
- Helps prevent runtime errors due to misconfiguration.

---

## Error Handling Strategy

Throughout the application, errors are managed using custom decorators (e.g., `@error_handler`) which:

- **Catch Exceptions:** Wrap functions to catch exceptions during execution.
- **Log Context:** Automatically log detailed context and error messages.
- **Re-Raise Standardized Errors:** Convert caught exceptions into one of the standardized error types listed above.

This approach ensures that error handling is consistent across all modules and services, aiding in debugging and maintenance.

---

## Conclusion

Understanding these error references is crucial for troubleshooting issues within the application. When you encounter an error, refer to this document to determine its meaning and context. For further details on each error type, consult the inline documentation within the source code (especially in `app/core/errors/base.py` and related modules).

---
