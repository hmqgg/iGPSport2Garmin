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
| `GARMIN_EMAIL` | Your Garmin Connect email | `user@example.com` |
| `GARMIN_PASSWORD` | Your Garmin Connect password | `your_garmin_password` |
| `GARMIN_DOMAIN` | Garmin domain (optional) | `garmin.com` (or `garmin.cn` for Garmin China users) |

### Step 3: Enable GitHub Actions

1. Go to the **Actions** tab in your forked repository
2. Click **I understand my workflows, go ahead and enable them**
3. The sync will now run automatically every 20 minutes by default
    - You can adjust the frequency in the workflow file if needed with the line `cron: '*/20 * * * *'`
4. You can also trigger it manually by going to **Actions** → **Sync iGPSport to Garmin** → **Run workflow**

### GitHub Actions Workflow Details

The repository includes a pre-configured workflow (`.github/workflows/sync.yml`) that:

- Runs automatically every 20 minutes
- Can be triggered manually
- Syncs your iGPSport activities to Garmin Connect
- Commits updated sync dates back to the repository

**You don't need to create this file - it's already included in the repository!**

### First Run

On the first run, the script will:

1. Create a `last_sync_date.json` file to track sync progress
2. Sync activities from the last 30 days
3. Create a `garmin_session` directory to store authentication data

### Subsequent Runs

The script will only sync new activities since the last successful sync, making it efficient for regular automated runs.

## How It Works

1. **Authentication**:
   - Logs into both iGPSport and Garmin Connect
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
   - Updates sync tracking data

## Configuration Files

- **`last_sync_date.json`**: Tracks the last successful sync date
- **`.github/workflows/sync.yaml`**: GitHub Actions workflow configuration
- **`garmin_session/`**: Directory containing Garmin authentication session data, ignored by Git
- **`requirements.txt`**: Python dependencies

## Troubleshooting

### Common Issues

1. **Authentication Failures**:
   - Verify your credentials are correct
   - Check if you have 2FA enabled (failed if enabled)
   - Ensure environment variables are set correctly

2. **No Activities Found**:
   - Check if you have activities in iGPSport newer than the last sync date
   - Verify your iGPSport activities have FIT files available

3. **Upload Failures**:
   - Garmin Connect may have rate limits; the script includes retry logic
   - Check Garmin Connect service status if uploads consistently fail

4. **Rate Limiting**:
   - The script includes built-in delays and retry mechanisms
   - If you encounter persistent rate limiting, consider reducing sync frequency

## Security Considerations

- Never commit credentials to the repository
- Use environment variables or secrets management
- The `garmin_session` directory contains sensitive authentication data
- Consider using app-specific passwords if available

## Dependencies

- **requests**: HTTP client for API calls
- **garth**: Garmin Connect API client
- **python-dateutil**: Date/time parsing utilities

## Disclaimer

This tool is for personal use only. Make sure you comply with both iGPSport and Garmin Connect terms of service. The authors are not responsible for any data loss or account issues that may occur from using this script.
