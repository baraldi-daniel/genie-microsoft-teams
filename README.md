# 🧞‍♂️ Databricks Genie via MS Teams (Cloud-Agnostic & Identity-Aware)

This repository demonstrates a robust, enterprise-grade integration of **Databricks Genie** into **Microsoft Teams**. The architecture is designed to be **cloud-agnostic**, allowing the Databricks workspace to reside on **Google Cloud Platform (GCP)**, **Microsoft Azure**, or **Amazon Web Services (AWS)**.

A key highlight of this implementation is its **Identity-Aware** security model. By leveraging an App Connection rather than service principal tokens, the integration natively respects user-level data governance and Unity Catalog permissions.

## 🏗️ Architecture & Data Flow

The communication stack is divided into four main layers:

1.  **Microsoft Teams (Frontend):** The conversational interface where users interact with the AI agent.
2.  **Azure Bot Service (Router):** Acts as the connector, routing messages from the Teams channel to our middleware.
3.  **Azure Web App (Orchestrator):** The core engine of the integration. This middleware handles message parsing, session management, and **User Authentication** via SSO/OAuth to ensure seamless identity passthrough.
4.  **Databricks Genie (AI Backend):** Receives natural language prompts, translates them into SQL, and returns insights. Unity Catalog strictly enforces the user's data access permissions.

## 📋 Prerequisites

* **Databricks:** Workspace on any major cloud with a configured **Genie Space**.
* **Azure:** An Azure Bot resource and an Azure Web App to host the middleware.
* **Teams:** Admin access to sideload custom applications.
* **Authentication:** A configured **Databricks App Connection** (OAuth) to handle direct user login.

## 🚀 Deployment Steps

### 1. Middleware Setup (Web App)
* Deploy your bot logic to the Azure Web App.
* Configure environment variables.
* Set the **Messaging Endpoint** in your Azure Bot to point to your Web App's URL.

### 2. Teams App Packaging
* Update `manifest.json` with your Bot ID.
* **Crucial:** Zip the files (`manifest.json`, `color.png`, `outline.png`) directly from the root. Do not zip the parent folder.

## ⚠️ Troubleshooting
**"File is missing from the app package" Error:**
Ensure the `manifest.json` is at the root of the `.zip` archive. If the zip contains a nested folder, the Teams Admin Center will reject the upload.
