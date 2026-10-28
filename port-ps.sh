#!/usr/bin/env bash

# PORTS — Gummy Process Explorer for macOS
# Features: process search, overview, IP sockets, TCP listeners,
# open files, UNIX sockets, resource search, and deduplication.

set -u

for dep in gum lsof ps python3 sed; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        printf 'Missing dependency: %s\n' "$dep" >&2
        exit 1
    fi
done

# ── Theme (gummy candy palette) ───────────────────────

STRAWBERRY="#FF5C8A"   # strawberry
ORANGE="#FF9F43"       # orange
LEMON="#FFD93D"        # lemon
LIME="#6BCB77"         # lime
BLUEBERRY="#4D96FF"    # blue raspberry
GRAPE="#B983FF"        # grape
MINT="#4ECDC4"         # mint
CARAMEL="#C08B4F"      # muted
COTTON="#FFF6E5"       # text

RAW_FILE=""
SCAN_STATUS=0
NETWORK_COUNT=0
LISTEN_COUNT=0
FILES_COUNT=0

cleanup() {
    if [[ -n "$RAW_FILE" && -f "$RAW_FILE" ]]; then
        rm -f "$RAW_FILE"
    fi
}

trap cleanup EXIT


PY_PROCESS_LIST=$(cat <<'PY'
import sys

width = int(sys.argv[1])

rows = []

for line in sys.stdin:
    parts = line.strip().split(None, 2)

    if len(parts) != 3:
        continue

    pid, user, command = parts

    command = command.rsplit("/", 1)[-1].strip()

    if pid.isdigit():
        rows.append((command.lower(), int(pid), user.strip(), command))

rows.sort(key=lambda row: (row[0], row[1]))


def elide(text, limit):
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]

    head = (limit - 1) // 2
    tail = limit - 1 - head

    return text[:head] + "…" + (text[-tail:] if tail else "")


if not rows:
    sys.exit(0)

pid_w = max([len(str(r[1])) for r in rows] + [3])
user_w = min(max([len(r[2]) for r in rows] + [4]), 18)

command_w = width - pid_w - user_w - 4

if command_w < 12:
    user_w = max(4, user_w - (12 - command_w))
    command_w = width - pid_w - user_w - 4

for _, pid, user, command in rows:
    print(
        "%-*s  %-*s  %s"
        % (
            pid_w,
            pid,
            user_w,
            elide(user, user_w),
            elide(command, command_w),
        )
    )
PY
)

PY_PARSE_TSV=$(cat <<'PY'
import os
import sys

raw_file, username, mode = sys.argv[1:4]

HEADER = "\t".join([
    "COMMAND", "PID", "USER", "FD",
    "TYPE", "PROTO", "NAME", "STATE"
])


def clean(value):
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def parse(raw_file, username, mode):
    if not os.path.exists(raw_file):
        return []

    pid = ""
    command = ""
    current = None
    seen = set()
    rows = []

    def emit():
        if current is None:
            return

        name = clean(current.get("name", "")) or "(unnamed)"
        resource_type = clean(current.get("type", ""))
        protocol = clean(current.get("protocol", ""))
        state = clean(current.get("state", ""))
        fd = clean(current.get("fd", ""))

        is_network = resource_type in ("IPv4", "IPv6")

        if mode == "network":
            keep = is_network
        elif mode == "listeners":
            keep = (
                is_network
                and protocol.upper() == "TCP"
                and state.upper() == "LISTEN"
            )
        elif mode == "files":
            keep = not is_network
        else:
            keep = True

        if not keep:
            return

        # Intentionally ignore FD so duplicate references collapse.
        key = (command, pid, username, name, state)

        if key in seen:
            return

        seen.add(key)

        rows.append([
            clean(command),
            clean(pid),
            clean(username),
            fd,
            resource_type,
            protocol,
            name,
            state,
        ])

    with open(raw_file, "r", errors="replace") as source:
        for line in source:
            line = line.rstrip("\n")

            if not line:
                continue

            field = line[0]
            value = line[1:]

            if field == "p":
                emit()
                pid = value
                command = ""
                current = None
                continue

            if field == "c":
                command = value
                continue

            if field == "f":
                emit()
                current = {
                    "fd": value,
                    "type": "",
                    "protocol": "",
                    "name": "",
                    "state": "",
                }
                continue

            if current is None:
                continue

            if field == "t":
                current["type"] = value

            elif field == "P":
                current["protocol"] = value

            elif field == "n":
                current["name"] = value

            elif field == "T":
                # lsof encodes state as TST=ESTABLISHED.
                # After reading the leading T, value is ST=ESTABLISHED.
                state_field, separator, state_value = value.partition("=")

                if separator and state_field == "ST":
                    current["state"] = state_value

    emit()

    return rows


rows = parse(raw_file, username, mode)

print(HEADER)

for row in rows:
    print("\t".join(row))
PY
)

PY_COUNTS=$(cat <<'PY'
import sys

network = 0
listeners = 0
files = 0

for line in sys.stdin:
    row = line.rstrip("\n").split("\t")

    if len(row) < 8 or row[0] == "COMMAND":
        continue

    is_network = row[4] in ("IPv4", "IPv6")

    if is_network:
        network += 1

        if row[5].upper() == "TCP" and row[7].upper() == "LISTEN":
            listeners += 1
    else:
        files += 1

print(network, listeners, files)
PY
)

PY_RENDER=$(cat <<'PY'
import re
import sys

mode = sys.argv[1]
width = int(sys.argv[2])

COTTON, CARAMEL, ORANGE, MINT, LIME, LEMON, STRAWBERRY = sys.argv[3:10]

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rgb(color):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def paint(text, color, bold=False):
    r, g, b = rgb(color)
    prefix = "\x1b[1m" if bold else ""
    return "%s\x1b[38;2;%d;%d;%dm%s\x1b[0m" % (prefix, r, g, b, text)


def elide(text, limit):
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]

    head = (limit - 1) // 2
    tail = limit - 1 - head

    return text[:head] + "…" + (text[-tail:] if tail else "")


def plain(text, limit):
    return elide(ANSI.sub("", text), limit)


if mode == "files":
    columns = [
        (3, "FD", 8, CARAMEL),
        (4, "TYPE", 9, ORANGE),
        (6, "RESOURCE", -1, COTTON),
    ]
else:
    columns = [
        (3, "FD", 8, CARAMEL),
        (4, "FAMILY", 9, ORANGE),
        (5, "PROTO", 7, MINT),
        (6, "ENDPOINT / RESOURCE", -1, COTTON),
        (7, "STATE", 12, LIME),
    ]

fixed = sum(col[2] for col in columns)
gaps = 2 * (len(columns) - 1)
flexible = width - fixed - gaps

if flexible < 12:
    flexible = 12

print(paint("  ".join(col[1] for col in columns), STRAWBERRY, bold=True))
print(paint("─" * (fixed + gaps + flexible), CARAMEL))

for line in sys.stdin:
    if not line.strip():
        continue

    row = line.rstrip("\n").split("\t")

    if len(row) < 8 or row[0] == "COMMAND":
        continue

    cells = []

    for index, _, size, color in columns:
        budget = flexible if size < 0 else size
        value = plain(row[index], budget)
        color = color

        if index == 7 and row[7]:
            if row[7].upper() == "LISTEN":
                color = LIME
            elif row[7].upper() == "ESTABLISHED":
                color = LEMON
            else:
                color = STRAWBERRY

        padded = value.ljust(size if size > 0 else len(value))
        cells.append(paint(padded, color))

    print("  ".join(cells).rstrip())
PY
)

PY_SEARCH=$(cat <<'PY'
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*m")

NAME_LIMIT = 46


def elide(text, limit):
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]

    head = (limit - 1) // 2
    tail = limit - 1 - head

    return text[:head] + "…" + (text[-tail:] if tail else "")


rows = []

for line in sys.stdin:
    row = ANSI.sub("", line.rstrip("\n")).split("\t")

    if len(row) < 8:
        continue

    rows.append([
        elide(row[3], 8),
        elide(row[4], 9),
        elide(row[5], 7),
        elide(row[6], NAME_LIMIT),
        elide(row[7], 12),
    ])

if not rows:
    sys.exit(0)

sizes = [0, 0, 0, 0, 0]

for row in rows:
    for i, cell in enumerate(row):
        sizes[i] = max(sizes[i], len(cell))

for row in rows:
    print("  ".join(
        cell.ljust(sizes[i]) if i < len(row) - 1 else cell
        for i, cell in enumerate(row)
    ).rstrip())
PY
)


term_width() {
    local w=""

    if [[ -n "${COLUMNS:-}" ]]; then
        w="$COLUMNS"
    else
        w=$(tput cols 2>/dev/null)
    fi

    [[ "$w" =~ ^[0-9]+$ ]] || w=$(stty size 2>/dev/null | awk '{print $2}')
    [[ "$w" =~ ^[0-9]+$ ]] || w=100

    (( w < 64 )) && w=64
    (( w > 220 )) && w=220

    printf '%s' "$w"
}

# ── UI components ────────────────────────────────────

banner() {
    local width
    width=$(term_width)
    (( width > 76 )) && width=76

    gum style \
        --border double \
        --border-foreground "$STRAWBERRY" \
        --foreground "$STRAWBERRY" \
        --bold \
        --align center \
        --width "$width" \
        --padding "1 2" \
        "◈ PORTS / PROCESS EXPLORER ◈" \
        "PROCESS · NETWORK · OPEN FILES"

    printf '\n'

    gum style \
        --foreground "$CARAMEL" \
        "Search processes and explore their ports, IPs, files and sockets."

    printf '\n\n'
}

chip() {
    local color="$1"
    shift

    gum style \
        --border rounded \
        --border-foreground "$color" \
        --foreground "$color" \
        --padding "0 1" \
        "$*"
}

show_message() {
    local title="$1"
    local subtitle="$2"
    local message="$3"
    local width
    width=$(term_width)
    (( width > 76 )) && width=76

    {
        gum style \
            --border double \
            --border-foreground "$GRAPE" \
            --foreground "$STRAWBERRY" \
            --bold \
            --align center \
            --width "$width" \
            --padding "1 2" \
            "$title" "$subtitle"

        printf '\n'

        gum style \
            --border rounded \
            --border-foreground "$MINT" \
            --foreground "$COTTON" \
            --padding "1 2" \
            "$message"

        printf '\n'

        gum style --foreground "$CARAMEL" \
            "Press q or Esc to return."
    } | gum pager --soft-wrap
}

# ── Process discovery ────────────────────────────────

build_process_list() {
    local width
    width=$(term_width)

    ps -axo pid=,user=,comm= 2>/dev/null |
        python3 -c "$PY_PROCESS_LIST" "$width"
}

# ── Collect a process's open resources ────────────────

scan_process() {
    local pid="$1"
    local output="$2"

    : > "$output"

    if gum spin \
        --spinner points \
        --title "Scanning PID $pid for open resources..." \
        -- lsof -nP -p "$pid" -FpcfntPT > "$output" 2>/dev/null
    then
        return 0
    fi

    if [[ -s "$output" ]]; then
        return 0
    fi

    if ps -p "$pid" >/dev/null 2>&1; then
        return 2
    fi

    return 1
}

# ── Parse and categorize lsof structured output ───────
#
# Uses lsof structured output rather than parsing its
# whitespace-aligned display table.
#
# Deduplication key:
# COMMAND + PID + USER + NAME + STATE
#
# FD, TYPE and PROTOCOL are excluded from the key.

resource_tsv() {
    local raw_file="$1"
    local username="$2"
    local mode="${3:-all}"

    python3 -c "$PY_PARSE_TSV" "$raw_file" "$username" "$mode"
}

resource_counts() {
    resource_tsv "$RAW_FILE" "$PROCESS_USER" all |
        python3 -c "$PY_COUNTS"
}

resource_table() {
    local mode="$1"
    local width="$2"

    resource_tsv "$RAW_FILE" "$PROCESS_USER" "$mode" |
        python3 -c "$PY_RENDER" "$mode" "$width" \
            "$COTTON" "$CARAMEL" "$ORANGE" "$MINT" \
            "$LIME" "$LEMON" "$STRAWBERRY"
}

# ── Scrollable colourful reports ──────────────────────

show_table_report() {
    local title="$1"
    local subtitle="$2"
    local mode="$3"
    local width rows table_width
    width=$(term_width)

    table_width=$(( width - 14 ))
    (( table_width < 52 )) && table_width=52

    rows=$(resource_tsv "$RAW_FILE" "$PROCESS_USER" "$mode" | wc -l | tr -d ' ')
    rows=$(( rows > 0 ? rows - 1 : 0 ))

    {
        gum style \
            --border double \
            --border-foreground "$BLUEBERRY" \
            --foreground "$STRAWBERRY" \
            --bold \
            --align center \
            --width 76 \
            --padding "1 2" \
            "◈ $title ◈" \
            "$subtitle"

        printf '\n'

        if (( rows == 0 )); then
            gum style \
                --border rounded \
                --border-foreground "$ORANGE" \
                --foreground "$ORANGE" \
                --padding "1 3" \
                "No resources found in this view."
        else
            resource_table "$mode" "$table_width"
        fi

        printf '\n'

        gum style \
            --foreground "$LIME" \
            "✓ $rows unique resource(s)"

        printf '\n'

        gum style \
            --foreground "$CARAMEL" \
            "Deduplication ignores FD." \
            "Search with / · Scroll with ↑↓ · Return with q or Esc."
    } | gum pager --soft-wrap
}

# ── Process overview ─────────────────────────────────

show_overview() {
    local process_info ppid user cpu mem elapsed full_command info width
    local ip_chip listen_chip files_chip warning

    width=$(term_width)

    process_info=$(
        ps -p "$PID" \
            -o ppid=,user=,%cpu=,%mem=,etime=,command= 2>/dev/null
    )

    if [[ -z "$process_info" ]]; then
        show_message \
            "PROCESS EXITED" "PID $PID" \
            "The process exited or is no longer accessible."
        return
    fi

    read -r ppid user cpu mem elapsed full_command <<< "$process_info"

    info=$(
        printf 'PID       %s\n' "$PID"
        printf 'PPID      %s\n' "${ppid:-unknown}"
        printf 'USER      %s\n' "${user:-unknown}"
        printf 'CPU       %s%%\n' "${cpu:-unknown}"
        printf 'MEMORY    %s%%\n' "${mem:-unknown}"
        printf 'ELAPSED   %s\n' "${elapsed:-unknown}"
    )

    if (( SCAN_STATUS == 2 )); then
        warning=$(
            gum style \
                --border rounded \
                --border-foreground "$STRAWBERRY" \
                --foreground "$LEMON" \
                --padding "0 2" \
                "⚠  Permission denied — this process belongs to another user."
        )
        info="$info"$'\n\n'"$warning"
    fi

    ip_chip=$(chip "$MINT" "IP sockets: $NETWORK_COUNT")
    listen_chip=$(chip "$LIME" "TCP listeners: $LISTEN_COUNT")
    files_chip=$(chip "$ORANGE" "Other resources: $FILES_COUNT")

    {
        gum style \
            --border double \
            --border-foreground "$STRAWBERRY" \
            --foreground "$STRAWBERRY" \
            --bold \
            --align center \
            --width 76 \
            --padding "1 2" \
            "◈ PROCESS OVERVIEW ◈" \
            "$PROCESS_NAME · PID $PID"

        printf '\n'

        gum style \
            --border rounded \
            --border-foreground "$GRAPE" \
            --foreground "$COTTON" \
            --padding "1 2" \
            "$info"

        printf '\n'

        gum join --horizontal \
            "$ip_chip" "$listen_chip" "$files_chip"

        printf '\n\n'

        gum style --foreground "$MINT" --bold "COMMAND LINE"

        local box_width=$(( width - 4 ))
        (( box_width > 72 )) && box_width=72
        (( box_width < 40 )) && box_width=40

        gum style \
            --border rounded \
            --border-foreground "$ORANGE" \
            --foreground "$COTTON" \
            --width "$box_width" \
            --padding "1 2" \
            "${full_command:-unavailable}"

        printf '\n'

        if (( SCAN_STATUS == 0 )); then
            gum style --foreground "$CARAMEL" \
                "Resource counts come from the current lsof snapshot."
        else
            gum style --foreground "$LEMON" \
                "Re-run with sudo to inspect processes owned by other users."
        fi
    } | gum pager --soft-wrap
}

# ── Search resources ─────────────────────────────────

search_resources() {
    local table choices selected width
    width=$(term_width)

    table=$(resource_tsv "$RAW_FILE" "$PROCESS_USER" all)

    if [[ "$table" == "COMMAND"$'\t'*"PID"* ]]; then
        if [[ "$(printf '%s\n' "$table" | wc -l | tr -d ' ')" -le 1 ]]; then
            show_message \
                "NO RESOURCES" "$PROCESS_NAME · PID $PID" \
                "No open resources were found."
            return
        fi
    fi

    choices=$(
        printf '%s\n' "$table" |
            sed '1d' |
            python3 -c "$PY_SEARCH"
    )

    if [[ -z "$choices" ]]; then
        show_message \
            "NO RESOURCES" "$PROCESS_NAME · PID $PID" \
            "No open resources were found."
        return
    fi

    selected=$(
        printf '%s\n' "$choices" |
            gum filter \
                --header "SEARCH OPEN RESOURCES" \
                --placeholder "Find an IP, port, state, path or FD..." \
                --prompt "find ❯ " \
                --height 20 \
                --prompt.foreground "$MINT" \
                --match.foreground "$LIME"
    ) || return 0

    [[ -n "$selected" ]] || return 0

    show_message \
        "RESOURCE MATCH" "$PROCESS_NAME · PID $PID" \
        "$selected"
}


refresh_snapshot() {
    scan_process "$PID" "$RAW_FILE"
    SCAN_STATUS=$?

    if (( SCAN_STATUS == 1 )); then
        show_message \
            "PROCESS EXITED" "PID $PID" \
            "The process is no longer running."
        return 1
    fi

    read -r NETWORK_COUNT LISTEN_COUNT FILES_COUNT < <(
        resource_counts
    )

    return 0
}

# ── Main application ─────────────────────────────────

MENU=(
    "◉  Process overview"
    "⇄  IP sockets and connections (TCP / UDP)"
    "◎  TCP listening ports"
    "▤  Files, pipes and UNIX sockets"
    "⌕  Search resources by IP, port or path"
    "▦  All open descriptors (deduplicated)"
    "↻  Refresh snapshot"
    "←  Back to process selector"
    "✕  Quit"
)

menu_index() {
    local wanted="$1"
    local i

    for i in "${!MENU[@]}"; do
        if [[ "${MENU[$i]}" == "$wanted" ]]; then
            printf '%s' "$i"
            return 0
        fi
    done

    printf '%s' "-1"
}

while true; do
    clear 2>/dev/null || true
    banner

    selected=$(
        build_process_list |
            gum filter \
                --header "SELECT A PROCESS" \
                --placeholder "Search PID, username or command..." \
                --prompt "process ❯ " \
                --height 22 \
                --prompt.foreground "$MINT" \
                --match.foreground "$LIME"
    ) || exit 0

    [[ -n "$selected" ]] || exit 0

    PID=$(
        printf '%s\n' "$selected" |
            python3 -c '
import sys
parts = sys.stdin.readline().split()
print(parts[0] if parts else "")
'
    )

    [[ "$PID" =~ ^[0-9]+$ ]] || continue

    PROCESS_NAME=$(
        ps -p "$PID" -o comm= 2>/dev/null |
            sed 's#.*/##; s/^[[:space:]]*//; s/[[:space:]]*$//'
    )

    PROCESS_USER=$(
        ps -p "$PID" -o user= 2>/dev/null |
            sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
    )

    if [[ -z "$PROCESS_NAME" ]]; then
        show_message \
            "PROCESS EXITED" "PID $PID" \
            "This process no longer exists or cannot be inspected."
        continue
    fi

    RAW_FILE=$(mktemp) || exit 1

    if ! refresh_snapshot; then
        rm -f "$RAW_FILE"
        RAW_FILE=""
        continue
    fi

    while true; do
        clear 2>/dev/null || true

        gum style \
            --border double \
            --border-foreground "$GRAPE" \
            --foreground "$STRAWBERRY" \
            --bold \
            --align center \
            --width 76 \
            --padding "1 2" \
            "◈ INSPECT PROCESS ◈" \
            "$PROCESS_NAME · PID $PID"

        printf '\n'

        gum join --horizontal \
            "$(chip "$MINT" "IP sockets: $NETWORK_COUNT")" \
            "$(chip "$LIME" "TCP listeners: $LISTEN_COUNT")" \
            "$(chip "$ORANGE" "Other resources: $FILES_COUNT")"

        if (( SCAN_STATUS == 2 )); then
            printf '\n'
            gum style --foreground "$LEMON" \
                "⚠  Permission denied — re-run with sudo to inspect this process."
        fi

        printf '\n\n'

        action=$(
            printf '%s\n' "${MENU[@]}" |
                gum choose \
                    --header "↑↓ Navigate · Enter Select · Esc Back" \
                    --height 13 \
                    --cursor "➜ " \
                    --cursor.foreground "$STRAWBERRY" \
                    --selected.foreground "$LIME" \
                    --item.foreground "$COTTON"
        ) || break

        [[ -n "$action" ]] || continue

        choice=$(menu_index "$action")

        (( choice < 0 )) && continue

        case "$choice" in
            0)
                show_overview
                ;;

            1)
                show_table_report \
                    "NETWORK CONNECTIONS" \
                    "$PROCESS_NAME · PID $PID · IPv4 / IPv6" \
                    network
                ;;

            2)
                show_table_report \
                    "TCP LISTENING PORTS" \
                    "$PROCESS_NAME · PID $PID · LISTEN" \
                    listeners
                ;;

            3)
                show_table_report \
                    "FILES & OTHER RESOURCES" \
                    "$PROCESS_NAME · PID $PID" \
                    files
                ;;

            4)
                search_resources
                ;;

            5)
                show_table_report \
                    "ALL OPEN DESCRIPTORS" \
                    "$PROCESS_NAME · PID $PID" \
                    all
                ;;

            6)
                if ! refresh_snapshot; then
                    break
                fi

                ;;

            7)
                break
                ;;

            8)
                exit 0
                ;;
        esac
    done

    rm -f "$RAW_FILE"
    RAW_FILE=""
done