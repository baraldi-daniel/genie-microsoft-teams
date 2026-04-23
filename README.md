Databricks Genie via MS Teams (Cloud-Agnostic & Identity-Aware)
This repository demonstrates a robust, enterprise-grade integration of Databricks Genie into Microsoft Teams. The architecture is designed to be cloud-agnostic, allowing the Databricks workspace to reside on Google Cloud Platform (GCP), Microsoft Azure, or Amazon Web Services (AWS).

A key highlight of this implementation is its Identity-Aware security model. By leveraging an App Connection rather than service principal tokens, the integration natively respects user-level data governance and Unity Catalog permissions.

🏗️ Architecture & Data Flow
The communication stack is divided into four main layers:

Microsoft Teams (Frontend): The conversational interface where users interact with the AI agent.

Azure Bot Service (Router): Acts as the connector, routing messages from the Teams channel to our middleware.

Azure Web App (Orchestrator): The core engine of the integration. This middleware handles:

Message parsing and session management.

User Authentication: Managing the App Connection (SSO/OAuth) to ensure seamless identity passthrough.

Managing REST API calls to the Genie endpoint on behalf of the logged-in user.

Formatting the JSON responses from Databricks into rich Teams cards.

Databricks Genie (AI Backend): The Genie Space receives natural language prompts, translates them into optimized SQL, executes them against your data, and returns insights. Because identity is passed through, Unity Catalog strictly enforces the user's data access permissions.

📋 Prerequisites
Databricks: A workspace on any major cloud (GCP, Azure, or AWS) with a configured Genie Space.

Azure: * An Azure Bot resource.

An Azure Web App (App Service) to host the middleware code.

Teams: Administrator access to the Teams Admin Center to sideload and manage custom applications.

Authentication: A configured Databricks App Connection (OAuth) to handle direct user login and preserve individual access rights.

🚀 Deployment Steps
1. Middleware Setup (Web App)
Deploy your bot logic to the Azure Web App.

Configure your Databricks App Connection details within the Web App environment to handle user SSO.

Set the Messaging Endpoint in your Azure Bot resource to point to your Web App's URL (e.g., https://your-app.azurewebsites.net/api/messages).

2. Databricks Integration
Configure your Genie Space.

Ensure your Unity Catalog governance rules are properly mapped to your users, as the bot will strictly respect their individual access levels.

The integration remains identical regardless of the underlying cloud provider (GCP/Azure/AWS), as it communicates via the standard Databricks REST API.

3. Teams App Packaging
Update your manifest.json with the Bot ID.

Crucial Step: Select the three files (manifest.json, color.png, outline.png) and zip them directly. Do not zip the parent folder.

⚠️ Troubleshooting
"File is missing from the app package" Error:
If the Teams Admin Center rejects your upload, check the .zip structure. The manifest.json must be at the root of the archive. If your zip contains a folder which then contains the files, the upload will fail.
