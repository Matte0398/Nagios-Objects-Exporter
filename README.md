# Nagios Objects Exporter

Export Nagios service and host check definitions from `objects.cache` to a semicolon-separated CSV file using [`ngs_object_info.sh`](ngs_object_info.sh).

The script collects check command names and arguments, substitutes `$ARGn$` macros into the corresponding command definitions, and extracts warning and critical thresholds where supported. It reads the cache without executing any monitoring checks or modifying the Nagios configuration.

## Requirements

- A shell environment with Bash, `awk`, and `tee` available.
- Read access to a Nagios `objects.cache` file containing the object and command definitions to export.
- Write access to the output directory, which must already exist.

No additional libraries or build steps are required. On Windows, run the script from an environment providing these tools, such as WSL or Git Bash.

## Quick start

From the project directory, export all service checks using the default cache path:

```bash
bash ngs_object_info.sh
```

By default, the script reads `/usr/local/nagios/var/objects.cache` and writes `nagios_objects.csv` in the current working directory.

To specify the input and output paths:

```bash
bash ngs_object_info.sh \
  -o /path/to/objects.cache \
  -O /path/to/nagios_objects.csv
```

**The output file is overwritten on each run.** Choose an output path different from the input cache file.

## Usage

```text
bash ngs_object_info.sh [service_filter]
bash ngs_object_info.sh [-a] [-H <host_filter>] [-s <service_filter>] [-o <objects_cache>] [-O <output_csv>]
```

| Option                | Description                                                 | Default                               |
| --------------------- | ----------------------------------------------------------- | ------------------------------------- |
| `-a`                  | Include host checks as well as service checks.              | Service checks only                   |
| `-H <host_filter>`    | Filter by text contained in the host name.                  | All hosts                             |
| `-s <service_filter>` | Filter by text contained in the service description.        | All services                          |
| `-o <objects_cache>`  | Path to the input cache file.                               | `/usr/local/nagios/var/objects.cache` |
| `-O <output_csv>`     | Path to the output CSV file.                                | `nagios_objects.csv`                  |
| `service_filter`      | Positional alternative to `-s`, retained for compatibility. | No filter                             |

Filters are case-insensitive substring matches, not regular expressions. Host and service filters can be combined. Quote filters and paths containing spaces, and place options before any positional filter.

Do not combine a positional service filter with `-s`. A nonempty service filter excludes host-check rows even when `-a` is present.

The script does not define a dedicated help option. Invalid options or missing option arguments display usage information and exit with status `1`.

### Examples

```bash
# Export services whose descriptions contain "disk"
bash ngs_object_info.sh disk

# Export services whose descriptions contain "CPU"
bash ngs_object_info.sh -s CPU

# Export services for host names containing "server01"
bash ngs_object_info.sh -H server01

# Combine host and service filters
bash ngs_object_info.sh -H web -s http

# Include all host checks and service checks
bash ngs_object_info.sh -a

# Include host and service checks for host names containing "db"
bash ngs_object_info.sh -a -H db -O database_checks.csv

# Read a local cache copy and filter a description containing spaces
bash ngs_object_info.sh -o ./objects.cache -s "Disk Space" -O disk_checks.csv
```

## CSV output

The first row contains the following column names:

```text
Object type;Host name;Service description;Check command;Command args;Warning;Critical;Expanded command
```

| Column                | Contents                                                               |
| --------------------- | ---------------------------------------------------------------------- |
| `Object type`         | `service` or `host`.                                                   |
| `Host name`           | The object's `host_name` value.                                        |
| `Service description` | The service description; empty for host checks.                        |
| `Check command`       | The command name, without the `!`-separated arguments.                 |
| `Command args`        | Arguments formatted as `ARG1=value \| ARG2=value`.                     |
| `Warning`             | The detected warning threshold, or an empty value.                     |
| `Critical`            | The detected critical threshold, or an empty value.                    |
| `Expanded command`    | The matching `command_line` with supplied `$ARGn$` values substituted. |

All data fields are enclosed in double quotes, and embedded double quotes are doubled. When importing into a spreadsheet, select `;` as the delimiter.

For example, given a service using `check_disk!20%!10%` and a command definition of `$USER1$/check_disk -w $ARG1$ -c $ARG2$`, its output row could be:

```csv
"service";"server01";"Disk Space";"check_disk";"ARG1=20% | ARG2=10%";"20%";"10%";"$USER1$/check_disk -w 20% -c 10%"
```

## Threshold extraction and limitations

- Warning options recognized: `-w`, `--warning`, `--warn`, `-warning`, and `-warn`.
- Critical options recognized: `-c`, `--critical`, `--crit`, `-critical`, and `-crit`.
- Options support a separate value or an equals sign, such as `-w 80` and `--warning=80`. Attached values such as `-w80` are not recognized.
- Threshold values are copied as text. The script does not interpret ranges, units, or plugin-specific semantics.
- Only supplied `$ARGn$` macros are substituted. Other macros, including `$USER1$`, `$HOSTADDRESS$`, and custom macros, remain unresolved; `resource.cfg` is not read.
- Argument splitting supports escaped separators such as `\!`, but command expansion and threshold extraction are text-based. They do not fully parse shell quoting or complex commands; review results for arguments containing special characters or spaces.
- If a command definition is missing, the row still contains the command name and arguments, while the expanded command and thresholds remain empty.
- Objects without a host name or a check command are skipped. The script expects the multiline `define host`, `define service`, and `define command` blocks used in `objects.cache`.
- The export describes the supplied configuration snapshot. It does not include live status, check results, or performance data.

## Troubleshooting

| Symptom                      | What to check                                                                                                              |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `objects.cache` not found    | Supply the correct file path with `-o`.                                                                                    |
| CSV contains only the header | Check the filters and confirm that the cache contains matching objects with a host name and check command.                 |
| Empty threshold columns      | Check that the matching command definition exists and uses one of the supported threshold options with extractable values. |
| CSV cannot be written        | Confirm that the output directory exists and is writable.                                                                  |
| Shell errors mentioning `\r` | Save the script with Unix line endings (LF) before running it in Bash.                                                     |

The script prints `CSV created: <path>` after processing. It does not explicitly propagate every read or write failure, so also check error output and the generated file when using it in automation.
