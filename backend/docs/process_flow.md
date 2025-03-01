# Process Flow: From TradingView Webhook to Trade Execution (Detailed)

This document details the complete flow of operations that occur in the application after a TradingView webhook is received. It covers the entire lifecycle—from the initial HTTP request to trade execution, performance update, and notifications—while explicitly incorporating the CCXT‑based symbol normalization step.

---

## 1. Webhook Reception and Validation

- **Receive Webhook:**  
  The backend exposes an API endpoint (in `api/v1/endpoints/webhook.py`) that listens for HTTP POST requests from TradingView. The incoming JSON payload must include the following fields:
  - **`botname`**: A non‑empty string (e.g. `"BotA_1m"`) that identifies the bot responsible for executing the signal.  
    *A Pydantic validator ensures that `botname` is provided and trimmed of whitespace.*
  - **`symbol`**: The raw trading symbol (e.g. `"BTC"`). 
  - **`side`**: The trading direction, either `"buy"` or `"sell"`.
  - **`order_type`**: A field that specifies the type of signal. Valid values include:
    - `"LONG_SIGNAL"` or `"SHORT_SIGNAL"` for standard signal orders.
    - `"LONG_LADDER"` or `"SHORT_LADDER"` for ladder orders.  
    *This field is crucial because it tells the system which processing path to follow.*
  - **`size`**: A value representing the risk percentage (often referred to as `"size"` in the code).
  - **`leverage`**: The leverage to be applied (e.g. `"10"`).
  - **`take_profit`**: (Optional for SIGNAL type) The target profit price.

- **Validate Webhook Secret:**  
  The webhook request also includes a secret token (configured via `WEBHOOK__TRADINGVIEW_WEBHOOK_SECRET`). The system checks this token against the expected value:
  - If the secret is invalid, the request is rejected.
  - If valid, the payload proceeds to the next step.

- **Payload Parsing and Field Validation:**  
  The system validates the payload:
  - **`botname`** is checked to ensure it isn’t empty.
  - **`order_type`** is examined to confirm that it is present and contains one of the allowed values (which distinguishes between Signal and Ladder processing).
  - The `"side"`, `"size"`, `"leverage"`, and (optionally) `"take_profit"` fields are also validated.  
  - Internally, a function (e.g. `check_required_fields_and_consistency`) maps:
    - `"buy"` with a corresponding `order_type` of `"LONG_SIGNAL"` or `"LONG_LADDER"`
    - `"sell"` with a corresponding `order_type` of `"SHORT_SIGNAL"` or `"SHORT_LADDER"`

---

## 2. Extracting and Parsing Trade Signal Data

- **Extract Signal Data:**  
  The system extracts key parameters from the validated webhook payload:
  - `botname`, `symbol`, `side`, `order_type`, `size` (risk percentage), `leverage`, and optionally `take_profit`.

- **Bot Lookup:**  
  The validated `botname` is used to look up the corresponding bot record. For example, in `webhook.py` the validator for `botname` ensures that a non‑empty value is provided:
  
  ```python
  @validator("botname")
  def validate_botname(cls, v: str) -> str:
      if not v or not v.strip():
          raise ValidationError("Bot name cannot be empty", context={"botname": v})
      return v.strip()
  ```
  
  This bot record contains the unique identifier (`bot_id`) and the list of trading accounts linked to the bot. This step ensures that the trade signal is directed only to the appropriate accounts.

---

## 3. Symbol Normalization via CCXT

- **Normalize Symbol:**  
  Although the webhook sends a raw symbol (e.g. `"BTC"`), the exchange expects a normalized symbol (for example, `"BTC/USDT"`). To achieve this:
  - The raw symbol is passed to the **Symbol Validator**.
  - The Symbol Validator uses the CCXT library to load market data from the exchange.
  - It converts the raw symbol into the exchange‑specific format.
  - The normalized symbol is cached for efficiency and is used for all further steps.

---

## 4. Initiating Trade Execution

- **Forward Signal to Trading Service:**  
  The structured trade signal (including `botname`, normalized `symbol`, `side`, `order_type`, `size`, `leverage`, and `take_profit`) is forwarded to the **Trading Service**.

- **Signal vs. Ladder Processing:**  
  Based on the `"order_type"` field:
  - **Signal Orders:**  
    - If `"order_type"` is `"LONG_SIGNAL"` or `"SHORT_SIGNAL"`, the Trading Service follows the Signal process.
    - This includes checking for existing positions, setting leverage, calculating size, and placing a standard trade order.
  - **Ladder Orders:**  
    - If `"order_type"` is `"LONG_LADDER"` or `"SHORT_LADDER"`, the service follows the Ladder process.
    - This path involves canceling existing orders, placing a ladder order that includes additional take-profit instructions, and handling the trade differently.
  
  *The function `check_required_fields_and_consistency` in the webhook processing code ensures that the signal type is valid and consistent with the expected values.*

## 5. Exchange Operations and Order Execution

- **Exchange Instance Creation:**  
  The **Exchange Factory** is called to create (or retrieve from cache) an exchange instance using the account’s credentials (stored in environment variables).

---

## 6. Handling Existing Positions and Leverage Setup

- **Existing Position Check:**  
  - The **Exchange Operations** module checks whether there’s an existing open position for the normalized symbol.
  
- **Position Handling:**  
  - If an existing position is found:
    - Verify if it is compatible with the new trade signal.
    - If the existing position is in the opposite direction, cancel any pending orders, close the position, and reset the leverage.
  - If no position exists:
    - Set the desired leverage for the upcoming trade.

---

## 7. Trade Size Calculation

- **Balance and Specification Retrieval:**  
  - Retrieve the current account balance and equity from the exchange.
  - Fetch trading specifications (tick size, lot size, contract size) via the Symbol Validator.

- **Calculation:**  
  - Calculate the appropriate position size based on the risk percentage, leverage, and current balance.
  - Adjust the raw position size to conform to the exchange’s lot size requirements.

---

## 8. Order Placement and Monitoring

- **Order Placement:**  
  - Place a new trade order on the exchange using the normalized symbol, calculated trade size, and current market price (retrieved via the exchange’s ticker API).  
  - The order type (market, limit, signal, or ladder with take profit) is determined based on the signal data.

- **Order Monitoring:**  
  - An order monitoring loop tracks the status of the order until it is filled or times out.
  - If the market conditions change during order execution, the order may be amended (e.g. price adjustment) to ensure successful execution.

---

## 9. Trade Recording

- **Database Logging:**  
  Once the trade is executed (whether fully filled or partially closed), a trade record is created using the **CRUD Trade** module.  
  The record includes:
  - Account ID, normalized symbol, order side, calculated trade size, entry price, execution time, etc.

---

## 10. Updating Performance Metrics

- **Performance Update Trigger:**  
  - After recording the trade, the **Performance Service** is invoked.
  
- **Metrics Calculation:**  
  - The service fetches the latest account balance and equity.
  - It aggregates data from closed (realized) trades to compute key performance metrics (total trades, win rate, gross and net PnL, ROI, etc.).
  
- **Database Update:**  
  - The daily performance record is either created or updated in MongoDB via the **Performance Storage** module.

---

## 11. Notifications and Reporting

- **Telegram Notifications:**  
  - The **Telegram Service** sends real‑time notifications to users via a Telegram bot.
  - Notifications include trade execution alerts, trade closure notifications, and bot status updates.

- **Reporting:**  
  - Additional reporting services (such as the Exporter module) can generate detailed reports or export closed trade data in CSV/Excel format.

---

## 12. Error Handling, Monitoring, and Cleanup

- **Error Handling:**  
  - At every step, robust error handling is applied. Custom errors such as `ValidationError`, `ExchangeError`, and `DatabaseError` are raised with rich context.
  
- **Centralized Logging:**  
  - All significant events and errors are logged for debugging and auditing.
  
- **Background Monitoring:**  
  - Separate services (such as the **Bot Monitor** and **Cron Jobs**) continuously perform health checks, manage WebSocket reconnections, and run scheduled tasks (performance aggregation, data cleanup, symbol verification).

- **Cleanup:**  
  - Temporary resources (e.g., caches, error counters) are cleaned up after the trade process completes.

---

## 13. End of Process

- **Completion:**  
  After the trade has been executed, recorded, performance metrics updated, and notifications sent, the process is complete.  
  The system then waits for the next TradingView webhook to repeat the cycle.

---

## Diagram

```mermaid
flowchart TD
    A[Receive TradingView Webhook<br/>(includes botname, symbol, side, order_type, size, leverage, take_profit)]
    B[Validate Webhook Secret & Parse Payload<br/>(validators ensure botname & order_type are non-empty)]
    C[Extract Trade Signal Data<br/>(botname, symbol, side, order_type, size, leverage, take_profit)]
    D[Lookup Bot by botname<br/>(validator in webhook.py ensures botname is valid)]
    E[Normalize Symbol via CCXT<br/>(Symbol Validator converts "BTC" to exchange format)]
    F[Forward Signal to Trading Service]
    G[Determine Signal Type:<br/>- LONG/SHORT SIGNAL<br/>- LONG/SHORT LADDER]
    H[Create/Retrieve Exchange Instance<br/>(Exchange Factory uses account credentials)]
    I[Check Existing Positions<br/>(cancel orders/close position if conflicting)]
    J[Calculate Position Size<br/>(based on risk (size), leverage, account balance, market specs)]
    K[Place Trade Order via Exchange API<br/>(Signal or Ladder process based on order_type)]
    L[Monitor Order Status until Filled or Timeout]
    M[Record Trade in Database<br/>(CRUD Trade records trade details)]
    N[Update Daily Performance<br/>(Performance Service updates metrics in DB)]
    O[Send Notifications<br/>(Telegram Service alerts users)]
    P[Error Handling & Monitoring<br/>(Centralized logging and background checks)]

    A --> B
    B -- Valid --> C
    B -- Invalid --> P
    C --> D
    D --> E
    E --> F
    F --> G
    G --> H
    H --> I
    I --> J
    J --> K
    K --> L
    L --> M
    M --> N
    N --> O
    P --> O
```

---

## Summary

When a TradingView webhook is received with a JSON payload such as:

```json
{
  "botname": "BotA_1m",
  "symbol": "BTC",
  "side": "buy",
  "order_type": "LONG_SIGNAL",
  "size": "2.5",
  "leverage": "10",
  "take_profit": "45000"
}
```

the system will:

1. **Validate the Webhook:**  
   Confirm the webhook secret is correct and all required fields (including `botname` and `order_type`) are present.

2. **Extract Signal Data & Lookup Bot:**  
   Parse the payload to extract `botname`, `symbol`, `side`, `order_type`, etc. Then, use the `botname` (validated in `webhook.py`) to look up the corresponding bot record and its connected accounts.

3. **Normalize the Symbol:**  
   Convert the raw symbol `"BTC"` into the exchange‑specific format (e.g. `"BTC/USDT"`) via the Symbol Validator using CCXT.

4. **Determine Signal Processing:**  
   Depending on the `order_type`:
   - **Signal Orders:**  
     Process as a standard signal order (for `LONG_SIGNAL` or `SHORT_SIGNAL`), which involves checking existing positions, setting leverage, calculating position size, and placing a standard order.
   - **Ladder Orders:**  
     Process as a ladder order (for `LONG_LADDER` or `SHORT_LADDER`), which involves canceling existing orders, placing a ladder order with additional take-profit instructions, and managing the trade accordingly.

5. **Execute Trade:**  
   The Trading Service coordinates with the Exchange Factory to create/retrieve the proper exchange instance, checks for and handles existing positions, calculates the correct trade size, and places the order. An order-monitor loop tracks the status until the order is filled.

6. **Record Trade:**  
   A trade record is stored in the database (via CRUD operations) with all relevant details.

7. **Update Performance:**  
   The Performance Service updates daily performance metrics based on the newly closed trade and current balance/equity.

8. **Send Notifications:**  
   The Telegram Service sends real‑time notifications to users about trade execution, closure, and any errors or status changes.

9. **Error Handling:**  
   Throughout the process, robust error handling, logging, and background monitoring ensure that any issues are caught and reported.

---

## Conclusion

This comprehensive process flow describes every step that occurs when the application receives a TradingView webhook. It highlights the key phases—including trade signal extraction, exchange operations, CCXT‑based symbol normalization, position handling, order placement, trade recording, performance updates, and user notifications. Robust error handling and continuous monitoring ensure that the system maintains consistency and reliability throughout the entire process.

```

---
