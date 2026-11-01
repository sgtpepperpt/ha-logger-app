# Logger for Home Assistant

The Home Assistant client of **Logger**, a self-hosted personal health logger (food, glucose,
medication, symptoms, tasks): its numbers as sensors, its quick entries as buttons, three actions,
one event — and Logger's hands in Home Assistant: the rules and scenes made in Logger run here
(*Rules and scenes*), so Home Assistant is never edited by hand; and Logger by voice through Assist
(*Voice*). Local polling over the LAN; no cloud, no account.

- Domain `logger_app` (Home Assistant's own `logger` integration owns `logger`); display name *Logger*.
- Needs Home Assistant **2026.2** or newer, HACS, and a Logger answering `GET /api/about` with `api: 1`.
  Rules and scenes need a Logger whose `/about` lists `features: ["rules"]`; with an older one the
  integration does exactly what it did before (no other call, no webhook, no scene). The calendar
  entity needs `"calendar"` there; voice (*Voice*: Assist's commands, answers and sentences),
  `"voice"`.

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
| scene | `scene.logger_<name>` | `GET /ha/scenes` | one per Logger scene, named and iconed as there; see *Scenes* |
| calendar | `calendar` | `calendar` · `GET /calendar/events` | Logger's calendars as one (read-only): on while an event is on (all-day ones all day), its `event` the one under way else the next (`message`, `start_time`, `location`…; attr `today` = how many today); the calendar panel and `calendar.get_events` read Logger's range; *unavailable* while Logger's Calendar is off; only with `features: ["calendar"]` |

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

## Rules and scenes

Made in Logger → Settings → Home Assistant; run here. With a Logger offering them (`features:
["rules"]`), after every poll that answered and on every nudge from Logger, one *cycle* (never two
at once; a failing one is logged and never makes the entities unavailable):

1. `GET /ha/actions` — what Logger's rules want done (*Logger → HA*), oldest first, each handed out
   once: run in order, each result posted back (`POST /ha/actions/<id>/result`, Logger's Recent).
2. `rules_rev` changed → `GET /ha/rules?kind=from_ha` — the *HA → Logger* rules, each registered as
   a Home Assistant trigger (below); only what changed is touched: a rule whose trigger is the
   same keeps its registration (a pending `for` runs on), one turned off or deleted is removed.
3. `scenes_rev` changed → `GET /ha/scenes` — the scene entities follow.
4. `PUT /ha/catalog` — what Home Assistant has, so Logger offers names, never ids: when Logger asks,
   at start, every 24 h, and 10 s after an entity/device/area registry change that changes it.

| Action Logger queues | Home Assistant runs | Words back |
| --- | --- | --- |
| `scene` · `script` | `scene.turn_on` · `script.turn_on` | *Cinema on* · *Good night started* |
| `light` on / off / flash | `light.turn_on` (`brightness_pct`, `color_temp_kelvin` when set) / `light.turn_off` / `light.turn_on` `flash: long` | *Kitchen flashed* |
| `switch` on / off | `switch.turn_on` / `turn_off` | *Kettle off* |
| `announce` | a satellite: `assist_satellite.announce`; a media player: `tts.speak` with Logger's engine, else the first `tts.*` | *Announced on Kitchen speaker* |
| `notify` | that `notify.<target>` with `title`, `message` | *Sent to Pixel 9* |
| `logger_scene` | a Logger scene's states (*Scenes*) | *Evening: 3 set, 1 not in Home Assistant* |

- An action fails — no such service, an entity Home Assistant hasn't got or one unavailable, a
  refusal, 30 s gone — with a short sentence, which Logger shows. Texts arrive filled in by Logger
  and go out as they are: no templates.
- **HA → Logger rules** (a button's press, a person entering or leaving a zone, an entity's state)
  are registered with Home Assistant's own automation triggers: `device` = the trigger config Home
  Assistant listed in the catalog — only ever a device trigger (anything else Logger hands back is
  refused: *Not a device trigger*), `zone` = `{person, zone, enter|leave}`, `state` = `{entity_id,
  to, from?, for?}`. When one fires, Logger is told (`POST /ha/rules/<id>/fire` `{at, event}`) and
  runs the rule itself — quiet hours, gaps, once a day are Logger's. No answer from Logger: the same
  call (same `event`, so it runs once) again after 30 s, 90 s, 180 s, then given up (logged).
- A trigger Home Assistant refuses (a device gone, its integration still loading) is the rule's
  *problem* in Logger (`POST /ha/rules/<id>/state`), tried again every 10 cycles; cleared once it
  registers.
- **The webhook** — the integration registers `POST /api/webhook/<id>` (a random id kept in the
  entry) and tells Logger its address in the catalog: Home Assistant's *internal* URL only,
  `local_only` (ignored from outside the LAN), no auth (Home Assistant webhooks have none: the id is
  the secret). It carries a nudge, never data — `{logger, reason}`; a nudge from another `logger` is
  ignored, any other runs a cycle at once (`reason: catalog` also sends the catalog). Everything else
  is fetched from Logger. Home Assistant without an internal URL, or Logger refusing it: no nudges —
  the poll carries everything one scan interval later.

### Scenes

Logger owns them; Home Assistant shows one `scene.logger_<name>` each (unique id
`<instance_id>_scene_<id>`), activatable from a dashboard, an automation or Assist. Activating one
applies its states entity by entity: lights on (brightness, colour temperature, a transition when
given) or off, switches, media players (on, volume, source; off — a speaker that can't be: stopped).
An entity Home Assistant hasn't got is skipped and counted; one that fails is an error once the rest
is applied. A scene added in Logger appears with the next cycle that sees the new `scenes_rev`, one
deleted goes (removed, not left unavailable); a rename keeps the entity id. They work while Logger
restarts (their states are here); they leave with the integration.

## Voice

Say it to Assist — a voice satellite, the phone, the Assist dialog — and Logger does it: *regista um
café*, *comprar leite amanhã*, *tomei a B12*, *lembra-me de ligar à mãe amanhã às 9*, *o meu peso é
62,9*; *quanto tenho livre hoje*, *o que falta*, *qual é a próxima tarefa*. Portuguese (pt-PT) and
English. Everything stays in the house: Home Assistant hears it, Logger gets the words. Needs a
Logger whose `/about` lists `"voice"`; Logger's own setup words: Settings → Home Assistant →
*Setup* → *Voice*.

**The sentences.** Assist only acts on sentences it knows. With **Configure → Voice sentences** on
(off by default: the file holds Logger's words — foods, products, shopping items, task lists,
symptoms, Money's items), the integration writes `custom_sentences/pt/logger.yaml` and
`custom_sentences/en/logger.yaml` into Home Assistant's config folder — and, when `/share` exists
(Home Assistant OS), the same files into `/share/speech-to-phrase/custom_sentences/`, where the
Speech-to-Phrase add-on reads them. Built from Logger's `GET /command/vocab` (its 200 usual foods,
its meals, products, task lists, shops and the shopping list's usual items, symptoms, portion words)
once Home Assistant has started, every 24 h and on **Refresh**; a file is rewritten only when its
words changed (Assist is reloaded; Speech-to-Phrase learns them again on its next command). Off
removes the files; so does removing the integration. A file at that path the integration didn't
write is never touched (diagnostics: `kept`).

- **Speech-to-Phrase** (fast, under a second on a Pi 4; fixed phrases only): it can say exactly what
  the file lists — a food by its name, a dose by its product, an item, a number in a range. It
  can't take free text: a new task's title or a note needs Whisper (or the app). The add-on on
  Home Assistant OS needs nothing more (it learns changed words within 5 minutes, or on the next
  command). Speech-to-Phrase in its own container: give it Home Assistant's `custom_sentences`
  folder once (`--custom-sentences-dir`, mounted read-only) and run it with `--retrain-on-connect`,
  as the add-on does.
- **Whisper** (any words; ~8 s on a Pi 4): the same file makes Home Assistant hand the whole sentence
  to Logger after a command word (*regista*, *comprar*, *lembra-me de*, *nota* …).

| Intent | Sentences (pt · en) | Does |
| --- | --- | --- |
| `LoggerCommand` | *regista / comi / bebi / tomei / fiz …* · *log / i ate / i had / took …* | a food, a dose, a reading, a rating, a spend |
| | *comprar / falta / preciso de comprar …* · *buy / we need …* | the shopping list |
| | *lembra-me de / tarefa …* · *remind me to / task …* | a task |
| | *nota …* · *note …* | a diary note |
| | *sinto / tenho / estou com …* · *i feel / i have …* | a symptom check-in |
| | *peso / temperatura / febre / tensão …* · *weight / temperature / blood pressure …* | weight, temperature, pressure (*120 por 80*) |
| | *gastei / paguei …* · *spent / paid …* | Money |
| `LoggerFreeToday` | *quanto tenho livre hoje* · *how much is free today* | today's free kcal (`/status`) |
| `LoggerWhatsDue` | *o que falta* · *what's due* | doses and care owed, tasks overdue and today |
| `LoggerNextTask` | *qual é a próxima tarefa [da lista …]* · *what's my next task [in …]* | the first task due today |

- **One brain**: what is said goes to Logger's command line whole — the same reading as its ⌘K
  palette. Logger reads it first (`GET /command/parse`, nothing written); only a reading Logger is
  sure of, of the kind the sentence asked for (a *comprar* sentence is never written as a task), is
  written: `POST /command/run {text, intent, source: voice}`. Anything less is said back with
  nothing written: what is missing (*Diz outra vez com a quantidade*), Logger's warning (*already
  taken*), its guess (*Não tenho a certeza: …?*). What a voice can't say is put back first: the
  colon of *tarefa:* and *nota:*, *120/80*, a measure's number.
- **The answer**: English = Logger's own words (*Logged Café 1 chávena*); Portuguese = built from
  Logger's reading (*Registado: Café 1 chávena.*), for a pt-PT voice (Piper). A write fires
  `logger_app_fired` `{kind: command, name: <Logger's intent>, words}` and is in Logger's Audit tab
  as client *ha*, source *voice*.
- An LLM conversation agent with the Assist API sees the four intents as tools (`LoggerCommand`
  takes `text` and `family`).

## Options

Settings → Devices & services → *Logger* → **Configure**: *scan interval* 15–600 s (60), *time zone*
(Home Assistant's by default — it decides what "today" is in Logger's answers and writes), *alerts*
on/off (the `problems` sensor), *voice sentences* on/off (off; see *Voice*). **Reconfigure** = a new
address for the same Logger (its `instance_id` is checked).

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
| Logger: *Home Assistant hasn't checked in* | no catalog for a day: Home Assistant can't reach Logger, or the integration was set up before Logger had rules — the feature is read at setup: reload the entry (or restart Home Assistant) after updating Logger |
| Logger: *push failing — polling* | Home Assistant has no internal URL (Settings → System → Network) or Logger refused it: actions still run, a scan interval later |
| a rule's problem in Logger: *Home Assistant refused the trigger: …* | its device or entity is gone, or its integration was still loading: tried again every 10 cycles, cleared once it registers |
| a scene activation error *Logger scene — …* | an entity of it refused (the words say which); the rest was applied |
| Assist: *Sorry, I couldn't understand that* to a Logger sentence | *Voice sentences* is off, Logger has no `voice` yet (update it, then reload the entry), or the phrase isn't in the file (a food Speech-to-Phrase hasn't learnt: Refresh, or wait a day) |
| Assist answers *Não percebi* / *Not sure …* | Logger read it as something else, or isn't sure: nothing written; say the full name |
| diagnostics → `voice.kept` | a `logger.yaml` the integration didn't write is in the way: move it, then Refresh |

- *Download diagnostics* (the integration's ⋮): the entry, `/about`, the last `/status`, `/quick`,
  `/alerts`; with rules: the webhook (registered, its URL, Logger's verdict), the last cycle and its
  error, actions run, triggers registered / refused / fired, the revs, scenes, the last catalog and
  its counts. The webhook id shows only its first 4 characters. With voice: the sentences' last
  build (files written / unchanged / kept, how many names each list holds, an error).
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
