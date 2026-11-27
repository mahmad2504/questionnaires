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

The sign-in list is `allowed.txt`. The file is encrypted, so opening it does not show names or emails. Sign in as an admin and open [http://127.0.0.1:5000/admin/people](http://127.0.0.1:5000/admin/people) to see the list, add a person, or remove one. Do not edit `allowed.txt` by hand.

Add a name and an email. The name is required to submit a form. Adding an email that is already on the list updates that person's name. You cannot remove your own email.

Removing someone only takes them off the sign-in list. Their password in `accounts.json` stays, and they stay on any questionnaire respondent list. Adding the same email back lets them sign in with the password they already chose.

The name shown on a reply comes from this list. Change it on the People page, and the next sign-in picks up the new name.

The encryption key comes from `secret_key` in `auth.json`. Leave that value as it is. Changing it makes `allowed.txt`, each questionnaire `respondents.txt`, and named lists in `respondent-lists/` unreadable.

If you replace `allowed.txt` with the old plain text, the app encrypts it again on the next request. One person per line:

```text
Ada Lovelace <ada@example.com>
Grace Hopper <grace@example.com>
```

Blank lines and lines starting with `#` are ignored. An email with no name can sign in, but the submit button stays off until you add the name.

### Admins

`admins.txt` is one email per line. Those addresses can open [http://127.0.0.1:5000/admin](http://127.0.0.1:5000/admin). An admin must also be in `allowed.txt`, or they cannot sign in.

```text
ada@example.com
```

### Starter password

On the first start, the app creates `auth.json` if it is missing. The shared starter password is `default_password` in that file. A newly created file uses `welcome`. Change that value before other people use the portal.

`auth.json` also holds `secret_key`, which the app fills in. Leave that value as it is. It is the key for `allowed.txt`, each questionnaire `respondents.txt`, and named lists in `respondent-lists/`.

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
| `services` | No | Services the person confirms they can access. Each checked box is saved as access, and each unchecked box is saved as no access. If you omit it, or the list is empty, the form does not show these checkboxes. When `service_results` is set, these names are the rows and the checkboxes are not shown. |
| `service_results` | No | Result choices for each service, such as Success, Failed, and Access denied. When this list is set, each service shows those choices instead of an access checkbox. A Completed or Done status requires one result for every service. Other statuses can leave them unset. Omit it, or use an empty list, to keep the checkboxes. |
| `label` | No | Short name next to the logo. If you omit it, the label is Responses. |
| `start` | No | Date the questionnaire appears on the people portal, `YYYY-MM-DD`, or date and time, `YYYY-MM-DDTHH:MM`. Times are local. A date opens at the beginning of that day. If you omit it, or the value is not a valid date, the questionnaire is visible as soon as it is turned on. On the admin page, a future start is listed under Planned with the time it becomes active. After that time it is listed under Active. It stays on the people portal until an admin turns it off. |
| `deadline` | No | Due date, `YYYY-MM-DD`, or date and time, `YYYY-MM-DDTHH:MM`. Times are local. A date is due through the end of that day. The due line is red after it has passed. If you omit it, or the value is not a valid date, no deadline is shown. |
| `enabled` | No | `true` accepts replies. `false` closes the form. If you omit it, the questionnaire is open. |

`statuses` must be a list of non-empty text. Done and Completed, in any capitalization, count as complete. When the list has neither, Completed is added at the front so the progress bar can move. `title` cannot be blank.

A Done or Completed reply stays locked for that person. On the people portal the card shows the title, status, updated time, and service results. It has no update text, links, progress bar, or due line. They cannot submit again until an admin records a different status. Other statuses can still be submitted again. Earlier updates stay.

`services` must be a list of non-empty text. Each name is at most 120 characters, names cannot repeat, and the list can hold at most 40 services. A list that breaks those rules hides the questionnaire until you fix it. The saved reply and summary.csv record every listed service, with `yes` or `no`. Replies saved before you add `services` do not show an access list.

`service_results` uses the same rules as `services`. A list that breaks those rules, or a result list with no services, hides the questionnaire until you fix it. The saved reply and summary.csv record each service with the chosen result, for example `GitLab: Access denied`. A Completed reply that skips a service is rejected.

### instructions.md

Markdown shown at the top of the form, above the questions. Headings, paragraphs, lists, and links are fine.

### respondents.txt

The list of people you expect a reply from. Sign in as an admin and open Respondents on that questionnaire, for example [http://127.0.0.1:5000/admin/q/release-checklist/respondents](http://127.0.0.1:5000/admin/q/release-checklist/respondents). Add an email or remove one there. Do not edit `respondents.txt` by hand.

The file is encrypted with `secret_key` from `auth.json`, the same key `allowed.txt` uses. Leave that value as it is. Changing it makes `respondents.txt` unreadable.

While the list exists, only these addresses can see the questionnaire. The people portal and the admin pages show how many of them are completed, a count for each other status, and how many have not submitted. The bar is the completed share of that list.

An empty list means nobody can open the questionnaire. Allow everyone who can sign in, on the Respondents page, deletes the file. If the file is missing, anyone in `allowed.txt` can open the questionnaire, and those pages do not show these counts.

If you replace `respondents.txt` with plain text, the app encrypts it again on the next request. One email per line:

```text
ada@example.com
grace@example.com
```

Blank lines and lines starting with `#` are ignored.

### Named respondent lists

On the Respondents page you can copy the current emails under a name, then import that name onto any questionnaire. Import adds missing addresses and leaves people already on the list.

The copy is stored under `respondent-lists/`, not inside the questionnaire folder. Deleting the questionnaire leaves the named list in place. The files are encrypted with `secret_key` from `auth.json`, the same key `respondents.txt` uses. Leave that value as it is. Do not edit `respondent-lists/` by hand.

### Turn a questionnaire on or off

Set `"enabled": false` in `config.json`, or sign in as an admin and use Turn off. A closed questionnaire leaves the admin list and the people portal, and is listed at [http://127.0.0.1:5000/admin/archive](http://127.0.0.1:5000/admin/archive). Saved replies stay in `responses/` and on the admin review page. People cannot send a new one until you turn it back on.

Restart is not required after you add or edit a questionnaire. Refresh the page.

### Review

On the admin page, Review responses opens the replies for that questionnaire. Download summary.csv on that page saves a spreadsheet of them. Each reply is also stored under `responses/<folder-name>/`.

Record update on a person’s row files a new reply under their name and email. The reply shows that you recorded it, and summary.csv includes your email in `recorded_by`. Their earlier updates stay. You can record an update after the questionnaire is turned off. Recording a status other than Done or Completed opens the form again for that person.

## Files that stay on this machine

`auth.json`, `accounts.json`, and `responses/` are listed in `.gitignore`. They are created on this computer and are not part of the repository.


nohup python -u app.py > app.log 2>&1 &
