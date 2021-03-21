#!/usr/bin/env python3
# Copyright 2013-present Barefoot Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Adapted by Robert MacDavid (macdavid@cs.princeton.edu) from scripts found in
# the p4app repository (https://github.com/p4lang/p4app)
#
#
# Further work: Edgar Costa Molero (cedgar@ethz.ch)
# Further work: Fabian Schleiss (fabian.schleiss@alumni.ethz.ch)
# Further work: Jurij Nota (junota@student.ethz.ch)

import os
import argparse
import mininet
from copy import deepcopy
from time import sleep
from ipaddress import ip_interface
from mininet.log import setLogLevel, info, output, debug, warning
from mininet.clean import sh

from p4utils.utils.helper import *
from p4utils.utils.compiler import P4C as DEFAULT_COMPILER
from p4utils.utils.controller import ThriftController as DEFAULT_CONTROLLER
from p4utils.utils.topology import Topology as DEFAULT_TOPODB
from p4utils.mininetlib.node import P4RuntimeSwitch as DEFAULT_SWITCH
from p4utils.mininetlib.node import P4Host as DEFAULT_HOST
from p4utils.mininetlib.topo import AppTopoStrategies as DEFAULT_TOPO
from p4utils.mininetlib.link import TCLink
from p4utils.mininetlib.cli import P4CLI
from p4utils.mininetlib.net import P4Mininet as DEFAULT_NET


class AppRunner(object):
    """
    Class for running P4 applications.

    Attributes:
        log_dir (string): directory for mininet log files.
        pcap_dump (bool): determines if we generate pcap files for interfaces.
        verbosity (string): see https://github.com/mininet/mininet/blob/master/mininet/log.py#L14
        hosts (list): list of mininet host names.
        switches (dict): mininet host names and their associated properties.
        links (list) : list of mininet link properties.
        switch_json (string): json of the compiled p4 example.
        bmv2_exe (string): name or path of the p4 switch binary.
        conf (dict): parsed configuration from conf_file.
        topo (Topo object): the mininet topology instance.
        net (Mininet object): the mininet instance.
    """

    def __init__(self, conf_file,
                 cli_enabled=True,
                 empty_p4=False,
                 log_dir=None,
                 pcap_dir=None,
                 verbosity='info'):
        """
        Initializes some attributes and reads the topology json.

        Args:
            conf_file (string): JSON configuration file according to the following structure.
            verbosity (string): see https://github.com/mininet/mininet/blob/master/mininet/log.py#L14
            conf_file (string): a json file which describes the mininet topology.
            empty_p4 (bool): use an empty program for debugging.
            cli_enabled (bool): enable mininet CLI.
            log_dir (string): directory where to store logs.
            pcap_dir (string): directory where to store pcap files.

        Example of JSON structure of conf_file:
        {
            "program": "<path_to_gobal_p4_program",
            "cli": "<true|false>",
            "pcap_dump": "<true|false>",
            "enable_log": "<true|false>",
            "host_node":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>",
                "options": options_passed_to_init
            },
            "switch_node":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>"
            },
            "compiler_module":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>",
                "options": options_passed_to_init
            },
            "controller_module":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>",
                "options": options_passed_to_init
            },
            "topo_module":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>"
            },
            "topodb_module":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>"
            },
            "mininet_module":
            {
                "file_path": "<path_to_module>",
                "module_name": "<module_file_name>",
                "object_name": "<module_object>"
            },
            "exec_scripts": 
            [
                {
                    "cmd": "<path_to_script>",
                    "reboot_run": "<true|false>"
                },
                {
                    "cmd": "<path_to_another_script>",
                    "reboot_run": "<true|false>"
                }
            ],
            "topology": 
            {
                "assignment_strategy": assignment_strategy,
                "default":
                {
                    <see parse_links and program_hosts>
                }
                "links": 
                [
                    <see parse_links>
                ],
                "hosts": 
                {
                    "h1":{}
                },
                "switches": {
                    <see parse_switch>
                }
            }
        }

        Notice: none of the modules or nodes are mandatory. In case they are not specified,
        default settings will be used.
        """

        # Set verbosity
        self.verbosity = verbosity
        setLogLevel(self.verbosity)

        # Read JSON configuration file
        self.conf_file = conf_file

        mininet.log.info('Reading JSON configuration file...\n')
        debug('Opening file {}\n'.format(self.conf_file))
        if not os.path.isfile(self.conf_file):
            raise FileNotFoundError("{} is not in the directory!".format(os.path.realpath(self.conf_file)))
        self.conf = load_conf(self.conf_file)

        # we can start topologies with no program to test
        # need to improve !!!!!!!!!!!!!!!!!!!!!!!!!
        if empty_p4:
            info('Empty P4 program selected.\n')
            import p4utils
            lib_path = os.path.dirname(p4utils.__file__)
            lib_path += '/../empty_program/empty_program.p4'
            # Set default program to empty program
            self.conf['program'] = lib_path

            # Override custom switch programs with empty program
            for switch, params in self.conf['topology']['switches'].items():
                if params.get('program', False):
                    params['program'] = lib_path

        self.cli_enabled = cli_enabled
        self.pcap_dir = pcap_dir
        self.log_dir = log_dir

        ## Get log settings
        self.log_enabled = self.conf.get("enable_log", False)

        # Ensure that all the needed directories exist and are directories
        if self.log_enabled:
            if not os.path.isdir(self.log_dir):
                if os.path.exists(self.log_dir):
                    raise FileExistsError("'{}' exists and is not a directory!".format(self.log_dir))
                else:
                    debug('Creating directory {} for logs.\n'.format(self.log_dir))
                    os.mkdir(self.log_dir)
        
        os.environ['P4APP_LOGDIR'] = self.log_dir

        ## Get pcap settings
        self.pcap_dump = self.conf.get("pcap_dump", False)

        # Ensure that all the needed directories exist and are directories
        if self.pcap_dump:
            if not os.path.isdir(self.pcap_dir):
                if os.path.exists(self.pcap_dir):
                    raise FileExistsError("'{}' exists and is not a directory!".format(self.pcap_dir))
                else:
                    debug('Creating directory {} for pcap files.\n'.format(self.pcap_dir))
                    os.mkdir(self.pcap_dir)

        """
        Inside self.conf can be present several module configuration objects. These are the possible values:
            - "switch_node" loads the switch node to be used with Mininet (see mininetlib/node.py),
            - "compiler_module" loads the compiler for P4 codes,
            - "host_node" loads the host node to be used with Mininet,
            - "controller_module" loads the controller to program switches from files.
            - "topo_module" loads Mininet topology module
            - "topodb_module" loads the topology database (will be removed soon)
            - "mininet_module" loads the network module
        """
        
        ## Mininet nodes
        # Load default switch node
        self.switch_node = {}
        if self.conf.get('switch_node', None):
            self.switch_node = load_custom_object(self.conf.get('switch_node'))
        else:
            self.switch_node = DEFAULT_SWITCH

        # Load default host node
        self.host_node = {}
        if self.conf.get('host_node', None):
            self.host_node = load_custom_object(self.conf.get('host_node'))
        else:
            self.host_node = DEFAULT_HOST

        ## Modules
        # Load default compiler module
        self.compiler_module = {}
        if self.conf.get('compiler_module', None):
            if self.conf['compiler_module'].get('object_name', None):
                self.compiler_module['module'] = load_custom_object(self.conf.get('compiler_module'))
            else:
                self.compiler_module['module'] = DEFAULT_COMPILER
            # Load default compiler module arguments
            self.compiler_module['kwargs'] = self.conf['compiler_module'].get('options', {})
        else:
            self.compiler_module['module'] = DEFAULT_COMPILER
            self.compiler_module['kwargs'] = {}

        # Load default controller module
        self.controller_module = {}
        # Set default options for the controller
        default_controller_kwargs = {
                                        'log_enabled': True,
                                        'log_dir': self.log_dir
                                    }
        if self.conf.get('controller_module', None):
            if self.conf['controller_module'].get('object_name', None):
                self.controller_module['module'] = load_custom_object(self.conf.get('controller_module'))
            else:
                self.controlle_module['module'] = DEFAULT_CONTROLLER
            # Load default controller module arguments
            self.controller_module['kwargs'] = self.conf['controller_module'].get('options', default_controller_kwargs)
        else:
            self.controller_module['module'] = DEFAULT_CONTROLLER
            self.controller_module['kwargs'] = default_controller_kwargs

        ## Old modules
        if self.conf.get('topo_module', None):
            self.app_topo = load_custom_object(self.conf.get('topo_module'))
        else:
            self.app_topo = DEFAULT_TOPO

        if self.conf.get('topodb_module', None):
            self.app_topodb = load_custom_object(self.conf.get('topodb_module'))
        else:
            self.app_topodb = DEFAULT_TOPODB

        if self.conf.get('mininet_module', None):
            self.app_mininet = load_custom_object(self.conf.get('mininet_module'))
        else:
            self.app_mininet = DEFAULT_NET

        # Clean default switches
        self.switch_node.stop_all()

        ## Load topology 
        topology = self.conf.get('topology', False)
        if not topology:
            raise Exception('Topology to create is not defined in {}'.format(self.conf))
        else:
            # Import topology components
            self.hosts = topology['hosts']
            self.switches = self.parse_switches(topology['switches'])
            self.links = self.parse_links(topology['links'])
            self.assignment_strategy = topology['assignment_strategy']

    def exec_scripts(self):
        """
        Executes the script present in the "exec_scripts" field of self.conf.
        """
        if isinstance(self.conf.get('exec_scripts', None), list):
            for script in self.conf.get('exec_scripts'):
                self.logger("Exec Script: {}".format(script['cmd']))
                run_command(script['cmd'])

    def parse_switches(self, unparsed_switches):
        """
        A switch should have the following structure inside the topology object
        "switches":
        {
            switch_name:
            {
                "program": path_to_p4_program,
                "cpu_port": <true|false>,
                "cli_input": path_to_cli_input_file,
                "switch_node": custom_switch_node,
                "controller_module": custom_controller_module,
                "opts":
                {
                    "sw_bin": switch_binary,
                    "thrift_port": thrift_port,
                    "grpc_port": grpc_port
                }
            },
            ...
        }

        These settings override the default ones. The field "opts" gathers all the mininet
        options used to initialize the Mininet switch class. None of these fields is mandatory.
        """
        switches = {}
        next_thrift_port = 9090
        next_grpc_port = 9559
        # Set general default options
        default = {
                    'program': self.conf['program'],
                    'cpu_port': True,
                    'switch_node': self.switch_node,
                    'controller_module': self.controller_module,
                    'opts':
                    {
                        'pcap_dump': self.pcap_dump,
                        'pcap_dir': self.pcap_dir,
                        'log_enabled': self.log_enabled
                    }
                  }

        for switch, custom_params in unparsed_switches.items():
            params = deepcopy(default)
            # Set switch specific default options
            params['opts']['thrift_port'] = next_thrift_port
            params['opts']['grpc_port'] = next_grpc_port
            # Set controller default options
            params['controller_module']['kwargs']['sw_name'] = switch
            params['controller_module']['kwargs']['thrift_port'] = next_thrift_port
            params['controller_module']['kwargs']['grpc_port'] = next_grpc_port
            # Set non default node type
            if 'switch_node' in custom_params:
                custom_params['switch_node'] = load_custom_object(custom_params['switch_node'])
            # Set non default controller type
            if 'controller_module' in custom_params:
                custom_params['controller_module']['module'] = load_custom_object(custom_params['controller_module'])
            params.update(custom_params)
            # Set switch node
            params['opts']['cls'] = params['switch_node']
            switches[switch] = deepcopy(params)
            # Update switch port numbers
            next_thrift_port = max(next_thrift_port + 1, params['opts']['thrift_port'])
            next_grpc_port = max(next_grpc_port + 1, params['opts']['grpc_port'])
    
        return switches

    def parse_links(self, unparsed_links):
        """
        Load a list of links descriptions of the form 
        "links":
        [
            [
                node1,
                node2, 
                { 
                    "weight": weight,
                    "port1": port1,
                    "port2": port2,
                    "intfName1": intfName1,
                    "intfName2": intfName2,
                    "addr1": addr1,
                    "addr2": addr2,
                    "params1": { parameters_for_interface1 },
                    "params2": { parameters_for_interface2 },
                    "bw": bandwidth,
                    "delay": transmit_delay,
                    "loss": loss,
                    "max_queue_size": max_queue_size
                }
            ],
            ...
        ]
        where the only mandatory fields are node1 and node2 and complete missing with
        default values and store them as self.links.

        For what concernes the Mininet classes used, we have that:
            "weight" is used by Networkx,
            "port*" are used by Mininet.topo and are propagated to link.TCLink.
            "intfName*" are propagated to link.TCLink and used by each Mininet.link.Intf of the link.
            "addr*" are propagated to link.TCLink and used by each Mininet.link.Intf of the link.
            "params*" are propagated to link.TCLink and used by each Mininet.link.Intf of the link.
            "bw", "delay", "loss" and "max_queue_size" are propagated to link.TCLink and used by both Mininet.link.Intf of the link.
        
        "weight", "bw", "delay", "loss", "max_queue_size" default value can be set by
        putting inside "topology" the following object:
        "default":
        {
            "weight": weight,
            "bw": bandwidth,
            "delay": transmit_delay,
            "loss": loss,
            "max_queue_size": max_queue_size
        }

        Args:
            uparsed_links (array): unparsed links from topology json

        Returns:
            array of parsed link dictionaries
        """

        links = []

        default = self.conf['topology'].get('default', {})
        default.setdefault('weight', 1)
        default.setdefault('bw', None)
        default.setdefault('delay', None)
        default.setdefault('loss', None)
        default.setdefault('max_queue_size', None)

        for link in unparsed_links:
            node1 = link[0]
            node2 = link[1]
            opts = default.copy()
            # If attributes are present for that link
            if len(link) > 2:
                opts.update(link[2])
            # Hosts are not allowed to connect to another host.
            if node1 in self.hosts:
                assert node2 not in self.hosts, 'Hosts should be connected to switches: {} <-> {} link not possible'.format(node1, node2)
            links.append([link[0],link[1],opts])

        return links

    def compile_p4(self):
        """
        Compile all the P4 files provided by the configuration file.

        Side effects:
            - The path of the compiled P4 JSON file is added to each switch
              in the field 'opts' under the name 'json_path'.
            - The dict self.compilers contains all the compilers object used
              (one per different P4 file).
        """
        info('Compiling P4 programs...\n')
        self.compilers = []
        for switch, params in self.switches.items():
            if not is_compiled(os.path.realpath(params['program']), self.compilers):
                compiler = self.compiler_module['module'](p4_filepath=params['program'],
                                                          **self.compiler_module['kwargs'])
                compiler.compile()
                self.compilers.append(compiler)
            else:
                compiler = get_by_attr('p4_filepath', os.path.realpath(params['program']), self.compilers)
            params['opts']['json_path'] = compiler.get_json_out()
            try:
                params['opts']['p4rt_path'] = compiler.get_p4rt_out()
            except P4InfoDisabled:
                pass
            self.switches[switch] = deepcopy(params)

    def create_network(self):
        """
        Create the mininet network object, and store it as self.net.

        Side effects:
            - Mininet topology instance stored as self.topo
            - Mininet instance stored as self.net
        """
        debug('Generating topology...\n')
        # Generate topology
        self.topo = self.app_topo(hosts = self.hosts, 
                                  switches = self.switches,
                                  links = self.links,
                                  assignment_strategy = self.assignment_strategy)

        # Start P4 Mininet
        debug('Starting network...\n')
        self.net = self.app_mininet(topo=self.topo,
                                    link=TCLink,
                                    host=self.host_node,
                                    controller=None)

    def program_hosts(self):
        """
        Adds static ARP entries and default routes to each mininet host.

        Assumes:
            A mininet instance is stored as self.net and self.net.start() has been called.
            A default field is found in "topology" containing (possibly) these fields.
            "default":
            {
                "auto_arp_tables": <true|false>,
                "auto_gw_arp": <true|false>
            }
        """
        default = self.conf['topology'].get('default', {})
        auto_arp_tables = default.get('auto_arp_tables', True)
        auto_gw_arp = default.get('auto_gw_arp', True)

        for host_name in self.topo.hosts():
            h = self.net.get(host_name)

            # Ensure each host's interface name is unique, or else
            # mininet cannot shutdown gracefully
            h_iface = list(h.intfs.values())[0]

            # if there is gateway assigned
            if auto_gw_arp:
                if 'defaultRoute' in h.params:
                    link = h_iface.link
                    sw_iface = link.intf1 if link.intf1 != h_iface else link.intf2
                    gw_ip = h.params['defaultRoute'].split()[-1]
                    h.cmd('arp -i %s -s %s %s' % (h_iface.name, gw_ip, sw_iface.mac))

            if auto_arp_tables:
                # set arp rules for all the hosts in the same subnet
                host_address = ip_interface('{}/{}'.format(h.IP(), self.topo.hosts_info[host_name]["mask"]))
                for hosts_same_subnet in self.topo.hosts():
                    if hosts_same_subnet == host_name:
                        continue

                    #check if same subnet
                    other_host_address = ip_interface(str("%s/%d" % (self.topo.hosts_info[hosts_same_subnet]['ip'],
                                                                            self.topo.hosts_info[hosts_same_subnet]["mask"])))

                    if host_address.network.compressed == other_host_address.network.compressed:
                        h.cmd('arp -i %s -s %s %s' % (h_iface.name, self.topo.hosts_info[hosts_same_subnet]['ip'],
                                                            self.topo.hosts_info[hosts_same_subnet]['mac']))

            # if the host is configured to use dhcp
            auto_ip = self.hosts[host_name].get('auto', False)
            if auto_ip:
                h.cmd('dhclient -r {}'.format(h_iface.name))
                h.cmd('dhclient {} &'.format(h_iface.name))

            # run startup commands (this commands must be non blocking)
            commands = self.hosts[host_name].get('commands', [])
            for command in commands:
                h.cmd(command)

    def program_switches(self):
        """
        If any command files were provided for the switches, this method will start up the
        CLI on each switch and use the contents of the command files as input.

        Assumes:
            A mininet instance is stored as self.net and self.net.start() has been called.
        """
        self.controllers = []
        for params in self.switches.values():
            controller = params['controller_module']['module'](conf_path=params.get('cli_input', None),
                                                               **params['controller_module']['kwargs'])
            if 'cli_input' in params:
                controller.configure()
            self.controllers.append(controller)

    def save_topology(self):
        """Saves mininet topology to database."""
        info("Saving mininet topology to database.\n")
        self.app_topodb(net=self.net).save("./topology.db")
    
    def do_net_cli(self):
        """
        Starts up the mininet CLI and prints some helpful output.

        Assumes:
            A mininet instance is stored as self.net and self.net.start() has been called.
        """
        for switch in self.net.switches:
            if self.topo.isP4Switch(switch.name):
                switch.describe()
        for host in self.net.hosts:
            host.describe()
        info("Starting mininet CLI...\n")
        # Generate a message that will be printed by the Mininet CLI to make
        # interacting with the simple switch a little easier.
        print('')
        print('======================================================================')
        print('Welcome to the P4 Utils Mininet CLI!')
        print('======================================================================')
        print('Your P4 program is installed into the BMV2 software switch')
        print('and your initial configuration is loaded. You can interact')
        print('with the network using the mininet CLI below.')
        print('')
        print('To inspect or change the switch configuration, connect to')
        print('its CLI from your host operating system using this command:')
        print('  {} --thrift-port <switch thrift port>'.format(DEFAULT_CONTROLLER.cli_bin))
        print('')
        print('To view a switch log, run this command from your host OS:')
        print('  tail -f {}/<switchname>.log'.format(self.log_dir))
        print('')
        print('To view the switch output pcap, check the pcap files in \n {}:'.format(self.pcap_dir))
        print(' for example run:  sudo tcpdump -xxx -r s1-eth1.pcap')
        print('')

        # Start CLI
        P4CLI(mininet=self.net, controllers=self.controllers, compilers=self.compilers)

    def run_app(self):
        """
        Sets up the mininet instance, programs the switches, and starts the mininet CLI.
        This is the main method to run after initializing the object.
        """
        # Compile P4 programs
        self.compile_p4()
        # Initialize Mininet with the topology specified by the configuration
        self.create_network()
        # Start Mininet
        self.net.start()
        sleep(1)

        # Some programming that must happen after the network has started
        self.program_hosts()
        self.program_switches()

        # Save mininet topology to a database
        #self.save_topology()
        sleep(1)

        # Execute configuration scripts on the nodes
        self.exec_scripts()

        # Start up the mininet CLI
        if self.cli_enabled or (self.conf.get('cli', False)):
            self.do_net_cli()
            # Stop right after the CLI is exited
            self.net.stop()


def get_args():
    cwd = os.getcwd()
    default_log = os.path.join(cwd, 'log')
    default_pcap = os.path.join(cwd, 'pcap')

    parser = argparse.ArgumentParser()
    parser.add_argument('--config', help='Path to configuration',
                        type=str, required=False, default='./p4app.json')
    parser.add_argument('--log-dir', type=str, required=False, default=default_log)
    parser.add_argument('--pcap-dir', help='Generate pcap files for interfaces.',
                        action='store_true', required=False, default=default_pcap)
    parser.add_argument('--cli', help='Run mininet CLI.',
                        action='store_true', required=False, default=True)
    parser.add_argument('--verbosity', help='Set messages verbosity.',
                        action='store_true', required=False, default='info')
    parser.add_argument('--clean', help='Cleans previous log files',
                        action='store_true', required=False, default=False)
    parser.add_argument('--clean-dir', help='Cleans previous log files and closes',
                        action='store_true', required=False, default=False)
    parser.add_argument('--empty-p4', help='Runs the topology with an empty p4 program that does nothing',
                    action='store_true', required=False, default=False)              

    return parser.parse_args()


def main():

    args = get_args()

    setLogLevel('info')

    # clean
    cleanup()

    # remove cli logs
    sh('find -type f -regex ".*cli_output.*" | xargs rm')

    if args.clean or args.clean_dir:
        # removes first level pcap and log dirs
        sh("rm -rf %s" % args.pcap_dir)
        sh("rm -rf %s" % args.log_dir)
        # tries to recursively remove all pcap and log dirs if they are named 'log' and 'pcap'
        sh('find -type d -regex ".*pcap" | xargs rm -rf')
        sh('find -type d -regex ".*log" | xargs rm -rf')
        # removes topologies files
        sh('find -type f -regex ".*db" | xargs rm')
        # remove compiler outputs
        sh('find -type f -regex ".*\(p4i\|p4rt\)" | xargs rm')

        # remove all the jsons that come from a p4
        out = sh('find -type f -regex ".*p4"')
        p4_files = [x.split("/")[-1].strip() for x in out.split("\n") if x]
        for p4_file in p4_files:
            tmp = p4_file.replace("p4", "json")
            reg_str = ".*{}".format(tmp)
            sh('find -type f -regex {} | xargs rm -f'.format(reg_str))

        if args.clean_dir:
            return

    app = AppRunner(args.config,
                    cli_enabled=args.cli,
                    empty_p4=args.empty_p4,
                    log_dir=args.log_dir,
                    pcap_dir=args.pcap_dir,
                    verbosity=args.verbosity)                  
    app.run_app()


if __name__ == '__main__':
    main()