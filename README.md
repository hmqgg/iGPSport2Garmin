# iGPSport to Garmin Sync

> This README is an AI Generated Content.

A Python script that automatically syncs cycling activities from iGPSport to Garmin Connect. This tool downloads FIT files from your iGPSport account and uploads them to Garmin Connect, with intelligent duplicate detection to avoid overlapping activities.

## Features

- 🔄 **Automatic Sync**: Periodically syncs new activities from iGPSport to Garmin Connect
- 🚫 **Duplicate Prevention**: Smart overlap detection prevents duplicate activities
- 💾 **Session Management**: Maintains authentication sessions to reduce login frequency
- 🔁 **Retry Mechanism**: Robust error handling with exponential backoff
- 📊 **Progress Tracking**: Tracks last sync date to only process new activities
- 🏃‍♂️ **GitHub Actions Ready**: Designed to run automatically in CI/CD pipelines

## Quick Start

The easiest way to use this tool is to fork this repository and use the pre-configured GitHub Actions:

1. **Fork this repository** to your GitHub account
2. **Configure GitHub Secrets** (see [GitHub Actions Setup](#github-actions-setup) below)
3. **Enable GitHub Actions** in your forked repository
4. **Done!** The sync will run automatically every 20 minutes by default, or you can trigger it manually by Run workflow in the Actions tab.

## GitHub Actions Setup

### Step 1: Fork This Repository

1. Click the "Fork" button at the top of this repository
2. Choose your GitHub account as the destination
3. Delete (or modify) the `last_sync_date.json` file in your forked repository (this will be created automatically on the first run)

### Step 2: Configure GitHub Secrets

In your forked repository:

1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add the following secrets:

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `IGPSPORT_USERNAME` | Your iGPSport username/email | `your_username` |
| `IGPSPORT_PASSWORD` | Your iGPSport password | `your_password` |
| `IGPSPORT_REGION` | Your iGPSport account region (optional, default by `cn`) | `global` (or `cn` for iGPSport China users) |
| `GARMIN_EMAIL` | Garmin Connect email for password login | `user@example.com` |
| `GARMIN_PASSWORD` | Garmin Connect password for password login | `your_garmin_password` |
| `GARMIN_DOMAIN` | Garmin domain (optional) | `garmin.com` (or `garmin.cn` for Garmin China users) |
| `GARMIN_TOKENS` | Renewable Garmin session from the local login below. This also enables encrypted session caching. | Contents of `garmin_tokens.json` |
| `GARMIN_SESSION_KEY` | Optional for password-only login. Set a random secret to keep refreshed sessions between runners. | Generate with `openssl rand -base64 32` |

#### Set up a Garmin session for Actions

If Garmin asks for a verification code or the runner gets HTTP 429, wait for the rate limit to clear. Then sign in once on your own computer. From the repository directory with Python 3.12 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python sync_igpsport_to_garmin.py --garmin-login
```

For Garmin China, set `GARMIN_DOMAIN=garmin.cn` for this command and in Actions. The command prompts for your Garmin email, password and any verification code. This login-only command saves a session to `garmin_session/garmin_tokens.json`.

Set the **`GARMIN_TOKENS`** repository secret to the full JSON file contents. With GitHub CLI signed in to your fork, you can do this without printing the tokens:

```sh
gh secret set GARMIN_TOKENS < garmin_session/garmin_tokens.json
```

Actions can use this secret without `GARMIN_EMAIL` or `GARMIN_PASSWORD`. Refreshed tokens are encrypted before caching. Replacing the bootstrap secret starts a new session. Old Garth session files cannot be reused.

For password-only setups, the existing email and password secrets still work when Garmin accepts a runner login. Add `GARMIN_SESSION_KEY` to enable encrypted caching, or the next fresh runner with new activities will need to sign in again.

### Step 3: Enable GitHub Actions

1. Go to the **Actions** tab in your forked repository
2. Click **I understand my workflows, go ahead and enable them**
3. The sync will now run automatically every 20 minutes by default
    - You can adjust the frequency in the workflow file if needed with the line `cron: '*/20 * * * *'`
4. You can also trigger it manually by going to **Actions** → **Sync iGPSport to Garmin** → **Run workflow**

### GitHub Actions Workflow Details

The repository includes a pre-configured workflow (`.github/workflows/sync.yaml`) that:

- Runs automatically every 20 minutes
- Can be triggered manually
- Syncs your iGPSport activities to Garmin Connect
- Commits updated sync dates back to the repository

**You don't need to create this file - it's already included in the repository!**

### First Run

On the first run, the script will:

1. Read `last_sync_date.json` if present, or look back 30 days on a new setup
2. Check candidate activities against Garmin and upload missing FIT files
3. Save sync progress after all candidates are accounted for

### Subsequent Runs

The script will only sync new activities since the last successful sync, making it efficient for regular automated runs.

## How It Works

1. **Authentication**:
   - Checks iGPSport first and opens a Garmin session only when there are candidates
   - Maintains session data for efficiency

2. **Activity Discovery**:
   - Fetches recent activities from iGPSport
   - Compares with existing Garmin activities to detect duplicates

3. **Smart Filtering**:
   - Only processes activities newer than the last sync date
   - Checks for time overlap with existing Garmin activities (±5 minutes buffer)

4. **Sync Process**:
   - Downloads FIT files from iGPSport
   - Uploads them to Garmin Connect
   - Saves the latest confirmed date after the batch completes. A fingerprint of the rides on that day lets idle runs skip Garmin while still picking up a late ride from the same day.

## Configuration Files

- **`last_sync_date.json`**: Tracks the last successful sync date
- **`.github/workflows/sync.yaml`**: GitHub Actions workflow configuration
- **`garmin_session/`**: Directory containing Garmin authentication session data, ignored by Git
- **`requirements.txt`**: Python dependencies

## Troubleshooting

### Common Issues

1. **Authentication Failures**:
   - Verify your credentials are correct
   - For MFA or a rejected session, repeat the local Garmin login above and replace the `GARMIN_TOKENS` secret
   - Ensure environment variables are set correctly

2. **No Activities Found**:
   - Check if you have activities in iGPSport newer than the last sync date
   - Verify your iGPSport activities have FIT files available

3. **Upload Failures**:
   - Garmin Connect may have rate limits; the script includes retry logic
   - Check Garmin Connect service status if uploads consistently fail

4. **Rate Limiting**:
   - HTTP 429 stops the run and leaves the sync date intact. Wait before running again.
   - If the runner cannot sign in, use the local session setup above.

## Security Considerations

- Never commit credentials to the repository
- Use environment variables or secrets management
- The `garmin_session` directory contains sensitive authentication data
- Consider using app-specific passwords if available

## Dependencies

- **requests**: HTTP client for API calls
- **garminconnect**: Garmin Connect API client and renewable session storage
- **python-dateutil**: Date/time parsing utilities

## Disclaimer

This tool is for personal use only. Make sure you comply with both iGPSport and Garmin Connect terms of service. The authors are not responsible for any data loss or account issues that may occur from using this script.
