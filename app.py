import imaplib
import email
import os
import re
import csv
import io
import html
import time
from datetime import datetime, date, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime, getaddresses
from email.message import Message
from typing import Optional, Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Mail Explorer",
    page_icon="✉️",
    layout="wide",
    initial_sidebar_state="expanded",
)

FOLDERS = [
    ("📥", "Inbox", "INBOX"),
    ("📤", "Sent", "[Gmail]/Sent Mail"),
    ("📁", "All Mail", "[Gmail]/All Mail"),
    ("📝", "Drafts", "[Gmail]/Drafts"),
    ("🗑️", "Trash", "[Gmail]/Trash"),
    ("⚠️", "Spam", "[Gmail]/Spam"),
]
PAGE_SIZE_OPTIONS = [25, 50]

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root { --line:#e5e7eb; --muted:#6b7280; --ink:#111827; --accent:#4f46e5; }
html, body, [class*="css"] { font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; }
.stApp { background: #f7f8fa; color: var(--ink); }
.block-container { padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1500px; }
section[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--line); }
div[data-testid="stMetric"] { background: white; border: 1px solid var(--line); padding: 16px 18px; border-radius: 14px; }
div[data-testid="stMetricLabel"] { color: var(--muted); font-size: .82rem; }
div[data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 700; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
div.stButton > button, div.stDownloadButton > button { border-radius: 9px; font-weight: 600; }
div[data-testid="stTextInput"] input, div[data-testid="stSelectbox"] div, div[data-testid="stDateInput"] input { border-radius: 9px; }
.mail-card { background: white; border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin: 4px 0 12px; }
.small-muted { color: #6b7280; font-size: .85rem; }
.status-pill { display:inline-block; border-radius:999px; padding:4px 10px; font-size:.78rem; font-weight:600; }
</style>
""", unsafe_allow_html=True)

def decode_mime(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()

def decode_header_text(raw: bytes) -> str:
    return decode_mime(raw.decode("utf-8", errors="replace"))

def parse_addresses(value: Optional[str]) -> str:
    if not value:
        return ""
    decoded = decode_mime(value)
    parsed = getaddresses([decoded])
    return ", ".join((f"{name} <{addr}>" if name and addr else name or addr) for name, addr in parsed if name or addr)

def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt and dt.tzinfo is None:
            return dt
        return dt
    except Exception:
        return None

def safe_folder_name(connection: imaplib.IMAP4_SSL, desired: str) -> str:
    typ, data = connection.list()
    if typ != "OK" or not data:
        return desired
    names = []
    for row in data:
        if not row:
            continue
        try:
            # Mailbox name is the final quoted or unquoted token.
            line = row.decode("utf-8", errors="replace")
            match = re.search(r'"((?:[^"\\\\]|\\\\.)*)"\s*$', line)
            if match:
                names.append(match.group(1).replace(r'\"', '"').replace(r'\\', '\\'))
            else:
                names.append(line.rsplit(" ", 1)[-1])
        except Exception:
            continue
    if desired in names:
        return desired
    # Gmail sometimes exposes UTF-7 encoded mailbox names; exact desired names are still tried first.
    aliases = {
        "[Gmail]/Sent Mail": ("[Gmail]/Sent", "[Google Mail]/Sent Mail"),
        "[Gmail]/All Mail": ("[Google Mail]/All Mail",),
        "[Gmail]/Drafts": ("[Google Mail]/Drafts",),
        "[Gmail]/Trash": ("[Google Mail]/Trash",),
        "[Gmail]/Spam": ("[Google Mail]/Spam",),
    }
    for candidate in aliases.get(desired, ()):
        if candidate in names:
            return candidate
    return desired

def connect_imap(user: str, password: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=25)
    client.login(user, password)
    return client

def ensure_connection() -> imaplib.IMAP4_SSL:
    client = st.session_state.get("imap")
    if client is not None:
        try:
            client.noop()
            return client
        except Exception:
            try:
                client.logout()
            except Exception:
                pass
            st.session_state.imap = None
    user = st.session_state.get("email_user", "")
    password = st.session_state.get("email_pass", "")
    if not user or not password:
        raise RuntimeError("Enter your Google Workspace email and App Password in the sidebar.")
    client = connect_imap(user, password)
    st.session_state.imap = client
    return client

def logout():
    client = st.session_state.get("imap")
    if client:
        try:
            client.logout()
        except Exception:
            pass
    st.session_state.imap = None
    st.session_state.connected = False

def select_folder(folder: str):
    client = ensure_connection()
    actual = safe_folder_name(client, folder)
    typ, data = client.select(f'"{actual}"', readonly=True)
    if typ != "OK":
        # Retry unquoted for servers that reject quoted mailbox names.
        typ, data = client.select(actual, readonly=True)
    if typ != "OK":
        raise RuntimeError(f"Could not open mailbox: {folder}. Gmail may not expose this folder for this account.")
    return client, actual, int(data[0] or 0)

def folder_counts() -> dict:
    client = ensure_connection()
    counts = {}
    for _, label, folder in FOLDERS:
        try:
            _, _, count = select_folder(folder)
            counts[label] = count
        except Exception:
            counts[label] = None
    return counts

def imap_search_ids(client, criteria: list[str]) -> list[bytes]:
    typ, data = client.search(None, *criteria)
    if typ != "OK" or not data:
        return []
    return data[0].split() if data[0] else []

def fetch_header(client, msg_id: bytes) -> dict:
    # Python 3.14 imaplib expects string command arguments, not bytes.
    message_set = msg_id.decode("ascii") if isinstance(msg_id, bytes) else str(msg_id)
    typ, data = client.fetch(
        message_set,
        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC BCC DATE MESSAGE-ID)] FLAGS)"
    )
    if typ != "OK" or not data:
        return {}
    raw = b""
    for part in data:
        if isinstance(part, tuple) and len(part) > 1:
            raw += part[1]
    if not raw:
        return {}
    msg = email.message_from_bytes(raw)
    dt = parse_date(msg.get("Date"))
    return {
        "uid": msg_id.decode("ascii", errors="ignore"),
        "subject": decode_mime(msg.get("Subject")) or "(No subject)",
        "from": parse_addresses(msg.get("From")),
        "to": parse_addresses(msg.get("To")),
        "cc": parse_addresses(msg.get("Cc")),
        "bcc": parse_addresses(msg.get("Bcc")),
        "date": dt,
        "message_id": msg.get("Message-ID", ""),
        "flags": " ".join(str(x) for x in data if isinstance(x, bytes) and b"FLAGS" in x),
    }

def gmail_search_terms(query: str) -> list[str]:
    # Gmail IMAP search supports BODY, SUBJECT, FROM, TO, CC and BCC.
    # Search is performed server-side. Individual terms are quoted to preserve spaces.
    query = query.strip()
    if not query:
        return []
    # IMAP search terms are separate OR groups. Search each field, then union IDs.
    return []

def search_messages(client, query: str, date_mode: str, custom_start: Optional[date],
                    custom_end: Optional[date], preset: str) -> list[bytes]:
    criteria = []
    today = date.today()
    if date_mode == "Today":
        criteria += ["SINCE", today.strftime("%d-%b-%Y")]
        criteria += ["BEFORE", (today + timedelta(days=1)).strftime("%d-%b-%Y")]
    elif date_mode == "Last 7 Days":
        criteria += ["SINCE", (today - timedelta(days=6)).strftime("%d-%b-%Y")]
    elif date_mode == "Last 30 Days":
        criteria += ["SINCE", (today - timedelta(days=29)).strftime("%d-%b-%Y")]
    elif date_mode == "Custom" and custom_start:
        criteria += ["SINCE", custom_start.strftime("%d-%b-%Y")]
        if custom_end:
            criteria += ["BEFORE", (custom_end + timedelta(days=1)).strftime("%d-%b-%Y")]
    if preset == "Workforce / Leave Bulletins":
        criteria += ["SUBJECT", '"Daily Leave Update"']
    if not query:
        return imap_search_ids(client, criteria or ["ALL"])
    # Query across subject, sender, recipients, plus message body search. Search returns IDs only.
    base = list(criteria)
    terms = []
    for field in ("SUBJECT", "FROM", "TO", "CC", "BCC"):
        terms.append(base + [field, f'"{query}"'])
    found = set()
    for term in terms:
        try:
            found.update(imap_search_ids(client, term))
        except Exception:
            continue
    return sorted(found, key=lambda x: int(x), reverse=True)

def fetch_attachment_payload(part: Message) -> tuple[str, bytes, str]:
    filename = decode_mime(part.get_filename()) or "attachment"
    payload = part.get_payload(decode=True) or b""
    mime_type = part.get_content_type()
    return filename, payload, mime_type

def fetch_full_email(uid: str) -> tuple[Message, list[dict]]:
    client = ensure_connection()
    # Keep imaplib arguments as strings for Python 3.14 compatibility.
    typ, data = client.fetch(str(uid), "(BODY.PEEK[])")
    if typ != "OK" or not data:
        raise RuntimeError("Could not retrieve this email. The connection may have expired.")
    raw = b""
    for part in data:
        if isinstance(part, tuple) and len(part) > 1:
            raw += part[1]
    if not raw:
        raise RuntimeError("Email content was empty.")
    msg = email.message_from_bytes(raw)
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or filename:
            name, payload, mime_type = fetch_attachment_payload(part)
            attachments.append({"filename": name, "payload": payload, "mime_type": mime_type, "size": len(payload)})
    return msg, attachments

def extract_bodies(msg: Message) -> tuple[str, str]:
    plain, html_body = [], []
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = part.get_content_disposition()
            if disp == "attachment" or part.get_filename():
                continue
            content_type = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain":
                plain.append(decoded)
            elif content_type == "text/html":
                html_body.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_body.append(decoded)
            else:
                plain.append(decoded)
    return "\n\n".join(plain), "\n\n".join(html_body)

def safe_html_document(body: str) -> str:
    # Render inside a sandboxed component. Remove active content and remote frames/forms.
    body = re.sub(r"(?is)<(script|iframe|object|embed|form|base|meta)\b.*?>.*?</\1\s*>", "", body)
    body = re.sub(r"(?is)<(script|iframe|object|embed|form|base|meta)\b[^>]*/?>", "", body)
    body = re.sub(r'(?i)\s+on[a-z]+\s*=\s*(".*?"|\'.*?\'|[^\s>]+)', "", body)
    body = re.sub(r'(?i)(href|src)\s*=\s*(["\'])\s*javascript:.*?\2', r'\1="#"', body)
    return f"""<!doctype html><html><head><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline' https:; font-src https: data:;">
    <style>body{{font-family:Arial,sans-serif;color:#202124;line-height:1.55;margin:18px;overflow-wrap:anywhere}} img{{max-width:100%;height:auto}} table{{max-width:100%}} a{{color:#4f46e5}}</style>
    </head><body>{body}</body></html>"""

def format_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "Unknown date"
    try:
        return dt.astimezone().strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return dt.strftime("%d %b %Y, %I:%M %p")

# Session defaults
for key, value in {
    "imap": None, "connected": False, "email_user": os.getenv("SMTP_USER", ""),
    "email_pass": os.getenv("SMTP_PASS", ""), "selected_uid": None, "page": 0,
    "cached_rows": [], "cached_ids": [], "last_folder": None, "last_query_key": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = value

# Sidebar
with st.sidebar:
    st.markdown("## ✉️ Mail Explorer")
    st.caption("Google Workspace · IMAP")
    st.divider()
    st.markdown("### Connection")
    env_available = bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))
    if env_available:
        st.success("Credentials loaded from environment")
    else:
        st.caption("Credentials are used for IMAP mailbox access. They must also be valid for Gmail IMAP authentication.")
    with st.form("login_form", clear_on_submit=False):
        email_user = st.text_input("SMTP User (SMTP_USER)", value=st.session_state.email_user, placeholder="SMTP username")
        email_pass = st.text_input("SMTP Password (SMTP_PASS)", value=st.session_state.email_pass, type="password", placeholder="SMTP password")
        submitted = st.form_submit_button("Connect securely", use_container_width=True)
    if submitted:
        logout()
        st.session_state.email_user = email_user.strip()
        st.session_state.email_pass = email_pass.strip()
        try:
            client = connect_imap(st.session_state.email_user, st.session_state.email_pass)
            st.session_state.imap = client
            st.session_state.connected = True
            st.success("Connected to Gmail")
            st.rerun()
        except Exception as exc:
            st.session_state.connected = False
            st.error(f"Connection failed: {exc}")
    if st.session_state.imap is not None:
        st.markdown('<span class="status-pill" style="background:#dcfce7;color:#166534">● Connected</span>', unsafe_allow_html=True)
        if st.button("Disconnect", use_container_width=True):
            logout()
            st.rerun()
    else:
        st.markdown('<span class="status-pill" style="background:#f3f4f6;color:#6b7280">● Disconnected</span>', unsafe_allow_html=True)
    st.divider()
    st.markdown("### Mailboxes")
    folder_labels = [f"{emoji}  {label}" for emoji, label, _ in FOLDERS]
    label_to_folder = {f"{emoji}  {label}": folder for emoji, label, folder in FOLDERS}
    selected_label = st.radio("Select folder", folder_labels, label_visibility="collapsed", key="folder_choice")
    selected_folder = label_to_folder[selected_label]
    if st.session_state.imap is not None:
        try:
            counts = folder_counts()
            for _, label, _ in FOLDERS:
                count = counts.get(label)
                st.caption(f"{label}  ·  {count:,}" if isinstance(count, int) else f"{label}  ·  unavailable")
        except Exception:
            st.caption("Mailbox counts refresh when connected.")
    st.divider()
    st.caption("Security note: use an App Password and keep .env out of version control.")

# Main header
st.markdown("# Email Explorer")
st.markdown("Search, audit and export mailbox metadata without downloading the entire mailbox.")
if st.session_state.imap is None:
    st.info("Enter SMTP_USER and SMTP_PASS in the sidebar to begin. These values are used to authenticate to Gmail IMAP, so they must be valid for IMAP access as well. Enable IMAP and use a Google App Password if required by your account.")
    st.stop()

# Search controls
with st.container(border=True):
    st.markdown("#### Search & filters")
    col1, col2, col3 = st.columns([2.4, 1.15, 1.25])
    with col1:
        query = st.text_input("Search subject, sender or recipients", placeholder="e.g. leave update, payroll, someone@company.com")
    with col2:
        date_mode = st.selectbox("Date range", ["Any time", "Today", "Last 7 Days", "Last 30 Days", "Custom"])
    with col3:
        preset = st.selectbox("Quick filter", ["None", "Workforce / Leave Bulletins", "Has Attachments"])
    custom_start, custom_end = None, None
    if date_mode == "Custom":
        d1, d2 = st.columns(2)
        with d1:
            custom_start = st.date_input("Start date", value=date.today() - timedelta(days=30))
        with d2:
            custom_end = st.date_input("End date", value=date.today())
    p1, p2, p3 = st.columns([1, 1, 3])
    with p1:
        page_size = st.selectbox("Rows per page", PAGE_SIZE_OPTIONS, index=0)
    with p2:
        if st.button("Refresh", use_container_width=True):
            st.session_state.last_query_key = None
            try:
                st.session_state.imap.noop()
            except Exception:
                st.session_state.imap = None
                st.session_state.imap = ensure_connection()
            st.rerun()
    with p3:
        st.caption("Metadata is fetched on demand. Full message bodies and attachments load only when opened.")

query_key = (selected_folder, query.strip(), date_mode, custom_start, custom_end, preset)
try:
    client, actual_folder, folder_count = select_folder(selected_folder)
    st.session_state.connected = True
    if st.session_state.last_query_key != query_key:
        with st.spinner("Searching mailbox index…"):
            ids = search_messages(client, query.strip(), date_mode, custom_start, custom_end, preset)
            # IMAP SEARCH returns identifiers only. Keep the identifier list, but fetch headers
            # strictly for the visible page below, never for the entire matching mailbox.
            ids = sorted(ids, key=lambda x: int(x), reverse=True)
            if preset == "Has Attachments":
                try:
                    typ, data = client.search(None, 'X-GM-RAW', '"has:attachment"')
                    if typ == "OK" and data and data[0]:
                        attachment_ids = set(data[0].split())
                        ids = [uid for uid in ids if uid in attachment_ids]
                except Exception:
                    st.warning("This Gmail server did not support attachment search. Try a standard search or the Workforce preset.")
            st.session_state.cached_ids = ids
            st.session_state.cached_rows = []
        st.session_state.last_query_key = query_key
        st.session_state.page = 0
        st.session_state.selected_uid = None
except Exception as exc:
    st.error(f"Mailbox connection issue: {exc}")
    st.caption("The app will reconnect on the next action. Check IMAP access and your App Password.")
    st.stop()

all_ids = st.session_state.cached_ids
# IMAP date criteria are applied server-side. Count and paginate identifiers, then fetch
# only the visible page's headers. Full bodies are fetched only after explicit selection.
total_matches = len(all_ids)

# Search metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Folder messages", f"{folder_count:,}")
col2.metric("Search matches", f"{total_matches:,}")
col3.metric("Page size", str(page_size))
col4.metric("Connection", "Secure IMAP" if st.session_state.imap else "Disconnected")

st.divider()
total_pages = max(1, (total_matches + page_size - 1) // page_size)
st.session_state.page = min(st.session_state.page, total_pages - 1)
start_idx = st.session_state.page * page_size
page_ids = all_ids[start_idx:start_idx + page_size]
page_rows = []
with st.spinner("Loading current page headers…"):
    for uid in page_ids:
        row = fetch_header(client, uid)
        if row:
            page_rows.append(row)
st.session_state.cached_rows = page_rows
rows = page_rows

left, right = st.columns([2.4, 1])
with left:
    st.markdown(f"### {selected_label.split('  ', 1)[-1]}  <span class='small-muted'>· {total_matches:,} results</span>", unsafe_allow_html=True)
with right:
    if total_matches:
        if st.button("Prepare full CSV export", use_container_width=True):
            export_rows = []
            progress = st.progress(0, text="Collecting metadata for export…")
            for idx, uid in enumerate(all_ids):
                item = fetch_header(client, uid)
                if item:
                    export_rows.append({
                        "Subject": item["subject"], "Sender": item["from"], "To": item["to"],
                        "Cc": item["cc"], "Bcc": item["bcc"], "Date": format_dt(item["date"]), "UID": item["uid"]
                    })
                if (idx + 1) % 25 == 0 or idx + 1 == len(all_ids):
                    progress.progress((idx + 1) / len(all_ids), text=f"Export metadata: {idx + 1:,} / {len(all_ids):,}")
            export_df = pd.DataFrame(export_rows)
            st.session_state.export_csv = export_df.to_csv(index=False).encode("utf-8-sig")
            progress.empty()
        if st.session_state.get("export_csv"):
            st.download_button("Download CSV", data=st.session_state.export_csv, file_name="email_search_results.csv",
                               mime="text/csv", use_container_width=True)

if not page_rows:
    st.info("No messages match the current search and filters.")
else:
    for i, row in enumerate(page_rows):
        with st.container(border=True):
            c1, c2, c3 = st.columns([4.2, 2.3, 1.3])
            with c1:
                st.markdown(f"**{html.escape(row['subject'])}**")
                st.caption(row["from"] or "Unknown sender")
                st.caption("To: " + (row["to"] or "—"))
            with c2:
                st.caption(format_dt(row["date"]))
                if row["cc"]:
                    st.caption("Cc: " + row["cc"])
                if row["bcc"]:
                    st.caption("Bcc: " + row["bcc"])
            with c3:
                if st.button("Open email", key=f"open_{row['uid']}", use_container_width=True):
                    st.session_state.selected_uid = row["uid"]
                    st.rerun()

    nav1, nav2, nav3, nav4 = st.columns([1, 1, 2, 1])
    with nav1:
        if st.button("← Previous", disabled=st.session_state.page <= 0, use_container_width=True):
            st.session_state.page -= 1
            st.rerun()
    with nav2:
        if st.button("Next →", disabled=st.session_state.page >= total_pages - 1, use_container_width=True):
            st.session_state.page += 1
            st.rerun()
    with nav3:
        st.caption(f"Page {st.session_state.page + 1} of {total_pages} · Showing {start_idx + 1}–{min(start_idx + page_size, total_matches)}")
    with nav4:
        if st.button("Clear selection", use_container_width=True):
            st.session_state.selected_uid = None
            st.rerun()

# Selected email detail
selected_uid = st.session_state.selected_uid
if selected_uid:
    selected_row = next((r for r in rows if r["uid"] == selected_uid), None)
    st.divider()
    st.markdown("## Email detail")
    try:
        msg, attachments = fetch_full_email(selected_uid)
        subject = decode_mime(msg.get("Subject")) or "(No subject)"
        from_addr = parse_addresses(msg.get("From"))
        to_addr = parse_addresses(msg.get("To"))
        cc_addr = parse_addresses(msg.get("Cc"))
        bcc_addr = parse_addresses(msg.get("Bcc"))
        date_value = parse_date(msg.get("Date"))
        st.markdown('<div class="mail-card">', unsafe_allow_html=True)
        st.markdown(f"### {html.escape(subject)}")
        st.markdown(f"**From:** {html.escape(from_addr or '—')}")
        st.markdown(f"**To:** {html.escape(to_addr or '—')}")
        st.markdown(f"**Cc:** {html.escape(cc_addr or '—')}")
        st.markdown(f"**Bcc:** {html.escape(bcc_addr or '—')}")
        st.markdown(f"**Date:** {html.escape(format_dt(date_value))}")
        st.markdown("</div>", unsafe_allow_html=True)
        plain_body, html_body = extract_bodies(msg)
        body_tab, raw_tab = st.tabs(["Rendered message", "Plain text"])
        with body_tab:
            if html_body:
                components.html(safe_html_document(html_body), height=560, scrolling=True)
            elif plain_body:
                st.text(plain_body)
            else:
                st.caption("This message has no displayable body.")
        with raw_tab:
            st.text_area("Plain text body", value=plain_body or "(No plain text part)", height=360, disabled=True)
        st.markdown("### Attachments")
        if not attachments:
            st.caption("No attachments detected.")
        else:
            for idx, attachment in enumerate(attachments):
                a1, a2, a3 = st.columns([3.5, 1.1, 1.5])
                with a1:
                    st.markdown(f"**{html.escape(attachment['filename'])}**")
                    st.caption(attachment["mime_type"])
                with a2:
                    size = attachment["size"]
                    st.caption(f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B")
                with a3:
                    st.download_button(
                        "Download", data=attachment["payload"], file_name=attachment["filename"],
                        mime=attachment["mime_type"] or "application/octet-stream",
                        key=f"download_{selected_uid}_{idx}", use_container_width=True
                    )
    except Exception as exc:
        st.error(f"Could not open email: {exc}")
        if st.button("Reconnect and retry"):
            st.session_state.imap = None
            ensure_connection()
            st.rerun()

st.caption("Mail Explorer reads mailbox data only. It does not send, delete, archive or modify messages.")
