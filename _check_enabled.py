import json
import shutil
from pathlib import Path

import app as application

application.ensure_secret()
client = application.app.test_client()
email = "mumtaz.ahmad@logicsilicon.com"
other = "enabled-check@example.com"
slug = "release-checklist"
root = Path(__file__).resolve().parent
config_path = root / "questionnaires" / slug / "config.json"
sample = root / "questionnaires" / "enabled-default-check"
planned = root / "questionnaires" / "planned-check"


def sign_in(address=email):
    with client.session_transaction() as sess:
        sess["email"] = address


def restore():
    application.set_questionnaire_enabled(slug, True)
    for folder in (sample, planned):
        if folder.exists():
            shutil.rmtree(folder)


def active_html(page: str) -> str:
    return page.split('id="planned"', 1)[0]


try:
    sign_in()
    home = client.get("/")
    assert home.status_code == 200, home.status_code
    body = home.get_data(as_text=True)
    assert "Release checklist" in body
    assert ">Open</a>" in body
    assert ">Admin</a>" in body
    assert "Turn off" not in body
    assert "Review responses" not in body
    assert "Open form" not in body
    assert "Turned off" not in body

    admin = client.get("/admin")
    assert admin.status_code == 200, admin.status_code
    admin_body = admin.get_data(as_text=True)
    assert "Release checklist" in active_html(admin_body)
    assert "Turn off" in admin_body
    assert "Open form" in admin_body
    assert "Review responses" in admin_body
    assert "Turned off" not in admin_body
    assert 'href="/admin"' in admin_body
    assert 'href="/admin/archive"' in admin_body

    off = client.post(f"/admin/q/{slug}/enabled", data={"enabled": "0"}, follow_redirects=True)
    assert off.status_code == 200, off.status_code
    off_body = off.get_data(as_text=True)
    assert "Archive" in off_body
    assert "Release checklist" in off_body
    assert "Turn on" in off_body
    assert "Open form" not in off_body
    assert 'value="http://localhost/q/release-checklist"' not in off_body
    admin_off = client.get("/admin")
    admin_off_body = admin_off.get_data(as_text=True)
    assert "Release checklist" not in admin_off_body

    user_off = client.get("/")
    user_off_body = user_off.get_data(as_text=True)
    assert "Release checklist" not in user_off_body
    assert "Closed. You can still read your saved response." not in user_off_body

    form = client.get(f"/q/{slug}")
    form_body = form.get_data(as_text=True)
    assert form.status_code == 200, form.status_code
    assert "This questionnaire is not accepting replies." in form_body
    assert 'name="update"' not in form_body

    posted = client.post(
        f"/q/{slug}",
        data={"name": "A", "status": "Done", "update": "should not save"},
    )
    assert posted.status_code == 404, posted.status_code

    thanks = client.get(f"/q/{slug}/thanks")
    assert thanks.status_code == 404, thanks.status_code

    review = client.get(f"/admin/q/{slug}")
    assert review.status_code == 200, review.status_code

    old_review = client.get(f"/q/{slug}/responses")
    assert old_review.status_code == 200, old_review.status_code
    assert "Your previous responses" in old_review.get_data(as_text=True)

    bad = client.post(f"/admin/q/{slug}/enabled", data={"enabled": "yes"})
    assert bad.status_code == 400, bad.status_code

    old_enabled = client.post(f"/q/{slug}/enabled", data={"enabled": "1"})
    assert old_enabled.status_code == 404, old_enabled.status_code

    stored = json.loads(config_path.read_text(encoding="utf-8"))
    assert stored["enabled"] is False
    assert stored["title"] == "Release checklist"
    assert stored["statuses"] == ["Done", "In progress", "Blocked"]

    on = client.post(f"/admin/q/{slug}/enabled", data={"enabled": "1"}, follow_redirects=True)
    assert on.status_code == 200, on.status_code
    on_body = on.get_data(as_text=True)
    assert "Open form" in on_body
    assert "Turned off" not in on_body
    assert client.get(f"/q/{slug}").status_code == 200
    assert "Release checklist" in client.get("/").get_data(as_text=True)

    real_allowed = application.load_allowed_emails
    application.load_allowed_emails = lambda: real_allowed() | {application.normalize_email(other)}
    sign_in(other)
    denied = client.get("/admin")
    assert denied.status_code == 404, denied.status_code
    denied_archive = client.get("/admin/archive")
    assert denied_archive.status_code == 404, denied_archive.status_code
    denied_body = denied.get_data(as_text=True)
    assert "Page not found" in denied_body
    assert "Turn off" not in denied_body
    assert "Review responses" not in denied_body
    denied_review = client.get(f"/admin/q/{slug}")
    assert denied_review.status_code == 404, denied_review.status_code
    denied_old = client.get(f"/q/{slug}/responses")
    assert denied_old.status_code == 404, denied_old.status_code
    denied_csv = client.get(f"/admin/q/{slug}/responses.csv")
    assert denied_csv.status_code == 404, denied_csv.status_code
    other_home = client.get("/")
    assert other_home.status_code == 200, other_home.status_code
    other_body = other_home.get_data(as_text=True)
    assert ">Admin</a>" not in other_body
    assert "Turn off" not in other_body
    blocked = client.post(f"/admin/q/{slug}/enabled", data={"enabled": "0"})
    assert blocked.status_code == 404, blocked.status_code
    still_on = json.loads(config_path.read_text(encoding="utf-8"))
    assert still_on["enabled"] is True
    application.load_allowed_emails = real_allowed

    sign_in()
    before_off = client.get("/admin").get_data(as_text=True).count("Turn off")
    sample.mkdir()
    (sample / "instructions.md").write_text("Hello\n", encoding="utf-8")
    (sample / "config.json").write_text(
        json.dumps({"title": "Default on", "statuses": ["Done"]}) + "\n",
        encoding="utf-8",
    )
    default_admin = client.get("/admin")
    default_body = default_admin.get_data(as_text=True)
    assert "Default on" in active_html(default_body)
    assert default_body.count("Turn off") == before_off + 1
    assert "Turned off" not in default_body
    default_home = client.get("/")
    default_home_body = default_home.get_data(as_text=True)
    assert "Default on" in default_home_body
    assert "Release checklist" in default_home_body
    assert "Turn off" not in default_home_body

    planned.mkdir()
    (planned / "instructions.md").write_text("Later\n", encoding="utf-8")
    (planned / "config.json").write_text(
        json.dumps({"title": "Opens later", "statuses": ["Done"], "start": "2099-01-01"}) + "\n",
        encoding="utf-8",
    )
    planned_body = client.get("/admin").get_data(as_text=True)
    assert 'id="planned"' in planned_body
    active_part, planned_part = planned_body.split('id="planned"', 1)
    assert "Opens later" not in active_part
    assert "Default on" in active_part
    assert "Opens later" in planned_part
    assert "Starts" in planned_part
    assert "Not on the people portal yet." in planned_part
    assert "1 Jan 2099" in planned_part
    home_later = client.get("/").get_data(as_text=True)
    assert "Opens later" not in home_later

    print("ok")
finally:
    restore()
    final = json.loads(config_path.read_text(encoding="utf-8"))
    assert final["enabled"] is True
    assert not sample.exists()
