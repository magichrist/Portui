<div align="center">

# ✧ PORTS EXPLORER ✧

### *find out what's listening, who owns it, and what it has open*

<a href="https://pypi.org/project/ports-explorer/"><img alt="pypi" src="https://img.shields.io/badge/pypi-FF5C8A?style=flat-square&labelColor=140F1B"></a>
<a href="https://www.python.org/"><img alt="python" src="https://img.shields.io/badge/python-3.12%2B-B983FF?style=flat-square&labelColor=140F1B"></a>
<a href="https://textual.textualize.io/"><img alt="textual" src="https://img.shields.io/badge/built%20with-Textual-4ECDC4?style=flat-square&labelColor=140F1B"></a>
<a href="#license"><img alt="license" src="https://img.shields.io/badge/license-MIT-6BCB77?style=flat-square&labelColor=140F1B"></a>
<a href="#tests"><img alt="tests" src="https://img.shields.io/badge/tests-281%20passing-FFD93D?style=flat-square&labelColor=140F1B"></a>
<img alt="macos" src="https://img.shields.io/badge/macOS%20only-FF9F43?style=flat-square&labelColor=140F1B">

<br>

**`lsof` and `psutil`, but make them pretty.**

An interactive terminal explorer for macOS processes, network sockets and
open file descriptors. Six candy-coloured themes, keyboard-first, and it tells
you the truth when it can't see something.

</div>

---

## ✦ Screenshot

```
◈ PORTS EXPLORER  ·  PROCESS · NETWORK · FILES
 ← Back   Processes  ›  gvproxy · PID 5564
 Overview  Network  Listening  Files  UNIX  All FDs
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╸━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FD  FAMILY  PROTO  LOCAL ENDPOINT   REMOTE ENDPOINT  STATE
  9   IPv4    TCP    127.0.0.1:60953  —                LISTEN
 14   IPv6    TCP    ::1:5432         ::1:58888        ESTABLISHED
 21   IPv6    TCP    *:7700           —                LISTEN
 35   IPv6    TCP    *:6379           —                LISTEN

26/26 shown · 2 collapsed   ● complete   1-6 tabs  R refresh  / search
```

---

## ✦ Install

Requires **Python 3.12+** and macOS `lsof`.

```bash
brew install lsof
git clone https://github.com/you/ports-explorer
cd ports-explorer
python3 -m pip install -e .
```

Then:

```bash
ports-explorer
```

<details>
<summary><b>Dev setup &amp; running without installing</b></summary>

```bash
python3 -m pip install -e . pytest pytest-asyncio ruff
python3 -m ports_explorer      # works without the console script
```

No `gum`. No `fzf`. No touching your `.zshrc`.
</details>

---

## ✦ What's inside

<table>
<tr><td width="50%">

### ♡ Features

- **Process browser** — every visible process with PID, PPID, user, CPU, memory, runtime, status and full path. Search any of it, including the complete command line. Sortable, refreshable, and your selection survives a refresh.
- **Six resource views** — network connections, TCP listeners, files, UNIX sockets, and all open descriptors.
- **Field-scoped search** — `port=8080`, `state=LISTEN`, `proto=UDP`, `rport=443`, `lip=127.0.0.1`, `fd=10`, or just words. Quote phrases for paths with spaces.
- **Responsive** — the table sheds low-priority columns and the metric grid reflows as your terminal narrows.
- **Six themes** — candy-bright or properly moody, switchable live with `Ctrl+T`.

</td><td width="50%">

### ♡ Honesty

This is the part we actually care about.

- **Permissions are stated, never faked.** If macOS hid something, the status bar says *partial* or *permission denied*. A short list is never dressed up as a complete one.
- **Unavailable beats zero.** If a number can't be determined it renders as `—`, styled differently from a real `0`. CPU shows as unavailable on the first snapshot because `psutil` needs two samples; the second one is the first honest number.
- **Raw values survive parsing.** When an endpoint can't be normalised, the original `lsof` string is kept and shown rather than dropped.
- **Read-only, mostly.** Nothing closes a connection or deletes a file. The one exception — terminating a process — takes two deliberate keystrokes and never escalates privileges on your behalf.

</td></tr>
</table>

---

## ✦ Keyboard

<table>
<tr><td width="50%">

### ♡ Everywhere

| Key | Action |
|:--|:--|
| `↑` `↓` | Move through rows and menus |
| `Enter` | Open the selected process or resource |
| `←` | Back (except inside tables and inputs) |
| `Esc` | Close the search box, then go back |
| `/` or `Ctrl+F` | Focus search |
| `R` | Pause / resume auto-update |
| `T` / `Ctrl+T` | Cycle themes / open the picker |
| `?` | Keyboard help |
| `Q` | Quit (when not typing) |
| `Tab` | Move focus between controls |

### ♡ Process detail

| Key | Action |
|:--|:--|
| `1` | Overview |
| `2` | Network connections |
| `3` | TCP listening ports |
| `4` | Files and directories |
| `5` | UNIX sockets |
| `6` | All open descriptors |
| `←` `→` | Cycle tabs |
| `k` `k` | Terminate this process |

</td><td width="50%">

### ♡ Options

| Flag | Does |
|:--|:--|
| `--refresh SECONDS` | Auto-update interval (default `2`). `0` starts paused. |
| `--timeout SECONDS` | Timeout for one `lsof` run (default `20`) |
| `--theme NAME` | Start with a specific palette |
| `--debug` | Diagnostics to `~/.cache/ports-explorer/debug.log` |
| `--log-file PATH` | Diagnostics somewhere specific |
| `--no-color` | Disable colour |

### ♡ Auto-update

The screen refreshes itself every 2 seconds. `R` toggles it, and the status bar
always tells you which state you're in — so a frozen screen is never mistaken
for a live one.

- **Running → `R`** pauses. Status switches to `paused · R to resume`.
- **Paused → `R`** does a full refresh and puts you back on the timer.

One press gets you a fresh picture *and* resumes ticking.

</td></tr>
</table>

---

## ✦ Themes

Six palettes, switchable live with `Ctrl+T`. The colour names below are the real
token names from `config.py`, not decoration.

<table>
<tr>
<td><b>♨ Gummy</b> <i>(default)</i></td>
<td><code>#FF5C8A</code> coral</td>
<td><code>#FF9F43</code> amber</td>
<td><code>#FFD93D</code> yellow</td>
<td><code>#6BCB77</code> lime</td>
<td><code>#4ECDC4</code> mint</td>
<td><code>#4D96FF</code> blue</td>
<td><code>#B983FF</code> purple</td>
<td><code>#FFF6E5</code> cotton</td>
</tr>
<tr>
<td><b>☾ Midnight</b></td>
<td colspan="7"><code>#14121B</code> deep neutral surfaces with the candy accents toned down</td>
</tr>
<tr>
<td><b>ᐟ Tokyo Night</b></td>
<td><code>#BB9AF7</code> purple</td>
<td><code>#7DCFFF</code> cyan</td>
<td><code>#9ECE6A</code> lime</td>
<td><code>#C0CAF5</code> cotton</td>
<td colspan="3"><code>#1A1B26</code> surface</td>
</tr>
<tr>
<td><b>❦ Gruvbox</b></td>
<td><code>#FE8019</code> orange</td>
<td><code>#FABD2F</code> amber</td>
<td><code>#B8BB26</code> lime</td>
<td><code>#EBDBB2</code> cotton</td>
<td colspan="3"><code>#282828</code> surface</td>
</tr>
<tr>
<td><b>✧ Nebula</b></td>
<td><code>#FF7AD9</code> coral</td>
<td><code>#C39BFF</code> purple</td>
<td><code>#7DF9A6</code> lime</td>
<td><code>#5FE3E1</code> cyan</td>
<td><code>#EDEAF6</code> cotton</td>
<td colspan="2"><code>#0D0B1A</code> surface</td>
</tr>
<tr>
<td><b>☀ Daylight</b> <i>(light)</i></td>
<td><code>#C2185B</code> coral</td>
<td><code>#1565C0</code> blue</td>
<td><code>#2E7D32</code> lime</td>
<td><code>#1A1A1A</code> cotton</td>
<td colspan="3"><code>#FAFAFA</code> surface</td>
</tr>
</table>

---

## ✦ Terminating a process

This is the only thing in the app that changes anything, so it's deliberately the
hardest thing to do.

Press <kbd>k</kbd> once and the action **arms** — the status bar says so and
nothing has been signalled. Press <kbd>k</kbd> again to confirm. Any other key,
moving the selection, or waiting 5 seconds cancels it.

```
  k  ─────▶  ARMED.  status bar: "TERMINATE armed · k to confirm in 5s"
              │
              ▼  k again
        SIGTERM sent, waiting 2s
              │
              ├── gone ──────────▶  "exited after SIGTERM"
              │
              └── still alive ───▶  "ignored SIGTERM"
                                       │
                                       ▼  k again
                                   SIGKILL sent
```

`SIGKILL` is never sent automatically. Three guardrails live in
`services/kill_process.py` rather than the UI, so no new caller can bypass them:
PID 0, PID 1 and Ports Explorer's own PID are always refused, and the app never
runs `sudo` for you.

<details>
<summary><b>⚠ Read this before you use it</b></summary>

`k` can kill anything you're able to signal — including your terminal, your
editor, or the process you're running Ports Explorer in. No application can
prevent that. Kill from a process you have actually looked at.

</details>

---

## ✦ Deduplication

The same socket is often reachable through several file descriptors, which makes
for a noisy table. Ports Explorer collapses these in **one centralized, tested
service** that every view uses identically.

The key is exactly:

```
(command, pid, user, name, state)
```

**The file descriptor is deliberately excluded.** So:

- One socket behind three FDs becomes one row showing the first FD — and the
  other FDs are preserved in an `aliases` field, shown in the detail view. No
  diagnostic information is thrown away.
- Different endpoint names stay separate rows.
- Different connection states stay separate rows.
- Two connections that merely *share a port* are never merged — the full endpoint
  string is part of the key.

The detail screen reports raw and unique counts, so you always know how many rows
collapsed.

---

## ✦ Architecture

```
src/ports_explorer/
  __main__.py            CLI entry point, logging setup
  app.py                 Textual app shell, navigation, screen stack
  models.py              Normalized records; no Textual or psutil imports
  config.py              Themes, design tokens, settings
  collectors/
    processes.py         psutil discovery, metadata, CPU sampling
    lsof.py              lsof execution and structured field parsing
  services/
    deduplication.py     The single dedup service and category filters
    kill_process.py      SIGTERM / SIGKILL signalling and its guardrails
    resource_search.py   Tokenized search and explicit filters
  screens/
    process_list.py      Process browser
    process_detail.py    Overview, tabs, per-process workers
    resource_browser.py  Resource search and single-resource detail
    theme_picker.py      Live palette switcher
    common.py            Header, banner, responsive + auto-update + kill mixins
  widgets/
    resource_table.py    Reusable sortable, filterable resource table
    status_bar.py        Counts, health badge, contextual key hints
  styles/app.tcss        The visual system
```

### Notes on the tricky parts

**`lsof` is the authoritative source for files, descriptors and socket state**,
because macOS permission rules routinely prevent full connection enumeration
through `psutil`. It's always invoked as an argument array — never through a
shell — with structured field output (`-FpcfntPTDismlug`) rather than a
whitespace-aligned table. stdout and stderr are captured separately so a
permission warning is reported as *data* instead of being mixed into results.

**Socket state arrives as `TST=ESTABLISHED`, but the `T` field also carries
unrelated queue counters** (`TQR=0`, `TQS=0`). Only the `ST` sub-field is
interpreted, and state is stored per file record so it can never leak into the
next descriptor — which was a real bug in the original Bash parser.

**CPU sampling needs a stable handle.** `psutil` diffs against the previous
sample held on a `Process` object, so constructing a fresh one per tick reports
a permanent `0.0%`. The collector caches one handle per PID, invalidates it if
the PID is recycled (`create_time` changes), and prunes dead entries. The first
sample reports *unavailable* rather than a fake zero.

**Repainting on a timer is deliberately cheap.** Tables compare their own row
keys against the current selection and only rebuild when they differ; the metric
grid repaints existing tiles in place. Row keys are derived from the dedup key,
not `id(record)`, so a refresh doesn't throw away your cursor. Both avoid
unmounting and remounting widgets on every tick.

**The auto-update timer uses `set_interval`, not `set_timer`** — `set_timer` is
one-shot and would fire exactly once before going silent. Resuming arms a fresh
timer rather than calling `Timer.resume()`, which would replay every missed tick
at once and storm the UI.

---

<a name="tests"></a>

## ✦ Tests

```bash
python3 -m pytest -q        # 281 tests
python3 -m ruff check .
python3 -m ruff format --check .
```

No test requires elevated privileges. `lsof` output is tested against fixture
files in `tests/fixtures/`, and the handful of tests that exercise the real
binary skip themselves cleanly when it isn't available.

Coverage includes structured `lsof` parsing, TCP state association,
IPv4/IPv6/TCP/UDP/listener combinations, paths with spaces, duplicate FDs,
identical endpoints with different states, empty and partial output, permission
errors, processes that exit mid-collection, CPU sampling and PID recycling,
periodic refresh, every search and filter path, deduplication consistency across
views, the two-key terminate contract, keyboard navigation, and headless UI smoke
tests.

Two tests genuinely spawn a disposable child process and kill it, to prove the
terminate path really works rather than just returning a cheerful message.

---

## ✦ Known limitations

- **Permissions are the hard limit.** macOS hides other users' descriptors from
  unprivileged processes. The UI reports *partial* or *permission denied* and
  tells you what's missing — it never presents a partial list as if it were
  complete. To see everything, relaunch elevated yourself:
  ```bash
  sudo ports-explorer
  ```
  That's your decision, not the app's.
- **CPU reads as unavailable on the very first snapshot**, because `psutil` needs
  two samples. The second snapshot is the first real number.
- **Endpoint parsing** handles `host:port`, `*:port`, `[::1]:port`,
  `local->remote`, and IPv4-mapped IPv6. When a value can't be normalised the raw
  `lsof` string is always preserved and shown, and the resource is flagged as raw
  rather than dropped.
- **File mode and size** appear only when `lsof` reports them; otherwise *unavailable*.
- **Environment variables are never read.** They commonly contain secrets.
- **Responsive layout is width-based.** Textual's TCSS has no `@media` support, so
  breakpoints are applied by toggling classes from resize handlers.

---

## ✦ Troubleshooting

| Symptom | What's happening |
|:--|:--|
| `lsof: not found on PATH` | `brew install lsof`, then press `R` |
| *permission denied* | The process belongs to another user. Accept the partial result, or relaunch under `sudo` yourself |
| *process exited* | It terminated between selection and collection — go back and pick another |
| A table looks empty | Press `Esc` to clear any filter, then `R`. The status bar always shows `shown / total` |
| Colours look wrong | `--no-color`, or set `NO_COLOR=1` |
| Want the full picture | `ports-explorer --debug`, then read `~/.cache/ports-explorer/debug.log` |

---

<a name="license"></a>

## ✦ License

MIT.

`port-ps.sh`, the original Bash/Gum implementation this replaces, is kept in the
repository for reference.

<div align="center">

made with ♡ for anyone who's ever wondered *what is actually listening on 3000*

</div>