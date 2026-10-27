# ha-logger-app — rules for AI sessions

Home Assistant custom integration `logger_app` (display name *Logger*), installed via HACS as a
custom repository from the PUBLIC repo `sgtpepperpt/ha-logger-app` (MIT). It is the HA client of
the user's own app **Logger** (private repo, local checkout `../logger`, beside this one).
**Derived repo: it answers to Logger and is managed FROM Logger's sessions** (user, 2026-10-27) —
Logger's contract (`GET /about` `api`, `backend/tests/fixtures/ha_contract.json`) leads; a Logger
change that touches the contract updates this repo in the same piece of work, copies the fixture
here and bumps `manifest.json` `version`. A `ship` / `ship both` in a Logger session ships this repo
too when it has pending work. Spec: Logger's
`docs/feature-plan-2026-10-pass3.md` → *F30*, `-pass4.md` → *F39* (rules + scenes). User doc: Logger's `docs/home-assistant.md` (mirrors
this README's tables — keep them consistent; when they must differ, tell the user what to change
there).

## Decided

- **Min HA 2026.2** (user's HA = 2026.2.3 and can't upgrade; decided 2026-10-26). Consequences,
  all deliberate — revisit only when the user's HA moves:
  - Python 3.13 syntax (no 3.14-only features); `voluptuous` (HA ≥ 2026.9 aliases it to probatio:
    works unchanged).
  - `async_setup_entry` / `async_unload_entry` return a **bool** (HA < 2026.11 refuses `None`;
    custom integrations may keep `True` after).
  - Options = `OptionsFlowWithReload`, **no update listener** (listener + reload methods = error
    from HA 2026.12).
  - `DeviceInfo(...)` with keyword args only (TypedDict ≤ 2026.10, dataclass from 2026.11).
  - `brand/icon.png` (256) + `icon@2x.png` (512) show from HA 2026.3; 2026.2 shows a placeholder.
  - Translation abort keys kept locally (`reconfigure_successful` …; central from HA 2026.10).
  - Tests run on BOTH: `.venv` (py3.13, `requirements_test.txt` pins
    pytest-homeassistant-custom-component 0.13.316 = HA 2026.2.3) and `.venv-latest` (py3.14,
    newest phcc). CI does the same (`test.yml` matrix).
  - When the user upgrades HA: raise `hacs.json` `homeassistant`, the phcc pin, the CI python;
    then the shims above may go.
- Domain `logger_app` (HA core owns `logger`). `integration_type: service`, `iot_class:
  local_polling`. No `categories` yet: hassfest's published image (= the last HA release,
  2026.10.x on 2026-10-27) rejects the key; add `"categories": ["health_wellbeing"]` once that
  image is ≥ 2026.11 (the HACS action only warns while it is missing).
- No HACS default-list submission, no brands-repo PR.
- `dependencies: ["webhook"]` only; no `after_dependencies` (hassfest allows `device_automation`,
  `zone`, `person`, `script`, `automation` and every platform).

## The contract (the two repos meet only here)

- `tests/fixtures/ha_contract.json` = an exact COPY of Logger's
  `backend/tests/fixtures/ha_contract.json`. Never edit it here. Logger changed it → copy it over
  (`cp …/logger/backend/tests/fixtures/ha_contract.json tests/fixtures/`), run the tests, adapt,
  release.
- `GET /api/about` → `{name, api, instance_id, code, features}`; `features` (optional, never an `api`
  bump): `"rules"` = the `/ha/*` routes. Read at setup only. Without it: no `/ha/*` call, no
  webhook, no scene.
- `/ha/*` bodies = the fixture's `ha_*` request shapes exactly, no `tz`. A result's 404/409 = fine.
  A fire is retried with the same `event` after 30/90/180 s on no answer or a 5xx; a 404 refetches
  the rules. `/state` only when the outcome differs from `problem`. A device trigger's `id` =
  `catalog.trigger_id` over its identity keys only (Logger keys rules by it: never hash the whole
  config). `const.API_MAX` = the highest `api` this
  integration reads (now 1); a higher one is refused (`unsupported_api`), lower ones are fine.
  Logger bumps `api` only on a breaking change to a shape read here; added keys never bump it.
- `instance_id` = the config entry's unique_id, the device identifier, every entity's unique_id
  prefix (`<instance_id>_<key>`). Never the URL. Setup refuses an address answering another one.
- Client rules (`api.py`): every request `X-Logger-Client: ha`; NEVER an `Origin` header; writes =
  JSON + `Content-Type: application/json` + `tz` in the body; 2xx = done (202 = queued for
  Nextcloud); a refusal = JSON `error` (or `words`) → `LoggerRefused` (incl. the tablet's own
  502); no answer / HTML / proxy 5xx → `LoggerConnectionError`.
- **Logger's null = HA `unknown`, never 0.** Value helpers in `coordinator.py` return None for
  null, missing and mistyped alike.
- Glucose rules = Logger's widgets (`frontend/src/lib/glucose/glucoseNow.ts`): stale > 10 min,
  hidden > 24 h; ages counted against `/status.as_of`.
- Quick entries have no Logger change kind → `/quick` re-read every `QUICK_EVERY` polls (+ on
  Refresh, + after a 404 press). If Logger gains a `quick` kind in `/status.changes`, switch.
- `/status.bag` (today's planned food) is NOT in the contract fixture yet → not read (follow-up:
  fixture first, then a `bag_kcal` sensor).

## Code map

`api.py` client + errors · `coordinator.py` poll + shared writes (fire / shopping / wake, the
`logger_app_fired` event) + value helpers · `entity.py` device · `sensor.py` / `binary_sensor.py`
description tables · `button.py` dynamic quick buttons (registry cleanup) · `services.py` actions
(registered once in `async_setup`) · `config_flow.py` user / reconfigure / options ·
`diagnostics.py` · `rules.py` the F39 cycle (queue → actions → rules → scenes → catalog; one at a time,
entry background task, never fails the poll) · `actions.py` runs a `do` or scene states ·
`triggers.py` `when` → HA trigger · `catalog.py` the PUT body + webhook URL · `webhook.py` the nudge
(`entry.data["webhook_id"]`) · `scene.py` dynamic Logger scenes, registry cleanup as `button.py` ·
`translations/en.json` = the source (no `strings.json` in custom
integrations); `pt.json` mirrors it (pt-PT).

- English entity names slug to their keys (entity ids `sensor.logger_<key>`) — keep it so.
- Triggers: `trigger:` config → `cv.TRIGGER_SCHEMA` → `async_validate_trigger_config` → one
  `async_initialize_triggers` per rule (failures only via `log_cb`, it never raises); never pass
  `home_assistant_start` (deprecated 2026.10).
- Work that resolves integrations (trigger registration, the catalog build) runs `_shielded`:
  cancelling HA's loader mid-resolution leaves that lookup hanging forever.
- Scenes: media players `turn_on`/`turn_off` only when supported, else `media_stop`.
- Device rules: only a `device` config with `domain` + `device_id`; anything else is refused before
  HA validation (*Not a device trigger*); `variables`/`enabled` dropped; `for_s` must be a number.
- Registrations kept per rule as id + built config (`triggers.config_key`): a refetch keeps
  unchanged ones (a pending `for:` runs on), re-registers or removes only changed, disabled or gone.
- hassfest refuses URLs and HTML in translation strings: the LAN example goes through the
  `{lan}` description placeholder.

## Verify

- `.venv/bin/pytest` and `.venv-latest/bin/pytest`; `.venv/bin/ruff check . && .venv/bin/ruff format --check .`
- hassfest + HACS action run in CI only (`validate.yml`); the HACS action also checks the GitHub
  repo's description, topics and issues.
- `conftest.mock_ha` answers `/ha/*` from the fixture with an empty queue and without the zha
  device rule (HA would try to import zha); pass `rules=` to include it.
- Live shapes: the Logger dev stack at `http://localhost:5173/api` — **GET only**
  (`/about`, `/status?tz=…`, `/quick`, `/alerts`). Its database is a copy of production: never
  POST/PUT/DELETE there, never add it to the user's HA (same `instance_id` as prod).

## Release (HACS offers releases only — `hide_default_branch: true`)

1. Bump `custom_components/logger_app/manifest.json` `version` — CalVer `YYYY.M.N` (release
   month, N from 0 within the month).
2. The user's `finalise` / `ship` commits + pushes (below).
3. `gh release create <version> --title <version> --notes "…"` (tag = the version) — a GitHub
   write: only with the user's explicit approval for that occasion.
4. HA: HACS → *Logger* → Update → restart HA.

## Git — the user's finalise rule (applies here)

No `git add` / commit / push between finalises (the repo + remote exist: `origin` = `git@github.com:sgtpepperpt/ha-logger-app.git`, default branch `main`). Commit + push
happen ONLY when the user's message is `finalise` / `finalize`, exactly `ship` or `ship both`
(also as the message's last standalone sentence). One finalise = ONE commit with everything since
the previous one; verify first (tests + ruff), docs in the same commit. Never rewrite history or
force-push without explicit approval. Wrapping up with uncommitted changes → say that `ship` would
commit + push them.
