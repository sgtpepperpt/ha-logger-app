# Logger for Home Assistant

The Home Assistant client of **Logger**, a self-hosted personal health logger (food, glucose,
medication, symptoms, tasks): its numbers as sensors, its quick entries as buttons, three actions,
one event. Local polling over the LAN; no cloud, no account.

- Domain `logger_app` (Home Assistant's own `logger` integration owns `logger`); display name *Logger*.
- Needs Home Assistant **2026.2** or newer, HACS, and a Logger answering `GET /api/about` with `api: 1`.

## Install

Home Assistant reaches Logger on its **LAN port**, `http://<host>:4173` (no sign-in there). Never the
public address: it answers with a sign-in page and the setup says *Failed to connect*. Logger →
Settings → Home Assistant shows the address to paste.

1. HACS → ⋮ → **Custom repositories**.
2. Repository `https://github.com/sgtpepperpt/ha-logger-app`, type **Integration** → **Add**.
3. Open *Logger* in HACS → **Download**.
4. Restart Home Assistant.
5. Settings → Devices & services → **Add integration** → *Logger*.
6. Paste the address: `http://<host>:4173`.

One device, *Logger*. Updates: a new release shows in HACS as an update → **Update** → restart.
Only releases are offered, never a bare commit.

## Entities

Entity ids are `<platform>.logger_<key>` (the quick-entry buttons: `button.logger_<name>`); unique
ids `<instance_id>_<key>` — Logger's `instance_id`, so a new address or a restored backup is the same
Logger with the same entities. **Logger's null is `unknown`, never 0**: no calorie goal, nothing
scheduled today, Tasks off, no CGM, a key this Logger doesn't send.

| Platform | Key | From `/status` | Notes |
| --- | --- | --- | --- |
| sensor | `free_kcal` `eaten_kcal` `planned_kcal` `goal_kcal` `banked_kcal` | `food.*` | kcal, measurement; `free` = Logger's Food strip *free* (the bank never in it) |
| sensor | `glucose` | `glucose.mgdl` | mg/dL (HA can show mmol/L); attrs `direction` `at` `source` `low` `high` `minutes_ago`; `unknown` once the reading is older than a day |
| sensor | `medication_overdue` `medication_due` `care_overdue` `care_due` | slot lists | how many slots; attr `rows` = `[{name, words, due, period}]` |
| sensor | `medication_next` | `medication.next.due.from` | timestamp; attrs `name` `words` |
| sensor | `steps_today` | `steps.today` | measurement (resets daily); attrs `day` `as_of` |
| sensor | `tasks_overdue` `tasks_today` `deliveries_overdue` `deliveries_today` | `tasks.*` | attr `rows` = `[{summary, due, words, list}]` (Logger ships at most 8 rows in all) |
| sensor | `tasks_waiting` | `tasks.waiting` | Tasks writes queued for Nextcloud |
| sensor | `problems` | `GET /alerts` | how many; attr `texts`; only with the *alerts* option on |
| sensor | `refill_soon` | `refill.n` | attr `rows` = `[{name, days_left, refill_by, approx}]`; `unknown` while no stock is counted |
| sensor | `sensor_day` · `sensor_ends` | `glucose.sensor` | the CGM sensor's day N (attrs `of` `started` `ends` `serial` `via` `expired` `words`) · when it ends |
| binary_sensor | `medication_overdue` `tasks_overdue` | counts > 0 | problem |
| binary_sensor | `glucose_low` | `mgdl < low` | safety; `unknown` while the reading is stale |
| binary_sensor | `glucose_stale` | no reading for over 10 min | problem (Logger's own rule) |
| binary_sensor | `tasks_read_only` | `tasks.read_only` | diagnostic |
| binary_sensor | `sensor_ending_soon` | `glucose.sensor.ending_soon` | problem; attrs `ends` `expired` |
| button | `quick_<id>` | `GET /quick` | one per quick entry, named and iconed as in Logger; a press fires it |
| button | `tablet_wake` · `refresh` | | wake the kitchen tablet · read Logger again now (always pressable) |

- One poll = `GET /status?tz=` every *scan interval* (+ `GET /alerts` with *alerts* on). Logger
  unreachable or a 5xx: every entity *unavailable* until a poll answers again.
- Quick entries: read on the first poll and every 10th after it (Logger has no change counter for
  them), at once after **Refresh**, and after a press Logger answers *no such entry*. One added in
  Logger gets its button; one deleted loses it (removed, not left unavailable).

## Actions

| Action | Data | Response |
| --- | --- | --- |
| `logger_app.fire` | `entry` (id or exact name), `text?`, `note?`, `kcal?`, `at?`, `config_entry_id?` | `{words, ids, kind, name}` |
| `logger_app.shopping_add` | `name`, `qty?`, `shop?`, `due?` (a date), `config_entry_id?` | `{item, queued}` — `queued: true` = waiting for Nextcloud, still done |
| `logger_app.tablet_wake` | `config_entry_id?` | `{outcome, words}` |

- The overrides follow the quick entry's kind exactly as in Logger: `text` (a diary note's text, a
  planned kcal's label, a shopping add's item), `note`, `kcal`, `at` (backdates a diary note, a
  check-in, planned kcal; a time without a zone is on the entry's time zone). One the kind has no
  use for is refused.
- A refusal is an error carrying Logger's sentence, nothing written: a tick already taken, an
  override the kind doesn't take, Tasks read-only, a tablet not set up.
- Every success — an action or a button press — fires the event **`logger_app_fired`**
  `{kind, name, words}` (`kind` = the quick entry's kind, `shopping_add` or `tablet_wake`).
- `config_entry_id` only matters with two Loggers set up; with one it is found.

```yaml
action: logger_app.fire
data:
  entry: Café
  note: with Ana
response_variable: r          # r.words = "Diary: Café"
```

A press's sentence as a phone notification:

```yaml
triggers:
  - trigger: event
    event_type: logger_app_fired
actions:
  - action: notify.mobile_app_<phone>
    data:
      title: Logger
      message: "{{ trigger.event.data.words }}"
```

## Options

Settings → Devices & services → *Logger* → **Configure**: *scan interval* 15–600 s (60), *time zone*
(Home Assistant's by default — it decides what "today" is in Logger's answers and writes), *alerts*
on/off (the `problems` sensor). **Reconfigure** = a new address for the same Logger (its
`instance_id` is checked).

## When it goes wrong

| Home Assistant says | Means |
| --- | --- |
| *Failed to connect* (`cannot_connect`) | wrong address, the Pi down, or the public address (its sign-in page): use `http://<host>:4173` |
| `unsupported_api` | this Logger's `api` is newer than the integration: update it in HACS |
| *Already configured* | this Logger (its `instance_id`) is set up already; a new address = **Reconfigure** |
| setup error *answers as another Logger* | the address now reaches a different Logger (e.g. the dev copy): Reconfigure with the right one |
| entities *unavailable* | the last poll failed (network, Logger restarting); back with the next answer |
| an entity *unknown* | Logger said null — not 0, not an error |
| a button / action error with a sentence | Logger refused it, nothing written |

- *Download diagnostics* (the integration's ⋮): the entry, `/about`, the last `/status`, `/quick`,
  `/alerts`.
- Every press and action is in Logger's Audit tab as client *ha* (the integration sends
  `X-Logger-Client: ha`).
- A copy of Logger restored elsewhere answers with the same `instance_id`: never add a dev copy of
  production to the same Home Assistant.

## Development

```sh
python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Tests run against Home Assistant 2026.2.3 (the minimum) and, in CI, the newest release too. The
contract with Logger is `tests/fixtures/ha_contract.json` — a copy of Logger's own; see `CLAUDE.md`.

MIT licensed.
