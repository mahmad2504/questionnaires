"""Local questionnaire server.

Run: python app.py
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import markdown
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, abort, redirect, render_template, request, send_file, session, url_for
from markupsafe import Markup
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
QUESTIONNAIRES = ROOT / "questionnaires"
RESPONSES = ROOT / "responses"
RESPONDENT_LISTS = ROOT / "respondent-lists"
AUTH_PATH = ROOT / "auth.json"
ACCOUNTS_PATH = ROOT / "accounts.json"
ALLOWED_PATH = ROOT / "allowed.txt"
ADMINS_PATH = ROOT / "admins.txt"
RESPONDENT_LIST_INDEX = RESPONDENT_LISTS / "index.json"
MAX_RESPONDENT_LIST_NAME = 80
LISTS_DIR = ROOT / "respondent-lists"
LISTS_INDEX = LISTS_DIR / "index.json"
MAX_LIST_NAME_LENGTH = 80
MIN_PASSWORD_LENGTH = 8
_store_lock = threading.RLock()

DEFAULT_STATUSES = ["Done", "In progress", "Blocked"]
DONE_STATUSES = {"done", "completed"}
COMPLETED_STATUS = "Completed"
DEFAULT_LABEL = "Responses"
IMAGE_UPLOADS_ENABLED = False
MAX_FILES = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_CONTENT_LENGTH = MAX_FILES * MAX_FILE_BYTES + 2_000_000
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_LINKS = 20
MAX_LINK_LENGTH = 2000
MAX_SERVICES = 40
MAX_SERVICE_LENGTH = 120
SLUG_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_PERSON_RE = re.compile(r"^(?P<name>[^<>]+?)\s*<(?P<email>[^<>\s]+)>\s*$")
SCREENSHOT_RE = re.compile(r"^screenshot-\d+\.(png|jpg|jpeg|gif|webp)$")
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{n}" for n in range(1, 10)},
    *{f"LPT{n}" for n in range(1, 10)},
}
SIGNATURES = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF8",
    ".webp": b"RIFF",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["TEMPLATES_AUTO_RELOAD"] = True


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_auth() -> dict:
    with _store_lock:
        data: dict = {}
        if AUTH_PATH.is_file():
            try:
                loaded = json.loads(AUTH_PATH.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                loaded = {}
            if isinstance(loaded, dict):
                data = loaded
        changed = False
        secret = data.get("secret_key")
        if not isinstance(secret, str) or not secret:
            data["secret_key"] = secrets.token_hex(32)
            changed = True
        if not isinstance(data.get("default_password"), str):
            data["default_password"] = "welcome"
            changed = True
        if changed or not AUTH_PATH.is_file():
            write_json(AUTH_PATH, data)
        return data


def ensure_secret() -> None:
    if app.secret_key:
        return
    app.secret_key = load_auth()["secret_key"]


def load_accounts() -> dict:
    with _store_lock:
        if not ACCOUNTS_PATH.is_file():
            return {}
        try:
            loaded = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return loaded


def save_accounts(accounts: dict) -> None:
    with _store_lock:
        write_json(ACCOUNTS_PATH, accounts)


def find_account(email: str) -> dict | None:
    record = load_accounts().get(normalize_email(email))
    if not isinstance(record, dict):
        return None
    stored = record.get("email")
    hashed = record.get("password_hash")
    name = record.get("name")
    if not isinstance(stored, str) or not stored:
        return None
    if not isinstance(hashed, str) or not hashed:
        return None
    if not isinstance(name, str):
        name = ""
    return {"email": stored, "name": name.strip(), "password_hash": hashed}


def create_account(display_email: str, password: str) -> None:
    key = normalize_email(display_email)
    person = allowed_person(display_email)
    record = {
        "email": display_email.strip(),
        "name": person["name"] if person else "",
        "password_hash": generate_password_hash(password, method="pbkdf2:sha256"),
    }
    with _store_lock:
        accounts = load_accounts()
        if key in accounts:
            raise FileExistsError(key)
        accounts[key] = record
        save_accounts(accounts)


def password_matches(stored_hash: str, password: str) -> bool:
    try:
        return check_password_hash(stored_hash, password)
    except (TypeError, ValueError):
        return False


def starter_password() -> str:
    value = load_auth().get("default_password")
    if not isinstance(value, str):
        return ""
    return value


def same_text(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def starter_password_matches(password: str) -> bool:
    expected = starter_password()
    if not expected:
        return False
    return same_text(password, expected)


def signed_in_email() -> str | None:
    email = session.get("email")
    if isinstance(email, str) and email:
        return email
    return None


def signed_in_name() -> str:
    email = signed_in_email()
    if not email:
        return ""
    person = allowed_person(email)
    if person is None:
        return ""
    return person["name"]


def safe_next(value: str | None) -> str:
    if not isinstance(value, str):
        return url_for("home")
    candidate = value.strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return url_for("home")
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return url_for("home")
    if parsed.path in {"/login", "/logout", "/account/password"}:
        return url_for("home")
    return candidate


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def load_email_file(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    emails: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = normalize_email(line)
        if EMAIL_RE.fullmatch(key):
            emails.add(key)
    return emails


def parse_allowed_line(line: str) -> tuple[str, str] | None:
    angled = ALLOWED_PERSON_RE.fullmatch(line.strip())
    if angled:
        name = " ".join(angled.group("name").split())
        email = angled.group("email").strip()
        if name and len(name) <= 200 and EMAIL_RE.fullmatch(email):
            return name, email
        return None
    if EMAIL_RE.fullmatch(line):
        return "", line
    return None


SEALED_PREFIX = "enc:v1"


class AllowListUnreadable(Exception):
    pass


class SealedFileUnreadable(Exception):
    pass


class ListLibraryUnreadable(Exception):
    pass


def seal_fernet(purpose: bytes) -> Fernet:
    secret = load_auth()["secret_key"]
    digest = hashlib.sha256(purpose + b"\0" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def allowlist_fernet() -> Fernet:
    return seal_fernet(b"allowed-list-v1")


def respondents_fernet() -> Fernet:
    return seal_fernet(b"respondents-v1")


def respondent_lists_fernet() -> Fernet:
    return seal_fernet(b"respondent-lists-v1")


def lists_fernet() -> Fernet:
    return seal_fernet(b"respondent-lists-v1")


def write_sealed_text(path: Path, plaintext: str, fernet: Fernet) -> None:
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    body = f"{SEALED_PREFIX}\n{token}\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)


def read_sealed_text(path: Path, fernet: Fernet) -> tuple[str | None, str]:
    if not path.is_file():
        return "", "missing"
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None, "broken"
    first, _, rest = raw.partition("\n")
    if first.strip() != SEALED_PREFIX:
        return raw, "plain"
    token = "".join(rest.split())
    if not token:
        return None, "broken"
    try:
        plaintext = fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None, "broken"
    return plaintext, "enc"


def people_from_lines(lines: list[str]) -> dict[str, dict[str, str]]:
    people: dict[str, dict[str, str]] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_allowed_line(line)
        if parsed is None:
            continue
        name, email = parsed
        key = normalize_email(email)
        if key in people:
            continue
        people[key] = {"email": email, "name": name}
    return people


def render_allowed_people(people: dict[str, dict[str, str]]) -> str:
    ordered = sorted(
        people.values(),
        key=lambda person: (person["name"].casefold(), normalize_email(person["email"])),
    )
    lines = []
    for person in ordered:
        name = person["name"].strip()
        email = person["email"].strip()
        if name:
            lines.append(f"{name} <{email}>")
        else:
            lines.append(email)
    text = "\n".join(lines)
    if text:
        text += "\n"
    return text


def clean_allowed_name(value: str) -> str | None:
    name = " ".join(value.split())
    if not name or len(name) > 200 or "<" in name or ">" in name:
        return None
    return name


def _write_allowed_plaintext_locked(plaintext: str) -> None:
    write_sealed_text(ALLOWED_PATH, plaintext, allowlist_fernet())


def _read_allowed_raw_locked() -> tuple[str | None, str]:
    return read_sealed_text(ALLOWED_PATH, allowlist_fernet())


def _read_allowed_locked() -> tuple[dict[str, dict[str, str]], bool]:
    plaintext, status = _read_allowed_raw_locked()
    if status == "broken" or plaintext is None:
        return {}, True
    people = people_from_lines(plaintext.splitlines())
    if status == "plain":
        try:
            _write_allowed_plaintext_locked(render_allowed_people(people))
        except OSError:
            pass
    return people, False


def load_allowed_list() -> tuple[dict[str, dict[str, str]], bool]:
    with _store_lock:
        return _read_allowed_locked()


def load_allowed_people() -> dict[str, dict[str, str]]:
    people, _broken = load_allowed_list()
    return people


def add_allowed_person(name: str, email: str) -> str:
    key = normalize_email(email)
    stored_email = email.strip()
    with _store_lock:
        people, broken = _read_allowed_locked()
        if broken:
            raise AllowListUnreadable
        existing = people.get(key)
        if existing and existing["name"] == name and existing["email"] == stored_email:
            return "same"
        people[key] = {"email": stored_email, "name": name}
        _write_allowed_plaintext_locked(render_allowed_people(people))
        return "updated" if existing else "added"


def remove_allowed_person(email: str) -> dict[str, str] | None:
    key = normalize_email(email)
    with _store_lock:
        people, broken = _read_allowed_locked()
        if broken:
            raise AllowListUnreadable
        person = people.pop(key, None)
        if person is None:
            return None
        _write_allowed_plaintext_locked(render_allowed_people(people))
        return person


def allowed_person(email: str) -> dict[str, str] | None:
    return load_allowed_people().get(normalize_email(email))


def recorder_label(email: str) -> str:
    if not isinstance(email, str):
        return ""
    text = email.strip()
    if not text:
        return ""
    person = allowed_person(text)
    if person and person["name"].strip():
        return person["name"].strip()
    return text


def load_allowed_emails() -> set[str]:
    return set(load_allowed_people())


def sync_account_name(display_email: str) -> None:
    person = allowed_person(display_email)
    if person is None:
        return
    key = normalize_email(display_email)
    with _store_lock:
        accounts = load_accounts()
        record = accounts.get(key)
        if not isinstance(record, dict) or record.get("name") == person["name"]:
            return
        record["name"] = person["name"]
        save_accounts(accounts)


def load_admin_emails() -> set[str]:
    return load_email_file(ADMINS_PATH)


def email_allowed(email: str) -> bool:
    return normalize_email(email) in load_allowed_emails()


def email_is_admin(email: str) -> bool:
    return normalize_email(email) in load_admin_emails()


def contained(parent: Path, path: Path) -> bool:
    root = parent.resolve()
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def slug_dir(parent: Path, slug: str) -> Path | None:
    if not SLUG_RE.fullmatch(slug):
        return None
    path = parent / slug
    if not contained(parent, path):
        return None
    return path


def wrap_instruction_steps(html: str) -> str:
    pieces = re.split(r"(?i)(?=<h2>)", html)
    if len(pieces) == 1:
        return html
    classes = {
        1: "instruction-step-1",
        2: "instruction-step-2",
        3: "instruction-step-3",
    }
    wrapped = [pieces[0]]
    for piece in pieces[1:]:
        match = re.match(r"(?is)<h2>(.*?)</h2>", piece)
        title = re.sub(r"<[^>]+>", "", match.group(1)) if match else ""
        step = re.match(r"step\s+(\d+)\b", title.strip(), re.IGNORECASE)
        if step is None:
            wrapped.append(piece)
            continue
        tone = classes.get(int(step.group(1)), "instruction-step-3")
        wrapped.append(f'<section class="instruction-step {tone}">{piece}</section>')
    return "".join(wrapped)


def render_instructions(text: str) -> Markup:
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = markdown.markdown(safe, extensions=["nl2br", "sane_lists"])
    html = re.sub(
        r'(?i)(href|src)\s*=\s*(["\'])\s*(?:javascript:|data:)',
        r"\1=\2#",
        html,
    )
    return Markup(wrap_instruction_steps(html))


def respondent_emails_from_lines(lines: list[str]) -> list[str]:
    emails: list[str] = []
    seen: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = normalize_email(line)
        if key in seen:
            continue
        seen.add(key)
        emails.append(line)
    return emails


def render_respondent_emails(emails: list[str]) -> str:
    text = "\n".join(sorted(emails, key=normalize_email))
    if text:
        text += "\n"
    return text


def respondents_path(slug: str) -> Path | None:
    folder = slug_dir(QUESTIONNAIRES, slug)
    if folder is None or not folder.is_dir():
        return None
    return folder / "respondents.txt"


def _read_respondents_locked(path: Path) -> tuple[list[str] | None, bool]:
    plaintext, status = read_sealed_text(path, respondents_fernet())
    if status == "missing":
        return None, False
    if status == "broken" or plaintext is None:
        return None, True
    emails = respondent_emails_from_lines(plaintext.splitlines())
    if status == "plain":
        try:
            write_sealed_text(path, render_respondent_emails(emails), respondents_fernet())
        except OSError:
            pass
    return emails, False


def load_respondents(path: Path) -> tuple[list[str] | None, bool]:
    with _store_lock:
        return _read_respondents_locked(path)


def add_respondent(slug: str, email: str) -> str:
    path = respondents_path(slug)
    if path is None:
        raise FileNotFoundError(slug)
    stored = email.strip()
    key = normalize_email(stored)
    with _store_lock:
        emails, broken = _read_respondents_locked(path)
        if broken:
            raise SealedFileUnreadable
        current = list(emails or [])
        if any(normalize_email(item) == key for item in current):
            return "same"
        current.append(stored)
        write_sealed_text(path, render_respondent_emails(current), respondents_fernet())
        return "added"


def remove_respondent(slug: str, email: str) -> str | None:
    path = respondents_path(slug)
    if path is None:
        raise FileNotFoundError(slug)
    key = normalize_email(email)
    with _store_lock:
        emails, broken = _read_respondents_locked(path)
        if broken:
            raise SealedFileUnreadable
        if emails is None:
            return None
        removed = None
        kept: list[str] = []
        for item in emails:
            if removed is None and normalize_email(item) == key:
                removed = item
                continue
            kept.append(item)
        if removed is None:
            return None
        write_sealed_text(path, render_respondent_emails(kept), respondents_fernet())
        return removed


def allow_all_respondents(slug: str) -> bool:
    path = respondents_path(slug)
    if path is None:
        raise FileNotFoundError(slug)
    with _store_lock:
        _emails, broken = _read_respondents_locked(path)
        if broken:
            raise SealedFileUnreadable
        if not path.is_file():
            return False
        path.unlink()
        return True


def clean_respondent_list_name(value: str) -> str | None:
    name = " ".join(value.split())
    if not name or len(name) > MAX_RESPONDENT_LIST_NAME:
        return None
    return name


def respondent_list_slug(name: str) -> str | None:
    cleaned = clean_respondent_list_name(name)
    if cleaned is None:
        return None
    slug = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-").casefold()
    if not slug or not SLUG_RE.fullmatch(slug) or slug.upper() in RESERVED_NAMES:
        return None
    return slug


def respondent_list_path(list_slug: str) -> Path | None:
    if not SLUG_RE.fullmatch(list_slug) or list_slug.upper() in RESERVED_NAMES:
        return None
    path = RESPONDENT_LISTS / f"{list_slug}.txt"
    if not contained(RESPONDENT_LISTS, path):
        return None
    return path


def _read_respondent_list_index_locked() -> dict[str, str]:
    if not RESPONDENT_LIST_INDEX.is_file():
        return {}
    try:
        loaded = json.loads(RESPONDENT_LIST_INDEX.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    names: dict[str, str] = {}
    for slug, name in loaded.items():
        if not isinstance(slug, str) or not isinstance(name, str):
            continue
        if not SLUG_RE.fullmatch(slug) or slug.upper() in RESERVED_NAMES:
            continue
        cleaned = clean_respondent_list_name(name)
        if cleaned is None:
            continue
        names[slug] = cleaned
    return names


def _write_respondent_list_index_locked(names: dict[str, str]) -> None:
    ordered = {
        slug: names[slug]
        for slug in sorted(names, key=lambda item: (names[item].casefold(), item))
    }
    write_json(RESPONDENT_LIST_INDEX, ordered)


def _read_saved_respondent_list_locked(path: Path) -> tuple[list[str] | None, bool]:
    plaintext, status = read_sealed_text(path, respondent_lists_fernet())
    if status == "missing":
        return None, False
    if status == "broken" or plaintext is None:
        return None, True
    emails = respondent_emails_from_lines(plaintext.splitlines())
    if status == "plain":
        try:
            write_sealed_text(path, render_respondent_emails(emails), respondent_lists_fernet())
        except OSError:
            pass
    return emails, False


def list_saved_respondent_lists() -> list[dict]:
    with _store_lock:
        names = _read_respondent_list_index_locked()
        rows: list[dict] = []
        for slug, name in names.items():
            path = respondent_list_path(slug)
            if path is None:
                continue
            emails, broken = _read_saved_respondent_list_locked(path)
            rows.append(
                {
                    "slug": slug,
                    "name": name,
                    "count": 0 if broken or emails is None else len(emails),
                    "broken": broken or emails is None,
                }
            )
        rows.sort(key=lambda row: (row["name"].casefold(), row["slug"]))
        return rows


def save_respondent_list_copy(questionnaire_slug: str, name: str) -> dict:
    list_name = clean_respondent_list_name(name)
    list_slug = respondent_list_slug(name) if list_name is not None else None
    if list_name is None or list_slug is None:
        raise ValueError("name")
    path = respondents_path(questionnaire_slug)
    list_path = respondent_list_path(list_slug)
    if path is None or list_path is None:
        raise FileNotFoundError(questionnaire_slug)
    with _store_lock:
        emails, broken = _read_respondents_locked(path)
        if broken:
            raise SealedFileUnreadable
        if not emails:
            raise ValueError("empty")
        RESPONDENT_LISTS.mkdir(parents=True, exist_ok=True)
        names = _read_respondent_list_index_locked()
        replaced = list_slug in names
        write_sealed_text(
            list_path,
            render_respondent_emails(emails),
            respondent_lists_fernet(),
        )
        names[list_slug] = list_name
        _write_respondent_list_index_locked(names)
        return {
            "action": "replaced" if replaced else "created",
            "slug": list_slug,
            "name": list_name,
            "count": len(emails),
        }


def import_saved_respondent_list(questionnaire_slug: str, list_slug: str) -> dict:
    path = respondents_path(questionnaire_slug)
    list_path = respondent_list_path(list_slug)
    if path is None or list_path is None:
        raise FileNotFoundError(questionnaire_slug)
    with _store_lock:
        names = _read_respondent_list_index_locked()
        list_name = names.get(list_slug)
        if list_name is None:
            raise FileNotFoundError(list_slug)
        source, source_broken = _read_saved_respondent_list_locked(list_path)
        if source_broken or source is None:
            raise SealedFileUnreadable
        current, broken = _read_respondents_locked(path)
        if broken:
            raise SealedFileUnreadable
        merged = list(current or [])
        seen = {normalize_email(item) for item in merged}
        added = 0
        for email in source:
            key = normalize_email(email)
            if key in seen:
                continue
            seen.add(key)
            merged.append(email)
            added += 1
        write_sealed_text(path, render_respondent_emails(merged), respondents_fernet())
        skipped = len(source) - added
        return {
            "name": list_name,
            "added": added,
            "skipped": skipped,
            "count": len(source),
        }


def delete_saved_respondent_list(list_slug: str) -> str | None:
    list_path = respondent_list_path(list_slug)
    if list_path is None:
        return None
    with _store_lock:
        names = _read_respondent_list_index_locked()
        list_name = names.pop(list_slug, None)
        if list_name is None:
            return None
        if list_path.is_file():
            list_path.unlink()
        _write_respondent_list_index_locked(names)
        return list_name


def email_on_respondent_list(questionnaire: dict, email: str) -> bool:
    if questionnaire.get("respondents_broken"):
        return False
    respondents = questionnaire["respondents"]
    if respondents is None:
        return True
    key = normalize_email(email)
    return any(normalize_email(item) == key for item in respondents)


_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_saved_at(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text.replace("T", " ")
    hour = moment.hour % 12 or 12
    period = "AM" if moment.hour < 12 else "PM"
    return (
        f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year} "
        f"at {hour}:{moment.minute:02d} {period}"
    )


def parse_moment(value) -> tuple[datetime, str, str] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            moment = datetime.strptime(text, "%Y-%m-%d")
            normalized = moment.date().isoformat()
            label = f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"
        elif len(text) == 16:
            moment = datetime.strptime(text, "%Y-%m-%dT%H:%M")
            normalized = moment.strftime("%Y-%m-%dT%H:%M")
            label = (
                f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}, "
                f"{moment.hour:02d}:{moment.minute:02d}"
            )
        else:
            return None
    except ValueError:
        return None
    return moment, normalized, label


def parse_deadline(value) -> dict | None:
    parsed = parse_moment(value)
    if parsed is None:
        return None
    moment, normalized, label = parsed
    due_at = moment.replace(hour=23, minute=59, second=59) if len(normalized) == 10 else moment
    return {
        "deadline": normalized,
        "deadline_label": label,
        "deadline_overdue": datetime.now() > due_at,
    }


def parse_start(value) -> dict | None:
    parsed = parse_moment(value)
    if parsed is None:
        return None
    moment, normalized, label = parsed
    return {
        "start": normalized,
        "start_label": label,
        "started": datetime.now() >= moment,
    }


def parse_services(value) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SERVICES:
        return None
    services: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            return None
        name = item.strip()
        if not name or len(name) > MAX_SERVICE_LENGTH or name in seen:
            return None
        seen.add(name)
        services.append(name)
    return services


def load_questionnaire(slug: str) -> dict | None:
    folder = slug_dir(QUESTIONNAIRES, slug)
    if folder is None or not folder.is_dir():
        return None
    config_path = folder / "config.json"
    instructions_path = folder / "instructions.md"
    if not config_path.is_file() or not instructions_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        instructions = instructions_path.read_text(encoding="utf-8-sig")
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(config, dict):
        return None
    title = config.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    statuses = config.get("statuses", DEFAULT_STATUSES)
    if (
        not isinstance(statuses, list)
        or not statuses
        or not all(isinstance(item, str) and item.strip() for item in statuses)
    ):
        return None
    services = parse_services(config.get("services"))
    if services is None:
        return None
    service_results = parse_services(config.get("service_results"))
    if service_results is None or (service_results and not services):
        return None
    enabled_value = config.get("enabled", True)
    enabled = enabled_value if isinstance(enabled_value, bool) else True
    label_value = config.get("label", DEFAULT_LABEL)
    label = label_value.strip() if isinstance(label_value, str) and label_value.strip() else DEFAULT_LABEL
    deadline = parse_deadline(config.get("deadline")) or {}
    start = parse_start(config.get("start")) or {}
    respondents, respondents_broken = load_respondents(folder / "respondents.txt")
    return {
        "slug": slug,
        "title": title.strip(),
        "label": label,
        "statuses": ensure_completed_status(statuses),
        "services": services,
        "service_results": service_results,
        "instructions": render_instructions(instructions),
        "respondents": respondents,
        "respondents_broken": respondents_broken,
        "enabled": enabled,
        "deadline": deadline.get("deadline", ""),
        "deadline_label": deadline.get("deadline_label", ""),
        "deadline_overdue": deadline.get("deadline_overdue", False),
        "start": start.get("start", ""),
        "start_label": start.get("start_label", ""),
        "started": start.get("started", True),
    }


def list_questionnaires() -> list[dict]:
    if not QUESTIONNAIRES.is_dir():
        return []
    found = []
    for folder in QUESTIONNAIRES.iterdir():
        if not folder.is_dir():
            continue
        questionnaire = load_questionnaire(folder.name)
        if questionnaire is not None:
            found.append(questionnaire)
    found.sort(key=lambda item: item["title"].casefold())
    return found


def response_root(slug: str) -> Path | None:
    return slug_dir(RESPONSES, slug)


def load_responses(slug: str) -> list[dict]:
    folder = response_root(slug)
    if folder is None or not folder.is_dir():
        return []
    replies = []
    for child in folder.iterdir():
        if not child.is_dir() or not contained(folder, child):
            continue
        meta_path = child / "response.json"
        if not meta_path.is_file():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(data, dict):
            continue
        screenshots = []
        for name in data.get("screenshots") or []:
            if isinstance(name, str) and SCREENSHOT_RE.fullmatch(name):
                if (child / name).is_file() and contained(child, child / name):
                    screenshots.append(name)
        replies.append(
            {
                "name": str(data.get("name") or ""),
                "email": str(data.get("email") or ""),
                "status": str(data.get("status") or ""),
                "update": str(data.get("update") or ""),
                "submitted_at": str(data.get("submitted_at") or ""),
                "folder": child.name,
                "screenshots": screenshots,
                "links": stored_links(data.get("links")),
                "access": stored_access(data.get("access")),
                "recorded_by": stored_recorder(data.get("recorded_by")),
            }
        )
    replies.sort(key=lambda item: item["submitted_at"], reverse=True)
    return replies


def responses_for_email(slug: str, email: str) -> list[dict]:
    key = normalize_email(email)
    if not key:
        return []
    return [
        reply
        for reply in load_responses(slug)
        if normalize_email(reply["email"]) == key
    ]


def group_responses(replies: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for reply in replies:
        grouped.setdefault(normalize_email(reply["email"]), []).append(reply)
    return grouped


def reply_progress(questionnaire: dict, replies: list[dict]) -> dict | None:
    if questionnaire.get("respondents_broken"):
        return None
    respondents = questionnaire["respondents"]
    if respondents is None:
        return None
    grouped = group_responses(replies)
    completed = 0
    waiting = 0
    statuses: list[dict] = []
    by_key: dict[str, dict] = {}

    def bucket(label: str) -> dict:
        key = label.casefold()
        item = by_key.get(key)
        if item is None:
            item = {"label": label, "count": 0}
            by_key[key] = item
            statuses.append(item)
        return item

    for status in questionnaire["statuses"]:
        label = status.strip()
        if label and not status_is_done(label):
            bucket(label)
    for email in respondents:
        matches = grouped.get(normalize_email(email))
        if not matches:
            waiting += 1
            continue
        label = matches[0]["status"].strip()
        if status_is_done(label):
            completed += 1
            continue
        if not label:
            waiting += 1
            continue
        bucket(label)["count"] += 1
    return {
        "completed": completed,
        "expected": len(respondents),
        "statuses": statuses,
        "waiting": waiting,
    }


def sanitize_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*' and ord(ch) >= 32)
    cleaned = re.sub(r"\s+", "-", cleaned.strip(" ."))
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")[:40]
    if not cleaned:
        cleaned = "respondent"
    if cleaned.casefold() in {item.casefold() for item in RESERVED_NAMES}:
        cleaned = f"name-{cleaned}"
    return cleaned.casefold()


def looks_like_image(ext: str, blob: bytes) -> bool:
    signature = SIGNATURES.get(ext)
    if signature is None or not blob.startswith(signature):
        return False
    if ext == ".webp":
        return len(blob) >= 12 and blob[8:12] == b"WEBP"
    return True


def read_uploads(files) -> tuple[list[tuple[str, bytes]], str | None]:
    selected = [item for item in files if item and item.filename]
    if len(selected) > MAX_FILES:
        return [], f"Attach at most {MAX_FILES} screenshots."
    uploads = []
    for index, item in enumerate(selected, start=1):
        ext = Path(item.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return [], "Screenshots must be PNG, JPG, GIF, or WebP files."
        blob = item.read(MAX_FILE_BYTES + 1)
        if not blob:
            return [], "One of the screenshots is empty."
        if len(blob) > MAX_FILE_BYTES:
            return [], "Each screenshot must be 8 MB or smaller."
        if not looks_like_image(ext, blob):
            return [], "One of the files is not a valid image."
        uploads.append((f"screenshot-{index}{ext}", blob))
    return uploads, None


def valid_link(value: str) -> str | None:
    link = value.strip()
    if not link or len(link) > MAX_LINK_LENGTH or any(ch.isspace() for ch in link):
        return None
    parsed = urlparse(link)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return link


def stored_recorder(raw) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def stored_access(raw) -> dict[str, bool | str]:
    if not isinstance(raw, dict):
        return {}
    access: dict[str, bool | str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        name = key.strip()
        if not name or name in access:
            continue
        if isinstance(value, bool):
            access[name] = value
        elif isinstance(value, str) and value.strip():
            access[name] = value.strip()
    return access


def checked_services(access) -> list[str]:
    if not isinstance(access, dict):
        return []
    return [name for name, allowed in access.items() if allowed is True]


def read_access(questionnaire: dict, form) -> dict[str, bool] | None:
    services = questionnaire.get("services") or []
    if not services:
        return None
    chosen = {item for item in form.getlist("access") if isinstance(item, str)}
    return {name: name in chosen for name in services}


def saved_results(access) -> dict[str, str]:
    if not isinstance(access, dict):
        return {}
    chosen: dict[str, str] = {}
    for name, value in access.items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        label = name.strip()
        result = value.strip()
        if label and result and label not in chosen:
            chosen[label] = result
    return chosen


def posted_results(questionnaire: dict, form) -> dict[str, str]:
    services = questionnaire.get("services") or []
    allowed = set(questionnaire.get("service_results") or [])
    chosen: dict[str, str] = {}
    for index, name in enumerate(services):
        raw = form.get(f"result-{index}", "")
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if value in allowed:
            chosen[name] = value
    return chosen


def read_service_results(questionnaire: dict, form) -> tuple[dict[str, str], list[str]]:
    services = questionnaire.get("services") or []
    allowed = set(questionnaire.get("service_results") or [])
    status = form.get("status", "")
    if not isinstance(status, str):
        status = ""
    require = status_is_done(status.strip())
    chosen: dict[str, str] = {}
    errors: list[str] = []
    for index, name in enumerate(services):
        raw = form.get(f"result-{index}", "")
        value = raw.strip() if isinstance(raw, str) else ""
        if value in allowed:
            chosen[name] = value
            continue
        if require or value:
            errors.append(f"Choose a result for {name}.")
    return chosen, errors


def submission_values(questionnaire: dict, saved: dict | None, form=None) -> dict:
    if form is not None:
        submitted_links = form.getlist("links")
        return {
            "status": form.get("status", ""),
            "update": form.get("update", ""),
            "links": submitted_links or [""],
            "access": form.getlist("access"),
            "results": posted_results(questionnaire, form),
        }
    access = saved.get("access") if saved else None
    return {
        "status": saved["status"] if saved else "",
        "update": "",
        "links": [""],
        "access": checked_services(access),
        "results": saved_results(access),
    }


def attach_access(questionnaire: dict, form, fields: dict) -> list[str]:
    if questionnaire.get("service_results"):
        results, errors = read_service_results(questionnaire, form)
        if errors:
            return errors
        if results:
            fields["access"] = results
        return []
    access = read_access(questionnaire, form)
    if access is not None:
        fields["access"] = access
    return []


def format_access(access: dict) -> str:
    if not access:
        return ""
    parts = []
    for name, value in access.items():
        if value is True:
            label = "yes"
        elif value is False:
            label = "no"
        elif isinstance(value, str) and value.strip():
            label = value.strip()
        else:
            continue
        parts.append(f"{name}: {label}")
    return "; ".join(parts)


def stored_links(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    links = []
    seen = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        link = valid_link(item)
        if link is None or link in seen:
            continue
        seen.add(link)
        links.append(link)
        if len(links) >= MAX_LINKS:
            break
    return links


def read_links(form) -> tuple[list[str], str | None]:
    raw = form.getlist("links")
    links = []
    seen = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        link = item.strip()
        if not link:
            continue
        if len(link) > MAX_LINK_LENGTH:
            return [], "Each link must be 2,000 characters or fewer."
        if valid_link(link) is None:
            return [], "Links must start with http:// or https://."
        if link in seen:
            continue
        seen.add(link)
        links.append(link)
        if len(links) > MAX_LINKS:
            return [], f"Add at most {MAX_LINKS} links."
    return links, None


MISSING_NAME = "Your name is missing from the sign-in list."


def validate_submission(questionnaire: dict, form, email: str, name: str) -> tuple[dict | None, list[str]]:
    name = name.strip()
    email = email.strip()
    status = form.get("status", "").strip()
    update = form.get("update", "").strip()
    errors = []
    if not name:
        errors.append(MISSING_NAME)
    elif len(name) > 200:
        errors.append("Name must be 200 characters or fewer.")
    if not email or len(email) > 200 or not EMAIL_RE.fullmatch(email):
        errors.append("Sign in again before submitting.")
    if status not in questionnaire["statuses"]:
        errors.append("Choose a status.")
    if len(update) > 20000:
        errors.append("Update must be 20,000 characters or fewer.")
    if errors:
        return None, errors
    return {"name": name, "email": email, "status": status, "update": update}, []


def create_submission_dir(slug: str, name: str) -> Path:
    folder = response_root(slug)
    if folder is None:
        abort(404)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    safe_name = sanitize_name(name)
    for _ in range(8):
        candidate = folder / f"{stamp}_{safe_name}_{secrets.token_hex(3)}"
        if not contained(folder, candidate):
            abort(400)
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise OSError("Could not create a new response folder.")


def save_submission(slug: str, fields: dict, uploads: list[tuple[str, bytes]]) -> None:
    submitted_at = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
    target = create_submission_dir(slug, fields["name"])
    names = []
    for filename, blob in uploads:
        destination = target / filename
        if not contained(target, destination) or not SCREENSHOT_RE.fullmatch(filename):
            abort(400)
        destination.write_bytes(blob)
        names.append(filename)
    payload = {
        "name": fields["name"],
        "email": fields["email"],
        "status": fields["status"],
        "update": fields["update"],
        "submitted_at": submitted_at,
        "screenshots": names,
        "links": list(fields.get("links") or []),
    }
    access = fields.get("access")
    if isinstance(access, dict) and access:
        payload["access"] = access
    recorded_by = stored_recorder(fields.get("recorded_by"))
    if recorded_by:
        payload["recorded_by"] = recorded_by
    (target / "response.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def status_is_done(status: str) -> bool:
    return status.casefold() in DONE_STATUSES


def submission_locked(saved: dict | None) -> bool:
    if not saved:
        return False
    return status_is_done(str(saved.get("status") or ""))


def ensure_completed_status(statuses: list[str]) -> list[str]:
    cleaned = [item.strip() for item in statuses]
    if any(status_is_done(item) for item in cleaned):
        return cleaned
    return [COMPLETED_STATUS, *cleaned]


def person_summary(email: str, matches: list[dict]) -> dict:
    latest = matches[0] if matches else None
    return {
        "email": email,
        "replies": matches,
        "waiting": latest is None,
        "latest": latest,
    }


def sort_people(rows: list[dict]) -> list[dict]:
    submitted = [row for row in rows if row["latest"]]
    waiting = [row for row in rows if not row["latest"]]
    submitted.sort(key=lambda row: row["latest"]["submitted_at"], reverse=True)
    return submitted + waiting


def status_options(questionnaire: dict, people: list[dict]) -> list[str]:
    options: list[str] = []
    for status in questionnaire["statuses"]:
        if status and status not in options:
            options.append(status)
    for row in people:
        status = row["latest"]["status"] if row["latest"] else "Waiting"
        if status and status not in options:
            options.append(status)
    return options


def review_model(questionnaire: dict, replies: list[dict]) -> dict:
    grouped = group_responses(replies)
    respondents = questionnaire["respondents"]
    if respondents is None:
        rows = sort_people(
            [
                person_summary(matches[0]["email"] or key, matches)
                for key, matches in grouped.items()
                if matches
            ]
        )
        return {
            "has_list": False,
            "rows": rows,
            "others": replies,
            "other_rows": [],
            "statuses": status_options(questionnaire, rows),
        }
    listed = {normalize_email(email) for email in respondents}
    rows = sort_people(
        [
            person_summary(email, grouped.get(normalize_email(email), []))
            for email in respondents
        ]
    )
    others = []
    other_rows = []
    for key, matches in grouped.items():
        if key not in listed and matches:
            others.extend(matches)
            other_rows.append(person_summary(matches[0]["email"] or key, matches))
    others.sort(key=lambda item: item["submitted_at"], reverse=True)
    other_rows = sort_people(other_rows)
    people = rows + other_rows
    return {
        "has_list": True,
        "rows": rows,
        "others": others,
        "other_rows": other_rows,
        "statuses": status_options(questionnaire, people),
    }


def review_person(questionnaire: dict, email: str) -> dict | None:
    key = normalize_email(email)
    if not key:
        return None
    model = review_model(questionnaire, load_responses(questionnaire["slug"]))
    for row in model["rows"] + model["other_rows"]:
        if normalize_email(row["email"]) == key:
            return row
    return None


def person_display_name(email: str, latest: dict | None) -> str:
    person = allowed_person(email)
    if person and person["name"].strip():
        return person["name"].strip()
    if latest:
        stored = str(latest.get("name") or "").strip()
        if stored:
            return stored
    return ""


def find_response(slug: str, folder: str) -> dict | None:
    if folder in {"", ".", ".."} or "/" in folder or "\\" in folder:
        return None
    root = response_root(slug)
    if root is None or not root.is_dir():
        return None
    directory = root / folder
    if not directory.is_dir() or not contained(root, directory):
        return None
    for reply in load_responses(slug):
        if reply["folder"] == folder:
            return reply
    return None


def summary_rows(questionnaire: dict, replies: list[dict]) -> list[dict]:
    model = review_model(questionnaire, replies)
    rows = []

    def add_reply(reply: dict) -> None:
        rows.append(
            {
                "email": reply["email"],
                "name": reply["name"],
                "status": reply["status"],
                "update": reply["update"],
                "submitted_at": reply["submitted_at"],
                "folder": reply["folder"],
                "screenshots": ";".join(reply["screenshots"]),
                "links": ";".join(reply.get("links") or []),
                "access": format_access(reply.get("access") or {}),
                "recorded_by": reply.get("recorded_by") or "",
            }
        )

    def add_person(row: dict) -> None:
        latest = row["latest"]
        if latest is None:
            rows.append(
                {
                    "email": row["email"],
                    "name": "",
                    "status": "Waiting",
                    "update": "",
                    "submitted_at": "",
                    "folder": "",
                    "screenshots": "",
                    "links": "",
                    "access": "",
                    "recorded_by": "",
                }
            )
        else:
            add_reply(latest)

    for row in model["rows"]:
        add_person(row)
    for row in model["other_rows"]:
        add_person(row)
    return rows


def build_csv(questionnaire: dict, replies: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["email", "name", "status", "update", "submitted_at", "folder", "screenshots", "links", "access", "recorded_by"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(summary_rows(questionnaire, replies))
    return buffer.getvalue().encode("utf-8-sig")


def require_questionnaire(slug: str) -> dict:
    questionnaire = load_questionnaire(slug)
    if questionnaire is None:
        abort(404)
    return questionnaire


def set_questionnaire_enabled(slug: str, enabled: bool) -> bool:
    folder = slug_dir(QUESTIONNAIRES, slug)
    if folder is None:
        return False
    config_path = folder / "config.json"
    with _store_lock:
        if not config_path.is_file():
            return False
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return False
        if not isinstance(loaded, dict):
            return False
        loaded["enabled"] = enabled
        write_json(config_path, loaded)
    return load_questionnaire(slug) is not None


def questionnaire_closed():
    return (
        render_template(
            "error.html",
            heading="Questionnaire turned off",
            message="This questionnaire is not accepting replies.",
        ),
        404,
    )


def hide_until_started(questionnaire: dict) -> None:
    email = signed_in_email() or ""
    if not questionnaire["started"] and not email_is_admin(email):
        abort(404)


@app.context_processor
def inject_admin():
    email = signed_in_email()
    return {
        "is_admin": bool(email and email_is_admin(email)),
        "signed_in_name": signed_in_name(),
        "recorder_label": recorder_label,
    }


@app.before_request
def require_login():
    ensure_secret()
    endpoint = request.endpoint
    if endpoint in {None, "static", "login", "logout"}:
        return None
    email = signed_in_email()
    if email:
        if email_allowed(email):
            if request.path.startswith("/admin") and not email_is_admin(email):
                abort(404)
            return None
        session.clear()
        session["login_error"] = "That email is not on the sign-in list."
        return redirect(url_for("login"))
    pending = session.get("pending_email")
    if isinstance(pending, str) and pending and not email_allowed(pending):
        session.clear()
        session["login_error"] = "That email is not on the sign-in list."
        return redirect(url_for("login"))
    if endpoint == "set_password" and isinstance(pending, str) and pending:
        return None
    if isinstance(pending, str) and pending:
        return redirect(url_for("set_password"))
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    current = signed_in_email()
    if current:
        if email_allowed(current):
            return redirect(url_for("home"))
        session.clear()
    errors: list[str] = []
    email = ""
    if request.method == "GET":
        held_email = session.pop("login_email", "")
        held_error = session.pop("login_error", "")
        if isinstance(held_email, str):
            email = held_email
        if isinstance(held_error, str) and held_error:
            errors.append(held_error)
    if request.method == "POST":
        next_url = safe_next(request.form.get("next"))
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if not email:
            errors.append("Enter your email.")
        elif len(email) > 200 or not EMAIL_RE.fullmatch(email):
            errors.append("Enter an email address.")
        if not password:
            errors.append("Enter your password.")
        if not errors and not email_allowed(email):
            errors.append("That email is not on the sign-in list.")
        if not errors:
            account = find_account(email)
            if account and password_matches(account["password_hash"], password):
                session.clear()
                session["email"] = account["email"]
                sync_account_name(account["email"])
                return redirect(next_url)
            if account is None and not starter_password():
                errors.append("The starter password is not set. Add default_password to auth.json.")
            elif account is None and starter_password_matches(password):
                session.clear()
                session["pending_email"] = normalize_email(email)
                session["pending_display"] = email
                session["next"] = next_url
                return redirect(url_for("set_password"))
            else:
                errors.append("Email or password is incorrect.")
    else:
        next_url = safe_next(request.args.get("next"))
    return render_template("login.html", errors=errors, email=email, next_url=next_url)


@app.route("/account/password", methods=["GET", "POST"])
def set_password():
    if signed_in_email():
        return redirect(url_for("home"))
    pending = session.get("pending_email")
    display = session.get("pending_display")
    if not isinstance(pending, str) or not pending or not isinstance(display, str) or not display:
        return redirect(url_for("login"))
    errors: list[str] = []
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        starter = starter_password()
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append("Use at least 8 characters.")
        elif starter and same_text(password, starter):
            errors.append("Choose a password that is different from the starter password.")
        elif password != confirm:
            errors.append("Those passwords do not match.")
        elif not email_allowed(display):
            session.clear()
            session["login_email"] = display
            session["login_error"] = "That email is not on the sign-in list."
            return redirect(url_for("login"))
        else:
            try:
                create_account(display, password)
            except FileExistsError:
                session.clear()
                session["login_email"] = display
                session["login_error"] = "That email already has a password. Sign in with it."
                return redirect(url_for("login"))
            destination = session.get("next")
            session.clear()
            session["email"] = display.strip()
            sync_account_name(display)
            return redirect(safe_next(destination if isinstance(destination, str) else None))
    return render_template("password.html", errors=errors, email=display)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def home():
    email = signed_in_email() or ""
    cards = []
    for questionnaire in list_questionnaires():
        if not questionnaire["started"] or not questionnaire["enabled"]:
            continue
        if not email_on_respondent_list(questionnaire, email):
            continue
        slug = questionnaire["slug"]
        replies = load_responses(slug)
        key = normalize_email(email)
        own_replies = [
            reply
            for reply in replies
            if key and normalize_email(reply["email"]) == key
        ]
        latest = own_replies[0] if own_replies else None
        cards.append(
            {
                "title": questionnaire["title"],
                "slug": slug,
                "link": url_for("questionnaire", slug=slug),
                "view_link": url_for("own_responses", slug=slug),
                "has_reply": bool(own_replies),
                "locked": submission_locked(latest),
                "access": latest["access"] if latest else {},
                "status": latest["status"] if latest else "Not submitted",
                "submitted_at": latest["submitted_at"] if latest else "",
                "saved_label": format_saved_at(latest["submitted_at"]) if latest else "",
                "deadline": questionnaire["deadline"],
                "deadline_label": questionnaire["deadline_label"],
                "deadline_overdue": questionnaire["deadline_overdue"],
                "start": questionnaire["start"],
                "start_label": questionnaire["start_label"],
                "started": questionnaire["started"],
                "progress": reply_progress(questionnaire, replies),
            }
        )
    cards.sort(key=lambda card: status_is_done(card["status"]))
    return render_template("home.html", cards=cards)


def admin_card(questionnaire: dict) -> dict:
    replies = load_responses(questionnaire["slug"])
    return {
        "title": questionnaire["title"],
        "slug": questionnaire["slug"],
        "link": url_for("questionnaire", slug=questionnaire["slug"], _external=True),
        "review_link": url_for("review", slug=questionnaire["slug"]),
        "respondents_link": url_for("respondents", slug=questionnaire["slug"]),
        "respondents_broken": questionnaire["respondents_broken"],
        "progress": reply_progress(questionnaire, replies),
        "deadline": questionnaire["deadline"],
        "deadline_label": questionnaire["deadline_label"],
        "deadline_overdue": questionnaire["deadline_overdue"],
        "start": questionnaire["start"],
        "start_label": questionnaire["start_label"],
        "started": questionnaire["started"],
    }


@app.get("/admin")
def admin():
    found = list_questionnaires()
    active_cards = []
    planned_cards = []
    for questionnaire in found:
        if not questionnaire["enabled"]:
            continue
        card = admin_card(questionnaire)
        if questionnaire["started"]:
            active_cards.append(card)
        else:
            planned_cards.append(card)
    return render_template(
        "admin.html",
        active_cards=active_cards,
        planned_cards=planned_cards,
        any_questionnaires=bool(found),
    )


def people_view(
    errors: list[str] | None = None,
    name: str = "",
    email: str = "",
    notice: str = "",
):
    people, broken = load_allowed_list()
    me = normalize_email(signed_in_email() or "")
    rows = [
        {
            "name": person["name"],
            "email": person["email"],
            "self": normalize_email(person["email"]) == me,
        }
        for person in sorted(
            people.values(),
            key=lambda person: (person["name"].casefold(), normalize_email(person["email"])),
        )
    ]
    return render_template(
        "people.html",
        rows=rows,
        errors=errors or [],
        name=name,
        email=email,
        notice=notice,
        broken=broken,
        count=len(rows),
    )


@app.route("/admin/people", methods=["GET", "POST"])
def people():
    if request.method == "POST":
        name_input = request.form.get("name", "")
        email_input = request.form.get("email", "")
        name = clean_allowed_name(name_input)
        email = email_input.strip()
        errors: list[str] = []
        if name is None:
            errors.append("Enter a name without < or >. Use at most 200 characters.")
        if not email or len(email) > 200 or not EMAIL_RE.fullmatch(email):
            errors.append("Enter an email address.")
        if errors or name is None:
            return people_view(errors=errors, name=" ".join(name_input.split()), email=email)
        try:
            action = add_allowed_person(name, email)
        except AllowListUnreadable:
            return people_view(
                errors=["The sign-in list could not be read. allowed.txt was left unchanged."],
                name=name,
                email=email,
            )
        if action == "added":
            session["people_notice"] = f"{name} can sign in now."
        elif action == "updated":
            session["people_notice"] = f"Updated the name for {email}."
        else:
            session["people_notice"] = f"{name} is already on the sign-in list."
        return redirect(url_for("people"))
    notice = session.pop("people_notice", "")
    if not isinstance(notice, str):
        notice = ""
    return people_view(notice=notice)


@app.post("/admin/people/remove")
def remove_person():
    email = request.form.get("email", "").strip()
    if normalize_email(email) == normalize_email(signed_in_email() or ""):
        return people_view(errors=["You cannot remove your own sign-in."])
    if not email or len(email) > 200 or not EMAIL_RE.fullmatch(email):
        return people_view(errors=["Enter an email address."])
    try:
        removed = remove_allowed_person(email)
    except AllowListUnreadable:
        return people_view(
            errors=["The sign-in list could not be read. allowed.txt was left unchanged."]
        )
    if removed is None:
        return people_view(errors=["That email is not on the sign-in list."])
    label = removed["name"].strip() or removed["email"]
    session["people_notice"] = f"{label} was removed from the sign-in list."
    return redirect(url_for("people"))


def respondent_rows(emails: list[str]) -> list[dict]:
    rows = []
    for item in emails:
        person = allowed_person(item)
        name = person["name"] if person else ""
        rows.append({"email": item, "name": name, "on_signin": person is not None})
    rows.sort(key=lambda row: (row["name"].casefold(), normalize_email(row["email"])))
    return rows


def respondents_view(
    questionnaire: dict,
    errors: list[str] | None = None,
    email: str = "",
    notice: str = "",
    list_name: str = "",
):
    broken = questionnaire["respondents_broken"]
    emails = [] if broken or questionnaire["respondents"] is None else questionnaire["respondents"]
    rows = respondent_rows(emails)
    return render_template(
        "respondents.html",
        questionnaire=questionnaire,
        rows=rows,
        errors=errors or [],
        email=email,
        notice=notice,
        broken=broken,
        has_list=questionnaire["respondents"] is not None and not broken,
        count=len(rows),
        saved_lists=list_saved_respondent_lists(),
        list_name=list_name,
    )


def pop_respondents_notice(slug: str) -> str:
    notice = session.pop("respondents_notice", None)
    if isinstance(notice, dict) and notice.get("slug") == slug and isinstance(notice.get("text"), str):
        return notice["text"]
    return ""


def remember_respondents_notice(slug: str, text: str) -> None:
    session["respondents_notice"] = {"slug": slug, "text": text}


@app.route("/admin/q/<slug>/respondents", methods=["GET", "POST"])
def respondents(slug: str):
    questionnaire = require_questionnaire(slug)
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email or len(email) > 200 or not EMAIL_RE.fullmatch(email):
            return respondents_view(questionnaire, errors=["Enter an email address."], email=email)
        try:
            action = add_respondent(slug, email)
        except FileNotFoundError:
            abort(404)
        except SealedFileUnreadable:
            return respondents_view(
                require_questionnaire(slug),
                errors=["The respondent list could not be read. respondents.txt was left unchanged."],
                email=email,
            )
        except OSError:
            return respondents_view(
                require_questionnaire(slug),
                errors=["Could not save the respondent list. Try again."],
                email=email,
            )
        if action == "added":
            remember_respondents_notice(slug, f"{email} is on the respondent list.")
        else:
            remember_respondents_notice(slug, f"{email} is already on the respondent list.")
        return redirect(url_for("respondents", slug=slug))
    return respondents_view(questionnaire, notice=pop_respondents_notice(slug))


@app.post("/admin/q/<slug>/respondents/remove")
def remove_questionnaire_respondent(slug: str):
    questionnaire = require_questionnaire(slug)
    email = request.form.get("email", "").strip()
    if not email or len(email) > 200 or "\n" in email or "\r" in email:
        return respondents_view(questionnaire, errors=["Enter an email address."])
    try:
        removed = remove_respondent(slug, email)
    except FileNotFoundError:
        abort(404)
    except SealedFileUnreadable:
        return respondents_view(
            require_questionnaire(slug),
            errors=["The respondent list could not be read. respondents.txt was left unchanged."],
        )
    except OSError:
        return respondents_view(
            require_questionnaire(slug),
            errors=["Could not save the respondent list. Try again."],
        )
    if removed is None:
        return respondents_view(
            require_questionnaire(slug),
            errors=["That email is not on the respondent list."],
        )
    remember_respondents_notice(slug, f"{removed} was removed from the respondent list.")
    return redirect(url_for("respondents", slug=slug))


@app.post("/admin/q/<slug>/respondents/everyone")
def respondents_allow_everyone(slug: str):
    require_questionnaire(slug)
    try:
        cleared = allow_all_respondents(slug)
    except FileNotFoundError:
        abort(404)
    except SealedFileUnreadable:
        return respondents_view(
            require_questionnaire(slug),
            errors=["The respondent list could not be read. respondents.txt was left unchanged."],
        )
    except OSError:
        return respondents_view(
            require_questionnaire(slug),
            errors=["Could not save the respondent list. Try again."],
        )
    if cleared:
        remember_respondents_notice(slug, "Anyone who can sign in can open this questionnaire.")
    else:
        remember_respondents_notice(slug, "Anyone who can sign in can already open this questionnaire.")
    return redirect(url_for("respondents", slug=slug))


@app.post("/admin/q/<slug>/respondents/lists/save")
def save_questionnaire_respondent_list(slug: str):
    questionnaire = require_questionnaire(slug)
    list_name = request.form.get("name", "")
    cleaned = clean_respondent_list_name(list_name)
    if cleaned is None or respondent_list_slug(list_name) is None:
        return respondents_view(
            questionnaire,
            errors=["Enter a list name using letters or numbers."],
            list_name=list_name.strip(),
        )
    try:
        saved = save_respondent_list_copy(slug, cleaned)
    except FileNotFoundError:
        abort(404)
    except SealedFileUnreadable:
        return respondents_view(
            require_questionnaire(slug),
            errors=["The respondent list could not be read. respondents.txt was left unchanged."],
            list_name=cleaned,
        )
    except ValueError as error:
        if str(error) == "empty":
            return respondents_view(
                require_questionnaire(slug),
                errors=["Add at least one email before saving a named list."],
                list_name=cleaned,
            )
        return respondents_view(
            require_questionnaire(slug),
            errors=["Enter a list name using letters or numbers."],
            list_name=list_name.strip(),
        )
    except OSError:
        return respondents_view(
            require_questionnaire(slug),
            errors=["Could not save the named list. Try again."],
            list_name=cleaned,
        )
    people = "person" if saved["count"] == 1 else "people"
    if saved["action"] == "replaced":
        remember_respondents_notice(
            slug,
            f"Replaced {saved['name']} with {saved['count']} {people} from this questionnaire.",
        )
    else:
        remember_respondents_notice(
            slug,
            f"Saved {saved['name']} with {saved['count']} {people}.",
        )
    return redirect(url_for("respondents", slug=slug))


@app.post("/admin/q/<slug>/respondents/lists/import")
def import_questionnaire_respondent_list(slug: str):
    questionnaire = require_questionnaire(slug)
    list_slug = request.form.get("list", "").strip()
    if not list_slug or not SLUG_RE.fullmatch(list_slug):
        return respondents_view(questionnaire, errors=["Choose a saved list to import."])
    try:
        result = import_saved_respondent_list(slug, list_slug)
    except FileNotFoundError:
        return respondents_view(
            require_questionnaire(slug),
            errors=["That saved list was not found."],
        )
    except SealedFileUnreadable:
        questionnaire = require_questionnaire(slug)
        if questionnaire["respondents_broken"]:
            return respondents_view(
                questionnaire,
                errors=["The respondent list could not be read. respondents.txt was left unchanged."],
            )
        return respondents_view(
            questionnaire,
            errors=["That saved list could not be read."],
        )
    except OSError:
        return respondents_view(
            require_questionnaire(slug),
            errors=["Could not save the respondent list. Try again."],
        )
    if result["added"] == 0:
        remember_respondents_notice(
            slug,
            f"Everyone from {result['name']} is already on the list.",
        )
    elif result["skipped"] == 0:
        people = "email" if result["added"] == 1 else "emails"
        remember_respondents_notice(
            slug,
            f"Added {result['added']} {people} from {result['name']}.",
        )
    else:
        added_label = "email" if result["added"] == 1 else "emails"
        skipped_label = "was" if result["skipped"] == 1 else "were"
        remember_respondents_notice(
            slug,
            f"Added {result['added']} {added_label} from {result['name']}. {result['skipped']} {skipped_label} already on the list.",
        )
    return redirect(url_for("respondents", slug=slug))


@app.post("/admin/q/<slug>/respondents/lists/remove")
def remove_saved_questionnaire_respondent_list(slug: str):
    require_questionnaire(slug)
    list_slug = request.form.get("list", "").strip()
    if not list_slug or not SLUG_RE.fullmatch(list_slug):
        return respondents_view(
            require_questionnaire(slug),
            errors=["Choose a saved list to remove."],
        )
    try:
        removed = delete_saved_respondent_list(list_slug)
    except OSError:
        return respondents_view(
            require_questionnaire(slug),
            errors=["Could not remove the named list. Try again."],
        )
    if removed is None:
        return respondents_view(
            require_questionnaire(slug),
            errors=["That saved list was not found."],
        )
    remember_respondents_notice(slug, f"{removed} was removed from the saved lists.")
    return redirect(url_for("respondents", slug=slug))


@app.get("/admin/archive")
def archive():
    cards = [
        admin_card(questionnaire)
        for questionnaire in list_questionnaires()
        if not questionnaire["enabled"]
    ]
    return render_template("archive.html", cards=cards)


@app.post("/admin/q/<slug>/enabled")
def update_questionnaire_enabled(slug: str):
    require_questionnaire(slug)
    raw = request.form.get("enabled", "")
    if raw not in {"0", "1"}:
        abort(400)
    if not set_questionnaire_enabled(slug, raw == "1"):
        abort(404)
    return redirect(url_for("archive" if raw == "0" else "admin"))


@app.route("/q/<slug>", methods=["GET", "POST"])
def questionnaire(slug: str):
    questionnaire = require_questionnaire(slug)
    email = signed_in_email() or ""
    hide_until_started(questionnaire)
    if questionnaire["enabled"] and not email_on_respondent_list(questionnaire, email):
        abort(404)
    if request.method == "POST" and not questionnaire["enabled"]:
        return questionnaire_closed()
    errors: list[str] = []
    name = signed_in_name()
    signer = {"name": name, "email": email}
    own_replies = responses_for_email(slug, email)
    saved = own_replies[0] if own_replies else None
    locked = submission_locked(saved)
    values = submission_values(questionnaire, saved)
    if request.method == "POST" and not locked:
        values = submission_values(questionnaire, saved, request.form)
        fields, errors = validate_submission(questionnaire, request.form, email, name)
        links, link_error = read_links(request.form)
        if link_error:
            errors.append(link_error)
        uploads: list[tuple[str, bytes]] = []
        if IMAGE_UPLOADS_ENABLED:
            uploads, upload_error = read_uploads(request.files.getlist("screenshots"))
            if upload_error:
                errors.append(upload_error)
        errors.extend(attach_access(questionnaire, request.form, fields or {}))
        if fields and not errors:
            fields["links"] = links
            try:
                sync_account_name(email)
                save_submission(slug, fields, uploads)
            except OSError:
                errors.append("Could not save the response. Try again.")
            else:
                return redirect(url_for("thanks", slug=slug))
    return render_template(
        "form.html",
        questionnaire=questionnaire,
        errors=errors,
        values=values,
        signer=signer,
        saved=saved,
        locked=locked,
        saved_label=format_saved_at(saved["submitted_at"]) if saved else "",
        screenshot_endpoint="own_screenshot",
        image_uploads_enabled=IMAGE_UPLOADS_ENABLED,
    )


@app.get("/q/<slug>/responses")
def own_responses(slug: str):
    questionnaire = require_questionnaire(slug)
    email = signed_in_email() or ""
    hide_until_started(questionnaire)
    if questionnaire["enabled"] and not email_on_respondent_list(questionnaire, email):
        abort(404)
    return render_template(
        "own.html",
        questionnaire=questionnaire,
        own_replies=responses_for_email(slug, email),
        screenshot_endpoint="own_screenshot",
    )


@app.get("/q/<slug>/thanks")
def thanks(slug: str):
    questionnaire = require_questionnaire(slug)
    email = signed_in_email() or ""
    hide_until_started(questionnaire)
    if questionnaire["enabled"] and not email_on_respondent_list(questionnaire, email):
        abort(404)
    if not questionnaire["enabled"]:
        return questionnaire_closed()
    own_replies = responses_for_email(slug, email)
    saved = own_replies[0] if own_replies else None
    return render_template(
        "thanks.html",
        questionnaire=questionnaire,
        locked=submission_locked(saved),
    )


@app.get("/admin/q/<slug>")
def review(slug: str):
    questionnaire = require_questionnaire(slug)
    replies = load_responses(slug)
    return render_template(
        "review.html",
        questionnaire=questionnaire,
        progress=reply_progress(questionnaire, replies),
        model=review_model(questionnaire, replies),
        team_link=url_for("questionnaire", slug=slug, _external=True),
    )


@app.route("/admin/q/<slug>/record", methods=["GET", "POST"])
def record_response(slug: str):
    questionnaire = require_questionnaire(slug)
    raw_email = request.args.get("email", "")
    if not isinstance(raw_email, str):
        abort(404)
    row = review_person(questionnaire, raw_email.strip())
    if row is None:
        abort(404)
    admin_email = signed_in_email()
    if not admin_email:
        abort(404)
    name = person_display_name(row["email"], row["latest"])
    person = {"name": name, "email": row["email"]}
    saved = row["latest"]
    errors: list[str] = []
    values = submission_values(questionnaire, saved)
    if request.method == "POST":
        values = submission_values(questionnaire, saved, request.form)
        fields, errors = validate_submission(questionnaire, request.form, row["email"], name)
        if not name:
            errors = [
                "Add their name on the People page before recording an update."
                if item == MISSING_NAME
                else item
                for item in errors
            ]
        links, link_error = read_links(request.form)
        if link_error:
            errors.append(link_error)
        uploads: list[tuple[str, bytes]] = []
        if IMAGE_UPLOADS_ENABLED:
            uploads, upload_error = read_uploads(request.files.getlist("screenshots"))
            if upload_error:
                errors.append(upload_error)
        errors.extend(attach_access(questionnaire, request.form, fields or {}))
        if fields and not errors:
            fields["links"] = links
            fields["recorded_by"] = admin_email
            try:
                save_submission(slug, fields, uploads)
            except OSError:
                errors.append("Could not save the response. Try again.")
            else:
                return redirect(url_for("review", slug=slug))
    return render_template(
        "record.html",
        questionnaire=questionnaire,
        errors=errors,
        values=values,
        person=person,
        saved=saved,
        image_uploads_enabled=IMAGE_UPLOADS_ENABLED,
    )


@app.get("/admin/q/<slug>/responses/<folder>")
def response_detail(slug: str, folder: str):
    questionnaire = require_questionnaire(slug)
    reply = find_response(slug, folder)
    if reply is None:
        abort(404)
    key = normalize_email(reply["email"])
    earlier = [
        item
        for item in load_responses(slug)
        if normalize_email(item["email"]) == key and item["folder"] != reply["folder"]
    ]
    is_latest = not earlier or reply["submitted_at"] >= earlier[0]["submitted_at"]
    return render_template(
        "response.html",
        questionnaire=questionnaire,
        reply=reply,
        earlier=earlier,
        is_latest=is_latest,
        screenshot_endpoint="screenshot",
    )


@app.get("/admin/q/<slug>/responses.csv")
def download_csv(slug: str):
    questionnaire = require_questionnaire(slug)
    payload = build_csv(questionnaire, load_responses(slug))
    return send_file(
        io.BytesIO(payload),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{slug}-summary.csv",
    )


@app.get("/q/<slug>/responses/<folder>/<filename>")
def own_screenshot(slug: str, folder: str, filename: str):
    questionnaire = require_questionnaire(slug)
    email = signed_in_email()
    root = response_root(slug)
    if (
        email is None
        or root is None
        or not SCREENSHOT_RE.fullmatch(filename)
        or (questionnaire["enabled"] and not email_on_respondent_list(questionnaire, email))
        or (not questionnaire["started"] and not email_is_admin(email))
    ):
        abort(404)
    if folder in {"", ".", ".."} or "/" in folder or "\\" in folder:
        abort(404)
    directory = root / folder
    meta_path = directory / "response.json"
    image_path = directory / filename
    if not meta_path.is_file() or not image_path.is_file():
        abort(404)
    if not contained(root, meta_path) or not contained(root, image_path):
        abort(404)
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        abort(404)
    if not isinstance(data, dict):
        abort(404)
    stored = data.get("email")
    listed = data.get("screenshots")
    if not isinstance(stored, str) or normalize_email(stored) != normalize_email(email):
        abort(404)
    if not isinstance(listed, list) or filename not in listed:
        abort(404)
    return send_file(image_path)


@app.get("/admin/q/<slug>/responses/<folder>/<filename>")
def screenshot(slug: str, folder: str, filename: str):
    require_questionnaire(slug)
    root = response_root(slug)
    if root is None or not SCREENSHOT_RE.fullmatch(filename):
        abort(404)
    if folder in {"", ".", ".."} or "/" in folder or "\\" in folder:
        abort(404)
    path = root / folder / filename
    if not path.is_file() or not contained(root, path):
        abort(404)
    return send_file(path)


@app.errorhandler(404)
def not_found(_error):
    return (
        render_template(
            "error.html",
            heading="Page not found",
            message="That questionnaire or page does not exist.",
        ),
        404,
    )


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return (
        render_template(
            "error.html",
            heading="Upload too large",
            message="The screenshots are too large. Send at most 8 images, 8 MB each.",
        ),
        413,
    )


if __name__ == "__main__":
    QUESTIONNAIRES.mkdir(exist_ok=True)
    RESPONSES.mkdir(exist_ok=True)
    RESPONDENT_LISTS.mkdir(exist_ok=True)
    ensure_secret()
    app.run(host="0.0.0.0", port=5000, debug=False)
