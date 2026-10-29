# Gmail / Google Workspace Email Explorer

## Run locally

1. Install Python 3.10 or newer.
2. Create and activate a virtual environment.
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and set `SMTP_USER` and `SMTP_PASS`, or enter credentials in the sidebar.
5. Start the app:

   ```bash
   streamlit run app.py
   ```

## Gmail setup

- Enable IMAP in Gmail settings if the account exposes that option.
- Prefer a Google App Password when 2-Step Verification is enabled.
- Some Google Workspace administrators disable App Passwords or IMAP. In that case, ask your administrator or use OAuth-based Gmail API access.
- Never commit `.env` to Git. Add `.env` to `.gitignore`.

## Notes and limitations

- IMAP folder labels can differ by account and language. The app attempts common Gmail folder names.
- Search is performed through IMAP search fields for subject, sender, and recipients.
- Gmail IMAP search returns matching message identifiers. The app fetches header metadata for matching messages and fetches full content only when a message is opened.
- The "Has Attachments" preset uses Gmail's `X-GM-RAW` extension when supported by the server.
- HTML messages are rendered in a Streamlit HTML component with basic active-content filtering and a restrictive content security policy. Treat all email content as untrusted.
- CSV export contains message metadata, not bodies or attachments.

## Production hardening recommendations

For deployment with sensitive mailboxes, use OAuth 2.0 rather than storing an account password, add an explicit session lock/timeout, protect the app behind authenticated access, and avoid shared multi-user sessions. Consider adding UIDVALIDITY tracking and a dedicated background worker if mailbox volumes and concurrent use grow.
