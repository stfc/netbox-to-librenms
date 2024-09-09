#!/usr/bin/python3

import json
import logging
from logging.handlers import RotatingFileHandler
import pynetbox
import requests
import sys

from script_config import ca_dir, libnms_token, libnms_api, netbox_token, netbox_api, log_file

rfh = RotatingFileHandler(
  filename = log_file,
  maxBytes = 5*1024*1024,
  backupCount = 1
)

logging.basicConfig(
  level = logging.INFO,
  format = "%(asctime)s:%(levelname)s - %(message)s",
  datefmt = "%Y-%m-%d %H:%M:%S",
  handlers = [rfh]
)

def link_device(libnms_name, netbox_id, libnms_session):
  """
  Link libreNMS and Netbox device by adding a component with the label netbox_id
  to the LibreNMS device with hostname libnms_name
  """
  try:
    response = libnms_session.post(libnms_api+libnms_name+'/components/netbox_id')
    if response.json()["status"] == "error":
      raise Exception(f'Error received from LibreNMS: {response.json()["message"]}') 
    #Get the ID of the component just created so it can be modified and labelled
    component_id = list(response.json()["components"])[0]
    component_data = '{"%s": {"type": "netbox_id", "label": "%s", "status": 1, "ignore": 0, "disabled": 0, "error": ""}}' % (component_id, netbox_id)
    response = libnms_session.put(libnms_api+libnms_name+'/components', data=component_data)
    if response.json()["status"] == "error":
      raise Exception(f'Error received from LibreNMS: {response.json()["message"]}') 
  except:
    logging.exception(f'Failed to link device with name "{libnms_name}" with netbox id "{netbox_id}": ')

def update_device(libnms_name, libnms_ip, netbox_name, netbox_ip, libnms_session):
  """
  Update name and IP of libreNMS device to be the same as that of the netbox device that
  it is linked to (if they are not already the same)
  """
  try:
    if libnms_name != netbox_name:
      print(libnms_name, netbox_name)
      response = libnms_session.patch(libnms_api + libnms_name + '/rename/' + netbox_name)
      if response.json()["status"] == "error":
        raise Exception(f'Error received from LibreNMS: {response.json["message"]}')
  except:
    logging.exception(f"""Failed to update libreNMS device name from '{libnms_name}' to '{netbox_name}'""")
  try:
    if libnms_ip != netbox_ip:
      data = '{"field": "overwrite_ip", "data": "%s"}' % netbox_ip
      print(data)
      response = libnms_session.patch(libnms_api + libnms_name, data=data)
      if response.json()["status"] == "error":
        raise Exception(f'Error received from LibreNMS: {response.json["message"]}')
  except:
    logging.exception(f"""Failed to update libreNMS device IP from '{libnms_ip}' to '{netbox_ip}'""")

logging.info("Script beginning")
 
#Create netbox and librenms sessions and get lists of devices
netbox_session = requests.Session()
netbox_session.verify = ca_dir
nb = pynetbox.api(netbox_api,token = netbox_token)
nb.http_session = netbox_session

#Create a list of netbox roles to select, including anything with the word 'switch' in it
try:
  netbox_roles = nb.dcim.device_roles.filter('switch')
except:
  logging.exception('Error when getting device roles from netbox: ')
  sys.exit()
netbox_roles_formatted = []
for role in netbox_roles:
  role = str(role)
  role = role.lower()
  role = role.replace(" ", "-")
  netbox_roles_formatted.append(role)
netbox_roles_formatted.append('router')
netbox_roles_formatted.append('pdu')

try:
  netbox_devices_init = nb.dcim.devices.filter(role = netbox_roles_formatted, tenant_group = 'rig')
except:
  logging.exception('Error when getting devices from netbox: ')
  sys.exit()

#Filter out netbox devices without a primary ip
netbox_devices = []
for device in netbox_devices_init:
  if dict(device)["primary_ip"]:
    netbox_devices.append(device)

libnms_session = requests.Session()
libnms_session.verify = ca_dir
libnms_session.headers = {'X-Auth-Token': libnms_token}

try:
  response = libnms_session.get(libnms_api)
  if response.json()["status"] == "error":
    raise Exception(f'Error received from LibreNMS: {response.json()["message"]}') 
except:
  logging.exception('Error when getting devices from LibreNMS: ')
  sys.exit()
librenms_devices = response.json()["devices"]

#Create dictionary linking netbox ids to the libreNMS device they have been linked to, if any
#Also create list of libreNMS devices with no netbox ID attached
linked_libnms_devices = {}
unlinked_libnms_devices = []
for device in librenms_devices:
  name = device["hostname"]
  try:
    response = libnms_session.get(libnms_api+name+'/components?type=netbox_id')
    if response.json()["status"] == "error":
      raise Exception(f'Error received from LibreNMS: {response.json()["message"]}') 
  except:
    logging.exception(f'Error when getting LibreNMS components for device "{name}": ')
    continue
  response = response.json()
  if "components" in response:
    #Get the last netbox_id attached. There should only be 1 netbox_id, if there are more there's a problem somewhere
    for component in response["components"].values():
      netbox_id = int(component["label"])
    linked_libnms_devices[netbox_id] = device
    if len(response["components"]) >= 2:
      logging.warning(f'More than one netbox ID attached to libreNMS device with name "{name}"')
  else:
    unlinked_libnms_devices.append(device)    

for netbox_device in netbox_devices:
  netbox_id = dict(netbox_device)["id"]
  netbox_name = dict(netbox_device)["name"]
  netbox_ip = dict(netbox_device)["primary_ip"]["address"]
  netbox_ip = (netbox_ip.split("/"))[0]
  if netbox_id in linked_libnms_devices:
    #Update linked device if any
    libnms_name = linked_libnms_devices[netbox_id]["hostname"]
    libnms_ip = linked_libnms_devices[netbox_id]["overwrite_ip"]
    update_device(libnms_name, libnms_ip, netbox_name, netbox_ip, libnms_session)
  else:
    #Try linking netbox device to an unlinked libreNMS device
    match_found = False
    for libnms_device in unlinked_libnms_devices:
      if libnms_device["ip"] == netbox_ip or libnms_device["hostname"] == netbox_name:
        match_found = True
        libnms_name = libnms_device["hostname"]
        libnms_ip = libnms_device["overwrite_ip"]
        link_device(libnms_name, netbox_id, libnms_session)
        update_device(libnms_name, libnms_ip, netbox_name, netbox_ip, libnms_session)
        break
    #If no match found, create a new LibreNMS device
    if match_found == False:
      input_data = '{"hostname": "%s", "overwrite_ip": "%s", "version": "v2c", "community": "public"}' % (netbox_name, netbox_ip)
      try:
        response = libnms_session.post(libnms_api, data=input_data)
        if response.json()["status"] == "error":
          raise Exception(f'Error received from LibreNMS: {response.json()["message"]}') 
      except:
        logging.exception(f'Error when creating LibreNMS device with name "{netbox_name}" and IP "{netbox_ip}": ')
        continue
      link_device(netbox_name, netbox_id, libnms_session)

logging.info("Script finished")
