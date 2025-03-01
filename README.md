# Trading WebApp

## Overview
Trading WebApp is a cryptocurrency trading platform that integrates with multiple exchanges (Bitget, Bybit, OKX) to manage trades, manually trade, monitor bots, and track performance for accounts/account-groups/bots. The application is built using FastAPI for the backend and supports real-time updates via WebSockets.

---

## Features
- **Multi-Exchange Support**: Manage accounts and execute trades on Bitget, Bybit, and OKX.
- **User Roles**:
  - **Admin**: Full access to all functionalities.
  - **Exporter**: Export trade history and performance metrics.
  - **Viewer**: View bot performance and assigned accounts/accountGroups.
- **Bot Monitoring**: Automate trading strategies with detailed bot management.
- **Trade Tracking**: Export trade history in CSV/Excel formats for tax purposes.
- **Webhooks**: Supports TradingView integration for automated signals.
- **Telegram Bot**: Receive notifications and interact with the system via Telegram.
- **Performance Metrics**: View account and accountGroup-level trading statistics.
- **Health Monitoring**: Includes `/health` endpoint and Prometheus metrics.

---

## Tech Stack
- **Backend**: FastAPI, Python
- **Database**: MongoDB (via Beanie ORM)
- **WebSocket Integration**: For real-time updates
- **APIs**: CCXT for exchange communication
- **Task Scheduling**: Apscheduler for cron jobs
- **Authentication**: JWT-based with roles and permissions
- **Frontend**: (Future Implementation) Next.js

---

## License

All Rights Reserved. This software is intended for personal use by [oryusan](https://github.com/oryusan). 
Redistribution, modification, or use for commercial purposes is prohibited without 
explicit permission.