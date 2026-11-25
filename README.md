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
  "deadline": "2026-12-01",
  "enabled": true
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `title` | Yes | Heading on the form and in the admin list. |
| `statuses` | No | Choices on the form. If you omit it, the choices are Done, In progress, and Blocked. |
| `label` | No | Short name next to the logo. If you omit it, the label is Responses. |
| `deadline` | No | Due date, `YYYY-MM-DD`, or date and time, `YYYY-MM-DDTHH:MM`. Times are local. A date is due through the end of that day. The due line is red after it has passed. If you omit it, or the value is not a valid date, no deadline is shown. |
| `enabled` | No | `true` accepts replies. `false` closes the form. If you omit it, the questionnaire is open. |

`statuses` must be a list of non-empty text. `title` cannot be blank.

### instructions.md

Markdown shown at the top of the form, above the questions. Headings, paragraphs, lists, and links are fine.

### respondents.txt

One email per line. This is the list of people you expect a reply from. While the questionnaire is open, only these addresses can see it, and the admin page shows how many of them have replied.

```text
ada@example.com
grace@example.com
```

If this file is missing, anyone in `allowed.txt` can open the questionnaire, and the admin page does not show a replied count.

### Turn a questionnaire on or off

Set `"enabled": false` in `config.json`, or sign in as an admin and use Turn off. A closed questionnaire stays in the admin list. People who already replied can still read their saved response. They cannot send a new one until you turn it back on.

Restart is not required after you add or edit a questionnaire. Refresh the page.

### Review

On the admin page, Review responses opens the replies for that questionnaire. Download summary.csv on that page saves a spreadsheet of them. Each reply is also stored under `responses/<folder-name>/`.

## Files that stay on this machine

`auth.json`, `accounts.json`, and `responses/` are listed in `.gitignore`. They are created on this computer and are not part of the repository.
