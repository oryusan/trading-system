## 1. Frontend UI Design & Component Layout

The frontend of the Trading WebApp shall be developed using React? It should provide a user‑friendly interface for monitoring trades, managing bots / accountgroups and accounts, and performing trading actions. The layout is designed to be modular, with different “dashboard” views for various roles (viewer, exporter and admin).

### 1.1. Login Page

- **Layout:**  
  A single row divided into two divs:
  - **Left Div:**  
    - Displays the **App Name** (e.g., “Trading WebApp [Development]”)  
    - Contains an **Assets Animation** (an animated graphic to showcase assets or trading activity as a graph)
  - **Right Div:**  
    - Input field for **Name**  
    - Input field for **Password**  
    - **Login Button**

### 1.2. Main Menu

- **Header Controls:** 
  - These controls should be on the far right next to the User Settings/Profile Picture
  - A Dropdown to select a assigned **AccountGroup** if more than 1
  - A sort selector (Day/Week/Month/Quarter/Year) 

- **Optional Layout:**  
  Either a top navigation bar or a left sidebar that allows users to switch between:
  - Bot Dashboard
  - AccountGroup Dashboard
  - Account Dashboard
  - Performance
  - Trading Panel (For admin)
  - Exports (For admin&exporter)
  - Error Logs (For admin)
  - Settings (For admin)

### 1.3. Bot Dashboard
  
- **Bot Cards/Divs (Displayed in rows of 3–4):**  
  Each card (a small/medium div) shows:
  - **Bot Name**  
  - **Bot Picture** (logo or avatar)  
  - **Status:** A small colored dot indicating Online (Green), Offline (Red), or Idle (Yellow)  
  - **PnL:** Displayed with a small inline graph  
  - **Total Trades:** A count of trades executed by the bot
  - **Total Assets:** A count of total sum of asset value in $ by the bot (Adds up all connected accounts)
  - **Terminate Button:** Allows admins to turn the bot on/off. (Turning off includes closing all trades and orders.)

### 1.4. AccountGroup Dashboard

- **First Row Overview Cards/Divs:**  
  Display summary metrics:
  - **Total Accounts**  
  - **Total Assets**  
  - **Total Trades**  
  - **Win Rate (%)**  
  - **PnL**

- **Second Row:**
  - **Graph of Balance Growth**
  - **Top Performing Assets** (later implementation)

- **Third Row:**  
  - **Current Active Trades/Orders/TPSL** (Take Profit/Stop Loss status)

### 1.5. Account Dashboard

- **First Row Overview Cards/Divs:**  
  Display summary metrics:
  - **Total Assets**  
  - **Total Trades**  
  - **Win Rate (%)**  
  - **PnL**  
  - **ROI**

- **Second Row:**
  - **Graph of Balance Growth**
  - **Top Performing Assets** (later implementation)

- **Third Row:**  
  - **Current Active Trades/Orders/TPSL**

### 1.6. Performance Report

- **Reports:**   
  - Main Div: A Performance Metrics Table (similar to the Admin/Reports view) showing aggregated data.  (later implementation)

### 1.7. Trading Panel

- **Header Controls:**
  - A Field to show the current selected AccountGroup

- **First Row – Trading Settings & Latest Trades:**  
  Split into two side-by-side divs:
  - **Trading Settings Div:**  
    - **Symbol:** Dropdown (populated with normalized symbols)  
    - **Leverage:** Input or slider (range 1‑100x)  
    - **Size/Risk:** Percentage input (e.g., “1% of balance”)  
    - **TakeProfit:** Input (optional; empty means no take profit)  
    - **StopLoss:** Input (optional)  
    - **Long Button:** Green button to trigger a long trade  
    - **Short Button:** Red button to trigger a short trade
  - **Latest Trades Div:**  
    - Displays a concise trade history (latest 5–10 trades) with key details (symbol, side, price, PnL).

- **Second Row:**  
  - **Open Positions/Orders/TPSL:** Real‑time view of active positions and pending orders, using data from the WebSocket stream.

### 1.8. Exports

- **Trade History Export (Admin/Exporter):**   
  - Main Div: A table of Trade History grouped by the selected interval with an **Export** button (to CSV/Excel/PDF).

### 1.9. Error Dashboard

- **Error Output Panel:**  
  A dedicated section to display error messages and logs for troubleshooting and information purposes.

### 2.0. ADMIN Settings Dashboard

This section is accessible only to the admin user and includes:

- **Admin Dashboard Overview:**  
  - Summary Cards showing:  
    - **Total Users**  
    - **Total Bots**  
    - **Total AccountGroups**  
    - **Total Accounts**  
    - **Total Trades**  
    - **Total Assets**  
    - **Win Rate (%)**
  - Graphs showing **Accounts Growth** and **Top Performing Assets**  (later implementation)
  - A Performance Metrics Table for detailed analysis (later implementation)

- **User Management:**  
  - Create, delete, and edit users (Name, Password, assign/unassign accountgroups)

- **Bot Management:**  
  - Create, delete, and edit bots (Name, Picture, TimeFrame, assign/unassign accounts)

- **AccountGroup Management:**  
  - Create, delete, and edit account groups (Name, Description)

- **Account Management:**  
  - Create, delete, and edit accounts (Name, Exchange, API Key, API Password, Connection Status)

- **Symbols Management:**  (Optional) -- later
  - Display a list of normalized symbols (as stored in the database) with detailed information and a option to add some manually

- **Trading Settings:**  (Optional) -- later
  - Set limits such as Maximum Position Size/Total Risk Size as a percentage of wallet balance, Maximum Leverage, and Trading Hours

- **Endpoint Configuration:**  
  - Configure an alternate redirection endpoint (if needed to forward TradingView webhook messages)

- **Telegram Settings:**  
  - Configure settings required for the Telegram bot(s)

- **Backup and Recovery:**  
  - Data backup and recovery options
