#!/usr/bin/env python3
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
from typing import Any, TypedDict, cast

import pynetbox
import requests
from pynetbox.models.dcim import Devices
from pynetbox.models.ipam import IpAddresses


class LibreDeviceInfo(TypedDict):
    """
    Typed dictionary for storing information about a device in 
    Librenms.
    """
    device_id: str
    hostname: str
    display: str


class LibreNMSClient:
    """
    Encapsulates LibreNMS API interactions and error handling.
    """

    def __init__(
        self, api_url: str, token: str, verify: bool | str, dry_run: bool
    ) -> None:
        """
        Initialize variables for class
        
        :param self:
        :param api_url: URL for LibreNMS API
        :param token: LibreNMS token
        :param verify: Determines whether to use TLS verification and CA certificate
          to verify LibreNMS connection session
        :param dry_run: Boolean for whether to have a dry run of the command
        :returns: None
        """
        self.api_url = api_url.rstrip("/") + "/"
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update({"X-Auth-Token": token})
        self.session.verify = verify

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        """
        Wrapper for requests that automatically handles standard LibreNMS errors.

        :param method: API method to use in request
        :param endpoint: LibreNMS endpoint
        :returns: Result of request or an empty dictionary
        :raises Exception: Raises error when not able to access LibreNMS API
        """
        url = self.api_url + endpoint.lstrip("/")
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()

        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            return {}

        if data.get("status") == "error":
            raise Exception(f"LibreNMS API Error: {data.get('message')}")
        return data

    def get_devices(self) -> list[dict[str, Any]]:
        """
        Gets LibreNMS devices and returns request result.

        :param self:
        :returns: List of LibreNMS devices
        """
        return self._request("GET", "")["devices"]

    def create_device(self, hostname: str, display_name: str) -> str:
        """
        Creates device in LibreNMS.

        :param self:
        :param hostname: hostname for librenms device
        :param display_name: Display name for librenms device
        :returns: String containing details of newly created device.
        """
        if self.dry_run:
            logging.info(
                f"[DRY RUN] Would create LibreNMS device: {hostname} ('{display_name}')"
            )
            return "dry-run-id"

        logging.info(f"Creating new LibreNMS device: {hostname} ('{display_name}')")
        payload = {
            "hostname": hostname,
            "display": display_name,
            "version": "v2c",
            "community": "public",
        }
        res = self._request("POST", "", json=payload)
        return str(res["devices"][0]["device_id"])

    def remove_overwrite_ip(self, device_id: str, hostname: str) -> None:
        """
        overwrite_ip fields have been deprecated in LibreNMS in favor 
        of hostname and display.
        This method removes the overwrite IP address that is stored in 
        the device's data field.
    
        :param self:
        :param device_id: librenms device ID
        :param hostname: name of host associated with a librenms device
        :returns: None
        """
        if self.dry_run:
            logging.info(
                f"[DRY RUN] Would remove deprecated overwrite IP for '{hostname}'"
            )
            return

        logging.info(f"Removing deprecated overwrite IP for '{hostname}'")
        self._request(
            "PATCH", str(device_id), json={"field": "overwrite_ip", "data": None}
        )

    def get_netbox_component(self, hostname: str) -> dict[str, Any]:
        """
        Get a Netbox component based on a hostname in LibreNMS.

        :param self:
        :param hostname: Name of host
        :returns: NetBox component dictionary for that specific hostname in LibreNMS
        """
        res = self._request("GET", f"{hostname}/components?type=netbox_id")
        return res.get("components", {})

    def unlink_component(
        self, device_id: str, component_id: str, netbox_id: str, hostname: str
    ) -> None:
        """
        Remove reference, unlinking, the NetBox ID from a hostname and delete the component
        from LibreNMS.

        :param self:
        :param device_id: LibreNMS device ID
        :param component_id: LibreNMS component ID for a host
        :param netbox_id: NetBox device ID
        :returns: None
        """
        if self.dry_run:
            logging.info(
                f"[DRY RUN] Would unlink Netbox ID '{netbox_id}' from '{hostname}'"
            )
            return

        logging.info(
            f"Unlinking deleted/decommissioned Netbox ID '{netbox_id}' from '{hostname}'"
        )
        self._request("DELETE", f"{device_id}/components/{component_id}")

    def link_device(
        self, libnms_id: str, libnms_hostname: str, netbox_id: str, netbox_name: str
    ) -> None:
        """
        Links hostname in LibreNMS to the ID of the Device record in NetBox.
        This is done by updating the components of a device in LibreNMS to include
        the NetBox device ID and name in Netbox for the host.

        :param self:
        :param libnms_id: Device ID in librenms for the host
        :param libnms_hostname: Hostname for device in LibreNMS
        :param netbox_id: Device ID in NetBox for the host
        :param netbox_name: Name of device in NetBox
        :returns: None
        """
        if self.dry_run:
            logging.info(
                f"[DRY RUN] Would link '{libnms_hostname}' to NetBox ID {netbox_id} ('{netbox_name}')"
            )
            return

        logging.info(
            f"Linking '{libnms_hostname}' to NetBox ID {netbox_id} ('{netbox_name}')"
        )
        res = self._request("POST", f"{libnms_id}/components/netbox_id")
        component_id = list(res["components"])[0]

        component_data = {
            str(component_id): {
                "type": "netbox_id",
                "label": str(netbox_id),
                "status": 1,
                "ignore": 0,
                "disabled": 0,
                "error": "",
            }
        }
        self._request("PUT", f"{libnms_id}/components", json=component_data)

    def rename_device(self, libnms_id: str, old_name: str, new_name: str) -> None:
        """
        Rename a device in LibreNMS. This changes the hostname for the device in LibreNMS.

        :param self:
        :param libnms_id: Device ID in libreNMS
        :param old_name: Current device name 
        :param new_name: New name to use for device
        :returns: None
        """
        if self.dry_run:
            logging.info(f"[DRY RUN] Would rename '{old_name}' to '{new_name}'")
            return
        logging.info(f"Renaming '{old_name}' to '{new_name}'")
        self._request("PATCH", f"{libnms_id}/rename/{new_name}")

    def update_display_name(
        self, libnms_id: str, hostname: str, old_display: str, new_display: str
    ) -> None:
        """
        Change the display name for a device in LibreNMS

        :param self:
        :param libnms_id: Device ID in libreNMS
        :param hostname: Device hostname
        :param old_name: Current display name 
        :param new_name: New display name to use on device
        :returns: None
        """
        if self.dry_run:
            logging.info(
                f"[DRY RUN] Would update display name for '{hostname}' from '{old_display}' to '{new_display}'"
            )
            return
        logging.info(
            f"Updating display name for '{hostname}' from '{old_display}' to '{new_display}'"
        )
        self._request(
            "PATCH", str(libnms_id), json={"field": "display", "data": new_display}
        )


def fetch_netbox_devices(
    nb: pynetbox.api, nb_config: dict[str, Any], netbox_roles: list[str]
) -> list[Devices]:
    """
    Fetch all devices in NetBox

    :param nb: pynetbox API
    :param nb_config: NetBox config values provided from config.toml file
    :param netbox_roles: Roles in NetBox
    :returns: List of NetBox Devices
    """
    tenant_slugs = [str(t) for t in nb_config["tenants"] if not str(t).isdigit()]
    tenant_ids = [int(t) for t in nb_config["tenants"] if str(t).isdigit()]

    devices_init: list[Devices] = []
    statuses: list[str] = nb_config["statuses"]

    if tenant_slugs:
        devices_init.extend(
            nb.dcim.devices.filter(
                role=netbox_roles, tenant=tenant_slugs, status=statuses
            )
        )
    if tenant_ids:
        devices_init.extend(
            nb.dcim.devices.filter(
                role=netbox_roles, tenant_id=tenant_ids, status=statuses
            )
        )
    if nb_config.get("override_pull_ids"):
        devices_init.extend(nb.dcim.devices.filter(id=nb_config["override_pull_ids"]))
    return devices_init


def filter_netbox_devices(devices_init: list[Devices]) -> dict[str, Devices]:
    """
    Filter devices from NetBox to devices that have a primary IP Address

    :param devices_init: List of devices fetched from NetBox
    :returns: Dictionary of devices with primary IP addresses found in NetBox
    """
    valid_devices: dict[str, Devices] = {}
    for device in devices_init:
        if device.primary_ip or device.oob_ip:
            valid_devices[str(device.id)] = device
        else:
            logging.warning(f"Device {device} has no primary or OOB IPv4")
    return valid_devices


def get_netbox_roles(nb: pynetbox.api, nb_config: dict[str, Any]) -> list[str]:
    """
    Gets list of Roles used in Netbox

    :param nb: NetBox API
    :param nb_config: NetBox config values provided from config.toml file
    :returns: List of roles found in NetBox
    """
    netbox_roles = [
        str(nb.dcim.device_roles.get(slug=r).slug)  # type: ignore
        for r in nb_config["roles"]
    ]  # type: ignore
    for fuzzy in nb_config["fuzzy_roles"]:
        netbox_roles.extend(
            [str(role.slug) for role in nb.dcim.device_roles.filter(fuzzy)]
        )
    return netbox_roles


def fetch_libnms_mapping(
    client: LibreNMSClient,
    netbox_devices: dict[str, Devices],
) -> tuple[dict[str, LibreDeviceInfo], dict[str, LibreDeviceInfo]]:
    """
    Maps LibreNMS devices to NetBox IDs, identifying linked and unlinked devices.

    :param client: LibreNMS client
    :param netbox_devices: Dictionary of devices in NetBox
    :returns: Tuple containing dictionaries of linked and unlinked devices
    """
    linked: dict[str, LibreDeviceInfo] = {}
    unlinked: dict[str, LibreDeviceInfo] = {}

    for device in client.get_devices():
        dev_id = str(device["device_id"])
        hostname = str(device["hostname"])
        display = str(device["display"])

        try:
            if device.get("overwrite_ip"):
                client.remove_overwrite_ip(dev_id, hostname)

            components = client.get_netbox_component(hostname)

            if not components:
                unlinked[dev_id] = {
                    "device_id": dev_id,
                    "hostname": hostname,
                    "display": display,
                }
                continue

            if len(components) > 1:
                raise ValueError(
                    f"Expected 1 Netbox ID attached to '{hostname}', got {len(components)}"
                )

            component_id, component = next(iter(components.items()))
            netbox_id = str(component["label"])

            if netbox_id not in netbox_devices:
                # Device must have been filtered out, or deleted/decommisioned. Unlink it.
                client.unlink_component(dev_id, component_id, netbox_id, hostname)
                unlinked[dev_id] = {
                    "device_id": dev_id,
                    "hostname": hostname,
                    "display": display,
                }
            else:
                linked[netbox_id] = {
                    "device_id": dev_id,
                    "display": display,
                    "hostname": hostname,
                }
        except Exception:
            logging.exception(
                f"Failed to process LibreNMS device mapping for '{hostname}'. Skipping device."
            )
            continue

    return linked, unlinked


def sync_device(
    client: LibreNMSClient,
    libnms_info: LibreDeviceInfo,
    nb_hostname: str,
    nb_name: str,
) -> None:
    """Checks and updates LibreNMS device hostnames based on corresponding NetBox 
    record and display names if out of sync

    :param client: LibreNMS client
    :param libnms_info: Device information in LibreNMS
    :param nb_hostname: Device hostname in NetBox
    :param nb_name: Device name in NetBox
    :returns: None
    ."""
    libnms_id = libnms_info["device_id"]

    if libnms_info["hostname"].lower() != nb_hostname.lower():
        client.rename_device(libnms_id, libnms_info["hostname"], nb_hostname)
        libnms_info["hostname"] = nb_hostname

    if libnms_info["display"] != nb_name:
        client.update_display_name(
            libnms_id, libnms_info["hostname"], libnms_info["display"], nb_name
        )
        libnms_info["display"] = nb_name


def get_netbox_ip_and_hostname(device: Devices, test=None) -> tuple[str, str]:
    """
    Extracts preferred IP and hostname from a NetBox device.
    Preferred IP Addres is an Out of Band (OOB) IP Address, else default to storing the 
    primary IPv4 Address.

    :param device: NetBox device
    :param test: ?
    :returns: Tuple with IP Address and Hostname of device from NetBox
    """
    # oob_ip: Out Of Band IP/ Management Network IP
    target_ip = cast(
        IpAddresses, device.oob_ip if device.oob_ip else device.primary_ip4
    )
    target_ip_address = str(target_ip.address)

    hostname = str(
        getattr(target_ip, "dns_name", None)
        or target_ip_address.split("/", maxsplit=1)[0]
    )
    ip_addr = target_ip_address.split("/", maxsplit=1)[0]
    return ip_addr, hostname


def sync_netbox_librenms(
    libnms: LibreNMSClient,
    netbox_devices: dict[str, Devices],
    linked_libnms: dict[str, LibreDeviceInfo],
    unlinked_libnms: dict[str, LibreDeviceInfo],
) -> None:
    """
    Sync device information between LibreNMS and NetBox, where NetBox is the 
    source of truth. If a device in NetBox does not exist in LibreNMS, the method 
    attempts to create a new device in LibreNMS and link it to the record in NetBox.

    :param libnms: LibreNMS Client
    :param netbox_devices: Dictionary of devices in NetBox
    :param linked_libnms: Devices in LibreNMS that are linked to corresponding NetBox Device
    :param unlinked_libnms: Devices in LibreNMS that are not linked to a corresponding NetBox Device
    :returns: None 
    """
    for nb_id, nb_device in netbox_devices.items():
        try:
            nb_ip, nb_hostname = get_netbox_ip_and_hostname(nb_device)
            nb_name = str(nb_device.name)

            if nb_id in linked_libnms:
                sync_device(libnms, linked_libnms[nb_id], nb_hostname, nb_name)
            else:
                match_found = False
                # Iteratively search for a device which might match
                for lib_id, lib_dev in list(unlinked_libnms.items()):
                    if (
                        lib_dev["hostname"] in (nb_hostname, nb_name, nb_ip)
                        or lib_dev["display"] == nb_name
                    ):
                        libnms.link_device(lib_id, lib_dev["hostname"], nb_id, nb_name)
                        sync_device(libnms, lib_dev, nb_hostname, nb_name)
                        del unlinked_libnms[lib_id]
                        linked_libnms[nb_id] = {
                            "device_id": lib_id,
                            "display": lib_dev["display"],
                            "hostname": lib_dev["hostname"],
                        }
                        match_found = True
                        break

                if not match_found:
                    # Still not found: try to create it
                    new_libnms_id = libnms.create_device(nb_hostname, nb_name)
                    if not libnms.dry_run:
                        libnms.link_device(new_libnms_id, nb_hostname, nb_id, nb_name)
                    linked_libnms[nb_id] = {
                        "device_id": new_libnms_id,
                        "display": nb_name,
                        "hostname": nb_hostname,
                    }
        except Exception:
            logging.exception(
                f"Failed to sync Netbox device '{nb_device}'. Skipping device."
            )
            continue


def attempt_find_orphans(
    nb: pynetbox.api, unlinked_devices: dict[str, LibreDeviceInfo]
) -> dict[str, list[Devices]]:
    """
    Attempts to match unlinked LibreNMS devices to NetBox devices which were not fetched.

    :param nb: NetBox API
    :param unlinked_devices: Dictionary of devices in LibreNMS that are not linked to a record in NetBox
    :returns: Dictionary of orphaned devices in LibreNMS that cannot be found in NetBox
    """
    orphans_and_candidates: dict[str, list[Devices]] = {}
    for libnms_id, dev in unlinked_devices.items():
        candidates: set[Devices] = set()

        # Check via IP
        nb_ip = cast(
            IpAddresses | None, nb.ipam.ip_addresses.get(dns_name=dev["hostname"])
        )
        if not nb_ip:
            try:
                ipaddress.ip_address(dev["hostname"])
                nb_ip = cast(
                    IpAddresses | None,
                    nb.ipam.ip_addresses.get(address=dev["hostname"]),
                )
            except ValueError:
                pass
        if nb_ip:
            candidates.update(nb.dcim.devices.filter(primary_ip4_id=nb_ip.id))
            candidates.update(nb.dcim.devices.filter(oob_ip_id=nb_ip.id))

        # Check via display name -> netbox name
        if dev["display"]:
            candidates.update(nb.dcim.devices.filter(name=dev["display"]))

        if candidates:
            resolved = [
                {
                    "id": c.id,
                    "name": c.name,
                    "role": c.role.slug if c.role else "None",
                    "tenant": c.tenant.slug if c.tenant else "None",
                }
                for c in candidates
            ]
            logging.warning(
                f"Orphaned LibreNMS device {libnms_id} ('{dev['hostname']}') might map to NetBox Device(s): {resolved}\n"
                "Check Status and Role configs—are they excluded intentionally?"
            )
            orphans_and_candidates[libnms_id] = list(candidates)
        else:
            logging.warning(
                f"Orphaned LibreNMS device {libnms_id} ('{dev['hostname']}') not found in NetBox."
            )
            orphans_and_candidates[libnms_id] = []
    return orphans_and_candidates


def main() -> None:
    """
    Main method for syncing LibreNMS devices to corresponding devices in NetBox.
    This is done in stages:
    
    - 1: Loads device roles from NetBox
    - 2: Loads device types from NetBox
    - 3: Loads devices from NetBox that have a primary/OOB (Out Of Band) IP Addresses
    - 4: Fetches mapping of devices between NetBox and LibreNMS
    - 5: Checks list of devices in LibreNMS against NetBox and syncs device details
    - 6: Identifies list of orphaned devices in LibreNMS

    :returns: None
    """
    config_path = Path(__file__).resolve().parent / "config.toml"
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    nb_config = config["netbox"]

    rfh = RotatingFileHandler(
        filename=config["general"]["log_file"], maxBytes=5 * 1024 * 1024, backupCount=1
    )
    sh = logging.StreamHandler()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s:%(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[rfh, sh],
    )

    logging.info("Script beginning")

    nb = pynetbox.api(nb_config["api"].rstrip("/") + "/", token=nb_config["token"])

    libnms = LibreNMSClient(
        api_url=config["librenms"]["api"],
        token=config["librenms"]["token"],
        verify=config["librenms"]["ca_verify"],
        dry_run=config["general"]["dry_run"],
    )
    logging.info("[Step 1/6] Loading roles from Netbox")
    netbox_roles = get_netbox_roles(nb, nb_config)
    logging.info(f"Found Roles: {netbox_roles}")

    logging.info("[Step 2/6] Loading devices matching tenants/roles from Netbox")
    devices_init = fetch_netbox_devices(nb, nb_config, netbox_roles)
    devices_init_count = len(devices_init)
    logging.info(f"Found {devices_init_count} matching devices")

    logging.info("[Step 3/6] Filtering out Netbox devices without a primary/OOB IP")
    netbox_devices = filter_netbox_devices(devices_init)
    netbox_devices_count = len(netbox_devices)
    logging.info(
        f"Filtered out {devices_init_count - netbox_devices_count} device(s). Now left with {netbox_devices_count} devices."
    )

    logging.info("[Step 4/6] Fetching map of Netbox/LibreNMS devices")
    linked_libnms, unlinked_libnms = fetch_libnms_mapping(
        client=libnms, netbox_devices=netbox_devices
    )
    initial_linked_count = len(linked_libnms)
    initial_unlinked_count = len(unlinked_libnms)
    logging.info(
        f"Found {initial_linked_count} devices already linked to Netbox, and {initial_unlinked_count} not yet linked"
    )

    logging.info("[Step 5/6] Checking Netbox devices against LibreNMS, and syncing")
    sync_netbox_librenms(libnms, netbox_devices, linked_libnms, unlinked_libnms)
    final_linked_count = len(linked_libnms)
    final_unlinked_count = len(linked_libnms)
    logging.info(
        f"{initial_linked_count - final_linked_count} newly linked devices, "
        f"consisting of {initial_unlinked_count - final_unlinked_count} previously unlinked devices that are now linked, "
        f"and {final_linked_count + final_unlinked_count - initial_linked_count - initial_unlinked_count} newly-created devices."
    )

    logging.info(
        f"[Step 6/6] Checking {final_unlinked_count} remaining unlinked devices for potential orphans"
    )
    # TODO: Do something with orphans remaining
    # For example, add to a LibreNMS device group for visibility
    orphans_and_candidates = attempt_find_orphans(nb, unlinked_libnms)  # noqa: F841

    logging.info("Script finished")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Script failed with an unhandled exception")
        raise
