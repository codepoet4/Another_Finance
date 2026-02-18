# iCloud Garage Automation

A [HACS](https://hacs.xyz/) custom integration for Home Assistant that automatically
opens your garage door when you or your wife arrive home — using iCloud location
data and a driveway motion sensor to confirm the arrival before acting.

---

## How It Works

```
Person leaves home zone
        │
        ▼
Poll location every 4/5 of estimated drive-home time
        │
        ▼
Speed between polls > 8 kph?  ──No──▶ keep polling (AWAY state)
        │ Yes
        ▼
DRIVING state confirmed
        │
        ▼
Within 20 m of home?  ──No──▶ schedule next poll
        │ Yes
        ▼
GPS accuracy ≤ 10 m?  ──No──▶ poll every 30 s for better fix
        │ Yes
        ▼
Motion in last 5 s?  ──Yes──▶ Open garage ✓
        │ No
        ▼
Wait 10 s for motion
        │
Motion detected?  ──Yes──▶ Open garage ✓
        │ No
        ▼
No action taken
```

### Safety guards

| Guard | Detail |
|---|---|
| Garage already open | Never sends open command if state is `open` or `opening` |
| 10-minute cooldown | After the garage closes, it cannot be triggered again for 10 minutes |
| GPS accuracy | Must be ≤ 10 m before the 20 m proximity check counts |
| At-home guard | Tracking only starts after a person **leaves** the home zone |

### Notification

Whenever the garage door is opened by this automation, a push notification is sent
to your iPhone via the configured notify service (e.g. `notify.mobile_app_rpip`).

---

## Prerequisites

1. **iCloud integration** (built-in or via iCloud3) providing `device_tracker` entities
   with `latitude`, `longitude`, and `gps_accuracy` attributes.
2. **Garage door** exposed as a `cover` entity in Home Assistant.
3. **Motion sensor** (driveway, garage, or doorbell) exposed as a `binary_sensor`
   or `sensor` that reports `on` / `detected` / `motion` when active.
4. **Mobile App** integration on your iPhone so notify services are available.

---

## Installation

### Via HACS (recommended)

1. Open HACS → **Integrations** → menu (⋮) → **Custom repositories**.
2. Add `https://github.com/codepoet4/Another_Finance` as an **Integration**.
3. Search for **iCloud Garage Automation** and install it.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/icloud_garage/` into your HA
   `config/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for
**iCloud Garage Automation**.

| Field | Description | Default |
|---|---|---|
| Your iPhone | `device_tracker` entity for your phone | `device_tracker.rpip` |
| Wife's iPhone | `device_tracker` entity for your wife's phone | `device_tracker.shyiphonexs` |
| Garage door | `cover` entity for the garage door | _(required)_ |
| Motion sensor | `binary_sensor` or `sensor` for driveway motion | _(required)_ |
| Notify service | Full HA notify service name for push alerts | _(required)_ |
| Home zone | Zone entity representing home | `zone.home` |
| Average driving speed | Used to calculate adaptive poll interval | `40 km/h` |

### Finding your notify service name

In Home Assistant go to **Developer Tools → Services** and look for
`notify.mobile_app_<your_device_name>`. Enter the full name in the
**Notify service** field (e.g. `notify.mobile_app_rpip`).

---

## Adaptive Polling

When you are far from home and stationary, the integration polls at:

```
interval = (straight-line distance / average speed) × 4/5
```

Bounded between **30 seconds** (minimum) and **30 minutes** (maximum).
This means when you are 10 km away at 40 km/h average, the poll interval is:

```
(10 / 40) × 60 min × 0.8 ≈ 12 minutes
```

When you are within a few hundred metres, polls happen every 30 seconds.

---

## Driving Detection

After leaving the home zone the integration records positions on each poll.
If the speed between two consecutive polls exceeds **8 kph**, the person is
considered to be **driving**. Only people in the driving state will trigger
the garage door.

---

## Troubleshooting

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.icloud_garage: debug
```

Common issues:

- **Garage does not open**: Check that the motion sensor fires within
  the correlation window (−5 s / +10 s around the 20 m proximity hit).
- **Wrong drive-time estimate**: Increase or decrease *Average driving speed*
  in the integration options.
- **Cooldown still active**: The garage closed less than 10 minutes ago.
  Wait and try again.
- **Poor GPS accuracy**: iCloud location can be coarse indoors. The
  integration will wait until accuracy is ≤ 10 m before acting.

---

## License

MIT
