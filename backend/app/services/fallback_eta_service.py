import math
from datetime import datetime, timezone, timedelta

class FallbackETAService:
    EARTH_RADIUS_KM = 6371.0
    URBAN_SPEED_KMH = 30.0
    HIGHWAY_SPEED_KMH = 60.0
    URBAN_BUFFER_MIN = 5
    HIGHWAY_BUFFER_MIN = 2
    URBAN_THRESHOLD_KM = 5.0
    
    def calculate_haversine_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lng = math.radians(lng2 - lng1)
        
        a = (math.sin(delta_lat / 2) ** 2 + 
             math.cos(lat1_rad) * math.cos(lat2_rad) * 
             math.sin(delta_lng / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return self.EARTH_RADIUS_KM * c
    
    def calculate_fallback_eta(self, tech_lat: float, tech_lng: float, 
                               site_lat: float, site_lng: float,
                               reason: str) -> dict:
        distance_km = self.calculate_haversine_distance(tech_lat, tech_lng, site_lat, site_lng)
        
        # Speed and buffer selection
        if distance_km < self.URBAN_THRESHOLD_KM:
            speed_kmh = self.URBAN_SPEED_KMH
            buffer_min = self.URBAN_BUFFER_MIN
            route_type = "urban"
        else:
            speed_kmh = self.HIGHWAY_SPEED_KMH
            buffer_min = self.HIGHWAY_BUFFER_MIN
            route_type = "highway"
        
        # Duration calculation
        duration_hours = distance_km / speed_kmh
        duration_minutes = (duration_hours * 60) + buffer_min
        duration_seconds = int(duration_minutes * 60)
        
        eta = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        
        return {
            "status": "estimated",
            "eta": eta.isoformat().replace("+00:00", "Z"),
            "duration_minutes": round(duration_minutes, 1),
            "distance_km": round(distance_km, 1),
            "route_type": route_type,
            "average_speed_kmh": int(speed_kmh),
            "buffer_minutes": int(buffer_min),
            "confidence": "low",
            "fallback_reason": reason,
            "disclaimer": "ETA is estimated using straight-line distance. Actual arrival may vary due to traffic and route conditions."
        }
