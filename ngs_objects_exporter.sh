#!/bin/bash

##################################################################################################################################
## Description: Script to extract Nagios host/service check commands and thresholds from objects.cache, and report them in csv
##
## Author: Matteo Z.
##################################################################################################################################

print_usage() {
    echo -e "\nDescription:"
    echo -e "\n   Script to extract Nagios host/service check commands and thresholds from objects.cache, and report them in csv"
    echo -e "\n   Default csv content -> object_type;host_name;service_description;check_command;command_args;warning;critical;expanded_command"
    echo -e "\nUsage:"
    echo -e "\n  $0 [service_filter]"
    echo -e "  $0 [-a] [-H <host_filter>] [-s <service_filter>] [-o <objects_cache>] [-O <output_csv>]"
    echo -e "\nOptions:"
    echo -e "\n  -a"
    echo -e "\tShow host checks and services. By default, only services are shown"
    echo -e "\n  -H"
    echo -e "\tHost name filter. The match is case insensitive and contains the specified text"
    echo -e "\n  -s"
    echo -e "\tService description filter. The match is case insensitive and contains the specified text"
    echo -e "\n  -o"
    echo -e "\tNagios objects.cache path (default: $objects_cache)"
    echo -e "\n  -O"
    echo -e "\tOutput csv path (default: $ngs_file_hosts)"
    echo -e "\nExamples:"
    echo -e "\n  $0                       All services"
    echo -e "  $0 disk                  Services containing 'disk' (compatibility mode)"
    echo -e "  $0 -s cpu                Services containing 'cpu'"
    echo -e "  $0 -H server01           All services for hosts containing 'server01'"
    echo -e "  $0 -H web -s http        Hosts containing 'web' with services containing 'http'"
    echo -e "  $0 -a                    Show host checks and services"
    echo -e "  $0 -a -H db              Host checks and services for hosts containing 'db'\n"
    exit 1
}


create_csv() {
     awk \
        -v include_hosts="$include_hosts" \
        -v host_filter="$host_filter" \
        -v service_filter="$service_filter" '
          function trim(s) {
               gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", s)
               return s
          }

          function csv_escape(s) {
               gsub(/"/, "\"\"", s)
               return "\"" s "\""
          }

          function match_filter(value, filter) {
               return filter == "" || index(tolower(value), tolower(filter)) > 0
          }

          function split_check_command(value, arr,    i, c, escaped, field, count) {
               count = 1
               field = ""
               escaped = 0

               for (i = 1; i <= length(value); i++) {
                    c = substr(value, i, 1)

                    if (escaped) {
                         field = field c
                         escaped = 0
                    } else if (c == "\\") {
                         escaped = 1
                    } else if (c == "!") {
                         arr[count++] = field
                         field = ""
                    } else {
                         field = field c
                    }
               }

               if (escaped) field = field "\\"
               arr[count] = field
               return count
          }

          function command_args(parts, part_count,    i, args) {
               args = ""

               for (i = 2; i <= part_count; i++) {
                    args = args (args == "" ? "" : " | ") "ARG" (i - 1) "=" parts[i]
               }

               return args
          }

          function expand_command_line(command_name, parts, part_count,    i, expanded, arg_key) {
               expanded = command_lines[command_name]

               if (expanded == "") return ""

               for (i = 1; i < part_count; i++) {
                    arg_key = "\\$ARG" i "\\$"
                    gsub(arg_key, parts[i + 1], expanded)
               }

               return expanded
          }

          function token_value(text, option,    token_count, tokens, i, value) {
               gsub(/[;\r\n]/, " ", text)
               token_count = split(text, tokens, /[ \t]+/)

               for (i = 1; i <= token_count; i++) {
                    if (tokens[i] == option && (i + 1) <= token_count) {
                         value = tokens[i + 1]
                         gsub(/^["'\''`]+|["'\''`]+$/, "", value)
                         return value
                    }

                    if (index(tokens[i], option "=") == 1) {
                         value = substr(tokens[i], length(option) + 2)
                         gsub(/^["'\''`]+|["'\''`]+$/, "", value)
                         return value
                    }
               }

               return ""
          }

          function find_thresholds(expanded, parts, part_count,    warning, critical) {
               warning = token_value(expanded, "-w")

               if (warning == "") warning = token_value(expanded, "--warning")
               if (warning == "") warning = token_value(expanded, "--warn")
               if (warning == "") warning = token_value(expanded, "-warning")
               if (warning == "") warning = token_value(expanded, "-warn")

               critical = token_value(expanded, "-c")

               if (critical == "") critical = token_value(expanded, "--critical")
               if (critical == "") critical = token_value(expanded, "--crit")
               if (critical == "") critical = token_value(expanded, "-critical")
               if (critical == "") critical = token_value(expanded, "-crit")

               return warning SUBSEP critical
          }

          function add_object(object_type, host, service, check_command) {
               object_count++
               object_types[object_count] = object_type
               object_hosts[object_count] = host
               object_services[object_count] = service
               object_check_commands[object_count] = check_command
          }

          function print_object(object_type, host, service, check_command,    parts, part_count, command_name, args, expanded, thresholds, values) {
               if (host == "" || check_command == "") return
               if (!match_filter(host, host_filter)) return
               if (object_type == "service" && !match_filter(service, service_filter)) return
               if (object_type == "host" && service_filter != "") return

               part_count = split_check_command(check_command, parts)
               command_name = parts[1]
               args = command_args(parts, part_count)
               expanded = expand_command_line(command_name, parts, part_count)
               thresholds = find_thresholds(expanded, parts, part_count)
               split(thresholds, values, SUBSEP)

               print csv_escape(object_type) ";" \
                         csv_escape(host) ";" \
                         csv_escape(service) ";" \
                         csv_escape(command_name) ";" \
                         csv_escape(args) ";" \
                         csv_escape(values[1]) ";" \
                         csv_escape(values[2]) ";" \
                         csv_escape(expanded)
          }

          /^[ \t]*define[ \t]+command[ \t]*\{/ {
               in_command = 1
               command_name = ""
               command_line = ""
               next
          }

          /^[ \t]*define[ \t]+host[ \t]*\{/ {
               in_host = 1
               host_name = ""
               host_check_command = ""
               next
          }

          /^[ \t]*define[ \t]+service[ \t]*\{/ {
               in_service = 1
               service_host_name = ""
               service_description = ""
               service_check_command = ""
               next
          }

          in_command && /^[ \t]*}/ {
               if (command_name != "") command_lines[command_name] = command_line
               in_command = 0
               next
          }

          in_host && /^[ \t]*}/ {
               if (include_hosts == "1") {
                    add_object("host", host_name, "", host_check_command)
               }

               in_host = 0
               next
          }

          in_service && /^[ \t]*}/ {
               add_object("service", service_host_name, service_description, service_check_command)
               in_service = 0
               next
          }

          in_command {
               line = $0

               if (line ~ /^[ \t]*command_name[ \t]+/) {
                    sub(/^[ \t]*command_name[ \t]+/, "", line)
                    command_name = trim(line)
               } else if (line ~ /^[ \t]*command_line[ \t]+/) {
                    sub(/^[ \t]*command_line[ \t]+/, "", line)
                    command_line = trim(line)
               }

               next
          }

          in_host {
               line = $0

               if (line ~ /^[ \t]*host_name[ \t]+/) {
                    sub(/^[ \t]*host_name[ \t]+/, "", line)
                    host_name = trim(line)
               } else if (line ~ /^[ \t]*check_command[ \t]+/) {
                    sub(/^[ \t]*check_command[ \t]+/, "", line)
                    host_check_command = trim(line)
               }
               
               next
          }

          in_service {
               line = $0

               if (line ~ /^[ \t]*host_name[ \t]+/) {
                    sub(/^[ \t]*host_name[ \t]+/, "", line)
                    service_host_name = trim(line)
               } else if (line ~ /^[ \t]*service_description[ \t]+/) {
                    sub(/^[ \t]*service_description[ \t]+/, "", line)
                    service_description = trim(line)
               } else if (line ~ /^[ \t]*check_command[ \t]+/) {
                    sub(/^[ \t]*check_command[ \t]+/, "", line)
                    service_check_command = trim(line)
               }

               next
          }

          END {
               for (i = 1; i <= object_count; i++) {
                    print_object(object_types[i], object_hosts[i], object_services[i], object_check_commands[i])
               }
          }
     ' "$objects_cache" >> "$ngs_file_hosts"
}


########## MAIN ##########

ngs_default_path="/usr/local/nagios"
objects_cache="${ngs_default_path}/var/objects.cache"
ngs_file_hosts="nagios_objects.csv"
include_hosts=0
host_filter=""
service_filter=""

while getopts ':aH:s:o:O:' opt; do
     case $opt in
          a)
               include_hosts=1 ;;
          H)
               host_filter="${OPTARG}" ;;
          s)
               service_filter="${OPTARG}" ;;
          o)
               objects_cache="${OPTARG}" ;;
          O)
               ngs_file_hosts="${OPTARG}" ;;
          *)
               echo -e "\nWarning!! Invalid option or missing argument!"
               print_usage ;;
    esac
done
shift $(($OPTIND-1))

if [[ $# -gt 1 ]]; then
     echo -e "\nWarning!! Too many arguments!"
     print_usage
fi

if [[ $# -eq 1 ]]; then
     if [[ -n "$service_filter" ]]; then
          echo -e "\nWarning!! Use either positional service filter or -s, not both!"
          print_usage
     fi

     service_filter="$1"
fi

if [[ ! -f "$objects_cache" ]]; then
     echo "Error!! The objects.cache file ($objects_cache) has not been found!"
     exit 1
fi

echo "Object type;Host name;Service description;Check command;Command args;Warning;Critical;Expanded command" | tee "$ngs_file_hosts" > /dev/null
create_csv

echo "CSV created: $ngs_file_hosts"