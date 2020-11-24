# netbox-to-librenms
This is a script (pull_netbox.py) created initially for use in the SCD RIG group to keep Netbox and LibreNMS synchronized 
with each other. It assumes Netbox is being used as a single source of truth and is designed to be run by a cron job on 
the LibreNMS host to pull new network devices and changes to existing devices from Netbox. The REST APIs of Netbox and 
LibreNMS are used to do this.

The script will link devices by attaching the ID of the Netbox device to the LibreNMS device in the form of a component
labelled with the ID (see https://docs.librenms.org/API/Devices/#add_components). The script will create new devices and 
update linked devices but also try to link pre-existing devices in LibreNMS by finding a device in Netbox with an equivalent 
IP address or name. There is no functionality to delete LibreNMS devices at the moment but that could easily be added.

As of 24/11/20 the script is hardcoded to pull devices with the roles 'Router', 'PDU', and anything with 'Switch' in the role
and also only devices from the RIG tenant group. This can be changed, see the bottom of this readme.

The 'algorithm' is as follows:
1. Get a filtered list of Netbox devices (i.e. only including switches, routers etc. and only from particular tenants)
2. Get a list of all LibreNMS devices
3. Create a dictionary of linked devices with the netbox IDs as the keys and the libreNMS objects as the values, place
any unlinked LibreNMS devices in a seperate list
4. For each Netbox device:
   1. If it has a linked LibreNMS device, update the linked device's name and IP address
   2. If no linked device was found, see if any unlinked LibreNMS devices have a matching IP or hostname and if so, link
   that device and update it
   3. If no linked device or matching unlinked device was found, create a new LibreNMS device and link it

This system means that if someone adds a device manually to LibreNMS, including devices added before this script begins running,
the script will try to find a matching Netbox device for it and link it to that.

### Configuration and usage in groups outside of RIG
A few things can be configured. Rename the file called 'script_config.py.example' to 'script_config.py' and change the entries
in there as desired. A URL for your LibreNMS API must be specified in the format given in the example as well as a LibreNMS token
with admin access. A CA certificate must also be specified which for SCD (and probably also STFC?) will usually be the UK eScience 
CA root (http://www.ngs.ac.uk/ukca/certificates/cacerts.html).

The script is not totally configurable at the moment (24/11/20) and so may need to be modified if other groups wish to use it. The
main problem is the Netbox device filters, which select a specific set of device roles only select RIG devices. You will probably want
different filter settings. You may also not need the CA certificate to be imported into the script. I would suggest either 
changing the script to make these things configurable or making a branch with your own preferred hardcoded settings for use outside RIG.
