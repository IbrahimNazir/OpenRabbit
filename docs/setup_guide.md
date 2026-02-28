# OpenRabbit Setup & Configuration Guide

To get OpenRabbit running, you need to register a GitHub App and configure your `.env` file.

## 1. Local Environment Fix (Poetry)
Since you just installed Poetry, run this in your current terminal to make it work immediately:
```powershell
$env:Path += ";C:\Users\IBRAHIM\AppData\Roaming\Python\Scripts"
# Test it:
poetry --version
```

---

## 2. Generate Secrets
First, create your `.env` file:
```powershell
cp .env.example .env
```
Generate a secure **Webhook Secret**:
```powershell
# In terminal:
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste this into `GITHUB_WEBHOOK_SECRET` in your `.env`.

---

## 3. GitHub App Registration
1.  Go to [GitHub Settings > Developer Settings > GitHub Apps > New GitHub App](https://github.com/settings/apps/new).
2.  **App Name**: OpenRabbit-{YourName} (must be unique).
3.  **Homepage URL**: You can use `https://github.com/{your-username}/OpenRabbit`.
4.  **Webhook**:
    *   **Webhook URL**: Go to [smee.io](https://smee.io) and click "Start a new channel". Copy the URL provided (e.g., `https://smee.io/abc123xyz`) and paste it as the `GITHUB_WEBHOOK_SECRET` in `.env`.
    *   **Webhook Secret**: Use the hex string you generated above.
5.  **Permissions** (Critical):
    *   **Contents**: Read & write (to read code and push fixes).
    *   **Pull Requests**: Read & write (to post reviews).
    *   **Metadata**: Read-only (required).
6.  **Events**:
    *   Check: `Installation`, `Pull request`, `Pull request review comment`.
7.  **Create App**:
    *   After creating, copy the **App ID** into `GITHUB_APP_ID` in `.env`.
    *   **Private Key**: Scroll down and click **"Generate a private key"**. Download the `.pem` file, rename it to `private-key.pem`, and place it in the project root (`d:\OpenRabbit\`).

---

## 4. External Services
1.  **Anthropic API Key**: Get it from the [Anthropic Console](https://console.anthropic.com/).
2.  **Docker Infra**:
    *   The `DATABASE_URL`, `REDIS_URL`, and `QDRANT_URL` are pre-configured to work with the provided `docker-compose.yml`. Just run:
    ```powershell
    docker compose up -d
    ```

---

## 5. Summary Table
| Key | Where to find |
|-----|---------------|
| `GITHUB_APP_ID` | GitHub App "General" page |
| `GITHUB_APP_PRIVATE_KEY_PATH` | The downloaded `.pem` file |
| `GITHUB_WEBHOOK_SECRET` | Generate yourself (or in GitHub App settings) |
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/) |
| `SMEE_URL` | From [smee.io](https://smee.io) |
| `ADMIN_SECRET` | Any secure string you choose |
