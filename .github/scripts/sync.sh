#!/usr/bin/env bash
# GitHub caches are readable by pull requests. Cache only encrypted tokens.
set -euo pipefail
if [[ -z ${GARMIN_SESSION_KEY:-} && -z ${GARMIN_TOKENS:-} ]]; then
    exec python sync_igpsport_to_garmin.py
fi

umask 077
mkdir -p garmin_session
tokens=garmin_session/garmin_tokens.json
archive=garmin_session/session.gpg

session_gpg() {
    # Bind the passphrase to the account, region and bootstrap secret. Hashing
    # also turns a multiline JSON secret into one line for GPG's passphrase FD.
    printf '%s\0' "${GARMIN_SESSION_KEY:-}" "${GARMIN_TOKENS:-}" \
        "${GARMIN_EMAIL:-}" "${GARMIN_DOMAIN:-garmin.com}" |
        sha256sum | gpg --batch --yes --no-symkey-cache --pinentry-mode loopback --passphrase-fd 0 "$@"
}

previous=''
if [[ -s "$archive" ]]; then
    if session_gpg --output "$tokens.tmp" --decrypt "$archive"; then
        mv "$tokens.tmp" "$tokens"
        previous=$(sha256sum "$tokens")
    else
        rm -f "$tokens.tmp"
        echo 'Saved Garmin session could not be opened. Using the configured login.'
    fi
fi

status=0
python sync_igpsport_to_garmin.py || status=$?

# A refresh token may rotate before a later activity fails. Keep the new
# session even then, without marking that batch as successfully synced.
if [[ -s "$tokens" && "$(sha256sum "$tokens")" != "$previous" ]]; then
    session_gpg --cipher-algo AES256 --output "$archive" --symmetric "$tokens"
    echo 'session_changed=true' >> "$GITHUB_OUTPUT"
fi
exit "$status"
