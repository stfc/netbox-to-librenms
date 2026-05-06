# Netbox-to-librenms

This is a script (`pull_netbox.py`) created initially for use in the SCD RIG group to keep **Netbox** and **LibreNMS** synchronised
with each other. It assumes Netbox is being used as a single source of truth and is designed to be run by a cron job on the
LibreNMS host to pull new network devices and changes to existing devices from Netbox. The REST APIs of Netbox and LibreNMS
are used to do this.

The script links devices by attaching the ID of the Netbox device to the LibreNMS device in the form of a component labelled
with that ID (see https://docs.librenms.org/API/Devices/#add_components). The script will create new devices and update linked
devices, but will also try to link pre-existing devices in LibreNMS by finding a Netbox device with an equivalent hostname,
IP address, or display name.

There is currently no functionality to delete LibreNMS devices, or mark them for human deletion, but this could easily be
added via the return of `attempt_find_orphans`.

Roles and tenants to target can be configured in `config.toml`, after copying the template from `config.toml.example`.

## Algorithm

1. Get a filtered list of Netbox devices (e.g. only switches, routers, etc., and only from selected tenants).
2. Get a list of all LibreNMS devices.
3. Create a dictionary of linked devices with Netbox IDs as the keys and LibreNMS devices as the values. Any unlinked
   LibreNMS devices are placed into a separate collection.
4. For each Netbox device:
   1. If it has a linked LibreNMS device, update the linked device's hostname and display name if required.
   2. If no linked device is found, attempt to match an unlinked LibreNMS device by hostname, IP address, or display name.
      If a match is found, link and update that device.
   3. If no linked or matching unlinked device is found, create a new LibreNMS device and link it.
5. For devices that remain unlinked/orphaned, attempt to find matches. As of writing, this is just logged.

This approach means that if someone adds a device manually to LibreNMS—including devices added before this script was first
run—the script will attempt to find and link the corresponding Netbox device automatically.

Additionally, `overwrite_ip` fields are removed, as this has been deprecated from LibreNMS in favour of `hostname` and `display` only.
If a DNS record is missing, `hostname` is set to an IP.

## Configuration and usage outside RIG

After copying `config.toml.example` to `config.toml`, the following can be configured:

- Netbox API URL and token (can be read-only)
- LibreNMS API URL and token (write access required)
- Target roles, tenants, and statuses
- Logging options
- TLS verification and CA configuration for LibreNMS connection
- Dry-run mode

The script is not fully generic and was originally written with RIG-specific Netbox roles and tenants in mind. Other groups
may need to adjust the Netbox filtering or matching logic to suit their environment. Depending on your setup, you may not
need to provide a custom CA certificate.

It is recommended that non-RIG users either make the remaining parameters configurable or maintain a local branch with
site-specific defaults.