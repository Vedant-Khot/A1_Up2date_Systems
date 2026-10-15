import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app, store

def test_pagination():
    with TestClient(app) as client:
        login = client.post("/api/login", json={"email": "office@sitelog.demo", "password": "office123"})
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        cookies = {"sitelog_session": token}

        # Get jobs list
        jobs_res = client.get("/api/jobs", headers=headers)
        assert jobs_res.status_code == 200
        jobs = jobs_res.json()["jobs"]
        assert len(jobs) > 0
        job_id = jobs[0]["id"]

        # Add 15 test messages to this job
        user = store.authenticate("office@sitelog.demo", "office123")
        assert user is not None
        for i in range(15):
            store.add_message(job_id, user["id"], f"Test message {i+1}")

        # Test GET /api/jobs/{job_id}/messages?limit=10
        resp = client.get(f"/api/jobs/{job_id}/messages?limit=10", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        print("Initial 10 messages received:", len(data["messages"]))
        print("Total count:", data["total"])
        print("Has more:", data["has_more"])
        assert len(data["messages"]) == 10
        assert data["has_more"] is True
        assert "job" in data
        assert data["job"]["work_ref"] == jobs[0]["work_ref"]

        # Test GET /api/jobs/{job_id}/messages?limit=10&before={oldest}
        oldest_stamp = data["messages"][0]["created_at"]
        resp_older = client.get(f"/api/jobs/{job_id}/messages?limit=10&before={oldest_stamp}", headers=headers)
        assert resp_older.status_code == 200, resp_older.text
        data_older = resp_older.json()
        print("Older messages received:", len(data_older["messages"]))
        print("Older has more:", data_older["has_more"])
        assert len(data_older["messages"]) > 0

        # Verify chat HTML page
        html_resp = client.get(f"/jobs/{job_id}/chat", cookies=cookies)
        assert html_resp.status_code == 200
        html = html_resp.text
        assert 'id="load-older-container"' in html
        assert 'id="load-older-btn"' in html
        assert 'switchToChat' in html
        assert 'data-chat-id' in html

        print("=== ALL CHAT PAGINATION AND SPA SWITCHER VERIFICATIONS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    test_pagination()
