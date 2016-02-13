#!/usr/bin/python

# Copyright 2016 Danko Miocevic. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Author: Danko Miocevic

"""
aws-nats - A script to manage a NATS cluster under AWS using CloudFormation 
"""

import boto3
import requests
import ConfigParser
from subprocess import Popen
import time, sys, getopt

DYNAMO_NAME = ""
SERVERS_TIMEOUT = 30
DELETE_TIMEOUT = 300
PUBLIC_IP = ""
NATS_CONFIG_FILE = 'gnats.conf'
CONFIG_FILE = 'aws-nats.conf'

def get_public_ip():
    """
    Gets the public ip of the actual server from the meta-data service.
    """
    global PUBLIC_IP
    response = requests.get('http://169.254.169.254/latest/meta-data/public-ipv4')
    if response.status_code != 200 :
        print "Error retrieving public ip."
        return
    PUBLIC_IP = response.text

def get_config():
    """
    Reads the configuration file and loads the options.
    """
    # The name of the DynamoDB Table
    global DYNAMO_NAME
    # The max timeout for the servers keepalive to consider
    # a server alive.
    global SERVERS_TIMEOUT
    # The timeout for the servers keepalive to remove
    # them from the table.
    global DELETE_TIMEOUT
    # The config file path
    global CONFIG_FILE

    Config = ConfigParser.ConfigParser()
    try:
        # Here is the configuration file name.
        Config.read(CONFIG_FILE)
        if 'DynamoDB' not in Config.sections():
            print "DynamoDB configuration missing."
            return False
    except:
        print "Cannot open config"
        return False
    
    try:
        DYNAMO_NAME = Config.get('DynamoDB', 'table')
    except:
        print "Cannot read DynamoDB table"
        return False

    try:
        SERVERS_TIMEOUT = int(Config.get('general', 'servers-timeout'))
    except:
        print "Cannot read servers timeout"
        return False

    try:
        DELETE_TIMEOUT = int(Config.get('general', 'delete-timeout'))
    except:
        print "Cannot read delete timeout"
        return False
    
    return True


def get_servers():
    """
    Gets the server list from DynamoDB Table.
    Only the servers that are alive will be selected.
    The servers that have been there too much time 
    without sending keepalives will be deleted.
    """
    print "Create list of active servers."
    timestamp = int(time.time())
    dbclient = boto3.resource('dynamodb')
    table = dbclient.Table(DYNAMO_NAME) 
    response = table.scan()
    items = response['Items']
    results = []
    for item in items:
        if item['ip'] == PUBLIC_IP:
            # Ignore if it is myself.
            continue
        if item['time'] > timestamp - SERVERS_TIMEOUT:
            # The server recently sent a keepalive.
            print "Add %s" % item['ip']
            results.append(item['ip']) 
        if item['time'] < timestamp - DELETE_TIMEOUT:
            # The server has been inactive for too long.
            print "Delete %s" % item['ip']
            table.delete_item(Key={'ip': item['ip']})
    print "Found %d NATS servers." % len(results)
    return results

def generate_nats_cluster(servers):
    """
    Creates a configuration file for the gnatsd.
    Receives a list of active servers to create the
    cluster.
    """
    print "Generating NATS cluster configuration."
    f = open(NATS_CONFIG_FILE, "w")
    f.write("port: 4242      # port to listen for client connections\n")
    f.write("http_port: 8222 # HTTP monitoring port\n")
    f.write("\ncluster {\n")
    f.write("\thost: '0.0.0.0'\n")
    f.write("\tport: 7244\n")
    if len(servers) > 0:
        f.write("\n\troutes = [")
        for server in servers:
            f.write("\t\tnats-route://")
            f.write(server)
            f.write(":7244\n")
        f.write("\t]")
    f.write("}\n")
    f.close()

def touch_status(status):
    """
    Updates the status of the current server and
    the last time it was modified.
    """
    print "Updating status: %s" % status
    timestamp = int(time.time())
    dbclient = boto3.resource('dynamodb')
    table = dbclient.Table(DYNAMO_NAME)
    table.put_item(
        Item={
            'ip': PUBLIC_IP,
            'time': timestamp,
            'status': status
        }
    )

def run_nats():
    """
    Run the gnatsd daemon!
    """
    print "Starting NATS"
    Popen('./gnatsd --pid /tmp/gnats.pid --config gnats.conf', shell=True)
    time.sleep(5)


def check_nats():
    """
    Connect to the internal mini server in the gnatsd 
    and request a list of routes to check if the server
    is still alive.
	"""
    print "Checking NATS status."
    response = requests.get('http://localhost:8222/routez')
    if response.status_code != 200 :
        print "Error connecting with NATS."
        raise Exception('Error connecting with NATS.')

    print response.text


def print_usage():
    """
    Prints how to use the script.
    """
    print 'aws-nats -c <configfile>'


def process_args(argv):
    """
    Process the command line arguments for the script.
    """
    global CONFIG_FILE
    try:
        opts, args = getopt.getopt(argv, "hc:", ["cfile="])
    except:
        print_usage()
        sys.exit(10)

    for opt, arg in opts:
        if opt == '-h':
            print_usage()
            exit(0)
        elif opt in ("-c", "--cfile"):
            CONFIG_FILE=arg

def main(argv):
    process_args(argv)
    get_public_ip()
    if not get_config():
        print "Error reading config."
        sys.exit(1) 

    try: 
        servers = get_servers()
    except:
        print "Cannot access DynamoDB"
        sys.exit(2) 

    try:
        touch_status('starting')
    except:
        print "Cannot change status."
        sys.exit(3) 

    try:
       generate_nats_cluster(servers)
    except:
        print "Cannot generate NATS configuration."
        touch_status('error')
        sys.exit(4) 

    try:
        run_nats()
    except:
        print "Cannot run NATS."
        touch_status('error')
        sys.exit(5)

    while(True):
        try:
            check_nats() 
        except:
            print "NATS is dead!"
            sys.exit(6)

        try:
            touch_status('working')
        except:
            print "Cannot change status."
            sys.exit(7)

        time.sleep(10)



if __name__ == "__main__":
	main(sys.argv[1:])
