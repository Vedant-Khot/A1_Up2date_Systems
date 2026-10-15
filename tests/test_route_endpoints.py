import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app, store

def test_route_endpoints():
    with TestClient(app) as client:
        # 1. Login as office
        login = client.post("/api/login", json={"email": "office@sitelog.demo", "password": "office123"})
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        cookies = {"sitelog_session": token}

        # 2. Check /route-planner web page
        page_res = client.get("/route-planner", cookies=cookies)
        assert page_res.status_code == 200, page_res.text
        html = page_res.text
        assert "Route Planner" in html
        assert "routeMap" in html
        assert "leaflet" in html.lower()

        # 3. Check GET /api/route-plan
        api_res = client.get("/api/route-plan", headers=headers)
        assert api_res.status_code == 200, api_res.text
        plan = api_res.json()
        assert "start" in plan
        assert "stops" in plan
        assert "total_distance_miles" in plan
        assert "google_maps_url" in plan
        assert "https://www.google.com/maps/dir/" in plan["google_maps_url"]
        print("GET /api/route-plan returned stops:", len(plan["stops"]))
        print("Total miles:", plan["total_distance_miles"])

        # 4. Check POST /api/route-plan with specific jobs
        jobs_res = client.get("/api/jobs", headers=headers)
        jobs = jobs_res.json()["jobs"]
        if jobs:
            job_ids = [j["id"] for j in jobs[:3]]
            post_res = client.post(
                "/api/route-plan",
                headers=headers,
                json={
                    "start_address": "HQ Office, 14 Baker Street, London NW1 6XE",
                    "job_ids": job_ids,
                    "round_trip": True,
                },
            )
            assert post_res.status_code == 200, post_res.text
            post_plan = post_res.json()
            assert len(post_plan["stops"]) == len(job_ids)
            assert post_plan["round_trip"] is True
            print("POST /api/route-plan returned stops:", len(post_plan["stops"]))
            print("Post round trip miles:", post_plan["total_distance_miles"])

        print("=== ALL ROUTE PLANNER ENDPOINTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    test_route_endpoints()
