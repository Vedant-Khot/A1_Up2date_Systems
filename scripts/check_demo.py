from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    with TestClient(app) as client:
        login = client.post("/api/login", json={"email": "office@sitelog.demo", "password": "office123"})
        print("login", login.status_code)
        token = login.json()["token"]
        body = client.get("/api/jobs", headers={"Authorization": f"Bearer {token}"}).json()
        print("office_counts", body["counts"])
        assert set(body["counts"]) == {"new", "in_progress", "discarded", "completed"}
        dashboard = client.post(
            "/login",
            data={"email": "office@sitelog.demo", "password": "office123"},
            follow_redirects=True,
        )
        print("dashboard", dashboard.status_code, "New Jobs" in dashboard.text and "Latest jobs" in dashboard.text)
        new_list = client.get("/jobs?status=new", cookies={"sitelog_session": token})
        print("new_section", new_list.status_code, "New Jobs" in new_list.text)
        job = next(item for item in body["jobs"] if item["status"] == "new")
        detail = client.get(f"/jobs/{job['id']}", cookies={"sitelog_session": token})
        print("detail", detail.status_code, "Chat" in detail.text and "Discard" in detail.text)
        chat = client.post(
            f"/jobs/{job['id']}/chat",
            data={"body": "Office note"},
            cookies={"sitelog_session": token},
            follow_redirects=True,
        )
        print("chat", chat.status_code, "Office note" in chat.text)
        discarded = client.post(
            f"/api/jobs/{job['id']}/discard",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "Duplicate visit"},
        )
        print("discard", discarded.status_code, discarded.json()["status"])
        restored = client.post(
            f"/api/jobs/{job['id']}/restore",
            headers={"Authorization": f"Bearer {token}"},
        )
        print("restore", restored.status_code, restored.json()["status"])
        engineers = client.get("/api/engineers", headers={"Authorization": f"Bearer {token}"}).json()["engineers"]
        created = client.post(
            "/api/jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "work_ref": "NL-2001",
                "site_address": "1 Demo Road",
                "issue": "Test lock",
                "engineer_ids": [person["id"] for person in engineers],
            },
        )
        print("create", created.status_code, created.json()["status"], len(created.json()["assignees"]))
        alex = client.post("/api/login", json={"email": "alex@sitelog.demo", "password": "engineer123"}).json()
        mine = client.get("/api/jobs", headers={"Authorization": f"Bearer {alex['token']}"}).json()
        print("alex_refs", [item["work_ref"] for item in mine["jobs"]])
        hidden = next(item for item in body["jobs"] if item["work_ref"] == "NL-1103")
        denied = client.get(f"/api/jobs/{hidden['id']}", headers={"Authorization": f"Bearer {alex['token']}"})
        print("alex_hidden", denied.status_code)
        mailbox = client.put(
            "/api/mailbox",
            headers={"Authorization": f"Bearer {token}"},
            json={"host": "imap.example.com", "email": "jobs@sitelog.demo", "password": "secret-app-password", "port": 993},
        )
        shown = client.get("/api/mailbox", headers={"Authorization": f"Bearer {token}"}).json()
        print("mailbox", mailbox.status_code, "secret-app-password" not in str(shown), shown["email"])


if __name__ == "__main__":
    main()
