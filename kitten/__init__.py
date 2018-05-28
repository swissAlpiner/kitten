#!/usr/bin/env python

from __future__ import unicode_literals

import argparse
import io
import os
import sys
import threading

import boto3
import fabric

__version__ = "0.1.9"

TIMEOUT = 15
CHUNK_SIZE = 100
HELP = {
    "command": "shell command to execute",
    "hosts": "list of IP addresses",
    "i": "private key path",
    "kind": "AWS resource type",
    "local": "path to local file",
    "public": "print public IPs where possible",
    "region": "AWS region",
    "remote": "path to remote file",
    "sudo": "run command via sudo",
    "timeout": "connection timeout in seconds",
    "user": "remote connection user",
    "values": "list of instance IDs or resource names",
}


def red(s):
    return "\033[31m" + s + "\033[0m"


def green(s):
    return "\033[32m" + s + "\033[0m"


def yellow(s):
    return "\033[33m" + s + "\033[0m"


def chunks(l, n):
    for i in range(0, len(l), n):
        yield l[i : i + n]


def instance_ids_to_ips(client, instance_ids):
    filters = [{"Name": "instance-id", "Values": instance_ids}]
    for instance in client.instances.filter(Filters=filters):
        yield {
            "public": instance.public_ip_address,
            "private": instance.private_ip_address,
        }


def asgs_to_instance_ids(client, asg_names):
    asgs = client.describe_auto_scaling_groups(AutoScalingGroupNames=asg_names)
    for asg in asgs["AutoScalingGroups"]:
        for instance in asg["Instances"]:
            yield instance["InstanceId"]


def elbs_to_instance_ids(client, elb_names):
    elbs = client.describe_load_balancers(LoadBalancerNames=elb_names)
    for elb in elbs["LoadBalancerDescriptions"]:
        for instance in elb["Instances"]:
            yield instance["InstanceId"]


def print_ips(client, instance_ids, public, region_name):
    for chunk in chunks(list(instance_ids), CHUNK_SIZE):
        for ip in instance_ids_to_ips(client, chunk):
            print(public and ip["public"] or ip["private"])


def ip(values, kind, public, region_name):
    if kind == "id":
        instance_ids = values
    elif kind == "asg":
        autoscaling = boto3.client("autoscaling", region_name=region_name)
        instance_ids = asgs_to_instance_ids(autoscaling, values)
    elif kind == "elb":
        elb = boto3.client("elb", region_name=region_name)
        instance_ids = elbs_to_instance_ids(elb, values)
    ec2 = boto3.resource("ec2", region_name=region_name)
    print_ips(ec2, instance_ids, public, region_name)


def run(c, command, sudo):
    print("{} run {}".format(yellow(c.host), yellow(command)))
    f = io.StringIO()
    func = c.sudo if sudo else c.run
    func(command, out_stream=f, err_stream=f)
    for line in f.getvalue().splitlines():
        print(yellow(c.host) + " " + line)
    c.close()


def put(c, local, remote):
    print("{} put {} to {}".format(yellow(c.host), yellow(local), yellow(remote)))
    try:
        c.put(local, remote=remote)
    except Exception as e:
        print(yellow(c.host) + " " + red(str(e)))
    else:
        print(yellow(c.host) + " " + green("ok"))
    c.close()


def get(c, remote):
    try:
        os.mkdir(c.host)
    except OSError:
        pass
    local = c.host + "/" + os.path.basename(remote)
    print("{} get {} to {}".format(yellow(c.host), yellow(remote), yellow(local)))
    try:
        c.get(remote, local=local)
    except Exception as e:
        print(yellow(c.host) + " " + red(str(e)))
    else:
        print(yellow(c.host) + " " + green("ok"))
    c.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="tool")

    aws_parser = subparsers.add_parser("ip")
    aws_parser.add_argument("--region", help=HELP["region"])
    aws_parser.add_argument("--public", action="store_true", help=HELP["public"])
    aws_parser.add_argument("kind", choices=("id", "asg", "elb"), help=HELP["kind"])
    aws_parser.add_argument("values", nargs="+", help=HELP["values"])

    ssh_parser = subparsers.add_parser("run")
    ssh_parser.add_argument("-i", help=HELP["i"])
    ssh_parser.add_argument(
        "--timeout", type=int, default=TIMEOUT, help=HELP["timeout"]
    )
    ssh_parser.add_argument("--sudo", action="store_true", help=HELP["sudo"])
    ssh_parser.add_argument("command", help=HELP["command"])
    ssh_parser.add_argument("user", help=HELP["user"])
    ssh_parser.add_argument("hosts", nargs="+", help=HELP["hosts"])

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("-i", help=HELP["i"])
    get_parser.add_argument(
        "--timeout", type=int, default=TIMEOUT, help=HELP["timeout"]
    )
    get_parser.add_argument("remote", help=HELP["remote"])
    get_parser.add_argument("user", help=HELP["user"])
    get_parser.add_argument("hosts", nargs="+", help=HELP["hosts"])

    put_parser = subparsers.add_parser("put")
    put_parser.add_argument("-i", help=HELP["i"])
    put_parser.add_argument(
        "--timeout", type=int, default=TIMEOUT, help=HELP["timeout"]
    )
    put_parser.add_argument("local", help=HELP["local"])
    put_parser.add_argument("remote", help=HELP["remote"])
    put_parser.add_argument("user", help=HELP["user"])
    put_parser.add_argument("hosts", nargs="+", help=HELP["hosts"])

    args = parser.parse_args()
    if not args.tool:
        parser.print_help()
        sys.exit(2)

    return args


def start_threads(targets):
    threads = [threading.Thread(target=target, args=args) for target, args in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main():
    args = parse_args()
    if args.tool == "ip":
        ip(args.values, args.kind, args.public, args.region)
    else:
        cs = [
            fabric.Connection(
                host,
                user=args.user,
                connect_timeout=args.timeout,
                connect_kwargs={"key_filename": args.i},
            )
            for host in args.hosts
        ]
        if args.tool == "run":
            targets = [(run, (c, args.command, args.sudo)) for c in cs]
        elif args.tool == "get":
            targets = [(get, (c, args.remote)) for c in cs]
        elif args.tool == "put":
            targets = [(put, (c, args.local, args.remote)) for c in cs]
        start_threads(targets)


if __name__ == "__main__":
    main()
