# LockMaster - Home Assistant Integration

A custom Home Assistant integration for managing PIN codes across multiple Zigbee door locks. Provides unified user management with automatic cross-lock synchronization.

## Features

- **Multi-Lock Support** - Manage users across multiple Zigbee locks from a single interface
- **Cross-Lock Sync** - Automatically detect and sync PIN mismatches between locks
- **User Management** - Create, update, and delete users with name, email, and PIN
- **Lock as Source of Truth** - PINs are always synced from the physical lock
- **Lovelace Card** - Custom card for easy user management in the UI

## Supported Locks

Works with Zigbee locks that support PIN codes via Zigbee2MQTT, including:

- Schlage locks
- Yale locks
- Kwikset locks
- Other ZCL Door Lock cluster compatible locks

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "LockMaster" and install
3. Restart Home Assistant

### Manual Installation

1. Copy `custom_components/lockmaster` to your `config/custom_components/` directory
2. Copy `www/lockmaster-card.js` to your `config/www/` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "LockMaster"
3. Complete the setup

## Lovelace Card Setup

Add the card resource:

1. Go to **Settings** → **Dashboards** → **Resources**
2. Add `/local/lockmaster-card.js` as JavaScript Module

Add the card to your dashboard:

```yaml
type: custom:lockmaster-card
entity: sensor.lockmaster_users
```

## Services

### `lockmaster.addlock`

Register a lock for management.

```yaml
service: lockmaster.addlock
data:
  lock: "Community Room Lock"
```

### `lockmaster.load`

Load user data from all registered locks.

```yaml
service: lockmaster.load
```

### `lockmaster.fetchusers`

Refresh user list from locks.

```yaml
service: lockmaster.fetchusers
```

### `lockmaster.updateuser`

Update a user's information.

```yaml
service: lockmaster.updateuser
data:
  id: 1
  name: "John Doe"
  email: "john@example.com"
  pin: 1234
```

### `lockmaster.disableuser`

Disable/delete a user from all locks.

```yaml
service: lockmaster.disableuser
data:
  id: 1
```

### `lockmaster.clear`

Clear all lock registrations.

```yaml
service: lockmaster.clear
```

## Sensor Attributes

The `sensor.lockmaster_users` entity provides:

| Attribute | Description |
|-----------|-------------|
| `users` | List of all users with id, name, email, pin, status |
| `locks` | List of registered lock names |
| `user_count` | Total number of users |

Each user object contains:

```json
{
  "id": 1,
  "name": "John Doe",
  "email": "john@example.com",
  "pin": "1234",
  "status": "enabled",
  "last_update": "2024-01-11T10:30:00"
}
```

## Startup Automation

Add locks on Home Assistant startup:

```yaml
automation:
  - alias: "LockMaster Startup"
    trigger:
      - platform: homeassistant
        event: start
    action:
      - service: lockmaster.clear
      - service: lockmaster.addlock
        data:
          lock: "Front Door Lock"
      - service: lockmaster.addlock
        data:
          lock: "Back Door Lock"
      - service: lockmaster.load
      - service: lockmaster.fetchusers
```

## PIN Validation Rules

When setting a PIN, LockMaster enforces:

- **Length** - Must be 4-8 digits
- **Numeric only** - Letters and symbols not allowed
- **No repeating digits** - PINs like "1111" or "0000" are rejected
- **Unique** - Each PIN must be unique across all enabled users
- **Non-zero** - PIN cannot be "0" or empty

If validation fails, the update is rejected with an error message.

## Cross-Lock PIN Sync

LockMaster automatically detects when PINs differ between locks for the same user slot. When a mismatch is detected:

1. The lock's PIN is treated as the source of truth
2. The internal state is updated to match the lock
3. If PINs differ between locks, the most recently updated PIN is pushed to other locks

## Lovelace Card Features

- User selection dropdown
- Editable fields for name, email, and PIN
- Show/Hide PIN toggle
- Update and Delete buttons
- Real-time status display
- Form state persistence while editing

## Requirements

- Home Assistant 2023.1+
- Zigbee2MQTT with MQTT integration
- Compatible Zigbee door lock(s)

## License

MIT License

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.
