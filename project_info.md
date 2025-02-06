# **Trading System Overview**  

This is a **multi-exchange cryptocurrency trading system** in progress that supports both **automated trading** via TradingView webhooks and **manual trading** through a web interface and potentially via telegram. The system integrates with **Bitget, Bybit, and OKX exchanges** and provides **real-time market data, risk management, and performance tracking**.  

## **Core Features**  

### **1. Account Management**  
- Connect multiple exchange accounts to Bots that work as signal distributor.  
- Group accounts for **portfolio-based management and trade exports**.  
- Track balances and performance of each **Account/AccountGroup/Bot**.
    -> on a Account level and add it up for: AccountGroups and Bots
- **Real-time position monitoring** with WebSockets.  

### **2. Bot System**  
- Multiple trading bots with configurable **timeframes (1m to 1D)**.  
- Each bot can **manage multiple accounts**.  
- **TradingView webhook integration** for signal execution.  
- **Automated risk management** (position sizing, leverage limits, stop loss, etc. received by TradingView).  

### **3. Trading Features**  
- **Symbol Normalization** Symbol normalization via CCXT.
- **Signal-based trading** Long/Short Signal (manual or automated).  
- **Ladder order placement** Long/Short Ladder for scaling into positions (automated only).  
- **Position control mechanisms** (e.g., emergency stop).  
- **Risk calculation and leverage management** per trade.  
- **Take Profit** settings if Tradingview alert provides the data.
- **StopLoss and Control** triggered events by Tradingview. 
- **Real-time order execution feedback** via WebSockets.  

### **4. Performance Tracking**  
- **Real-time P&L monitoring** per account and accountgroup if the app is currently running (in order to safe traffic/costs).  
- **Daily performance metrics** and trend analysis as a cronjob every 24H.  
- **Aggregated performance tracking** across groups of accounts and bots.  
- **Trade history**. tradeHistory for exports as excel or .csv.
- **Statistics, and analytics** for display purpose (sorted by: Daily/Weekly/Monthly/Quarterly/Yearly)

---

# **Frontend Requirements**  

## **1. Dashboards**
### **Bots Dashboards**
- **Bot Overview:**  Picture, Name, Timeframe.  
- **Daily P&L & Performance Metrics:** Profit, ROI, Trades, Win%.  
- **Bot Status Overview:** Active bots, state and emergency button  
- **System Health:** WebSocket status, API limits, and DB health.
- **Enable/Disable Button** Adjust bots configurations.

### **AccountGroup Dashboard**
- **Account Overview:** Total balance, equity, stats, profit graph.  
- **Active Positions:** View all running trades across grouped accounts.  
- **Daily P&L & Performance Metrics:** Visualized with charts per account/accountGroup/bot.  
- **Bot Status Overview:** Active bots, their current strategies, pnl, state and emergency button  
- **System Health:** WebSocket status, API limits, and DB health.  

### **Account Dashboard**
- **Account Overview:** Total balance, equity, stats, profit graph and active positions.  
- **Active Positions:** View all running trades for the specific account.  
- **Daily P&L & Performance Metrics:** Visualized with charts.  
- **Bot Status Overview:** Active bots, their current strategies, pnl, state and emergency button  
- **System Health:** WebSocket status, API limits, and DB health.  

## **2. Trading Panel**  
- **Symbol selection dropdown** with search.  
- **Leverage & Risk Configuration:** Customizable settings per trade.  
- **Take Profit & Stop Loss input fields.**  
- **One-click Trading Buttons:** Long/Short execution.  
- **Emergency Stop:** Close all open trades instantly.  
- **Live Order Book & Recent Trades** integration (optional).  

## **3. Bot Management**  
- **Enable/Disable bots** and adjust configurations.  
- **View connected accounts** per bot.  
- **Performance tracking per bot** (win rate, P&L, trade success rate).  
- **Signal history** (triggers from TradingView and execution logs).  

## **4. Account Management**  
- **Connect new exchange accounts** via API credentials.  
- **Manage API keys securely** with encryption.  
- **Account grouping & permissions.**  
- **Balance history tracking.**  

## **5. Performance Analytics**  
- **Charts & Reports** for trade history, account growth, and P&L.  
- **Advanced filtering options** (account group, date range).  
- **Export functionality (CSV/Excel).**  
- **Compare performance across multiple accounts/groups.**  

---

# **User Types & Access Levels**  

### **1. Admin**  
✅ Full access to all features.  
✅ Can create and manage users.
✅ Can create and manage accounts/accountGroups/bots. 
✅ Modify system-wide settings and risk parameters.  

### **2. Exporter**
✅ Can view the **Bot dashboard & the Bots its assigned to**.
✅ Can view the **AccountGroup dashboard its assigned to**.
✅ Can view the **Account Dashboard and account performance**.
✅ Can **export AccountGroup data** but cannot modify settings.  
✅ Access limited to assigned groups.  

### **3. Viewer**
✅ Can view the **Bot dashboard & the Bots its assigned to**.
✅ Can view the **AccountGroup dashboard its assigned to**
✅ Can view the **Account Dashboard and account performance**.  
✅ Cannot execute trades or change bot settings. 
✅ Access restricted to assigned accounts/groups and bot that are attached to them.  

---

# **Technical Integration Points**  

## **1. API Integration**  
- **RESTful API endpoints** for data retrieval and trade execution.  
- **WebSocket connections** for **real-time data updates**.  
- **JWT authentication** with role-based access control.  
- **Redis caching** for fast data retrieval and rate limiting.  

## **2. Real-time Updates**  
- **Live position updates** via WebSockets.  
- **Instant balance changes** reflected on the dashboard.  
- **Bot status updates and trade notifications.**  
- **Performance metrics auto-refreshing.**  

## **3. Data Export**  
- **CSV/Excel export functionality** for trade history & reports.  
- **Downloadable performance reports** per account or bot.  
- **API access for external reporting tools** (optional).  

---

# **Frontend Tech Stack Recommendations**  
### **Preferred:**  
✅ **React.js with Next.js** (Server-side rendering + API routes).  
✅ **TailwindCSS + ShadCN OR Material UI/Ant Design** (for a modern UI).  
✅ **React Query / Zustand** (efficient state management).  
✅ **Highcharts / TradingView Widgets** (for data visualization).  

### **Alternative:**  
✅ **Vue.js with Nuxt.js** (if Vue is preferred).  
✅ **Chart.js or D3.js** for custom analytics.  

---

# **Ideal Freelancer Profile**  

A **strong candidate** should have experience with:  
✅ **React.js / Next.js OR Vue.js / Nuxt.js** for frontend development.  
✅ **WebSocket integration** for real-time data.  
✅ **FastAPI / Python backend** (to understand API integration).  
✅ **Trading platforms & financial applications** (familiarity with trading concepts).  
✅ **Data visualization (charts, graphs, performance analytics).**  
✅ **Authentication & Role-Based Access Control (JWT, OAuth).**  

---

# **Next Steps**  
- ✅ **Review the backend code** to optimize API performance.  
- ✅ **Develop a frontend Admin Dashboard** with real-time data.  
- ✅ **Ensure smooth API integration** between frontend & backend.

Looking forward to working with an experienced developer to bring this project to the next level! 🔥