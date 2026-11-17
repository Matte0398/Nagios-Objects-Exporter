# Nagios Objects Exporter

Export the Nagios monitoring inventory from a generated `objects.cache` file. This utility writes one row per host check and per
service, with the plugin used, the monitoring method, a short description of what is checked and the configured thresholds, as CSV or JSON.

Descriptions and methods come from a JSON **plugin catalog**
([plugin_profiles.json](examples/plugin_profiles_example.json)). The exporter only reads `objects.cache` and the catalog: it never runs a plugin.

## Contents

- [Features](#features)
- [Repository structure](#repository-structure)
- [Requirements](#requirements)
- [Usage](#usage)
- [Export fields](#export-fields)
- [Plugin catalog](#plugin-catalog)
- [How each value is determined](#how-each-value-is-determined)
- [Remote checks: NRPE and SSH](#remote-checks-nrpe-and-ssh)
- [Missing catalog entries](#missing-catalog-entries)
- [Credentials and privacy](#credentials-and-privacy)
- [License](#license)

## Features

- Export host and service checks to CSV or JSON without executing plugins.
- Describe monitoring methods and thresholds through a configurable plugin catalog.
- Analyze local checks and remote checks over NRPE or SSH.
- Include group memberships, custom metadata, and check configuration.
- Filter by host or service and report missing catalog entries.
- Redact sensitive custom macros during command analysis.

## Repository structure

```text
Nagios-Objects-Exporter/
|-- examples/
|   |-- host1_services.csv
|   |-- inventory.csv
|   |-- inventory.json
|   `-- plugin_profiles_example.json
|-- LICENSE
|-- ngs_objects_exporter.py
`-- README.md
```

## Requirements

- Python 3.6 or later, standard library only.
- Read access to `objects.cache` and to the plugin catalog.
- For `-O`, an existing output directory where a temporary file can be created.

## Usage

```text
ngs_objects_exporter.py [-o FILE] [--profiles FILE] [-H NAME] [-s NAME] [-J] [-O FILE]
ngs_objects_exporter.py --missing [-o FILE] [--profiles FILE] [-H NAME] [-s NAME] [-J] [-O FILE]
ngs_objects_exporter.py -h
```

| Option                 | Behavior                                                                                                                                                         |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-o`, `--cache FILE`   | `objects.cache` to read. Default `/usr/local/nagios/var/objects.cache`.                                                                                          |
| `--profiles FILE`      | Plugin catalog. Default `plugin_profiles.json` in the directory of the script (symbolic links are resolved). The export stops if the file is missing or invalid. |
| `-H`, `--host NAME`    | Only this host. The whole name must match, case-insensitively; `*` and `?` are wildcards (`'srv*'`).                                                             |
| `-s`, `--service NAME` | Only this service, with the same matching rules (`'Disk*'`). Host checks are skipped. Can be combined with `-H`: both must match.                                |
| `-J`, `--json`         | JSON instead of CSV.                                                                                                                                             |
| `-O`, `--output FILE`  | Write to FILE instead of stdout.                                                                                                                                 |
| `--missing`            | Instead of the inventory, list the plugins and remote commands that are not in the catalog.                                                                      |
| `-h`, `--help`         | Show the help with a complete example. Running without arguments does the same.                                                                                  |

Examples:

```bash
./ngs_objects_exporter.py -O inventory.csv
./ngs_objects_exporter.py -H HOST1 -s 'Disk*'
./ngs_objects_exporter.py -o objects_sample.cache -J -O inventory.json
./ngs_objects_exporter.py --missing
```

Host checks are always exported, one row per host, unless `-s` is used. Rows are
sorted by host name, with the host check before its services.

### Output formats

- **CSV**: semicolon-separated, with a header. Lists (groups) are joined with `, `,
  booleans become `Yes`/`No`, missing values are empty. With `-O` the file is written
  as UTF-8 with a BOM so that Excel reads accented characters correctly.
- **JSON**: an indented array of objects with the same keys as the CSV. Numbers stay
  numbers, booleans stay `true`/`false`, groups stay lists and missing values are `null`.

With `-O` the file is written to a temporary file first and then replaces the
destination. Existing permissions are kept; a new file gets `0600`.

A summary such as `Inventory: 120 host checks, 1450 service checks, 3 unknown catalog
entries` is written to stderr, so it never mixes with the CSV or JSON on stdout.

### Exit codes

| Code | Meaning                                                                              |
| ---- | ------------------------------------------------------------------------------------ |
| `0`  | Done                                                                                 |
| `1`  | Error reading the cache or the catalog, invalid catalog, or error writing the output |
| `2`  | Invalid command-line options                                                         |
| `3`  | `--missing` found plugins or remote commands to add to the catalog                   |

## Export fields

| Field                                                         | Content                                                                                  |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `HOST_NAME`                                                   | Host name.                                                                               |
| `HOST_ADDRESS`                                                | Host `address`, as configured (IP or host name).                                         |
| `CUSTOMER`                                                    | Host custom variable `_customer`.                                                        |
| `SYSTEM_TYPE`                                                 | Host custom variable `_type`.                                                            |
| `SYSTEM_VENDOR`                                               | Host custom variable `_vendor`.                                                          |
| `SERVICE_NAME`                                                | Service description; empty for host checks.                                              |
| `OBJECT_TYPE`                                                 | `HOST` or `SERVICE`.                                                                     |
| `CHECK_COMMAND`                                               | Nagios command name, e.g. `check_local_disk`.                                            |
| `PLUGIN`                                                      | Plugin file actually run by the command, e.g. `check_disk`.                              |
| `REMOTE_COMMAND`                                              | For `check_nrpe` the remote command (`-c`); for `check_by_ssh` the remote plugin (`-C`). |
| `MONITORING_METHOD`                                           | E.g. `SNMP`, `API`, `Local`, `Local via NRPE`, or `N/A`.                                 |
| `DESCRIPTION`                                                 | What the check does, or `N/A`.                                                           |
| `WARNING`, `CRITICAL`                                         | Thresholds; see [Thresholds](#thresholds).                                               |
| `THRESHOLD_STATUS`                                            | How the thresholds were determined; see [Threshold status](#threshold-status).           |
| `HOST_GROUPS`, `SERVICE_GROUPS`                               | Group memberships, nested groups included.                                               |
| `CHECK_INTERVAL`, `RETRY_INTERVAL`                            | As configured, in Nagios interval units (not necessarily seconds).                       |
| `MAX_CHECK_ATTEMPTS`, `CHECK_PERIOD`, `NOTIFICATION_INTERVAL` | As configured.                                                                           |
| `NOTIFICATIONS_ENABLED`, `ACTIVE_CHECKS_ENABLED`              | As configured; changes made from the web interface are not reflected.                    |
| `NOTES`                                                       | What could not be determined and why.                                                    |

Custom variable names are case-insensitive: `_customer` and `_CUSTOMER` are the same.

## Plugin catalog

The catalog describes each plugin once. A plugin used by Nagios but missing from the
catalog is exported with `N/A` as description and method.

```json
{
  "local_plugins": {
    "check_disk": {
      "method": "Local",
      "description": "Free space on mounted file systems",
      "sensitive_options": []
    },
    "check_qnap3.sh": {
      "method": "SNMP",
      "description": "QNAP NAS health",
      "sensitive_options": ["-A", "-C", "-u", "-X"],
      "modes": { "cpu": "System CPU", "usedvolume": "Volume utilization" }
    },
    "check_powerstore_api.py": {
      "method": "API",
      "description": "Dell PowerStore health",
      "sensitive_options": ["-p", "-u", "--password", "--password-id"],
      "modes": {
        "psu": "Show status of powersupply",
        "capacity": "Check total capacity"
      },
      "thresholds_by_mode": {
        "capacity": { "warning": "-f:1", "critical": "-f:2" }
      }
    }
  },
  "check_nrpe": {
    "check_disk*": {
      "method": "Local",
      "description": "Filesystem free space",
      "sensitive_options": [],
      "warning": "-a:1",
      "critical": "-a:2"
    }
  }
}
```

### Sections

| Section         | Key of each entry                                                                                  |
| --------------- | -------------------------------------------------------------------------------------------------- |
| `local_plugins` | File name of the plugin run by the Nagios server, e.g. `check_disk` from `$USER1$/check_disk ...`. |
| `check_nrpe`    | Remote command name, i.e. the value of `check_nrpe -c` as defined in `nrpe.cfg`.                   |
| `check_by_ssh`  | File name of the plugin inside `check_by_ssh -C "..."`.                                            |

Names may contain `*` and `?`. An exact name wins over patterns; between two patterns
the one with more literal characters wins (`check_qnap*.sh` before `check_*`). When the
command starts with `env`, `python…`, `perl`, `bash` or `sh`, the plugin is the file
that follows.

### Fields

| Field                                 | Required | Content                                                                             |
| ------------------------------------- | -------- | ----------------------------------------------------------------------------------- |
| `method`                              | yes      | Monitoring method, free text: `SNMP`, `API`, `Local`, `SSH`…                        |
| `description`                         | yes      | What the plugin or command checks.                                                  |
| `sensitive_options`                   | no       | Options carrying credentials; they are never read as thresholds.                    |
| `modes`                               | no       | Description for each value of the plugin's check selector.                          |
| `warning`, `critical`                 | no       | Where the threshold is in the command; `""` means the plugin has no such threshold. |
| `default_warning`, `default_critical` | no       | Value used by the plugin when the command sets no threshold.                        |
| `warning_option`, `critical_option`   | no       | One more option to search for thresholds.                                           |
| `thresholds_by_mode`                  | no       | `warning`/`critical`/`default_*` rules valid only for one mode.                     |
| `thresholds_supported`                | no       | `false` for plugins that never use thresholds.                                      |

**`sensitive_options`** — e.g. `["-C", "-p", "--password"]`. Since the command line is
not exported, this field only prevents a credential (`-c S3cr3t1`) from being taken as
a threshold. A `:N` suffix (`"--cache:2,3"`) is accepted and ignored.

**`modes`** — the exporter looks for a command argument equal to a key of `modes`
(`-m usedvolume`, `--check=psu`, `-c psu,fan`, `-m hdstatus:2,5`). All recognized modes contribute their descriptions in command order, separated
by line breaks, and select their own `thresholds_by_mode` rules. Identical
mode/selector pairs are included once. A colon starts selectors belonging to the
preceding mode: `hdstatus:2,5` describes one mode as `Hard drive status (2,5)`.
A recognized mode name after a comma starts another mode, so
`hdstatus:2,5,cpu` selects `hdstatus` with selectors `2,5`, followed by `cpu`.
The generic `description` is used only when no mode is recognized. Nagios
`_check_description` and `notes` still take precedence.

**`warning`, `critical`** — a rule `"-x"` or `"-x:N"`:

| Rule          | Meaning                                | Command                  | Result |
| ------------- | -------------------------------------- | ------------------------ | ------ |
| `"-w"`        | first value after `-w`                 | `-w 80`                  | `80`   |
| `"-a:1"`      | first argument of `check_nrpe -a`      | `-a 20% 10% /var`        | `20%`  |
| `"--cache:4"` | fourth value after `--cache`           | `--cache ORCL u p 90 95` | `90`   |
| `"-f:2"`      | second item of a comma-separated value | `-f 80,90`               | `90`   |
| `""`          | no such threshold                      | `-w 20`                  | empty  |

A comma-separated value is split only when the rule gives a position: `"-w"` returns
`5,4,3` whole, `"-l:2"` returns the second item of `5,80,90`. The value found must look
like a threshold (`80`, `80%`, `10:`, `~:20`, `@5:10`); otherwise the rule counts as
not found.

**`default_warning`, `default_critical`** — free text such as `"1000"` or `"1000s"`.
They are used only when neither a rule nor the command gives a threshold and the
service has no `_WARNING`/`_CRITICAL`.

**`warning_option`, `critical_option`** — e.g. `"critical_option": "--max-age"`. Unlike
`warning`/`critical`, they add an option to the automatic search without excluding
the others.

**`thresholds_by_mode`** — e.g.
`{"CPULOAD": {"warning": "-l:2", "critical": "-l:3"}}`: with `-v CPULOAD -l 5,80,90` the
thresholds are `80` and `90`; other modes use the general rules.
With multiple modes, mode-specific thresholds occupy separate lines labeled
with the mode name, such as `cpu: 80` and `volume: 85`. Shared thresholds are
included once without a mode label. A declared rule that cannot be resolved
makes the overall status `UNDETERMINED`, even if other modes have thresholds;
`NOTES` identifies the affected mode. Explicit `_warning` and `_critical`
override the entire corresponding field.

JSON strings encode line breaks as `\n`; CSV quotes fields containing line breaks.

### Catalog validation

The catalog must be a JSON object containing only the three sections. The export stops
with exit code 1 and a message naming the entry when the JSON is invalid, a key is
duplicated, `method` or `description` is missing or empty, or a field has the wrong
type. A badly written threshold rule (e.g. `"a:1"`) is reported when a check uses it.

## How each value is determined

### Method and description

| Value               | Order of precedence                                                                                                                                                                          |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MONITORING_METHOD` | service `_monitoring_method`; host `_monitoring_method`; catalog `method` (plus ` via NRPE`/` via SSH` for remote checks); `NRPE`/`SSH` when the remote command is not in the catalog; `N/A` |
| `DESCRIPTION`       | service `_check_description`; service `notes`; catalog descriptions for all selected modes; catalog `description`; `N/A`                                                                     |

### Thresholds

For each of warning and critical:

1. Service custom variable `_warning` / `_critical`.
2. Catalog rule for the selected mode (`thresholds_by_mode`).
3. Catalog rule `warning` / `critical` (`""` means none).
4. Automatic search in the command: `-w`, `--warning`, `--warn` (warning) or `-c`,
   `--critical`, `--crit` (critical), long options whose name contains `warn`/`crit`
   (`--size-warning`), and `warning_option`/`critical_option`. Options listed in
   `sensitive_options` are skipped. The value must contain a digit, so `-c psu` (a
   check selector) is not taken as a threshold.
5. Catalog `default_warning` / `default_critical`.

When the automatic search finds several options, all are listed:
`--size-warning=80; --inode-warning=70`.

### Threshold status

| Value                    | When                                                                                                                                                                                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `UNDETERMINED`           | The plugin could not be identified; the remote command is not in the catalog; a threshold contains an unresolved macro such as `$USER5$`; a catalog rule found nothing; an NRPE check shows no threshold; or the plugin is not in the catalog and no threshold was found. |
| `DEFAULT`                | A `default_warning`/`default_critical` value was used.                                                                                                                                                                                                                    |
| `CONFIGURED`             | At least one threshold was found in the command or in a Nagios variable.                                                                                                                                                                                                  |
| `NOT_APPLICABLE`         | No threshold and `"thresholds_supported": false`.                                                                                                                                                                                                                         |
| `NO_EXPLICIT_THRESHOLDS` | The plugin is in the catalog and the command sets no threshold.                                                                                                                                                                                                           |

### Macros

Before analysis, the command line is expanded with `$ARGn$`, `$HOSTNAME$`,
`$HOSTADDRESS$`, `$HOSTALIAS$`, `$SERVICEDESC$` and the host and service custom
variables (`$_HOSTxxx$`, `$_SERVICExxx$`). Custom variables whose name suggests a
credential (password, secret, token, community, key…) are replaced with `[REDACTED]`.
`$USERn$` macros from `resource.cfg` are not resolved.

## Remote checks: NRPE and SSH

**`check_nrpe`** — the remote command is the value of `-c`/`--command` and is looked up
in the `check_nrpe` section. Its arguments are everything after `-a`/`--args`. NRPE
thresholds are often defined only in `nrpe.cfg` on the remote host: in that case the
export cannot see them, `THRESHOLD_STATUS` is `UNDETERMINED` and `NOTES` says so. When
`-a` passes positional thresholds (`-a 20% 10%`), describe their position with
`"warning": "-a:1"` and `"critical": "-a:2"`; when it passes options
(`-a "-w 80 -c 90"`), they are found automatically.

**`check_by_ssh`** — the command inside `-C`/`--command` is analysed like a local one.
Its plugin is looked up in the `check_by_ssh` section first, then in `local_plugins`.

In both cases the method becomes `<method> via NRPE` or `<method> via SSH`.

## Missing catalog entries

```bash
./ngs_objects_exporter.py --missing
```

On screen, the report lists the missing entries grouped by section, ordered by how many
checks use them, followed by entries ready to complete and paste into the catalog:

```text
1 plugins/remote commands are missing from plugin_profiles.json 1 checks affected!

[local_plugins]
  check_http  1 checks on 1 hosts   command: check_http
              e.g. localhost / HTTP

Entries to complete and add to the catalog:
{
  "local_plugins": {
    "check_http": {"method": "", "description": "", "sensitive_options": []}
  }
}
```

When nothing is missing:

```text
OK: all plugins and remote commands used by 1570 checks are in plugin_profiles.json
```

## Credentials and privacy

- The export does not contain command lines, so credentials passed as arguments do
  not appear in it.
- Thresholds are never read from options listed in `sensitive_options`, and custom
  variables with a sensitive name are redacted before analysis.
- Host names, addresses, customers, service names, groups and Nagios `notes` are
  exported as configured. Review them before sharing an export outside your team.

## License

Released under the MIT License. See [LICENSE](LICENSE).
