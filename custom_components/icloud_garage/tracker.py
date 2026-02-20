"""
Core tracking logic for iCloud Garage Automation.

Architecture
-----------
GarageCoordinator
  ├── PersonTracker("me",   device_tracker.rpip)
  └── PersonTracker("wife", device_tracker.shyiphonexs)

Each PersonTracker runs an independent, dynamically-timed poll loop:
  • When at home        → standby; resume on zone-leave event
  • When away/driving   → poll every 4/5 of estimated drive-home time
  • When within 20 m    → require GPS accuracy ≤ 10 m, then check motion
  • Motion correlation  → within 5 s before OR 10 s after proximity hit
  → call GarageCoordinator.open_garage() which enforces all safety guards
"""
from __future__ import annotations

import logging
import math
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.util.dt import utcnow

from .const import (
    ACCURACY_REQUIRED_M,
    ARRIVAL_PROXIMITY_M,
    CONF_AVG_SPEED_KPH,
    CONF_GARAGE_DOOR,
    CONF_HOME_ZONE,
    CONF_MOTION_SENSOR,
    CONF_MY_DEVICE,
    CONF_MY_NOTIFY,
    CONF_NOTIFICATION_TARGET,
    CONF_WIFE_DEVICE,
    CONF_WIFE_NOTIFY,
    DEFAULT_AVG_SPEED_KPH,
    DEFAULT_HOME_ZONE,
    DRIVING_SPEED_THRESHOLD_KPH,
    GARAGE_COOLDOWN_S,
    LOCATION_UPDATE_WAIT_S,
    MAX_POLL_INTERVAL_S,
    MIN_POLL_INTERVAL_S,
    MOTION_AFTER_S,
    MOTION_BEFORE_S,
    STATE_APPROACHING,
    STATE_AWAY,
    STATE_DRIVING,
    STATE_HOME,
)

_LOGGER = logging.getLogger(__name__)

# Cover states that mean "already open" — don't try to open again
_GARAGE_OPEN_STATES = {"open", "opening"}

# Motion sensor states that count as "motion detected"
_MOTION_ACTIVE_STATES = {"on", "detected", "motion", "active"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_notify_service(device_entity: str) -> str:
    """
    Derive the Companion App notify service name from a device_tracker entity ID.

    Example: device_tracker.rpip  →  notify.mobile_app_rpip
    """
    name = device_entity.split(".", 1)[-1]
    return f"notify.mobile_app_{name}"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two GPS points."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# PersonTracker
# ---------------------------------------------------------------------------

class PersonTracker:
    """
    Independently tracks one iPhone and decides when to trigger garage open.

    State machine
    -------------
    STATE_HOME       → person is inside the home zone; polling paused
    STATE_AWAY       → left home zone; polling started; driving not yet confirmed
    STATE_DRIVING    → speed between polls exceeded threshold; now tracking approach
    STATE_APPROACHING→ within 20 m, waiting for motion correlation
    """

    def __init__(
        self,
        hass: HomeAssistant,
        label: str,
        device_entity: str,
        coordinator: "GarageCoordinator",
        notify_service: Optional[str] = None,
    ) -> None:
        self.hass = hass
        self.label = label                    # human-readable: "me" or "wife"
        self.device_entity = device_entity    # e.g. device_tracker.rpip
        self.coordinator = coordinator

        # Notify service used to send request_location_update to this phone.
        # Falls back to the auto-derived name if not explicitly provided.
        self._location_notify_service: str = (
            notify_service or _derive_notify_service(device_entity)
        )

        self.state: str = STATE_HOME

        # Poll scheduling
        self._cancel_poll: Optional[Callable] = None

        # Previous position for speed estimation
        self._prev_lat: Optional[float] = None
        self._prev_lon: Optional[float] = None
        self._prev_poll_time: Optional[datetime] = None

        # Approaching / motion-wait state
        self._proximity_time: Optional[datetime] = None
        self._waiting_for_motion: bool = False
        self._cancel_motion_wait: Optional[Callable] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inspect current zone state and kick off polling if away."""
        entity_state = self.hass.states.get(self.device_entity)
        raw_state = entity_state.state if entity_state else "unavailable"
        _LOGGER.info(
            "[%s] tracker starting — entity=%s  current_state='%s'  "
            "location_notify=%s",
            self.label, self.device_entity, raw_state,
            self._location_notify_service,
        )
        if entity_state and entity_state.state.lower() == "home":
            self.state = STATE_HOME
            _LOGGER.info("[%s] starting in STATE_HOME — polling suspended until zone exit", self.label)
        else:
            self.state = STATE_AWAY
            _LOGGER.info(
                "[%s] starting in STATE_AWAY — scheduling first poll in %ds",
                self.label, MIN_POLL_INTERVAL_S,
            )
            self._schedule_poll(MIN_POLL_INTERVAL_S)

    def stop(self) -> None:
        """Cancel any pending callbacks."""
        _LOGGER.info("[%s] tracker stopping — cancelling any scheduled callbacks", self.label)
        if self._cancel_poll:
            self._cancel_poll()
            self._cancel_poll = None
        if self._cancel_motion_wait:
            self._cancel_motion_wait()
            self._cancel_motion_wait = None

    # ------------------------------------------------------------------
    # Zone events (called by GarageCoordinator)
    # ------------------------------------------------------------------

    def on_zone_left(self) -> None:
        """Person left the home zone — begin tracking."""
        if self.state != STATE_HOME:
            _LOGGER.info(
                "[%s] zone-left event received but state is already '%s' — ignoring",
                self.label, self.state,
            )
            return
        _LOGGER.info(
            "[%s] LEFT home zone — state HOME→AWAY, scheduling first poll in %ds",
            self.label, MIN_POLL_INTERVAL_S,
        )
        self.state = STATE_AWAY
        # Reset movement history so first speed sample is clean
        self._prev_lat = None
        self._prev_lon = None
        self._prev_poll_time = None
        self._schedule_poll(MIN_POLL_INTERVAL_S)

    def on_zone_entered(self) -> None:
        """Person entered the home zone — suspend tracking."""
        _LOGGER.info(
            "[%s] ENTERED home zone — state %s→HOME, polling suspended",
            self.label, self.state,
        )
        self.state = STATE_HOME
        if self._cancel_poll:
            self._cancel_poll()
            self._cancel_poll = None
        if self._cancel_motion_wait:
            self._cancel_motion_wait()
            self._cancel_motion_wait = None
        self._waiting_for_motion = False

    # ------------------------------------------------------------------
    # Poll scheduling
    # ------------------------------------------------------------------

    def _schedule_poll(self, delay_s: float) -> None:
        """Schedule _poll() after *delay_s* seconds (clamped to bounds)."""
        if self._cancel_poll:
            self._cancel_poll()
            self._cancel_poll = None

        raw = delay_s
        delay_s = max(MIN_POLL_INTERVAL_S, min(delay_s, MAX_POLL_INTERVAL_S))
        if delay_s != raw:
            _LOGGER.info(
                "[%s] poll interval %.0f s clamped to %.0f s (bounds %d–%d s)",
                self.label, raw, delay_s, MIN_POLL_INTERVAL_S, MAX_POLL_INTERVAL_S,
            )
        else:
            _LOGGER.info("[%s] next location request in %.0f s", self.label, delay_s)

        @callback
        def _fire(_now):
            self._cancel_poll = None
            self.hass.async_create_task(self._poll())

        self._cancel_poll = async_call_later(self.hass, delay_s, _fire)

    # ------------------------------------------------------------------
    # Main poll — two-phase: request then read
    # ------------------------------------------------------------------

    async def _poll(self) -> None:
        """
        Phase 1 — send `request_location_update` to the iPhone via its
        Companion App notify service, then schedule _read_location() after
        LOCATION_UPDATE_WAIT_S seconds to evaluate the refreshed position.
        """
        if self.state == STATE_HOME:
            _LOGGER.info("[%s] _poll() skipped — state is HOME", self.label)
            return
        if self._waiting_for_motion:
            _LOGGER.info("[%s] _poll() skipped — waiting for motion correlation", self.label)
            return

        notify_svc = self._location_notify_service.replace("notify.", "", 1)
        _LOGGER.info(
            "[%s] sending request_location_update → %s  (will read in %ds)",
            self.label, self._location_notify_service, LOCATION_UPDATE_WAIT_S,
        )
        try:
            await self.hass.services.async_call(
                "notify",
                notify_svc,
                {"message": "request_location_update"},
                blocking=False,
            )
            _LOGGER.info("[%s] request_location_update sent OK", self.label)
        except Exception as exc:  # noqa: BLE001
            # If the service doesn't exist yet (phone offline, etc.) just
            # continue — we will still read whatever state is available.
            _LOGGER.warning(
                "[%s] request_location_update FAILED (%s) — "
                "will read cached device_tracker state instead",
                self.label, exc,
            )

        # Schedule location read after the phone has had time to respond.
        @callback
        def _do_read(_now):
            self.hass.async_create_task(self._read_location())

        async_call_later(self.hass, LOCATION_UPDATE_WAIT_S, _do_read)

    async def _read_location(self) -> None:
        """
        Phase 2 — read the (now-fresh) device_tracker state, update the
        driving state machine, check proximity, and schedule the next poll.
        """
        if self.state == STATE_HOME:
            _LOGGER.info("[%s] _read_location() skipped — state is HOME", self.label)
            return
        if self._waiting_for_motion:
            _LOGGER.info("[%s] _read_location() skipped — waiting for motion correlation", self.label)
            return

        entity_state = self.hass.states.get(self.device_entity)
        if not entity_state:
            _LOGGER.warning(
                "[%s] entity '%s' not found in HA — retrying in %ds",
                self.label, self.device_entity, MIN_POLL_INTERVAL_S,
            )
            self._schedule_poll(MIN_POLL_INTERVAL_S)
            return

        attrs = entity_state.attributes
        lat: Optional[float] = attrs.get("latitude")
        lon: Optional[float] = attrs.get("longitude")
        accuracy: float = float(attrs.get("gps_accuracy", 9999))

        if lat is None or lon is None:
            _LOGGER.warning(
                "[%s] device_tracker has no latitude/longitude attributes "
                "(zone state='%s') — retrying in %ds",
                self.label, entity_state.state, MIN_POLL_INTERVAL_S,
            )
            self._schedule_poll(MIN_POLL_INTERVAL_S)
            return

        home_lat = self.coordinator.home_lat
        home_lon = self.coordinator.home_lon
        distance_m = _haversine_m(lat, lon, home_lat, home_lon)
        now = utcnow()

        _LOGGER.info(
            "[%s] location read — state=%s  dist=%.1f m  accuracy=%.1f m  "
            "pos=(%.6f, %.6f)",
            self.label, self.state, distance_m, accuracy, lat, lon,
        )

        # ── Driving detection ──────────────────────────────────────────
        if self._prev_lat is not None and self._prev_poll_time is not None:
            elapsed_s = (now - self._prev_poll_time).total_seconds()
            if elapsed_s > 0:
                moved_m = _haversine_m(lat, lon, self._prev_lat, self._prev_lon)
                speed_kph = (moved_m / elapsed_s) * 3.6
                _LOGGER.info(
                    "[%s] movement since last read: %.1f m in %.0f s = %.1f kph  "
                    "(threshold %.1f kph)",
                    self.label, moved_m, elapsed_s, speed_kph, DRIVING_SPEED_THRESHOLD_KPH,
                )
                if speed_kph >= DRIVING_SPEED_THRESHOLD_KPH and self.state == STATE_AWAY:
                    _LOGGER.info(
                        "[%s] DRIVING confirmed (%.1f kph ≥ %.1f kph) — state AWAY→DRIVING",
                        self.label, speed_kph, DRIVING_SPEED_THRESHOLD_KPH,
                    )
                    self.state = STATE_DRIVING
                elif self.state == STATE_AWAY:
                    _LOGGER.info(
                        "[%s] speed %.1f kph below threshold — still STATE_AWAY (not driving)",
                        self.label, speed_kph,
                    )
        else:
            _LOGGER.info(
                "[%s] no previous position recorded — skipping speed check this cycle",
                self.label,
            )

        self._prev_lat = lat
        self._prev_lon = lon
        self._prev_poll_time = now

        # ── Proximity check ────────────────────────────────────────────
        if self.state == STATE_DRIVING:
            if distance_m <= ARRIVAL_PROXIMITY_M:
                if accuracy <= ACCURACY_REQUIRED_M:
                    _LOGGER.info(
                        "[%s] PROXIMITY HIT — %.1f m from home, accuracy %.1f m — "
                        "entering motion correlation",
                        self.label, distance_m, accuracy,
                    )
                    await self._on_proximity_confirmed(now)
                    return
                else:
                    _LOGGER.info(
                        "[%s] within %.1f m of home but GPS accuracy is %.1f m "
                        "(need ≤%d m) — requesting better fix in %ds",
                        self.label, distance_m, accuracy,
                        ACCURACY_REQUIRED_M, MIN_POLL_INTERVAL_S,
                    )
                    self._schedule_poll(MIN_POLL_INTERVAL_S)
                    return
            else:
                _LOGGER.info(
                    "[%s] driving — %.1f m from home (trigger at %d m)",
                    self.label, distance_m, ARRIVAL_PROXIMITY_M,
                )
        elif self.state == STATE_AWAY:
            _LOGGER.info(
                "[%s] away (not yet driving) — %.1f m from home",
                self.label, distance_m,
            )

        # ── Adaptive next-poll interval ────────────────────────────────
        # Interval = (estimated drive time home) × 4/5
        avg_kph = self.coordinator.avg_speed_kph
        if avg_kph > 0 and distance_m > ARRIVAL_PROXIMITY_M:
            drive_time_s = (distance_m / 1000.0 / avg_kph) * 3600.0
            interval_s = drive_time_s * 0.8   # 4/5
            _LOGGER.info(
                "[%s] next poll calc: %.1f km ÷ %.0f kph = %.0f s drive time × 0.8 = %.0f s",
                self.label, distance_m / 1000.0, avg_kph, drive_time_s, interval_s,
            )
        else:
            interval_s = MAX_POLL_INTERVAL_S
            _LOGGER.info(
                "[%s] distance/speed unavailable — using max interval %ds",
                self.label, MAX_POLL_INTERVAL_S,
            )

        self._schedule_poll(interval_s)

    # ------------------------------------------------------------------
    # Proximity confirmed → motion correlation
    # ------------------------------------------------------------------

    async def _on_proximity_confirmed(self, t0: datetime) -> None:
        """
        Person is within 20 m with GPS accuracy ≤ 10 m.
        Check for motion in the [t0 − 5 s, t0] window; if absent, wait 10 s
        and re-check the full [t0 − 5 s, t0 + 10 s] window.
        """
        self.state = STATE_APPROACHING
        self._proximity_time = t0
        self._waiting_for_motion = True

        t_before_start = t0 - timedelta(seconds=MOTION_BEFORE_S)
        motion_buf_size = len(self.coordinator._motion_events)

        _LOGGER.info(
            "[%s] PROXIMITY CONFIRMED — checking motion buffer "
            "(window [t−%ds, t0], %d events in buffer)",
            self.label, MOTION_BEFORE_S, motion_buf_size,
        )

        # Immediate check: was there motion in the last MOTION_BEFORE_S seconds?
        if self.coordinator.has_motion_in_window(t_before_start, t0):
            _LOGGER.info(
                "[%s] motion found in pre-arrival window [t−%ds, t0] — triggering garage",
                self.label, MOTION_BEFORE_S,
            )
            await self._finish_arrival()
            return

        # No prior motion — wait MOTION_AFTER_S seconds for subsequent motion
        _LOGGER.info(
            "[%s] no motion in pre-arrival window — waiting %ds for post-arrival motion",
            self.label, MOTION_AFTER_S,
        )

        @callback
        def _after_wait(_now):
            self._cancel_motion_wait = None
            self.hass.async_create_task(self._evaluate_motion_after_wait(t0))

        self._cancel_motion_wait = async_call_later(
            self.hass, MOTION_AFTER_S, _after_wait
        )

    async def _evaluate_motion_after_wait(self, t0: datetime) -> None:
        """Called MOTION_AFTER_S seconds after proximity was confirmed."""
        t_start = t0 - timedelta(seconds=MOTION_BEFORE_S)
        t_end = t0 + timedelta(seconds=MOTION_AFTER_S)
        motion_buf_size = len(self.coordinator._motion_events)

        _LOGGER.info(
            "[%s] post-arrival motion check — window [t−%ds, t+%ds], "
            "%d events in buffer",
            self.label, MOTION_BEFORE_S, MOTION_AFTER_S, motion_buf_size,
        )

        if self.coordinator.has_motion_in_window(t_start, t_end):
            _LOGGER.info(
                "[%s] motion found in full window [t−%ds, t+%ds] — triggering garage",
                self.label, MOTION_BEFORE_S, MOTION_AFTER_S,
            )
            await self._finish_arrival()
        else:
            _LOGGER.info(
                "[%s] NO motion found in full window [t−%ds, t+%ds] — "
                "garage will NOT be opened",
                self.label, MOTION_BEFORE_S, MOTION_AFTER_S,
            )
            self._reset_approach()

    async def _finish_arrival(self) -> None:
        """Trigger garage open and reset state."""
        await self.coordinator.open_garage(self.label)
        self._reset_approach()

    def _reset_approach(self) -> None:
        """Return to HOME state after an arrival attempt (success or miss)."""
        _LOGGER.info("[%s] resetting to STATE_HOME — tracking suspended", self.label)
        self.state = STATE_HOME
        self._waiting_for_motion = False
        self._proximity_time = None
        if self._cancel_motion_wait:
            self._cancel_motion_wait()
            self._cancel_motion_wait = None


# ---------------------------------------------------------------------------
# GarageCoordinator
# ---------------------------------------------------------------------------

class GarageCoordinator:
    """
    Top-level coordinator that owns both PersonTrackers and all shared
    resources (motion buffer, garage cooldown, HA service calls).
    """

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        self.hass = hass
        self._config = config

        # Loaded from home zone entity
        self.home_lat: float = 0.0
        self.home_lon: float = 0.0
        self.avg_speed_kph: float = float(config.get(CONF_AVG_SPEED_KPH, DEFAULT_AVG_SPEED_KPH))

        # Entity IDs
        self._garage_entity: str = config[CONF_GARAGE_DOOR]
        self._motion_entity: str = config[CONF_MOTION_SENSOR]
        self._notification_target: str = config[CONF_NOTIFICATION_TARGET]
        self._my_device: str = config[CONF_MY_DEVICE]
        self._wife_device: str = config[CONF_WIFE_DEVICE]
        self._home_zone: str = config.get(CONF_HOME_ZONE, DEFAULT_HOME_ZONE)

        # Garage cooldown — track last close time
        self._garage_last_closed: Optional[datetime] = None

        # Rolling buffer of motion event timestamps (UTC)
        self._motion_events: deque[datetime] = deque(maxlen=200)

        # Person trackers
        self._trackers: dict[str, PersonTracker] = {}

        # Unsubscribe callbacks
        self._unsub: list[Callable] = []

    # ------------------------------------------------------------------
    # Setup / shutdown
    # ------------------------------------------------------------------

    async def async_setup(self) -> bool:
        """Initialise zone, create trackers, subscribe to HA events."""
        # Resolve home zone coordinates
        zone_state = self.hass.states.get(self._home_zone)
        if not zone_state:
            _LOGGER.error(
                "Home zone entity '%s' not found. Check your configuration.", self._home_zone
            )
            return False

        self.home_lat = float(zone_state.attributes.get("latitude", 0.0))
        self.home_lon = float(zone_state.attributes.get("longitude", 0.0))
        _LOGGER.info(
            "iCloud Garage starting — home zone: %s @ (%.6f, %.6f)  "
            "garage=%s  motion=%s  avg_speed=%.0f kph",
            self._home_zone, self.home_lat, self.home_lon,
            self._garage_entity, self._motion_entity, self.avg_speed_kph,
        )
        _LOGGER.info(
            "iCloud Garage — my device: %s  wife device: %s  "
            "alert notify: %s",
            self._my_device, self._wife_device, self._notification_target,
        )

        # Instantiate per-person trackers.
        # notify_service is used to send request_location_update; falls back to
        # auto-derived notify.mobile_app_<device_name> if not explicitly set.
        self._trackers["me"] = PersonTracker(
            self.hass, "me", self._my_device, self,
            notify_service=self._config.get(CONF_MY_NOTIFY),
        )
        self._trackers["wife"] = PersonTracker(
            self.hass, "wife", self._wife_device, self,
            notify_service=self._config.get(CONF_WIFE_NOTIFY),
        )

        # ── HA event subscriptions ──────────────────────────────────────

        # Device zone transitions
        self._unsub.append(
            async_track_state_change_event(
                self.hass,
                [self._my_device, self._wife_device],
                self._handle_device_state_change,
            )
        )

        # Garage door close detection (for cooldown)
        self._unsub.append(
            async_track_state_change_event(
                self.hass,
                [self._garage_entity],
                self._handle_garage_state_change,
            )
        )

        # Motion sensor events
        self._unsub.append(
            async_track_state_change_event(
                self.hass,
                [self._motion_entity],
                self._handle_motion_state_change,
            )
        )

        # Seed motion buffer with current state if sensor is already active
        motion_state = self.hass.states.get(self._motion_entity)
        if motion_state and motion_state.state.lower() in _MOTION_ACTIVE_STATES:
            self._motion_events.append(utcnow())

        # Start trackers
        for tracker in self._trackers.values():
            tracker.start()

        return True

    async def async_shutdown(self) -> None:
        """Tear down all callbacks and trackers."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

        for tracker in self._trackers.values():
            tracker.stop()
        self._trackers.clear()

    # ------------------------------------------------------------------
    # HA event handlers
    # ------------------------------------------------------------------

    @callback
    def _handle_device_state_change(self, event) -> None:
        """Detect home-zone enter / leave for either tracked device."""
        entity_id: str = event.data["entity_id"]
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if not old_state or not new_state:
            return

        old_zone = old_state.state.lower()
        new_zone = new_state.state.lower()
        tracker_key = "me" if entity_id == self._my_device else "wife"

        _LOGGER.info(
            "device_tracker event — %s (%s): '%s' → '%s'",
            entity_id, tracker_key, old_zone, new_zone,
        )

        tracker = self._trackers.get(tracker_key)
        if not tracker:
            return

        if old_zone == "home" and new_zone != "home":
            tracker.on_zone_left()
        elif new_zone == "home" and old_zone != "home":
            tracker.on_zone_entered()
        else:
            _LOGGER.info(
                "device_tracker event — no zone boundary crossed, no action taken",
            )

    @callback
    def _handle_garage_state_change(self, event) -> None:
        """Record timestamp when garage door closes (for cooldown guard)."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        old_s = old_state.state if old_state else "unknown"
        new_s = new_state.state if new_state else "unknown"
        _LOGGER.info("Garage door state change: '%s' → '%s'", old_s, new_s)
        if new_state and new_state.state == "closed":
            self._garage_last_closed = utcnow()
            _LOGGER.info(
                "Garage CLOSED — 10-minute cooldown started (no auto-open until %s)",
                (self._garage_last_closed + timedelta(seconds=GARAGE_COOLDOWN_S)).isoformat(),
            )

    @callback
    def _handle_motion_state_change(self, event) -> None:
        """Append a timestamp to the motion buffer whenever motion is detected."""
        new_state = event.data.get("new_state")
        if not new_state:
            return
        if new_state.state.lower() in _MOTION_ACTIVE_STATES:
            ts = utcnow()
            self._motion_events.append(ts)
            _LOGGER.info(
                "Motion detected at %s (buffer now has %d events)",
                ts.isoformat(), len(self._motion_events),
            )
        else:
            _LOGGER.info(
                "Motion sensor state → '%s' (not an active state, not recorded)",
                new_state.state,
            )

    # ------------------------------------------------------------------
    # Shared query helpers
    # ------------------------------------------------------------------

    def has_motion_in_window(self, t_start: datetime, t_end: datetime) -> bool:
        """Return True if any motion event falls within [t_start, t_end]."""
        for ts in self._motion_events:
            if t_start <= ts <= t_end:
                return True
        return False

    def _cooldown_active(self) -> bool:
        """Return True if less than GARAGE_COOLDOWN_S seconds have passed since last close."""
        if self._garage_last_closed is None:
            return False
        elapsed = (utcnow() - self._garage_last_closed).total_seconds()
        return elapsed < GARAGE_COOLDOWN_S

    # ------------------------------------------------------------------
    # Garage door control
    # ------------------------------------------------------------------

    async def open_garage(self, triggered_by: str) -> None:
        """
        Open the garage door, honouring all safety guards:
          1. 10-minute cooldown after last close
          2. Already open / opening → skip
        Then notify the owner's iPhone.
        """
        # Guard 1: cooldown
        if self._cooldown_active():
            elapsed = (utcnow() - self._garage_last_closed).total_seconds()
            remaining = GARAGE_COOLDOWN_S - elapsed
            _LOGGER.info(
                "GARAGE NOT OPENED — cooldown active (closed %.0f s ago, "
                "%.0f s remaining before auto-open is allowed) — triggered by: %s",
                elapsed, remaining, triggered_by,
            )
            return

        # Guard 2: already open
        garage_state = self.hass.states.get(self._garage_entity)
        if garage_state and garage_state.state in _GARAGE_OPEN_STATES:
            _LOGGER.info(
                "GARAGE NOT OPENED — already '%s' — triggered by: %s",
                garage_state.state, triggered_by,
            )
            return

        # Open the door
        _LOGGER.info("OPENING GARAGE DOOR — triggered by: %s", triggered_by)
        await self.hass.services.async_call(
            "cover",
            "open_cover",
            {"entity_id": self._garage_entity},
            blocking=True,
        )

        # Notify owner's iPhone
        who = "your wife" if triggered_by == "wife" else "you"
        message = (
            f"Garage door opened: {who} arrived home "
            f"(triggered by iCloud Garage Automation)."
        )
        notify_service = self._notification_target.replace("notify.", "")
        try:
            await self.hass.services.async_call(
                "notify",
                notify_service,
                {"message": message, "title": "Garage Opened"},
                blocking=False,
            )
            _LOGGER.info("Notification sent → %s", self._notification_target)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Notification failed: %s", exc)
