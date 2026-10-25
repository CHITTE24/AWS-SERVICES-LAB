"""Experiment: EC2 Security Review. Finds security group rules that allow traffic from the whole internet."""

from experiments.steps import count_or_unknown, run_step  # Shared experiment helpers.
from integrations import ec2  # The service this experiment uses.

# Source ranges that mean "anyone on the internet" (IPv4 and IPv6).
OPEN_TO_WORLD = ("0.0.0.0/0", "::/0")

# Ports that should almost never be open to the whole internet: SSH, RDP, and common databases.
SENSITIVE_PORTS = {22: "SSH", 3389: "RDP", 3306: "MySQL", 5432: "PostgreSQL", 1433: "SQL Server", 27017: "MongoDB"}


def rule_severity(rule):
    """Returns ("HIGH" or "MEDIUM", reason) for a rule that is open to the world."""
    # Protocol "-1" means every protocol and every port.
    if rule["protocol"] == "-1":
        return "HIGH", "All traffic is allowed"

    # Checks whether any sensitive port falls inside the rule's port range.
    for port, port_name in SENSITIVE_PORTS.items():
        if rule["from_port"] is not None and rule["from_port"] <= port <= rule["to_port"]:
            return "HIGH", f"{port_name} (port {port}) is exposed"

    return "MEDIUM", "Port open to the internet. Check that this is intended (for example 80/443 for a website)."


def find_open_rules(groups):
    # Builds one finding row for each rule whose sources include 0.0.0.0/0 or ::/0.
    findings = []
    for group in groups:
        for rule in group["inbound_rules"]:
            # Keeps only the sources that mean "the whole internet" (a list comprehension: a short for-loop).
            open_sources = [source for source in rule["sources"] if source in OPEN_TO_WORLD]
            if not open_sources:
                continue  # This rule is restricted to specific addresses. Skip it.
            severity, reason = rule_severity(rule)
            findings.append({
                "severity": severity,
                "group_id": group["group_id"],
                "group_name": group["group_name"],
                "protocol": rule["protocol"],
                "ports": f"{rule['from_port']}-{rule['to_port']}",
                "reason": reason,
            })
    return findings


def run(region=None):
    """Reviews the security groups in one region. Returns an experiment result dictionary."""
    errors = []
    groups = run_step(errors, "List security groups", ec2.list_security_groups, region)
    instances = run_step(errors, "List instances", ec2.list_instances, region)

    # "groups or []" uses an empty list when the step failed, so the code below still works.
    findings = find_open_rules(groups or [])
    # Counts the findings marked HIGH.
    high_count = len([finding for finding in findings if finding["severity"] == "HIGH"])

    # Instances with a public IP address can be reached from the internet if their security group allows it.
    public_instances = [instance for instance in instances or [] if instance["public_ip"]]

    summary = {
        "Security groups checked": count_or_unknown(groups),
        "Rules open to the internet": len(findings),
        "HIGH severity findings": high_count,
        "Instances with a public IP": len(public_instances),
    }
    notes = ["Fix HIGH findings by limiting the source to your own IP address, "
             "or by using AWS Systems Manager Session Manager instead of opening SSH."]
    return {
        "summary": summary,
        "tables": [{"title": "Findings", "rows": findings},
                   {"title": "Instances with a public IP", "rows": public_instances}],
        "notes": notes,
        "errors": errors,
    }
