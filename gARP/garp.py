"""
Description: 
    PA Gratuitous ARP Script
    Connect to PA or Panorama and determine which IP Addresses and Interfaces should
    Send Gratuitous ARP out, typically during the cutover of a new FW.

Requires:
    ipcalc
    requests
    xmltodict
    lxml
        to install try: pip3 install xmltodict requests lxml ipcalc

Author:
    Ryan Gillespie rgillespie@compunet.biz
    Docstring stolen from Devin Callaway

Tested:
    Tested on macos 10.13.6
    Python: 3.6.2
    PA VM100, Panorama, PA 5250

Example usage:
        $ python3 garp.py <destination folder> <PA(N) mgmt IP> <username>
        Password: 

Cautions:
    - Source-NAT only (discovers and outputs others)
    - Panorama Post-NAT rules only (for now)
    

Legal:
    THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
    WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
    MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
    ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
    WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
    ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
    OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

from getpass import getpass
import sys
import os
import ipcalc
import time
import argparse
import copy

import xmltodict
import api_lib_pa as pa_api

DEBUG = False

class mem: 
    ip_to_eth_dict = {}
    review_nats = []
    address_object_entries = None


def iterdict(d, searchfor):
    """
    Traverse through the dictionary (d) and find the key: (searchfor).
    Return the value of that key.
    """
    for k, v in d.items():
        if searchfor in k:
            return v
        elif isinstance(v, dict):
            return iterdict(v, searchfor)
        else:
            pass


def interface_lookup(ip):
    """
    Used to find which physical interface is associated with this IP (NAT entry).
    Uses existing dictionary mapping ip/mask to physical interface.
    Searches based on the subnet mask in use on the physical interface.
    """
    for key, v in mem.ip_to_eth_dict.items():
        iprange = ipcalc.Network(key)
        if ip in iprange:
            return v


def address_lookup(entry):
    """
    Used to find the translated addresses objects on the PA/Panorama.
    Runs another api call to grab the object and return it's value (the actual ip address)
    If the NAT rule isn't using an object, we can assume this value is the IP address.
    Returns a LIST
    """

    if not isinstance(mem.address_object_entries, list):
        mem.address_object_entries = [mem.address_object_entries]

    found = False
    for addr_object in mem.address_object_entries:
        if entry in addr_object.get("@name"):
            if "ip-netmask" in addr_object:
                found = True
                ips = addr_object["ip-netmask"]
            else:
                add_review_entry(addr_object, "not-ip-netmask")
    if not found:
        ips = entry 

    if isinstance(ips,list):
        pass # Good (for now)
    else:
        ips = [ips]

    return ips # Always returns a list (currently)


def add_review_entry(entry, type):
    
    if type == "disabled":
        mem.review_nats.append(f"Disabled Rule: Check {entry['@name']}")
    elif type == "dnat":
        mem.review_nats.append(f"DNAT, - Check NAT rule named: '{entry['@name']}' for details.")
    elif type == "not-ip-netmask":
        mem.review_nats.append(f"non IP-NETMASK used for translated address: '{entry['@name']}' for details.")

        print("Not implemented yet. I can add it easily if you need it. Send me the natrules.xml")
        print("Most likely an address-object using 'IP Range', 'IP Wildcard Mask', or 'FQDN'.")
        print("For NAT? I know a couple use cases, but maybe manually add this gARP after reviewing.")
        print("May be redundant or otherwise unnecessary.")

    pa_api.create_xml_files(entry, f"api/review/review-{entry['@name']}.xml")

    return None


def add_garp_command(ip, ifname, nat=None):
    """
    Update global garp_commands list.
    First remove the subnet mask from the 'ip' received.
    example: 'test arp gratuitous ip 5.5.5.4 interface ethernet1/1'
    """
    # Update Global Table
    if not nat:
        mem.ip_to_eth_dict.update({ip: ifname})
    # Removes anything in IP after /, ie /24
    ip = ip.split("/", 1)[0]  
    garp_command = f"test arp gratuitous ip {ip} interface {ifname}"
    return garp_command



def process_interface_entry(entry):
    # Set interface name
    ifname = entry["@name"]
    commands = []

    # Should have an IP
    if "layer3" in entry:
        found = False
        errors = []
        # Normal IP Address on interface
        if "ip" in entry["layer3"]:
            found = True
            try:
                # Normal 'test arp' generation
                ip = entry["layer3"]["ip"]["entry"]["@name"]
                commands.append(add_garp_command(ip, ifname))
            except:
                # Secondary addresses, 'test arp'
                for x_ip in entry["layer3"]["ip"]["entry"]:
                    ip = x_ip["@name"]
                    commands.append(add_garp_command(ip, ifname))

        # Sub Interfaces
        if "units" in entry["layer3"]:
            try:
                subifname = entry["layer3"]["units"]["entry"]["@name"]
                # 1 Subif, 1 IP
                if "ip" in entry["layer3"]["units"]["entry"]:
                    found = True
                    ip = entry["layer3"]["units"]["entry"]["ip"]
                    try:    # Subif Generate 'test arp'
                        ip = entry["entry"]["@name"]
                        commands.append(add_garp_command(ip, ifname))
                    except: # Secondary addresses, 1 Subif, 'test arp'
                         for x_ip in ip:
                             ip_ = x_ip["@name"]
                             commands.append(add_garp_command(ip_, subifname))
                else:
                    errors.append(f"No IP address found (e1)-(DHCP?), {subifname}") 
            except: # Multiple Subinterfaces
                for subif in entry["layer3"]["units"]["entry"]:
                    subifname = subif["@name"]

                    if "ip" in subif:
                        found = True
                        try:    # Subif Generate 'test arp'
                            ip = subif["ip"]["entry"]["@name"]
                            commands.append(add_garp_command(ip, subifname))
                        except: # Secondary addresses on subinterface, 'test arp'
                            for x_ip in subif["ip"]["entry"]:
                                ip_ = x_ip["@name"]
                                commands.append(add_garp_command(ip, subifname))
                    else:
                        errors.append(f"No IP address found (e2)-(DHCP?), {subifname}")             

        if found:  
            return commands
        else:   # Probably DHCP, let DHCP handle it.
            errors.append(
                f"No IP address found (e3)-(DHCP?), {ifname}"
            )
            return errors

    else:  # No 'layer3', no IP Address here.
        test_garp = f"No IP address found (e4)-(DHCP?), {ifname}"
        return test_garp
    


def process_nat_entry(entry):
    
    commands = []

    if "disabled" in entry:
        if entry["disabled"] == "yes":
            add_review_entry(entry, "disabled")
            return None
    if "destination-translation" in entry:
        add_review_entry(entry, "dnat")
    if "source-translation" in entry:
        snat = entry["source-translation"]

        # Returns Address Object typically, could also be an IP
        # Usually 'translated-address' but also check for 'interface-address'
        x_addr_obj = iterdict(snat, "translated-address")
        int_addr_obj = iterdict(snat, "interface-address")

        # 'translated-address'
        if x_addr_obj:
            try: # Find the real-ip from the address-object, add 'test arp'
                ips = address_lookup(x_addr_obj)
                for ip in ips:
                    # Set the interface name
                    ifname = interface_lookup(ip)
                    if not ifname:
                        ifname = "INTERFACE NOT FOUND"
                    commands.append(add_garp_command(ip, ifname, "nat"))
            except: # I think the only use-case is dynamic-pat with multiple pat pools/ip's.
                print("\nDYNAMIC PAT MULTIPLE IP'S? IF NOT PLEASE LET ME KNOW\n")
                ipobjs = []
                if isinstance(x_addr_obj["member"], list):
                    [ipobjs.append(obj) for obj in addr_obj["member"]]
                else:
                    ipobjs.append(x_addr_obj["member"])
                
                for ipobj in ipobjs:
                    ips = address_lookup(ipobj)
                    for ip in ips:
                        ifname = interface_lookup(ip)
                        if not ifname:
                            ifname = "INTERFACE NOT FOUND"
                        commands.append(add_garp_command(ip, ifname, "nat"))
        if int_addr_obj:    # Dynamic IP and Port / Interface Address
            ifname = int_addr_obj["interface"]

            if "ip" in int_addr_obj:
                ipobj = int_addr_obj["ip"]
                ips = address_lookup(ipobj)
                for ip in ips:
                    commands.append(add_garp_command(ip, ifname, "nat"))
            else: # No IP Found (DHCP?), we have interface already, should have 'test arp' command from that.
                ip = "IP NOT FOUND, ARP TAKEN CARE OF VIA: "
                commands.append(add_garp_command(ip, ifname, "nat"))
        
        if commands:
            return commands
        else:
            return f"Error, SNAT possibly misconfigured, {snat}"
    
    return None


def build_garp_commands(input_type, entries):
    """
    Search through PA/Panorama NAT Rules.
    Build a list of 'test arp' commands based on what is found.
    Currently only supports source-nat, dest-nat's are noted at the end of the output.
    Return this list.
    """
    # Set whether this is NAT or Interface based
    if input_type == "ethernet" or input_type == "aggregate-ethernet":
        process_entries = process_interface_entry
    elif input_type == "pre-nat-rules" or input_type == "post/nat-rules":
        process_entries = process_nat_entry
    else:
        print(f"Unsupported Type - {input_type}")
        sys.exit(0)

    # Begin
    results = []
    if entries:

        entries = entries["entry"]
        print(f"Searching through {input_type}")

        if not isinstance(entries, list):
            # Only 1 entry, run once and return it as a list.
            results = process_entries(entries)
            return [results]
        else:
            # Normal operations, process each entry individually, build the output.
            for entry in entries:
                results.append(process_entries(entry))

        # Returns the list of 'test arp' commands based on the input type received.
        return results

    else:  
        print(f"No {input_type} found.")
        return []


def validate_output(output_type):
    validated_output = copy.deepcopy(output_type)

    if output_type:
        if output_type.get("result"):
            return validated_output["result"]
    else:
        return None


def print_garp_output(command_list):
    for command in command_list:
        if isinstance(command,list):
            for ip in command:
                if ip:
                    print(ip)
        else:
            if command:
                print(command)


def garp_logic(pa_ip, username, password, pa_type, filename=None):
    """
    Main point of entry.
    Connect to PA/Panorama.
    Grab 'test arp' output from interfaces and NAT rules.
    Print out the commands.
    """

    if pa_type != "xml":
        pa = pa_api.api_lib_pa(pa_ip, username, password, pa_type)
    else: # XML - DEBUGGING
        pass
        # pre_nat_output = None
        # post_nat_output = None
        # fin = open(filename, 'r')
        # temp = fin.read()
        # fin.close()
        # int_output = xmltodict.parse(temp)
        # int_output = int_output["response"]
        # # print(int_output["response"])
        # sys.exit(0)

    # Set the correct XPATH for what we need (interfaces and nat rules)
    if pa_type == "panorama":

        incorrect_input = True
        while incorrect_input:
            # Needs Template Name & Device Group
            device_groups, template_names = pa.grab_panorama_objects()
            print("\nTemplate Names:")
            print("---------------------")
            for template in template_names:
                print(template)
            print("--------------\n")
            print("Device Groups:")
            print("--------------")
            for dg in device_groups:
                print(dg)
                
            pa.template_name = input("\nEnter the Template Name: ")
            pa.device_group = input("\nEnter the Device Group Name: ")

            incorrect_input = (
                pa.device_group not in device_groups or
                pa.template_name not in template_names
            )
            if incorrect_input:
                print("\n\nERROR: Template or Device Group not found.\n")

        # Found Device Group / Template Name, Proceed
        XPATH_INTERFACES = pa_api.XPATH_INTERFACES_PAN.replace("TEMPLATE_NAME", pa.template_name)
        XPATH_PRE = pa_api.XPATH_NAT_RULES_PRE_PAN.replace("DEVICE_GROUP", pa.device_group)
        XPATH_POST = pa_api.XPATH_NAT_RULES_POST_PAN.replace("DEVICE_GROUP", pa.device_group)
        XPATH_ADDR = pa_api.XPATH_ADDRESS_OBJ_PAN.replace("DEVICE_GROUP", pa.device_group)
        
        # Grab NAT Rules
        pre_nat_output = pa.grab_api_output(
            "xml", XPATH_PRE, f"api/pre-natrules.xml"
        )
        post_nat_output = pa.grab_api_output(
            "xml", XPATH_POST, f"api/post-natrules.xml"
        )
    
    elif pa_type == "pa":
        XPATH_INTERFACES = pa_api.XPATH_INTERFACES
        XPATH_NATRULES = pa_api.XPATH_NAT_RULES
        XPATH_ADDR = pa_api.XPATH_ADDRESS_OBJ

        # Grab NAT Rules 
        pre_nat_output = None
        post_nat_output = pa.grab_api_output(
            "xml", XPATH_NATRULES, f"api/pa-natrules.xml"
        )
    
    # We have what we need, begin the work.
    start = time.perf_counter()
    print("\n\nStarting...")

    # Grab Interfaces/objects, NAT already pulled from above
    int_output = pa.grab_api_output(
        "xml", XPATH_INTERFACES, f"api/interfaces.xml"
    )
    address_objects = pa.grab_api_output(
        "xml", XPATH_ADDR, f"api/address-objects.xml"
    )

    # Organize all the XML:
    # Get rid of 'Nonetype' issues
    int_output = validate_output(int_output)
    addr_objects = validate_output(address_objects)
    pre_nat_output = validate_output(pre_nat_output)
    post_nat_output = validate_output(post_nat_output)
    # Can't run without an interface or a nat rule
    if not int_output or not post_nat_output:
        print("\nUnable to load required information, see above and correct the issue.\n")
        sys.exit(0)
    # Get the actual entries
    eth_entries = int_output["interface"].get("ethernet")
    ae_entries = int_output["interface"].get("aggregate-ethernet")
    post_nat_entries = post_nat_output["rules"]
    pre_nat_entries = pre_nat_output["rules"] if pre_nat_output else None
    mem.address_object_entries = addr_objects["address"]["entry"] if addr_objects else None

    # Start grabbing test arp commands from the entries
    eth_commands = build_garp_commands("ethernet", eth_entries)
    ae_commands = build_garp_commands("aggregate-ethernet", ae_entries)
    pre_nat_commands = build_garp_commands("pre-nat-rules", pre_nat_entries)
    post_nat_commands = build_garp_commands("post/nat-rules", post_nat_entries)

    # Output
    print(f"\n\ngARP Test Commands:")
    print("-----------------------------------------------------------")
    print("--------------------ARP FOR Interfaces---------------------")
    if eth_commands:
        print_garp_output(eth_commands)
    if ae_commands:
        print_garp_output(ae_commands)
    print("-------------------------ARP FOR NAT-----------------------")
    if pre_nat_commands:
        print_garp_output(pre_nat_commands)
    if post_nat_commands:
        print_garp_output(post_nat_commands)
    print("-----------------------------------------------------------")
    print("--------------------REVIEW THESE NATS----------------------")
    for nat in mem.review_nats:
        print(nat)
    print("-----------------------------------------------------------\n")
    end = time.perf_counter()
    runtime = end - start
    print(f"Took {runtime} Seconds.")


# If run from the command line
if __name__ == "__main__":

    # Check arguments, if 'xml' then don't need the rest of the input
    argrequired = '--xml' not in sys.argv and '-x' not in sys.argv
    parser = argparse.ArgumentParser(description="Please use this syntax:")
    parser.add_argument("-x", "--xml", help="Optional XML Filename", type=str)
    parser.add_argument("-u", "--username", help="Username", type=str, required=argrequired)
    parser.add_argument("-i", "--ipaddress", help="IP or FQDN of PA/Panorama", type=str, required=argrequired)
    args = parser.parse_args()

    # IF XML, do not connect to PA/Pan
    if args.xml:
        DEBUG = True
        filename = args.xml
        garp_logic("n/a", "n/a", "n/a", "xml", filename)
        sys.exit(0)

    # Gather input
    pa_ip = args.ipaddress
    username = args.username
    password = getpass("Enter Password: ")    

    # Create connection with the Palo Alto as 'obj' to test login success
    try:
        paobj = pa_api.api_lib_pa(pa_ip, username, password, "test")
        del(paobj)
    except:
        print(f"Error connecting to: {pa_ip}\nCheck username/password and network connectivity.")
        sys.exit(0)
    
    # PA or Panorama?
    pa_type = pa_api.get_pa_type()
    
    # Run program
    garp_logic(pa_ip, username, password, pa_type)
