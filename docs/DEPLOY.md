# Deploying job-scout as a container

The pipeline is long-running and mostly idle, which makes it a better fit for
an always-on NAS than a workstation. The model server, however, needs a GPU
and usually lives elsewhere -- so the container wakes that machine before each
run rather than assuming it is up.

## Layout

Two services from one image, both on host networking:

| Service | Command | Why |
|---|---|---|
| `scheduler` | `schedule loop --all` | Runs the pipeline on the configured slots |
| `web` | `web --port $JOB_SCOUT_PORT` | The dashboard |

Host networking is not cosmetic: a wake-on-LAN magic packet is a layer-2
broadcast and does not survive Docker's bridge NAT. It also means the
dashboard binds a port on the host directly, so pick one nothing else uses.

Both services bind-mount `./data`, so the SQLite databases stay visible in the
NAS share and are picked up by its backup job.

## Configuration

Two places, deliberately split.

**The dashboard**, under Schedule &rarr; Automatic runs, owns anything you
might want to change later: when to run, and how to wake the model-server
host. It is stored in `data/config.yaml` and the scheduler re-reads it every
cycle, so a change applies within a minute without restarting anything.

Setting `JOB_SCOUT_SCHEDULE`, `JOB_SCOUT_WAKE_MAC` or
`JOB_SCOUT_LLM_HEALTH_URL` as environment variables still works and takes
precedence -- but then the dashboard can no longer change them, so leave them
unset unless you specifically want to pin a value.

**`.env`** (gitignored) holds what has to be known before the container
starts:

```sh
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `JOB_SCOUT_PORT` | Dashboard port on the host |
| `JOB_SCOUT_DASHBOARD_TOKEN` | Leave empty for no auth; set to require a token |
| `JOB_SCOUT_TZ` | Container timezone, for log timestamps |

`data/config.yaml` also holds `local_base_url`. It must point at an address
the **container** can reach -- a Docker bridge address like `172.17.0.1` on
another machine will not resolve from the NAS.

## Install

On an ASUSTOR NAS (ADM):

```sh
sh /volume1/Docker/job-scout/deploy/nas-install.sh
```

Idempotent: re-run after editing `.env` or re-syncing the source. It checks
Docker access, seeds `.env`, fixes ownership on `data/` for uid 1000, probes
the model server, builds, starts, and waits for the dashboard.

ADM keeps the Docker binaries off the SSH `PATH` and the socket root-only, so
the script adds the AppCentral path itself and falls back to passwordless
sudo. If neither works it prints the exact `sudoers.d` rule to add.

## Migrating existing data

`cv_path` in each user's config is an absolute host path. Copy the CV inside
`data/` and rewrite the path to its `/data/...` equivalent, or the container
will not find it:

```yaml
cv_path: /data/users/Jeroen/CV_Jeroen.pdf
```

## Operating

```sh
docker compose logs -f scheduler          # what it is doing
docker compose exec scheduler job-scout run --all   # run now, off-schedule
docker compose exec scheduler job-scout wake --url ''   # test the magic packet
docker compose down                       # stop
```

The scheduler logs `Next run at <timestamp>` on start and after every run, so
its idea of the schedule is always visible in the logs.

## Notes

- Slots are evaluated in a real timezone, so a 17:00 run stays at 17:00 across
  a daylight-saving change.
- A failing run is logged and the loop continues; one bad run does not end the
  schedule.
- Waking is skipped when the endpoint already answers, and a failed wake logs
  and runs anyway rather than aborting.
- `tzdata` is installed in the image on purpose: `zoneinfo` reads the system
  timezone database, and without it every slot would be interpreted as UTC.
