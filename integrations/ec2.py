"""Amazon EC2 (virtual servers): read-only instance, security group, and region information."""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def get_name_tag(tags):
    # EC2 stores an instance's display name as a tag whose Key is "Name". Returns "" if there is none.
    for tag in tags or []:
        if tag["Key"] == "Name":
            return tag["Value"]
    return ""


def list_instances(region=None):
    """Lists EC2 instances in one region.

    region: the region to look in. Empty means the default region.
    Returns: a list of dictionaries with each instance's ID, name, type, state, and IP addresses.
    """
    ec2 = get_client("ec2", region)
    # A paginator calls the API repeatedly until every page of results has been read.
    paginator = ec2.get_paginator("describe_instances")

    instances = []
    for page in paginator.paginate():
        # AWS groups instances into "reservations" (instances launched together).
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                instances.append({
                    "instance_id": instance["InstanceId"],
                    "name": get_name_tag(instance.get("Tags")),
                    "type": instance["InstanceType"],
                    "state": instance["State"]["Name"],
                    "public_ip": instance.get("PublicIpAddress", ""),
                    "private_ip": instance.get("PrivateIpAddress", ""),
                })
    return instances


def simplify_rule(rule):
    # Turns one AWS inbound rule into a simple dictionary. Protocol "-1" means "all traffic".
    sources = []
    for ip_range in rule.get("IpRanges", []):
        sources.append(ip_range["CidrIp"])  # IPv4 ranges such as 0.0.0.0/0.
    for ip_range in rule.get("Ipv6Ranges", []):
        sources.append(ip_range["CidrIpv6"])  # IPv6 ranges such as ::/0.
    return {
        "protocol": rule.get("IpProtocol"),
        "from_port": rule.get("FromPort"),
        "to_port": rule.get("ToPort"),
        "sources": sources,
    }


def list_security_groups(region=None):
    """Lists security groups (virtual firewalls) and their inbound rules in one region.

    Returns: a list of dictionaries with group ID, name, VPC ID, and simplified inbound rules.
    """
    ec2 = get_client("ec2", region)
    paginator = ec2.get_paginator("describe_security_groups")

    groups = []
    for page in paginator.paginate():
        for group in page["SecurityGroups"]:
            rules = []
            # IpPermissions holds the inbound (incoming traffic) rules.
            for rule in group.get("IpPermissions", []):
                rules.append(simplify_rule(rule))
            groups.append({
                "group_id": group["GroupId"],
                "group_name": group["GroupName"],
                "vpc_id": group.get("VpcId", ""),
                "inbound_rules": rules,
            })
    return groups


def list_regions():
    """Returns the names of regions enabled for this account, sorted alphabetically."""
    ec2 = get_client("ec2")
    response = ec2.describe_regions()
    names = []
    for region in response["Regions"]:
        names.append(region["RegionName"])
    return sorted(names)
