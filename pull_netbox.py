#!/usr/bin/python3
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pynetbox==7.5.0",
#     "requests>=2.33.1",
# ]
# ///

import ipaddress
import logging
import tomllib
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pynetbox
import requests

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"

with CONFIG_PATH.open("rb") as f:
    config = tomllib.load(f)

LIBNMS_API = config["librenms"]["api"].rstrip("/") + "/"
LIBNMS_TOKEN = config["librenms"]["token"]

NETBOX_API = config["netbox"]["api"].rstrip("/") + "/"
NETBOX_TOKEN = config["netbox"]["token"]
NETBOX_TENANTS = config["netbox"]["tenants"]
NETBOX_ROLES = config["netbox"]["roles"]
NETBOX_FUZZY_ROLES = config["netbox"]["fuzzy_roles"]
NETBOX_STATUSES = config["netbox"]["statuses"]
NETBOX_OVERRIDE_PULL = config["netbox"]["override_pull_ids"]

LOG_FILE = config["general"]["log_file"]

DRY_RUN = config["general"]["dry_run"]

CA_VERIFY = config["general"]["ca_verify"]


rfh = RotatingFileHandler(filename=LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=1)
sh = logging.StreamHandler()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[rfh, sh],
)


def link_device(libnms_id, libnms_hostname, netbox_id, netbox_name, libnms_session):
    """
    Link libreNMS and Netbox device by adding a component with the label netbox_id
    to the LibreNMS device with hostname libnms_name
    """
    if DRY_RUN:
        logging.info(
            f"[DRY RUN] Would link LibreNMS device {libnms_id} ('{libnms_hostname}') to NetBox ID {netbox_id} ('{netbox_name}')"
        )
        return
    try:
        logging.info(
            f"Linking LibreNMS device {libnms_id} ('{libnms_hostname}') to NetBox ID {netbox_id} ('{netbox_name}')"
        )
        response = libnms_session.post(LIBNMS_API + libnms_id + "/components/netbox_id")
        if response.json()["status"] == "error":
            raise Exception(
                f"Error received from LibreNMS: {response.json()['message']}"
            )
        # Get the ID of the component just created so it can be modified and labelled
        component_id = list(response.json()["components"])[0]
        component_data = (
            '{"%s": {"type": "netbox_id", "label": "%s", "status": 1, "ignore": 0, "disabled": 0, "error": ""}}'
            % (component_id, netbox_id)
        )
        response = libnms_session.put(
            LIBNMS_API + libnms_id + "/components", data=component_data
        )
        if response.json()["status"] == "error":
            raise Exception(
                f"Error received from LibreNMS: {response.json()['message']}"
            )
    except Exception:
        logging.exception(
            f'Failed to link device with name "{libnms_hostname}" with netbox id "{netbox_id}".'
        )
        raise


def update_device(
    libnms_id,
    libnms_hostname,
    libnms_display_name,
    netbox_hostname,
    netbox_name,
    libnms_session,
):
    """
    Update hostname and display name of libreNMS device to be the same as that of the netbox device that
    it is linked to (if they are not already the same)
    """

    try:
        if libnms_hostname.lower() != netbox_hostname.lower():
            if DRY_RUN:
                logging.info(
                    f"[DRY RUN] Would change librenms device hostname '{libnms_hostname}' to '{netbox_hostname}'"
                )

            else:
                logging.info(f"Renaming '{libnms_hostname}' to '{netbox_hostname}'")
                response = libnms_session.patch(
                    LIBNMS_API + libnms_id + "/rename/" + netbox_hostname
                )
                if response.json()["status"] == "error":
                    raise Exception(
                        f"Error received from LibreNMS: {response.json()['message']}"
                    )
            libnms_hostname = netbox_hostname
    except Exception:
        logging.exception(
            f"""Failed to update libreNMS device hostname from '{libnms_hostname}' to '{netbox_hostname}'"""
        )
        raise
    try:
        if libnms_display_name != netbox_name:
            if DRY_RUN:
                logging.info(
                    f"[DRY RUN] Would update display name for '{libnms_hostname}' from '{libnms_display_name}' to '{netbox_name}'"
                )
            else:
                logging.info(
                    f"Updating display name for '{libnms_hostname}' from '{libnms_display_name}' to '{netbox_name}'"
                )
                data = '{"field": "display", "data": "%s"}' % netbox_name
                response = libnms_session.patch(LIBNMS_API + libnms_id, data=data)
                if response.json()["status"] == "error":
                    raise Exception(
                        f"Error received from LibreNMS: {response.json()['message']}"
                    )
            libnms_display_name = netbox_name
    except Exception:
        logging.exception(
            f"""Failed to update libreNMS device display_name from '{libnms_display_name}' to '{netbox_name}'"""
        )
        raise


def main():
    logging.info("Script beginning")

    # Create netbox and librenms sessions and get lists of devices
    nb = pynetbox.api(NETBOX_API, token=NETBOX_TOKEN)

    # Create a list of netbox roles to select, including anything with the word 'switch' in it
    logging.info("[Step 1/5] Loading roles from Netbox")
    netbox_roles = []
    try:
        for fuzzy_role in NETBOX_FUZZY_ROLES:
            netbox_roles.extend(
                [role.slug for role in nb.dcim.device_roles.filter(fuzzy_role)]
            )
        for role in NETBOX_ROLES:
            netbox_roles.append(nb.dcim.device_roles.get(slug=role).slug)  # type: ignore
    except Exception:
        logging.exception("Error when getting device roles from netbox.")
        raise

    logging.info(f"Found Roles: {netbox_roles}")

    logging.info("[Step 2/5] Loading tenants from Netbox")
    try:
        assert len(NETBOX_TENANTS) > 0, "NETBOX_TENANTS was empty"
        tenant_slugs = []
        tenant_ids = []
        for t in NETBOX_TENANTS:
            try:
                tenant_ids.append(int(t))
            except (TypeError, ValueError):
                tenant_slugs.append(str(t))

        netbox_devices_init = []

        if tenant_slugs:
            netbox_devices_init.extend(
                nb.dcim.devices.filter(
                    role=netbox_roles, tenant=tenant_slugs, status=NETBOX_STATUSES
                )
            )

        if tenant_ids:
            netbox_devices_init.extend(
                nb.dcim.devices.filter(
                    role=netbox_roles, tenant_id=tenant_ids, status=NETBOX_STATUSES
                )
            )
        if len(NETBOX_OVERRIDE_PULL) > 0:
            netbox_devices_init.extend(nb.dcim.devices.filter(id=NETBOX_OVERRIDE_PULL))

    except Exception:
        logging.exception("Error when getting devices from netbox.")
        raise

    # Filter out netbox devices without a primary/oob ip
    logging.info("[Step 3/5] Filtering out Netbox devices without a primary/oob IP")
    netbox_devices = {}

    for device in netbox_devices_init:
        if (device.primary_ip is not None) or (device.oob_ip is not None):
            netbox_devices[str(device.id)] = device
        else:
            logging.warning(f"Device {device} has no primary ipv4")

    libnms_session = requests.Session()
    libnms_session.headers = {"X-Auth-Token": LIBNMS_TOKEN}
    libnms_session.verify = CA_VERIFY

    try:
        response = libnms_session.get(LIBNMS_API)
        if response.json()["status"] == "error":
            raise Exception(
                f"Error received from LibreNMS: {response.json()['message']}"
            )
    except Exception:
        logging.exception("Error when getting devices from LibreNMS: ")
        raise
    librenms_devices = response.json()["devices"]

    # Create dictionary linking netbox ids to the libreNMS device they have been linked to, if any
    # Also create list of libreNMS devices with no netbox ID attached
    logging.info("[Step 4/5] Fetching map of Netbox/LibreNMS devices")
    linked_libnms_devices = {}
    unlinked_libnms_devices = {}
    for device in librenms_devices:
        name = device["hostname"]
        id = str(device["device_id"])

        if device["overwrite_ip"]:
            if DRY_RUN:
                logging.info(
                    f"[DRY RUN] Overwrite IP found for '{name}': {device['overwrite_ip']}. Would remove it as it is deprecated."
                )
            else:
                try:
                    logging.info(
                        f"Overwrite IP found for '{name}': {device['overwrite_ip']}. Removing as it is deprecated."
                    )
                    data = '{"field": "overwrite_ip", "data": null}'
                    logging.info(data)
                    response = libnms_session.patch(LIBNMS_API + id, data=data)
                    if response.json()["status"] == "error":
                        raise Exception(
                            f"Error received from LibreNMS: {response.json()['message']}"
                        )
                except Exception:
                    logging.exception(
                        f"""Failed to remove overwrite IP from '{name}'"""
                    )
                    raise

        try:
            response = libnms_session.get(
                LIBNMS_API + name + "/components?type=netbox_id"
            )
            if response.json()["status"] == "error":
                raise Exception(
                    f"Error received from LibreNMS: {response.json()['message']}"
                )
        except Exception:
            logging.exception(
                f'Error when getting LibreNMS components for device "{name}"'
            )
            continue
        response = response.json()
        if "components" in response:
            # Get the last netbox_id attached. There should only be 1 netbox_id, if there are more there's a problem somewhere
            try:
                components = response["components"]

                if len(components) != 1:
                    raise ValueError(
                        f"Expected one netbox ID attached to libreNMS device with hostname '{name}', got {len(components)}"
                    )

                component_id, component = next(iter(components.items()))
                netbox_id = str(component["label"])

            except Exception:
                logging.exception(
                    f'Error when getting LibreNMS netbox ID of components for device "{name}"'
                )
                continue

            if netbox_id not in netbox_devices:
                if (
                    (found_device := nb.dcim.devices.get(id=netbox_id)) is not None
                ) and (found_device.status in NETBOX_STATUSES):
                    errors = []
                    if (str(found_device.tenant.id) not in NETBOX_TENANTS) or (
                        found_device.tenant.slug in NETBOX_TENANTS
                    ):
                        errors.append(
                            f"This is in Tenant ID {found_device.tenant.id} / Slug '{found_device.tenant.slug}', which is not configured in config.toml"
                        )

                    if found_device.role.slug not in netbox_roles:
                        errors.append(
                            f"This has netbox role {found_device.role.name} which this script doesn't check for."
                        )

                    if errors:
                        joined_errors = errors[0] + "".join(
                            f"\nAdditionally, {e[0].lower() + e[1:]}"
                            for e in errors[1:]
                        )
                    else:
                        joined_errors = (
                            "Tenant and Role look fine, so no clear cause found."
                        )

                    error = (
                        f"LibreNMS Device {id} ('{name}') is linked to NetBox ID {netbox_id}.\n"
                        f"{joined_errors}"
                    )

                    logging.error(error)
                    raise ValueError(error)

                if DRY_RUN:
                    logging.info(
                        f"[DRY RUN] Would unlink deleted/decomissioned netbox ID '{netbox_id}' from device '{name}'"
                    )
                else:
                    logging.info(
                        f"Unlinking deleted/decomissioned netbox ID '{netbox_id}' from device '{name}'"
                    )
                    try:
                        response = libnms_session.delete(
                            LIBNMS_API + id + f"/components/{component_id}"
                        )
                        if response.json()["status"] == "error":
                            raise Exception(
                                f"Error received from LibreNMS: {response.json()['message']}"
                            )

                    except Exception:
                        logging.exception(
                            f'Error when getting LibreNMS netbox ID of components for device "{name}"'
                        )
                        continue
                unlinked_libnms_devices[str(device["device_id"])] = {
                    "device_id": str(device["device_id"]),
                    "hostname": device["hostname"],
                    "display": device["display"],
                }

                continue

            linked_libnms_devices[netbox_id] = {
                "device_id": str(device["device_id"]),
                "display": str(device["display"]),
                "hostname": device["hostname"],
            }

        else:
            unlinked_libnms_devices[str(device["device_id"])] = {
                "device_id": str(device["device_id"]),
                "hostname": device["hostname"],
                "display": device["display"],
            }

    logging.info("[Step 5/5] Checking LibreNMS against Netbox and Updating")
    for netbox_id, netbox_device in netbox_devices.items():
        netbox_name = netbox_device.name

        # Libre is for switches, primarily.
        # Hypervisors are on there, it's a grey area
        # Draw the line at only keeping/adding their OOB, else we'd have two entries per system
        if (netbox_oob_ip := netbox_device.oob_ip) is not None:
            netbox_hostname = getattr(netbox_oob_ip, "dns_name", None) or None
            netbox_ip = netbox_oob_ip.address.split("/")[0]
        else:
            netbox_ip = netbox_device.primary_ip4
            netbox_hostname = getattr(netbox_ip, "dns_name", None) or None
            netbox_ip = netbox_ip.address.split("/")[0]

        # Set hostname to the IP if we didn't find a DNS name
        if netbox_hostname is None:
            netbox_hostname = netbox_ip

        if netbox_id in linked_libnms_devices:
            # Update linked device if any
            libnms_name = linked_libnms_devices[netbox_id]["hostname"]
            libnms_id = linked_libnms_devices[netbox_id]["device_id"]
            libnms_display = linked_libnms_devices[netbox_id]["display"]
            update_device(
                libnms_id=libnms_id,
                libnms_hostname=libnms_name,
                libnms_display_name=libnms_display,
                netbox_hostname=netbox_hostname,
                netbox_name=netbox_name,
                libnms_session=libnms_session,
            )
        else:
            # Try linking netbox device to an unlinked libreNMS device
            match_found = False
            for libnms_id, libnms_device in unlinked_libnms_devices.items():
                if (
                    libnms_device["hostname"] == netbox_hostname
                    or libnms_device["hostname"] == netbox_name
                    or libnms_device["display"] == netbox_name
                    or libnms_device["hostname"] == netbox_ip
                ):
                    match_found = True
                    libnms_name = libnms_device["hostname"]
                    libnms_display = libnms_device["display"]
                    link_device(
                        libnms_id=libnms_id,
                        libnms_hostname=libnms_name,
                        netbox_id=netbox_id,
                        netbox_name=netbox_name,
                        libnms_session=libnms_session,
                    )
                    update_device(
                        libnms_id=libnms_id,
                        libnms_hostname=libnms_name,
                        libnms_display_name=libnms_display,
                        netbox_hostname=netbox_hostname,
                        netbox_name=netbox_name,
                        libnms_session=libnms_session,
                    )
                    unlinked_libnms_devices.pop(libnms_id)
                    break
            # If no match found, create a new LibreNMS device
            if not match_found:
                if DRY_RUN:
                    logging.info(
                        f"[DRY RUN] Would create new LibreNMS device: {netbox_hostname} ('{netbox_name}')"
                    )
                    continue
                logging.info(
                    f"Creating new LibreNMS device: {netbox_hostname} ('{netbox_name}')"
                )
                input_data = f'{{"hostname": "{netbox_hostname}", "display": "{netbox_name}", "version": "v2c", "community": "public"}}'

                try:
                    response = libnms_session.post(LIBNMS_API, data=input_data)
                    if response.json()["status"] == "error":
                        raise Exception(
                            f"Error received from LibreNMS: {response.json()['message']}"
                        )
                    libnms_device = response.json()["devices"][0]
                    libnms_id = str(libnms_device["device_id"])
                    libnms_name = libnms_device["hostname"]
                except Exception:
                    logging.exception(
                        f'Error when creating LibreNMS device with name "{netbox_hostname}" and display name "{netbox_name}": '
                    )
                    continue

                link_device(
                    libnms_id, libnms_name, netbox_id, netbox_hostname, libnms_session
                )
    logging.info("Checking remaining unlinked devices")
    for libnms_id, libnms_device in unlinked_libnms_devices.items():
        try:
            trial_resolve_nb = set()
            libnms_name = libnms_device["hostname"]
            libnms_display = libnms_device["display"]

            nb_ip = nb.ipam.ip_addresses.get(dns_name=libnms_name)
            if not nb_ip:
                try:
                    ip = ipaddress.ip_address(libnms_name)
                    nb_ip = nb.ipam.ip_addresses.get(address=str(ip))
                except ValueError:
                    # hostname is not an IP address
                    pass

            if nb_ip:
                trial_resolve_nb.update(nb.dcim.devices.filter(primary_ip4_id=nb_ip.id))
                trial_resolve_nb.update(nb.dcim.devices.filter(oob_ip_id=nb_ip.id))

            if libnms_display:
                trial_resolve_nb.update(nb.dcim.devices.filter(name=libnms_display))

            if len(trial_resolve_nb) > 0:
                resolved_devices = [
                    {
                        "id": device.id,
                        "name": device.name,
                        "role": device.role.slug,
                        "ip": device.primary_ip,
                        "tenant": device.tenant.slug,
                        "status": device.status,
                    }
                    for device in trial_resolve_nb
                ]
                logging.warning(
                    f"""Orphaned LibreNMS device with ID {libnms_id} wasn't in our list of netbox devices: {libnms_device}
                    but it might map to Netbox Device: {resolved_devices}
                    Check its Status and Role, are they excluded on purpose?"""
                )
                continue
        except Exception:
            logging.exception(
                f"Error when finding candidate netbox matches for {libnms_device['hostname']}"
            )
            continue
        logging.warning(
            f"Orphaned LibreNMS device with ID {libnms_id} not found in Netbox: {libnms_device}"
        )
    logging.info("Script finished")


if __name__ == "__main__":
    main()
