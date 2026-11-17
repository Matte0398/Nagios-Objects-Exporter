#!/usr/bin/python3

#######################################################################
## Description: Export Nagios host and service monitoring inventory
##
## Author: Matteo Z.
#######################################################################

import argparse
import csv
import fnmatch
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
from typing import Dict, List, Match, Optional, Set, Tuple, Union

LOGGER = logging.getLogger("ngs_objects_exporter")
DEFAULT_CACHE = Path("/usr/local/nagios/var/objects.cache")
DEFAULT_PROFILES = Path(__file__).resolve().with_name("plugin_profiles.json")
FIELDS = (
    "HOST_NAME", "HOST_ADDRESS", "CUSTOMER", "SYSTEM_TYPE", "SYSTEM_VENDOR",
    "SERVICE_NAME", "OBJECT_TYPE",
    "CHECK_COMMAND", "PLUGIN", "REMOTE_COMMAND", "MONITORING_METHOD",
    "DESCRIPTION", "WARNING", "CRITICAL", "THRESHOLD_STATUS",
    "HOST_GROUPS", "SERVICE_GROUPS", "CHECK_INTERVAL", "RETRY_INTERVAL",
    "MAX_CHECK_ATTEMPTS", "CHECK_PERIOD", "NOTIFICATION_INTERVAL",
    "NOTIFICATIONS_ENABLED", "ACTIVE_CHECKS_ENABLED", "NOTES",
)
MISSING_FIELDS = ("SECTION", "NAME", "CHECKS", "HOSTS", "COMMANDS", "EXAMPLES")
SHELL_SUBSTITUTION = re.compile(r"`|\$\(|[\r\n]")
SENSITIVE_MACRO = re.compile(
    r"password|passwd|passphrase|secret|token|community|credential|private.?key|api.?key",
    re.IGNORECASE,
)
THRESHOLD_VALUE = re.compile(r"^(?:@?(?:~|:|[+-]?(?:\d|\.\d)))\S*$")


def read_objects(path: Path) -> List[Tuple[str, Dict[str, str]]]:
    """Read effective Nagios objects from objects.cache."""
    objects = []
    object_type = ""
    attributes = {}

    with path.open(encoding="utf-8-sig") as source:
        for line_number, raw_line in enumerate(source, 1):
            line = raw_line.strip()

            if not line or line.startswith(("#", ";")):
                continue

            opening = re.fullmatch(r"define\s+(\w+)\s*\{", line)

            if opening:
                if object_type:
                    raise ValueError(f"Nested object at line {line_number}")

                object_type = opening.group(1)
                attributes = {}
            elif line == "}":
                if not object_type:
                    raise ValueError(f"Unexpected closing brace at line {line_number}")

                objects.append((object_type, attributes))
                object_type = ""
            elif object_type:
                parts = line.split(None, 1)
                attributes[parts[0].upper() if parts[0].startswith("_") else parts[0]] = (
                    parts[1] if len(parts) == 2 else ""
                )
            else:
                raise ValueError(f"Unexpected content at line {line_number}")

    if object_type:
        raise ValueError("Unclosed Nagios object")

    return objects


def members(value: str) -> List[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def index_objects(objects: List[Tuple[str, Dict[str, str]]]) -> Tuple[dict, dict, dict, dict]:
    """Index hosts, commands, and memberships for efficient lookups."""
    hosts, commands, host_groups, service_groups = {}, {}, {}, {}
    nested_host_groups, nested_service_groups = {}, {}

    for object_type, attributes in objects:
        if object_type in ("host", "command"):
            name_field = "host_name" if object_type == "host" else "command_name"
            name = attributes.get(name_field, "")
            target = hosts if object_type == "host" else commands

            if not name or name in target:
                raise ValueError(f"Missing or duplicated {name_field}: {name or '(empty)'}")

            target[name] = attributes

            if object_type == "host":
                # Preserve memberships already collected from hostgroup definitions.
                host_groups.setdefault(name, []).extend(members(attributes.get("hostgroups", "")))
        elif object_type == "service":
            for host_name in members(attributes.get("host_name", "")):
                key = (host_name, attributes.get("service_description", ""))
                service_groups.setdefault(key, []).extend(members(attributes.get("servicegroups", "")))
        elif object_type == "hostgroup":
            group_name = attributes.get("hostgroup_name", "")

            for host_name in members(attributes.get("members", "")):
                host_groups.setdefault(host_name, []).append(group_name)

            nested_host_groups[group_name] = members(attributes.get("hostgroup_members", ""))
        elif object_type == "servicegroup":
            group_name = attributes.get("servicegroup_name", "")
            pairs = members(attributes.get("members", ""))

            for index in range(0, len(pairs) - 1, 2):
                key = (pairs[index], pairs[index + 1])
                service_groups.setdefault(key, []).append(group_name)

            nested_service_groups[group_name] = members(attributes.get("servicegroup_members", ""))

    # Resolve nested groups without recursion, including cyclic references safely.
    for memberships, nesting in ((host_groups, nested_host_groups), (service_groups, nested_service_groups)):
        changed = True

        while changed:
            changed = False

            for groups in memberships.values():
                for parent, children in nesting.items():
                    if parent not in groups and any(child in groups for child in children):
                        groups.append(parent)
                        changed = True

    return hosts, commands, host_groups, service_groups


def read_profiles(path: Path) -> dict:
    """Read the plugin catalog, retaining its existing section-based format."""
    def unique_keys(pairs: List[Tuple[str, object]]) -> dict:
        result = {}

        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate plugin catalog field: {key}")

            result[key] = value

        return result

    catalog = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)

    if not isinstance(catalog, dict):
        raise ValueError("Plugin catalog must be a JSON object")

    for section, profiles in catalog.items():
        if section not in ("local_plugins", "check_nrpe", "check_by_ssh") or not isinstance(profiles, dict):
            raise ValueError(f"Invalid plugin catalog section: {section}")

        for name, profile in profiles.items():
            label = f"{section}.{name}"

            if not isinstance(profile, dict):
                raise ValueError(f"Invalid plugin profile: {label}")
            
            if not all(isinstance(profile.get(field), str) and profile[field].strip() for field in ("description", "method")):
                raise ValueError(f"Missing method or description: {label}")
            
            if not isinstance(profile.get("sensitive_options", []), list) or any(not isinstance(option, str) for option in profile.get("sensitive_options", [])):
                raise ValueError(f"Invalid sensitive options: {label}")
            
            if not isinstance(profile.get("modes", {}), dict) or any(not isinstance(value, str) for value in profile.get("modes", {}).values()):
                raise ValueError(f"Invalid modes: {label}")
            
            if any(field in profile and not isinstance(profile[field], str) for field in ("warning", "critical", "warning_option", "critical_option", "default_warning", "default_critical")):
                raise ValueError(f"Invalid threshold options: {label}")
            
            if "thresholds_supported" in profile and not isinstance(profile["thresholds_supported"], bool):
                raise ValueError(f"Invalid thresholds_supported: {label}")
            
            mode_rules = profile.get("thresholds_by_mode", {})
            
            if not isinstance(mode_rules, dict) or any(
                not isinstance(rules, dict) or any(key not in ("warning", "critical", "default_warning", "default_critical") or not isinstance(value, str) for key, value in rules.items()
                ) for rules in mode_rules.values()
            ):
                raise ValueError(f"Invalid mode threshold rules: {label}")

    return catalog


def lookup_profile(catalog: dict, section: str, name: str) -> Optional[dict]:
    profiles = catalog.get(section, {})

    if name in profiles:
        return profiles[name]

    patterns = [key for key in profiles if fnmatch.fnmatchcase(name, key)]

    if patterns:
        chosen = max(patterns, key=lambda item: len(item.replace("*", "").replace("?", "")))

        return profiles[chosen]

    return None


def split_check_command(value: str) -> List[str]:
    """Split a Nagios command and its arguments, respecting escaped exclamations."""
    parts = [""]
    index = 0

    while index < len(value):
        if value[index] == "\\" and value[index + 1:index + 2] in ("!", "\\"):
            index += 1
            parts[-1] += value[index]
        elif value[index] == "!":
            parts.append("")
        else:
            parts[-1] += value[index]

        index += 1

    return parts


def expand_command(template: str, arguments: List[str], host: dict, service: dict) -> str:
    """Expand only macros available in the effective Nagios configuration."""
    macros = {
        "HOSTNAME": host.get("host_name", ""),
        "HOSTADDRESS": host.get("address", ""),
        "HOSTALIAS": host.get("alias", ""),
        "SERVICEDESC": service.get("service_description", ""),
    }

    for prefix, attributes in (("_HOST", host), ("_SERVICE", service)):
        for name, value in attributes.items():
            if name.startswith("_"):
                macros[prefix + name[1:].upper()] = ("[REDACTED]" if SENSITIVE_MACRO.search(name) else value)

    def replace(match: Match[str]) -> str:
        return macros.get(match.group(1), match.group(0))

    for position, argument in enumerate(arguments, 1):
        macros[f"ARG{position}"] = re.sub(r"\$([^$\s]+)\$", replace, argument)

    return re.sub(r"\$([^$\s]+)\$", replace, template)


def command_tokens(command: str) -> List[str]:
    """Read simple command lines; never run or interpret shell expressions."""
    if SHELL_SUBSTITUTION.search(command):
        return []

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return []

    if any(re.fullmatch(r"[;&|<>()]+", token) for token in tokens):
        return []

    return tokens


def plugin_name(tokens: List[str]) -> str:
    if not tokens:
        return ""

    index = 0

    if Path(tokens[index]).name == "env" and len(tokens) > 1:
        index += 1

    if re.fullmatch(r"(?:python[\d.]*|perl|bash|sh)", Path(tokens[index]).name):
        index += 1

    if index >= len(tokens) or tokens[index].startswith("-"):
        return ""

    return Path(tokens[index]).name


def is_option(value: str) -> bool:
    return value.startswith("-") and len(value) > 1 and not re.match(r"^-\d", value)


def option_values(tokens: List[str], option: str, rest: bool = False) -> List[str]:
    """Read an option's values, including -w80 and --warning=80 forms."""
    for index, token in enumerate(tokens[1:], 1):
        attached = ""

        if token == option:
            pass
        elif token.startswith(option + "="):
            attached = token[len(option) + 1:]
        elif len(option) == 2 and token.startswith(option) and len(token) > 2:
            attached = token[2:]
        else:
            continue

        values = [attached] if attached else []

        for following in tokens[index + 1:]:
            if not rest and is_option(following):
                break

            values.append(following)

        return values

    return []


def sensitive_options(profile: Optional[dict]) -> Set[str]:
    if profile is None:
        return set()

    return {option.split(":", 1)[0] for option in profile.get("sensitive_options", [])}


def threshold_candidates(tokens: List[str], level: str, profile: Optional[dict]) -> List[str]:
    """Extract numerical threshold expressions, ignoring credential options."""
    basic = ("-w", "--warning", "--warn") if level == "warning" else ("-c", "--critical", "--crit")
    explicit = (profile or {}).get(f"{level}_option")
    choices = list(basic) + ([explicit] if isinstance(explicit, str) else [])
    excluded = sensitive_options(profile)
    results = []

    for token in tokens[1:]:
        if token.startswith("--"):
            name = token.split("=", 1)[0]

            if ("warn" if level == "warning" else "crit") in name.lower():
                choices.append(name)

    for option in dict.fromkeys(choices):
        if option in excluded:
            continue

        values = option_values(tokens, option)

        if not values:
            continue

        value = values[0]

        if THRESHOLD_VALUE.match(value) and re.search(r"\d", value):
            results.append((option, value))

    if len(results) == 1:
        return [results[0][1]]

    return [f"{option}={value}" for option, value in results]


def declared_threshold(profile: Optional[dict], level: str, tokens: List[str], remote_arguments: Optional[List[str]] = None, mode: str = "") -> Optional[str]:
    """Read a catalog rule such as -a:1 or a mode-specific -l:2."""
    if not profile:
        return None

    rule = profile.get("thresholds_by_mode", {}).get(mode, {}).get(level, profile.get(level))

    if not rule:
        return None

    specification = rule
    match = re.fullmatch(r"(-{1,2}[\w-]+)(?::(\d+))?", specification)

    if not match:
        raise ValueError(f"Invalid {level} specification in plugin catalog")

    option, position = match.group(1), int(match.group(2) or 1)
    explicit_position = match.group(2) is not None

    if option in ("-a", "--args") and remote_arguments is not None:
        values = remote_arguments
    else:
        values = option_values(tokens, option)

    if len(values) == 1 and "," in values[0] and explicit_position:
        values = members(values[0])

    if len(values) >= position and THRESHOLD_VALUE.match(values[position - 1]):
        return values[position - 1]

    return None


def selected_modes(profile: Optional[dict], tokens: List[str]) -> List[Tuple[str, str]]:
    """Collect modes in command order, retaining comma-separated selectors."""
    if not profile:
        return []

    modes = profile.get("modes", {})
    selections = []

    for token in tokens[1:]:
        value = token.split("=", 1)[-1] if token.startswith("-") and "=" in token else token
        current_mode = ""
        selectors = []

        for item in value.split(",") + [""]:
            mode_name, separator, selector = item.partition(":")

            if current_mode and (mode_name in modes or not item):
                selection = (current_mode, ",".join(selectors))

                if selection not in selections:
                    selections.append(selection)

                current_mode = ""
                selectors = []

            if mode_name in modes:
                current_mode = mode_name
                selectors = [selector] if separator else []
            elif current_mode and selectors:
                selectors.append(item)

    return selections


def describe(profile: Optional[dict], tokens: List[str]) -> str:
    if not profile:
        return "N/A"

    descriptions = [
        profile["modes"][mode] + (f" ({selectors})" if selectors else "")
        for mode, selectors in selected_modes(profile, tokens)
    ]

    return "\n".join(descriptions) if descriptions else profile["description"]


def check_details(check: dict, host: dict, commands: dict, catalog: dict) -> Tuple[dict, List[Tuple[str, str]]]:
    """Resolve a check to its plugin, description, method, and available thresholds."""
    notes = []
    missing = []
    parts = split_check_command(check.get("check_command", ""))
    command_name = parts[0]
    template = commands.get(command_name, {}).get("command_line", "")
    tokens = command_tokens(expand_command(template, parts[1:], host, check)) if template else []
    name = plugin_name(tokens)
    profile = lookup_profile(catalog, "local_plugins", name)
    remote_name = ""
    target_profile = profile
    target_tokens = tokens
    remote_arguments = None
    wrapper = ""

    if not template:
        notes.append("Check command missing from objects.cache")
    elif not tokens:
        notes.append("Command uses unsupported shell syntax or unresolved quoting")
    elif not name:
        notes.append("Plugin name could not be identified")
    elif not profile:
        missing.append(("local_plugins", name))

    if name == "check_nrpe":
        wrapper = "NRPE"
        remote_name = next(iter(option_values(tokens, "-c") or option_values(tokens, "--command")), "")
        remote_arguments = option_values(tokens, "-a", rest=True)

        if not remote_arguments:
            remote_arguments = option_values(tokens, "--args", rest=True)

        target_profile = lookup_profile(catalog, "check_nrpe", remote_name)

        if remote_name and not target_profile:
            missing.append(("check_nrpe", remote_name))

        if not remote_name:
            notes.append("NRPE command could not be identified")

        target_tokens = [remote_name]

        for argument in remote_arguments:
            target_tokens.extend(command_tokens(argument) or [argument])
    elif name == "check_by_ssh":
        wrapper = "SSH"
        remote_command = next(iter(option_values(tokens, "-C") or option_values(tokens, "--command")), "")
        target_tokens = command_tokens(remote_command) if remote_command else []
        remote_name = plugin_name(target_tokens)
        target_profile = (lookup_profile(catalog, "check_by_ssh", remote_name) or lookup_profile(catalog, "local_plugins", remote_name))

        if remote_name and not target_profile:
            missing.append(("check_by_ssh", remote_name))

        if not remote_name:
            notes.append("SSH command could not be analysed")

    if target_profile:
        description = describe(target_profile, target_tokens)
        method = target_profile["method"] + (f" via {wrapper}" if wrapper else "")
    else:
        description = "N/A"
        method = wrapper or "N/A"

    if name == "check_nrpe":
        candidates = target_tokens
    else:
        candidates = target_tokens if wrapper else tokens

    warning = threshold_candidates(candidates, "warning", target_profile)
    critical = threshold_candidates(candidates, "critical", target_profile)

    used_defaults = []

    selections = selected_modes(target_profile, target_tokens)
    mode_names = list(dict.fromkeys(mode for mode, selectors in selections)) or [""]
    has_multiple_modes = len(mode_names) > 1
    resolved_thresholds = {"warning": [], "critical": []}
    has_missing_thresholds = False

    for mode in mode_names:
        special_rules = (target_profile or {}).get("thresholds_by_mode", {}).get(mode, {})

        for level, candidates_found in (("warning", warning), ("critical", critical)):
            if check.get(f"_{level.upper()}"):
                continue

            values = list(candidates_found)
            profile_rules = target_profile or {}
            rule = special_rules.get(level, profile_rules.get(level))

            if rule == "":
                continue

            declared = declared_threshold(target_profile, level, candidates, remote_arguments, mode)
            default = special_rules.get(f"default_{level}", profile_rules.get(f"default_{level}", ""))

            if rule is not None and declared is None:
                has_missing_thresholds = True
                notes.append(f"{level.title()} threshold declared but not found" + (f" for mode {mode}" if mode else ""))
                values = []

            if declared is not None:
                values = [declared]
            elif not values and default:
                values = [default]
                used_defaults.append(level)

            if not values:
                continue

            threshold_text = "; ".join(values)
            has_mode_rule = level in special_rules or f"default_{level}" in special_rules

            if has_multiple_modes and has_mode_rule:
                threshold_text = f"{mode}: {threshold_text}"

            if threshold_text not in resolved_thresholds[level]:
                resolved_thresholds[level].append(threshold_text)

    if used_defaults:
        notes.append("Plugin default used for " + " and ".join(dict.fromkeys(used_defaults)))

    warning_value = check.get("_WARNING") or "\n".join(resolved_thresholds["warning"]) or None
    critical_value = check.get("_CRITICAL") or "\n".join(resolved_thresholds["critical"]) or None
    threshold_options = ("-w", "-c", "--warning", "--critical", "--warn", "--crit")
    unresolved = any("$" in str(value) for value in (warning_value, critical_value))
    unresolved |= any("$" in value for option in threshold_options for value in option_values(candidates, option)[:1])

    if unresolved:
        notes.append("Threshold contains an unresolved Nagios macro")

    if (unresolved or not name or (wrapper and not target_profile) or has_missing_thresholds):
        threshold_status = "UNDETERMINED"
    elif used_defaults:
        threshold_status = "DEFAULT"
    elif warning_value or critical_value:
        threshold_status = "CONFIGURED"
    elif target_profile and target_profile.get("thresholds_supported") is False:
        threshold_status = "NOT_APPLICABLE"
    elif wrapper == "NRPE":
        threshold_status = "UNDETERMINED"
    elif not target_profile:
        threshold_status = "UNDETERMINED"
    else:
        threshold_status = "NO_EXPLICIT_THRESHOLDS"

    if wrapper == "NRPE" and not warning_value and not critical_value:
        notes.append("NRPE thresholds may be defined on the remote host")

    for section, value in missing:
        notes.append(f"Not found in catalog: {section}/{value}")

    return {
        "CHECK_COMMAND": command_name,
        "PLUGIN": name,
        "REMOTE_COMMAND": remote_name,
        "MONITORING_METHOD": check.get("_MONITORING_METHOD") or host.get("_MONITORING_METHOD") or method,
        "DESCRIPTION": check.get("_CHECK_DESCRIPTION") or check.get("notes") or description,
        "WARNING": warning_value,
        "CRITICAL": critical_value,
        "THRESHOLD_STATUS": threshold_status,
        "NOTES": " | ".join(dict.fromkeys(notes)),
    }, missing


def optional_number(value: str) -> Optional[Union[int, float]]:
    if not value:
        return None

    try:
        number = float(value)
    except ValueError:
        return None

    if not math.isfinite(number):
        return None

    return int(number) if number.is_integer() else number


def optional_bool(value: str) -> Optional[bool]:
    return {"1": True, "0": False}.get(value)


def matches(name: str, pattern: str) -> bool:
    """Whole name, case-insensitive; * and ? are wildcards; empty matches all."""
    return not pattern or fnmatch.fnmatchcase(name.casefold(), pattern.casefold())


def export_inventory(objects: list, catalog: dict, host_filter: str = "", service_filter: str = "") -> Tuple[List[dict], List[dict]]:
    """Produce one row per host check or host/service pairing."""
    hosts, commands, host_groups, service_groups = index_objects(objects)
    rows = []
    missing = {}

    for object_type, attributes in objects:
        if object_type not in ("host", "service"):
            continue

        service_name = attributes.get("service_description", "") if object_type == "service" else ""

        if service_filter and not matches(service_name, service_filter):
            continue

        for host_name in members(attributes.get("host_name", "")):
            if not matches(host_name, host_filter):
                continue

            host = hosts.get(host_name)

            if host is None:
                LOGGER.warning("Service references undefined host: %s", host_name)
                host = {"host_name": host_name}

            details, absent = check_details(attributes, host, commands, catalog)
            groups = sorted(set(members(host.get("hostgroups", "")) + host_groups.get(host_name, [])) - {""})
            service_memberships = []

            if object_type == "service":
                service_memberships = (members(attributes.get("servicegroups", "")) + service_groups.get((host_name, service_name), []))

            row = {
                "HOST_NAME": host_name,
                "HOST_ADDRESS": host.get("address", ""),
                "CUSTOMER": host.get("_CUSTOMER", ""),
                "SYSTEM_TYPE": host.get("_TYPE", ""),
                "SYSTEM_VENDOR": host.get("_VENDOR", ""),
                "SERVICE_NAME": service_name,
                "OBJECT_TYPE": object_type.upper(),
                **details,
                "HOST_GROUPS": groups,
                "SERVICE_GROUPS": sorted(set(service_memberships) - {""}),
                "CHECK_INTERVAL": optional_number(attributes.get("check_interval", "")),
                "RETRY_INTERVAL": optional_number(attributes.get("retry_interval", "")),
                "MAX_CHECK_ATTEMPTS": optional_number(attributes.get("max_check_attempts", "")),
                "CHECK_PERIOD": attributes.get("check_period", ""),
                "NOTIFICATION_INTERVAL": optional_number(attributes.get("notification_interval", "")),
                "NOTIFICATIONS_ENABLED": optional_bool(attributes.get("notifications_enabled", "")),
                "ACTIVE_CHECKS_ENABLED": optional_bool(attributes.get("active_checks_enabled", "")),
            }
            rows.append({field: row.get(field) for field in FIELDS})

            for section, name in set(absent):
                entry = missing.setdefault((section, name), {
                    "SECTION": section, "NAME": name, "CHECKS": 0,
                    "HOSTS": set(), "COMMANDS": set(), "EXAMPLES": [],
                })
                entry["CHECKS"] += 1
                entry["HOSTS"].add(host_name)
                entry["COMMANDS"].add(details["CHECK_COMMAND"])

                if len(entry["EXAMPLES"]) < 3:
                    entry["EXAMPLES"].append(f"{host_name} / {service_name or '(host check)'}")

    rows.sort(key=lambda row: (row["HOST_NAME"].casefold(), row["OBJECT_TYPE"] != "HOST", row["SERVICE_NAME"].casefold()))
    missing_rows = [
        dict(entry, HOSTS=len(entry["HOSTS"]), COMMANDS=sorted(entry["COMMANDS"]))
        for entry in sorted(missing.values(), key=lambda item: (-item["CHECKS"], item["SECTION"], item["NAME"].casefold()))
    ]

    return rows, missing_rows


def missing_text(rows: List[dict], catalog: Path, total_checks: int) -> str:
    """Readable --missing report, with entries ready to complete."""
    if not rows:
        return (f"OK: all plugins and remote commands used by {total_checks} checks are in {catalog.name}\n")

    affected = sum(row["CHECKS"] for row in rows)
    width = max(len(row["NAME"]) for row in rows) + 2
    lines = [f"{len(rows)} plugins/remote commands are missing from {catalog.name} {affected} checks affected!"]
    skeleton = {}

    for section in ("local_plugins", "check_nrpe", "check_by_ssh"):
        group = [row for row in rows if row["SECTION"] == section]

        if not group:
            continue

        lines += ["", f"[{section}]"]

        for row in group:
            lines.append(f"  {row['NAME']:<{width}}{row['CHECKS']} checks on {row['HOSTS']} hosts"
                         f"   command: {', '.join(row['COMMANDS'])}")
            lines.append(f"  {'':<{width}}e.g. {'; '.join(row['EXAMPLES'])}")
            skeleton.setdefault(section, {})[row["NAME"]] = {
                "method": "", "description": "", "sensitive_options": []}

    entries = ",\n".join(
        f'  "{section}": {{\n' + ",\n".join(
            f'    {json.dumps(name)}: {json.dumps(entry)}' for name, entry in names.items()
        ) + "\n  }"
        for section, names in skeleton.items()
    )
    lines += ["", "Entries to complete and add to the catalog:", "{\n" + entries + "\n}"]

    return "\n".join(lines) + "\n"


def serialize(rows: List[dict], fields: Tuple[str, ...], json_format: bool) -> str:
    if json_format:
        return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=";", lineterminator="\n")
    writer.writeheader()

    for row in rows:
        writer.writerow({key: ", ".join(value) if isinstance(value, list)
                        else "Yes" if value is True else "No" if value is False
                        else "" if value is None else value for key, value in row.items()})

    return buffer.getvalue()


def write_output(path: Path, content: str, json_format: bool) -> None:
    """Atomically replace the export, preserving existing file permissions."""
    permissions = path.stat().st_mode & 0o777 if path.exists() else 0o600
    temp_name = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8" if json_format else "utf-8-sig",
            newline="", dir=path.parent, delete=False
        ) as destination:
            temp_name = destination.name
            destination.write(content)

        os.chmod(temp_name, permissions)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


USAGE = """\

Export the Nagios monitoring inventory from objects.cache: one row per host check and per service, with plugin, monitoring method, description and thresholds.
Descriptions and methods come from the plugin catalog (JSON). No plugin is ever executed.

Usage:
  PROG [-o FILE] [--profiles FILE] [-H TEXT] [-s TEXT] [-J] [-O FILE]
  PROG --missing [-o FILE] [--profiles FILE] [-H TEXT] [-s TEXT] [-J] [-O FILE]
  PROG -h

Options:
  -o, --cache FILE     Nagios objects.cache to read (default: CACHE)
  --profiles FILE      plugin catalog (default: PROFILES next to this script)
  -H, --host NAME      only this host; * and ? are wildcards, e.g. 'srv*' (case-insensitive)
  -s, --service NAME   only this service; * and ? are wildcards, e.g. 'Disk*'; host checks are skipped. -H and -s can be combined
  -J, --json           JSON output instead of CSV
  -O, --output FILE    write to FILE instead of the screen
  --missing            list the plugins and remote commands that are not in the catalog, instead of the inventory
  -h, --help           show this help

Exit codes:
  0  done            1  error (files, catalog)
  2  wrong options   3  --missing found entries to add

Example:
  Nagios configuration (objects.cache):

    define command {
        command_name  check_local_disk
        command_line  $USER1$/check_disk -w $ARG1$ -c $ARG2$ -p $ARG3$
    }
    define service {
        host_name            srv01
        service_description  Disk /var
        check_command        check_local_disk!20%!10%!/var
    }

  Catalog entry (plugin_profiles.json):

    "local_plugins": {
      "check_disk": {
        "method": "Local",
        "description": "Free space on mounted file systems",
        "sensitive_options": []
      }
    }

  Command:

    PROG -H srv01 -s 'Disk*' -O inventory.csv

  Result (main columns):

    HOST_NAME;SERVICE_NAME;PLUGIN;MONITORING_METHOD;DESCRIPTION;WARNING;CRITICAL;THRESHOLD_STATUS
    srv01;Disk /var;check_disk;Local;Free space on mounted file systems;20%;10%;CONFIGURED

  Plugins used by Nagios but not yet in the catalog:

    PROG --missing

Catalog fields (per plugin or remote command):
  method, description   required
  sensitive_options     options never read as thresholds, e.g. ["-C", "-p"]
  modes                 description per mode: {"psu": "Power supply status"}
  warning, critical     where the threshold is: "-w", "-a:1", "-f:2"; "" = none
  default_warning,
  default_critical      plugin default when the command sets no threshold
  warning_option,
  critical_option       one more option to search, e.g. "--max-age"
  thresholds_by_mode    rules per mode: {"capacity": {"warning": "-f:1"}}
  thresholds_supported  false for plugins that never use thresholds

Sections: local_plugins, check_nrpe (key: -c command), check_by_ssh (key: plugin inside -C). Names may contain * and ?.

"""


def print_usage(stream=sys.stdout) -> None:
    """Print the full help, with the real script name and defaults."""
    stream.write(USAGE.replace("PROG", Path(sys.argv[0]).name)
                 .replace("CACHE", str(DEFAULT_CACHE))
                 .replace("PROFILES", DEFAULT_PROFILES.name))


class ArgumentParser(argparse.ArgumentParser):
    """argparse with a short error message instead of its own usage line."""

    def error(self, message: str) -> None:
        sys.stderr.write(f"ERROR: {message}\nRun '{Path(sys.argv[0]).name} -h' for help.\n")
        sys.exit(2)


def main() -> int:
    parser = ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("-o", "--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("-O", "--output", type=Path)
    parser.add_argument("-J", "--json", action="store_true")
    parser.add_argument("-H", "--host", default="")
    parser.add_argument("-s", "--service", default="")
    parser.add_argument("--missing", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")

    if len(sys.argv) == 1:
        print_usage()
        return 0

    arguments = parser.parse_args()

    if arguments.help:
        print_usage()
        return 0
    
    try:
        objects = read_objects(arguments.cache)
        catalog = read_profiles(arguments.profiles)
        rows, missing = export_inventory(objects, catalog, arguments.host, arguments.service)
        export_rows = missing if arguments.missing else rows
        fields = MISSING_FIELDS if arguments.missing else FIELDS

        if arguments.missing and not arguments.json and not arguments.output:
            content = missing_text(missing, arguments.profiles, len(rows))
        else:
            content = serialize(export_rows, fields, arguments.json)

        if arguments.output:
            write_output(arguments.output, content, arguments.json)
            LOGGER.info("Exported %d rows to %s", len(export_rows), arguments.output)
        else:
            sys.stdout.write(content)

        if not arguments.missing:
            LOGGER.info("Inventory: %d host checks, %d service checks, %d unknown catalog entries",
                        sum(row["OBJECT_TYPE"] == "HOST" for row in rows),
                        sum(row["OBJECT_TYPE"] == "SERVICE" for row in rows), len(missing))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        LOGGER.error("Export failed: %s", error)
        
        return 1

    if arguments.missing and missing:
        return 3
    
    return 0


########## MAIN ##########

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())