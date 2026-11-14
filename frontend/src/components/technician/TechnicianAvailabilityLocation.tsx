
import { useEffect } from "react";
import { sendTechnicianAvailabilityLocation } from "../../services/gpsService";
import {
  GPS_AVAILABILITY_MAX_ACCURACY_METERS,
} from "../../services/geolocationService";
import { publishTechnicianGpsFix } from "../../store/technicianGpsStore";

interface TechnicianAvailabilityLocationProps {
  technicianId: string;
  tenantId: string;
  enabled?: boolean;
}

const LOCATION_SEND_INTERVAL_MS = 30_000;

const TechnicianAvailabilityLocation = ({
  technicianId,
  tenantId,
  enabled = true,
}: TechnicianAvailabilityLocationProps) => {
  useEffect(() => {
    let active = true;
    let requestInFlight = false;
    let intervalId: number | undefined;

    const requestAndSendLocation = async () => {
      if (
        !active ||
        requestInFlight ||
        !technicianId ||
        !tenantId
      ) {
        return;
      }

      if (!navigator.geolocation) {
        console.error(
          "[TechnicianGPS] Browser geolocation is not supported."
        );
        return;
      }

      requestInFlight = true;

      try {
        console.log(
          "[TechnicianGPS] Requesting technician location..."
        );

        const position = await new Promise<GeolocationPosition>(
          (resolve, reject) => {
            navigator.geolocation.getCurrentPosition(
              resolve,
              reject,
              {
                enableHighAccuracy: true,
                maximumAge: 0,
                timeout: 30000,
              }
            );
          }
        );

        if (!active) return;

        const {
          latitude,
          longitude,
          accuracy,
          altitude,
        } = position.coords;

        if (
          !Number.isFinite(latitude) ||
          !Number.isFinite(longitude) ||
          !Number.isFinite(accuracy) ||
          latitude < -90 ||
          latitude > 90 ||
          longitude < -180 ||
          longitude > 180 ||
          accuracy <= 0 ||
          accuracy > GPS_AVAILABILITY_MAX_ACCURACY_METERS
        ) {
          console.warn(
            "[TechnicianGPS] Ignoring invalid or inaccurate GPS fix:",
            { latitude, longitude, accuracy }
          );
          return;
        }

        publishTechnicianGpsFix(
          technicianId,
          tenantId,
          position
        );

        await sendTechnicianAvailabilityLocation(
          tenantId,
          {
            latitude,
            longitude,
            accuracy,
            altitude: altitude ?? null,
            timestamp: new Date(
              position.timestamp
            ).toISOString(),
          }
        );

        if (!active) return;

        console.log(
          "[TechnicianGPS] Location successfully sent:",
          {
            technicianId,
            latitude,
            longitude,
            accuracy,
            timestamp: new Date(
              position.timestamp
            ).toISOString(),
          }
        );
      } catch (error) {
        if (!active) return;

        console.error(
          "[TechnicianGPS] Location update failed. Will retry on next interval:",
          error
        );
      } finally {
        requestInFlight = false;
      }
    };

    if (
      !enabled ||
      !technicianId ||
      !tenantId
    ) {
      return;
    }

    console.log(
      "[TechnicianGPS] Tracking started. Technician status does not affect GPS tracking."
    );

    // First GPS request immediately when the dashboard opens.
    void requestAndSendLocation();

    // Request a fresh location every 30 seconds.
    intervalId = window.setInterval(() => {
      void requestAndSendLocation();
    }, LOCATION_SEND_INTERVAL_MS);

    return () => {
      active = false;

      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
      }

      console.log(
        "[TechnicianGPS] Tracking stopped because the dashboard was unmounted."
      );
    };
  }, [technicianId, tenantId, enabled]);

  return null;
};

export default TechnicianAvailabilityLocation;
