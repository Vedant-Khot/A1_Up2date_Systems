import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routing import plan_route, geocode_address

def test_routing():
    lat, lng = geocode_address("14 Baker Street, London NW1 6XE")
    print("Baker Street geocode:", lat, lng)
    assert 51.0 < lat < 52.0
    assert -1.0 < lng < 1.0

    jobs = [
        {"id": "1", "work_ref": "NL-1101", "site_address": "14 Baker Street, London NW1 6XE", "status": "new"},
        {"id": "2", "work_ref": "NL-1102", "site_address": "8 Quay Road, London E14 9SH", "status": "in_progress"},
        {"id": "3", "work_ref": "NL-1103", "site_address": "Flat 3, 22 King Street, London SE1 8PB", "status": "completed"},
    ]

    plan = plan_route("14 Baker Street, London NW1 6XE", jobs, round_trip=True)
    print("Total miles:", plan["total_distance_miles"])
    print("Total mins:", plan["total_duration_mins"])
    print("Stops count:", len(plan["stops"]))
    assert len(plan["stops"]) == 3
    assert plan["total_distance_miles"] > 0
    assert "https://www.google.com/maps/dir/" in plan["google_maps_url"]
    print("Google Maps URL:", plan["google_maps_url"])
    print("TEST ROUTING PASSED!")

if __name__ == "__main__":
    test_routing()
