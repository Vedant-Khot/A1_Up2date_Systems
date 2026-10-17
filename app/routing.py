import hashlib
import itertools
import json
import math
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

# Cache geocoded coordinates to avoid redundant network lookups
_GEOCODE_CACHE: dict[str, tuple[float, float]] = {}

LONDON_AREA_COORDS: dict[str, tuple[float, float]] = {
    "NW1": (51.532, -0.144),
    "NW2": (51.554, -0.218),
    "NW3": (51.555, -0.176),
    "NW5": (51.550, -0.142),
    "NW8": (51.531, -0.170),
    "E1": (51.517, -0.058),
    "E2": (51.529, -0.063),
    "E3": (51.527, -0.026),
    "E14": (51.507, -0.019),
    "E16": (51.509, 0.024),
    "EC1": (51.523, -0.098),
    "EC2": (51.519, -0.086),
    "WC1": (51.524, -0.123),
    "WC2": (51.513, -0.123),
    "W1": (51.514, -0.146),
    "W2": (51.515, -0.180),
    "SW1": (51.498, -0.141),
    "SW3": (51.491, -0.166),
    "SW7": (51.495, -0.179),
    "SE1": (51.499, -0.093),
    "SE10": (51.482, 0.005),
    "SE16": (51.498, -0.050),
    "DEFAULT": (51.52366, -0.158516),  # Default: HQ at Baker Street, London
}


def extract_uk_postcode(address: str) -> Optional[str]:
    """Extract full UK postcode from address string if present."""
    match = re.search(r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b", address.upper())
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return None


def extract_uk_outcode(address: str) -> Optional[str]:
    """Extract UK outcode / postal district (e.g. NW1, E14, SE1)."""
    match = re.search(r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\b", address.upper())
    if match:
        return match.group(1)
    return None


def geocode_address(address: str) -> tuple[float, float]:
    """
    Geocode an address to (latitude, longitude).
    Uses free postcodes.io API for UK postcodes with instant local fallbacks.
    """
    clean = address.strip()
    if not clean:
        return LONDON_AREA_COORDS["DEFAULT"]

    if clean in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean]

    # Try full postcode with postcodes.io
    postcode = extract_uk_postcode(clean)
    if postcode:
        formatted = postcode.replace(" ", "")
        try:
            url = f"https://api.postcodes.io/postcodes/{urllib.parse.quote(formatted)}"
            req = urllib.request.Request(url, headers={"User-Agent": "SiteLog-RoutePlanner/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == 200 and data.get("result"):
                    coords = (float(data["result"]["latitude"]), float(data["result"]["longitude"]))
                    _GEOCODE_CACHE[clean] = coords
                    return coords
        except Exception:
            pass

    # Try outcode in local dictionary
    outcode = extract_uk_outcode(clean)
    if outcode and outcode in LONDON_AREA_COORDS:
        coords = LONDON_AREA_COORDS[outcode]
        _GEOCODE_CACHE[clean] = coords
        return coords

    # Try postcodes.io outcode endpoint
    if outcode:
        try:
            url = f"https://api.postcodes.io/outcodes/{urllib.parse.quote(outcode)}"
            req = urllib.request.Request(url, headers={"User-Agent": "SiteLog-RoutePlanner/1.0"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == 200 and data.get("result"):
                    coords = (float(data["result"]["latitude"]), float(data["result"]["longitude"]))
                    _GEOCODE_CACHE[clean] = coords
                    return coords
        except Exception:
            pass

    # Deterministic fallback offset based on address hash near Central London
    # Ensures test/mock addresses get realistic spread across Greater London
    h = int(hashlib.md5(clean.encode()).hexdigest()[:6], 16)
    d_lat = ((h % 1000) / 1000.0 - 0.5) * 0.12
    d_lon = (((h // 1000) % 1000) / 1000.0 - 0.5) * 0.16
    base_lat, base_lon = LONDON_AREA_COORDS["DEFAULT"]
    coords = (round(base_lat + d_lat, 5), round(base_lon + d_lon, 5))
    _GEOCODE_CACHE[clean] = coords
    return coords


# Cache road distance matrix and route geometry
_ROAD_MATRIX_CACHE: dict[str, tuple[list[list[float]], list[list[float]]]] = {}
_ROAD_ROUTE_CACHE: dict[str, dict[str, Any]] = {}


def fetch_road_matrix(
    coords: list[tuple[float, float]],
) -> Optional[tuple[list[list[float]], list[list[float]]]]:
    """
    Fetch pairwise road driving distance (miles) and duration (mins) matrix via OSRM.
    Returns (distances_matrix, durations_matrix) or None if request fails.
    """
    if len(coords) < 2:
        return None
    coords_str = ";".join(f"{round(lon, 5)},{round(lat, 5)}" for lat, lon in coords)
    if coords_str in _ROAD_MATRIX_CACHE:
        return _ROAD_MATRIX_CACHE[coords_str]

    url = f"https://router.project-osrm.org/table/v1/driving/{coords_str}?annotations=distance,duration"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SiteLog-RoutePlanner/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            if data.get("code") == "Ok" and "distances" in data and "durations" in data:
                # OSRM distances are in meters, convert to miles
                dist_matrix = [
                    [round((d or 0.0) / 1609.344, 2) for d in row]
                    for row in data["distances"]
                ]
                # OSRM durations are in seconds, convert to minutes
                dur_matrix = [
                    [int(round((t or 0.0) / 60.0)) for t in row]
                    for row in data["durations"]
                ]
                _ROAD_MATRIX_CACHE[coords_str] = (dist_matrix, dur_matrix)
                return dist_matrix, dur_matrix
    except Exception:
        pass
    return None


def fetch_road_route(
    ordered_coords: list[tuple[float, float]],
    round_trip: bool = False,
) -> Optional[dict[str, Any]]:
    """
    Fetch actual road route driving geometry, distance per leg, and drive duration via OSRM.
    Returns dictionary with total_distance_miles, total_duration_mins, legs, geometry_coords,
    or None if request fails.
    """
    if len(ordered_coords) < 2:
        return None
    coords_str = ";".join(f"{round(lon, 5)},{round(lat, 5)}" for lat, lon in ordered_coords)
    cache_key = f"{coords_str}_rt{round_trip}"
    if cache_key in _ROAD_ROUTE_CACHE:
        return _ROAD_ROUTE_CACHE[cache_key]

    url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SiteLog-RoutePlanner/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                total_distance_miles = round(route.get("distance", 0.0) / 1609.344, 1)
                total_duration_mins = int(round(route.get("duration", 0.0) / 60.0))
                legs_data: list[dict[str, Any]] = []
                for leg in route.get("legs", []):
                    legs_data.append({
                        "distance_miles": round(leg.get("distance", 0.0) / 1609.344, 1),
                        "duration_mins": int(round(leg.get("duration", 0.0) / 60.0)),
                    })
                geometry_coords = route.get("geometry", {}).get("coordinates", [])
                result = {
                    "total_distance_miles": total_distance_miles,
                    "total_duration_mins": total_duration_mins,
                    "legs": legs_data,
                    "geometry_coords": geometry_coords,
                }
                _ROAD_ROUTE_CACHE[cache_key] = result
                return result
    except Exception:
        pass
    return None


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate estimated driving distance in miles between two coordinates.
    Includes a 1.25 urban street network winding factor.
    """
    r = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    straight_line = 2.0 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))
    return straight_line * 1.25


def estimate_drive_minutes(miles: float) -> int:
    """Estimate driving time in London city conditions (~18-20 mph average)."""
    if miles <= 0.05:
        return 0
    mins = int(round((miles / 18.0) * 60.0))
    return max(2, mins)


def solve_best_route(
    start_lat: float,
    start_lon: float,
    stops: list[dict[str, Any]],
    round_trip: bool = False,
    dist_matrix: Optional[list[list[float]]] = None,
) -> list[int]:
    """
    Find optimal order of stops to minimize total route distance.
    Returns list of indices into `stops`.
    Uses dist_matrix if provided (where index 0 is start depot, 1..N are stops),
    otherwise uses haversine_miles.
    Uses exact permutation search for N <= 8, and Nearest Neighbor + 2-Opt for larger sets.
    """
    n = len(stops)
    if n <= 1:
        return list(range(n))

    coords = [(s["lat"], s["lng"]) for s in stops]

    def pair_dist(from_idx: int, to_idx: int) -> float:
        # from_idx / to_idx: 0 is start depot, 1..n is stop (idx-1 in stops)
        if dist_matrix and len(dist_matrix) > max(from_idx, to_idx):
            return dist_matrix[from_idx][to_idx]
        p1 = (start_lat, start_lon) if from_idx == 0 else coords[from_idx - 1]
        p2 = (start_lat, start_lon) if to_idx == 0 else coords[to_idx - 1]
        return haversine_miles(p1[0], p1[1], p2[0], p2[1])

    def route_distance(order: list[int]) -> float:
        total = 0.0
        cur_node = 0  # 0 is start depot
        for idx in order:
            nxt_node = idx + 1
            total += pair_dist(cur_node, nxt_node)
            cur_node = nxt_node
        if round_trip and order:
            total += pair_dist(cur_node, 0)
        return total

    # Exact brute-force search for up to 8 stops (8! = 40,320 checks, takes ~15ms)
    if n <= 8:
        best_order: list[int] = []
        min_dist = float("inf")
        for perm in itertools.permutations(range(n)):
            cand = list(perm)
            d = route_distance(cand)
            if d < min_dist:
                min_dist = d
                best_order = cand
        return best_order

    # Nearest Neighbor heuristic
    unvisited = set(range(n))
    order: list[int] = []
    cur_node = 0
    while unvisited:
        nearest = min(
            unvisited,
            key=lambda idx: pair_dist(cur_node, idx + 1),
        )
        order.append(nearest)
        unvisited.remove(nearest)
        cur_node = nearest + 1

    # 2-Opt refinement
    improved = True
    passes = 0
    best_dist = route_distance(order)
    while improved and passes < 50:
        improved = False
        passes += 1
        for i in range(n - 1):
            for j in range(i + 1, n):
                new_order = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                new_dist = route_distance(new_order)
                if new_dist < best_dist - 1e-4:
                    order = new_order
                    best_dist = new_dist
                    improved = True
                    break
            if improved:
                break

    return order


def build_navigation_urls(lat: float, lng: float, address: str = "") -> dict[str, str]:
    """Single-destination links for Google Maps, Apple Maps, and Waze."""
    coords = f"{lat},{lng}"
    encoded_coords = urllib.parse.quote(coords)
    label = (address or "").strip()
    encoded_label = urllib.parse.quote(label) if label else encoded_coords
    return {
        "google": (
            f"https://www.google.com/maps/dir/?api=1"
            f"&destination={encoded_coords}"
            f"&travelmode=driving"
        ),
        "apple": f"https://maps.apple.com/?daddr={encoded_coords}&dirflg=d",
        "waze": f"https://waze.com/ul?ll={encoded_coords}&navigate=yes",
        "google_search": f"https://www.google.com/maps/search/?api=1&query={encoded_label}",
        "apple_search": f"https://maps.apple.com/?daddr={encoded_label}&dirflg=d",
        "waze_search": f"https://waze.com/ul?q={encoded_label}&navigate=yes",
    }


def build_google_maps_directions_url(
    start_label: str,
    start_coords: tuple[float, float],
    ordered_stops: list[dict[str, Any]],
    round_trip: bool = False,
) -> str:
    """Build multi-stop Google Maps URL."""
    if not ordered_stops:
        return f"https://www.google.com/maps/search/?api=1&query={start_coords[0]},{start_coords[1]}"

    origin = f"{start_coords[0]},{start_coords[1]}"
    dest_stop = ordered_stops[-1] if not round_trip else None
    destination = (
        f"{dest_stop['lat']},{dest_stop['lng']}"
        if dest_stop
        else f"{start_coords[0]},{start_coords[1]}"
    )

    waypoints_stops = (
        ordered_stops[:-1] if not round_trip else ordered_stops
    )
    waypoint_strs = [f"{s['lat']},{s['lng']}" for s in waypoints_stops]
    waypoints_param = urllib.parse.quote("|".join(waypoint_strs)) if waypoint_strs else ""

    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={urllib.parse.quote(origin)}"
        f"&destination={urllib.parse.quote(destination)}"
    )
    if waypoints_param:
        url += f"&waypoints={waypoints_param}"
    url += "&travelmode=driving"
    return url


def plan_route(
    start_address: str,
    jobs: list[dict[str, Any]],
    round_trip: bool = False,
) -> dict[str, Any]:
    """
    Main route planning function.
    Geocodes all jobs, finds the best route order using real road distance matrix,
    calculates road distances/durations and turn-by-turn geometry,
    and returns a complete route plan ready for display and navigation.
    """
    start_lat, start_lng = geocode_address(start_address)
    start_info = {
        "label": start_address.strip() or "HQ Office",
        "address": start_address.strip() or "HQ Office, 14 Baker Street, London NW1 6XE",
        "lat": start_lat,
        "lng": start_lng,
    }

    if not jobs:
        return {
            "start": start_info,
            "stops": [],
            "total_distance_miles": 0.0,
            "total_duration_mins": 0,
            "round_trip": round_trip,
            "google_maps_url": build_google_maps_directions_url(start_info["label"], (start_lat, start_lng), [], round_trip),
            "geometry_coords": [],
            "is_road_calculated": True,
        }

    # Geocode all stops
    enriched_stops: list[dict[str, Any]] = []
    for job in jobs:
        lat, lng = geocode_address(job.get("site_address") or "")
        enriched_stops.append({
            "job_id": job["id"],
            "work_ref": job["work_ref"],
            "client": job.get("client") or "",
            "site_address": job.get("site_address") or "",
            "engineers": job.get("engineers") or "",
            "status": job.get("status") or "new",
            "status_label": job.get("status_label") or (job.get("status") or "new").title(),
            "issue": job.get("issue") or "",
            "job_date": job.get("job_date") or "",
            "lat": lat,
            "lng": lng,
        })

    # Collect all coords: index 0 is Start Depot, 1..N are stops
    all_coords = [(start_lat, start_lng)] + [(s["lat"], s["lng"]) for s in enriched_stops]
    road_matrix = fetch_road_matrix(all_coords)
    dist_matrix = road_matrix[0] if road_matrix else None

    # Solve TSP with road matrix (or haversine fallback)
    optimal_indices = solve_best_route(
        start_lat,
        start_lng,
        enriched_stops,
        round_trip=round_trip,
        dist_matrix=dist_matrix,
    )

    # Ordered coordinates for route calculation
    ordered_coords = [(start_lat, start_lng)] + [
        (enriched_stops[idx]["lat"], enriched_stops[idx]["lng"])
        for idx in optimal_indices
    ]
    if round_trip:
        ordered_coords.append((start_lat, start_lng))

    # Fetch real road route geometry and leg breakdown
    road_route = fetch_road_route(ordered_coords, round_trip=round_trip)
    is_road_calculated = road_route is not None

    ordered_stops: list[dict[str, Any]] = []
    cur_lat, cur_lon = start_lat, start_lng
    cum_dist = 0.0
    cum_dur = 0
    road_legs = road_route.get("legs", []) if road_route else []

    for step_num, idx in enumerate(optimal_indices, start=1):
        stop_data = dict(enriched_stops[idx])
        leg_idx = step_num - 1

        if leg_idx < len(road_legs):
            leg_dist = road_legs[leg_idx]["distance_miles"]
            leg_dur = road_legs[leg_idx]["duration_mins"]
        else:
            leg_dist = haversine_miles(cur_lat, cur_lon, stop_data["lat"], stop_data["lng"])
            leg_dur = estimate_drive_minutes(leg_dist)

        cum_dist += leg_dist
        cum_dur += leg_dur

        stop_data["stop_number"] = step_num
        stop_data["leg_distance_miles"] = round(leg_dist, 1)
        stop_data["leg_duration_mins"] = leg_dur
        stop_data["cumulative_distance_miles"] = round(cum_dist, 1)
        stop_data["cumulative_duration_mins"] = cum_dur

        # Single stop navigation links (Google / Apple / Waze)
        nav = build_navigation_urls(stop_data["lat"], stop_data["lng"], stop_data.get("site_address") or "")
        stop_data["nav_url"] = nav["google"]
        stop_data["nav_google"] = nav["google"]
        stop_data["nav_apple"] = nav["apple"]
        stop_data["nav_waze"] = nav["waze"]

        ordered_stops.append(stop_data)
        cur_lat, cur_lon = stop_data["lat"], stop_data["lng"]

    # If round trip, calculate return leg
    return_leg = None
    if round_trip and ordered_stops:
        ret_leg_idx = len(ordered_stops)
        if ret_leg_idx < len(road_legs):
            ret_dist = road_legs[ret_leg_idx]["distance_miles"]
            ret_dur = road_legs[ret_leg_idx]["duration_mins"]
        else:
            ret_dist = haversine_miles(cur_lat, cur_lon, start_lat, start_lng)
            ret_dur = estimate_drive_minutes(ret_dist)

        cum_dist += ret_dist
        cum_dur += ret_dur
        return_leg = {
            "leg_distance_miles": round(ret_dist, 1),
            "leg_duration_mins": ret_dur,
            "cumulative_distance_miles": round(cum_dist, 1),
            "cumulative_duration_mins": cum_dur,
        }

    total_dist = road_route["total_distance_miles"] if road_route else round(cum_dist, 1)
    total_dur = road_route["total_duration_mins"] if road_route else cum_dur

    # Road geometry coordinates: list of [lng, lat]
    if road_route and "geometry_coords" in road_route:
        geometry_coords = road_route["geometry_coords"]
    else:
        # Fallback straight line coordinates: [lng, lat]
        geometry_coords = [[lng, lat] for lat, lng in ordered_coords]

    gmaps_url = build_google_maps_directions_url(
        start_info["label"],
        (start_lat, start_lng),
        ordered_stops,
        round_trip=round_trip,
    )

    return {
        "start": start_info,
        "stops": ordered_stops,
        "return_leg": return_leg,
        "total_distance_miles": total_dist,
        "total_duration_mins": total_dur,
        "round_trip": round_trip,
        "google_maps_url": gmaps_url,
        "geometry_coords": geometry_coords,
        "is_road_calculated": is_road_calculated,
    }
