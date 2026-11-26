# Questionnaires

Local form portal. People on the allowed list sign in and send updates. Admins review them.

## Install

You need Python 3.10 or newer.

Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS and Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

From the project folder, with the virtual environment active:

```powershell
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The server listens only on this machine, port 5000.

Stop it with Ctrl+C.

## Accounts

### Who can sign in

`allowed.txt` is the sign-in list. One person per line, with a name and an email:

```text
Ada Lovelace <ada@example.com>
Grace Hopper <grace@example.com>
```

Blank lines and lines starting with `#` are ignored. The name is required to submit a form. An email with no name can sign in, but the submit button stays off until you add the name.

The name shown on a reply comes from this file. Change it here, and the next sign-in picks up the new name.

### Admins

`admins.txt` is one email per line. Those addresses can open [http://127.0.0.1:5000/admin](http://127.0.0.1:5000/admin). An admin must also be in `allowed.txt`, or they cannot sign in.

```text
ada@example.com
```

### Starter password

On the first start, the app creates `auth.json` if it is missing. The shared starter password is `default_password` in that file. A newly created file uses `welcome`. Change that value before other people use the portal.

`auth.json` also holds `secret_key`, which the app fills in. Leave that value as it is.

### Choosing a password

The first time someone signs in, they use the starter password, then choose their own. It must be at least 8 characters and different from the starter password. The app stores that password in `accounts.json`. Later sign-ins use the password they chose, not the starter password.

Do not edit the password hashes in `accounts.json`.

`accounts.sample.json` shows the file shape. Both sample passwords are `sample-password`. Copy it to `accounts.json` only on a new machine that does not already have accounts. Those sample emails also need to be in `allowed.txt` before they can sign in. The real `accounts.json` is not committed.

### Reset a password

There is no reset page. Stop the server, open `accounts.json`, and delete the entry for that email. The key is the email in lowercase. They can then sign in again with the starter password and choose a new one.

## Questionnaires

Each questionnaire is a folder under `questionnaires/`. The folder name is the link, so `questionnaires/release-checklist/` is served at `/q/release-checklist`. Use letters, numbers, and single hyphens. `release-checklist` is included as an example.

A questionnaire needs two files. `respondents.txt` is optional.

### config.json

```json
{
  "title": "Release checklist",
  "label": "Status update",
  "statuses": ["Done", "In progress", "Blocked"],
  "services": ["VPN", "Email", "Cloud PC"],
  "start": "2026-12-01",
  "deadline": "2026-12-01",
  "enabled": true
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `title` | Yes | Heading on the form and in the admin list. |
| `statuses` | No | Choices on the form. If you omit it, the choices are Done, In progress, and Blocked. A list with no Done or Completed choice gets Completed at the front. |
| `services` | No | Services the person confirms they can access. Each checked box is saved as access, and each unchecked box is saved as no access. If you omit it, or the list is empty, the form does not show these checkboxes. |
| `label` | No | Short name next to the logo. If you omit it, the label is Responses. |
| `start` | No | Date the questionnaire appears on the people portal, `YYYY-MM-DD`, or date and time, `YYYY-MM-DDTHH:MM`. Times are local. A date opens at the beginning of that day. If you omit it, or the value is not a valid date, the questionnaire is visible as soon as it is turned on. On the admin page, a future start is listed under Planned with the time it becomes active. After that time it is listed under Active. It stays on the people portal until an admin turns it off. |
| `deadline` | No | Due date, `YYYY-MM-DD`, or date and time, `YYYY-MM-DDTHH:MM`. Times are local. A date is due through the end of that day. The due line is red after it has passed. If you omit it, or the value is not a valid date, no deadline is shown. |
| `enabled` | No | `true` accepts replies. `false` closes the form. If you omit it, the questionnaire is open. |

`statuses` must be a list of non-empty text. Done and Completed, in any capitalization, count as complete. When the list has neither, Completed is added at the front so the progress bar can move. `title` cannot be blank.

`services` must be a list of non-empty text. Each name is at most 120 characters, names cannot repeat, and the list can hold at most 40 services. A list that breaks those rules hides the questionnaire until you fix it. The saved reply and summary.csv record every listed service, with `yes` or `no`. Replies saved before you add `services` do not show an access list.

### instructions.md

Markdown shown at the top of the form, above the questions. Headings, paragraphs, lists, and links are fine.

### respondents.txt

One email per line. This is the list of people you expect a reply from. While the questionnaire is open, only these addresses can see it. The people portal and the admin pages show how many of them are completed, a count for each other status, and how many have not submitted. The bar is the completed share of that list.

```text
ada@example.com
grace@example.com
```

If this file is missing, anyone in `allowed.txt` can open the questionnaire, and those pages do not show these counts.

### Turn a questionnaire on or off

Set `"enabled": false` in `config.json`, or sign in as an admin and use Turn off. A closed questionnaire leaves the admin list and the people portal, and is listed at [http://127.0.0.1:5000/admin/archive](http://127.0.0.1:5000/admin/archive). Saved replies stay in `responses/` and on the admin review page. People cannot send a new one until you turn it back on.

Restart is not required after you add or edit a questionnaire. Refresh the page.

### Review

On the admin page, Review responses opens the replies for that questionnaire. Download summary.csv on that page saves a spreadsheet of them. Each reply is also stored under `responses/<folder-name>/`.

Record update on a person’s row files a new reply under their name and email. The reply shows that you recorded it, and summary.csv includes your email in `recorded_by`. Their earlier updates stay. You can record an update after the questionnaire is turned off.

## Files that stay on this machine

`auth.json`, `accounts.json`, and `responses/` are listed in `.gitignore`. They are created on this computer and are not part of the repository.


nohup python -u app.py > app.log 2>&1 &
