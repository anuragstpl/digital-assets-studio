# Security

## Reporting a vulnerability

Please open a [private security advisory](https://github.com/anuragstpl/digital-assets-studio/security/advisories/new)
rather than a public issue. You should get a first response within a week.

## How this app handles your secrets

Worth knowing before you trust it with publishing credentials.

- **API keys go to the OS credential store** — Windows Credential Manager, macOS
  Keychain, Secret Service on Linux — through `keyring`. They are never written to
  `settings.json`, which holds only which provider serves which role.
- **If no keychain exists**, keys fall back to an obfuscated file in the workspace.
  That is encoding, not encryption, and the app says so plainly in Settings. On a
  shared machine, install a keyring backend before saving anything sensitive.
- **OAuth**: the suite never sees or stores your Google password. Sign-in happens in
  your own browser against a loopback address; only the refresh token is kept.
- **The KDP browser step** drives a visible browser with a persistent profile. You
  sign in yourself. The suite never types credentials and never reads them.
- **Nothing is sent anywhere except the providers you configure.** There is no
  telemetry, no analytics and no phone-home.

## What to keep out of the repository

`.gitignore` already excludes `.env`, `*.p8`, service-account JSON and
`client_secret*.json`. If you fork this, check before your first push.
