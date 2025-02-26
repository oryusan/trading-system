# Deployment Guide

This guide provides step‑by‑step instructions for deploying the Trading WebApp in a production environment. It covers the prerequisites, environment configuration, deployment steps, scaling, monitoring, and security considerations.

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Environment Configuration](#environment-configuration)
- [Deployment Steps](#deployment-steps)
  - [Preparing the Production Environment](#preparing-the-production-environment)
  - [Installing Dependencies](#installing-dependencies)
  - [Configuring Environment Variables](#configuring-environment-variables)
  - [Building and Deploying the Application](#building-and-deploying-the-application)
- [Monitoring and Maintenance](#monitoring-and-maintenance)
- [Scaling and High Availability](#scaling-and-high-availability)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Additional Resources](#additional-resources)

---

## Overview

The Trading WebApp is a multi‑service application that integrates with several trading exchanges via REST and WebSocket APIs. It includes components for user management, trading operations, real‑time data streaming, performance tracking, error handling, and Telegram notifications. This guide explains how to deploy the entire backend in a production environment.

---

## Prerequisites

- **Operating System:** A production‑ready OS (e.g. Ubuntu 20.04 LTS or equivalent).
- **Python:** Python 3.8 or newer.
- **Database:** A production‑grade MongoDB instance.
- **Caching:** A Redis instance for caching and rate‑limiting.
- **Web Server:** Uvicorn (or Gunicorn with Uvicorn workers) for serving the ASGI application.
- **Reverse Proxy:** Nginx (or similar) for HTTPS termination and load balancing.
- **Containerization (optional):** Docker and Docker Compose (recommended for consistency).

---

## Environment Configuration

All configuration is managed via environment variables. For production, create a file (e.g. `.env.production`) in the project root. This file should include production‑grade settings such as:

- **Application Settings:** `APP_PROJECT_NAME`, `APP_VERSION`, `APP_API_V1_STR`
- **Database Configuration:** `DATABASE__MONGODB_URL`, `DATABASE__MONGODB_DB_NAME`, etc.
- **Security Settings:** `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `ALGORITHM`, etc.
- **CORS Settings:** `BACKEND_CORS_ORIGINS`
- **Webhook and Telegram Bot Settings**
- **Cron Job Schedules**
- **Performance Tracking and Balance Sync Parameters**
- **Exchange Defaults and Rate Limiting**
- **Caching and Monitoring Settings**
- **Logging:** `LOG_LEVEL`, `LOG_FILE_PATH`, etc.

*Note:* The `.env.development` file used for local development is not required in production. Ensure sensitive information is stored securely and not committed to version control.

---

## Deployment Steps

### 1. Preparing the Production Environment

- **Server Setup:**  
  Set up a production‑ready server (or cluster) with your preferred operating system. Install necessary packages (e.g., Python, Docker, etc.).
  
- **Database & Cache:**  
  Deploy MongoDB and Redis in production mode. Ensure they are properly secured and backed up.

- **Reverse Proxy:**  
  Configure Nginx (or another reverse proxy) to:
  - Terminate HTTPS.
  - Route requests to the application server.
  - Handle WebSocket upgrades.

### 2. Installing Dependencies

#### Option A: Using Docker (Recommended)

- **Docker Setup:**  
  Ensure Docker and Docker Compose are installed on your server.
  
- **Dockerfile and Compose:**  
  Create a `Dockerfile` for the backend and a `docker-compose.yml` file to orchestrate:
  - The backend application.
  - MongoDB.
  - Redis.
  
- **Build and Start:**  
  ```bash
  docker-compose build
  docker-compose up -d
  ```

#### Option B: Manual Deployment

- **Virtual Environment:**  
  Create and activate a virtual environment:
  ```bash
  python -m venv venv
  source venv/bin/activate  # (or venv\Scripts\activate on Windows)
  ```
- **Install Dependencies:**  
  ```bash
  pip install -r requirements.txt
  ```

### 3. Configuring Environment Variables

- Create a `.env.production` file in the project root.
- Populate it with production settings (as described above).
- Ensure file permissions restrict access to the `.env.production` file.

### 4. Building and Deploying the Application

#### With Docker:

- **Build and Run:**  
  Follow the steps in Option A above.

- **Logging and Monitoring:**  
  Ensure that logs are written to a persistent volume and that monitoring endpoints are accessible.

#### Without Docker:

- **Run the Application:**  
  Use Uvicorn to start the application server:
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
  ```
- **Process Management:**  
  Use a process manager (e.g. systemd or supervisor) to manage the application process.

---

## Monitoring and Maintenance

- **Logging:**  
  Logs are written to the `/logs` directory. Configure log rotation and retention as needed.
  
- **Health Checks:**  
  The application exposes health‑check endpoints (e.g. `/health`). Integrate these with your monitoring tools.

- **Cron Jobs:**  
  Ensure the cron job scheduler (for performance tracking, cleanup, etc.) is running as configured.

- **Error Notifications:**  
  The system sends error notifications via Telegram. Verify that your Telegram bot is properly configured.

---

## Scaling and High Availability

- **Horizontal Scaling:**  
  Deploy multiple instances behind a load balancer to handle increased traffic.
  
- **Database Scaling:**  
  Use MongoDB replica sets and sharding for high availability and scalability.

- **Container Orchestration:**  
  Consider using Kubernetes or another orchestration tool for automated scaling and management.

- **Caching:**  
  Configure Redis for caching to improve performance and reduce load on the database.

---

## Security Considerations

- **HTTPS:**  
  Ensure all traffic to the backend is secured via HTTPS.
  
- **Secrets Management:**  
  Store sensitive environment variables securely (e.g., using a secrets manager).

- **Firewall and Access Controls:**  
  Configure firewalls and network policies to restrict access to the application and databases.

- **Regular Updates:**  
  Keep dependencies and server software updated to mitigate vulnerabilities.

---

## Troubleshooting

- **Connection Issues:**  
  Check logs for errors related to database or WebSocket connections.
  
- **Performance Issues:**  
  Monitor performance metrics via the provided endpoints and adjust resource allocation if necessary.
  
- **Error Notifications:**  
  Review Telegram notifications and logs to identify and resolve issues promptly.

---

## Additional Resources

- **API Documentation:**  
  Refer to the [API Documentation](api.md) for detailed endpoint information.
  
- **Error References:**  
  See [Error References](errors.md) for a comprehensive list of custom error types and their meanings.
  
- **Development Guide:**  
  The [Development Guide](development.md) provides details on code structure, testing, and local development practices.

---

