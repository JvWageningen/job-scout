# Notifications

A run ends with one job: telling you what it found. Everything before it —
scraping, screening, commute filtering, evaluation, the live re-check that a
vacancy is still open — exists so that this message is short and worth
reading.

job-scout ships four delivery channels, all implemented against the same
`Notifier` interface in `src/job_scout/notify/`: **ntfy**, **email (SMTP)**,
**Slack** and **Discord**. Exactly one is active per user, chosen with
`notification_channel`. Each can deliver either one message per match or a
single digest per run.

Notifications are sent at the very end of the pipeline, after matches have been
verified as still open and enriched with the employer's own posting URL and a
company review. A notification therefore links to a vacancy that was confirmed
live during the same run.

---

## Picking a channel

| Channel | Best for | What you need | Scope of the setting |
|---|---|---|---|
| `ntfy` | A phone alert the moment a match lands | Nothing but a topic name (free public server) | `ntfy_topic` per user, `ntfy_server` global |
| `email` | An archive you can search and forward | An SMTP relay you control, or an app password | Relay is global, recipient per user |
| `slack` | A shared channel, or a personal workspace | An incoming-webhook URL | Per user |
| `discord` | A private server or DM channel | A webhook URL | Per user |

Set the channel per user:

```bash
uv run job-scout config set notification_channel ntfy --user alex
```

Omit `--user` and the value is written to the global `data/config.yaml`
instead, where it acts as the fallback for every user. Global-only keys
(`ntfy_server`, `smtp_host`, `smtp_port`, `smtp_from`) are the reverse: passing
`--user` to those is rejected.

Everything below can also be done from the dashboard's **Notifications** tab,
which is the easier route for the ntfy pairing flow. See
[WEB_DASHBOARD.md](WEB_DASHBOARD.md).

---

## Per-job or digest

`notification_mode` is per user and defaults to `per_job`.

| | `per_job` | `digest` |
|---|---|---|
| Messages per run | One per matched job | One, covering every match |
| Sent when | Each match, as the run finishes | Once, at the end of the run |
| Detail per job | Full: score, reasoning, salary, vacation, location, travel, source | Title, company, fit score, link |
| Top pick marked | n/a | Yes — highest fit score is flagged |
| Tap-through link | Yes, the message itself opens the listing | Per job inside the body, except on ntfy |
| If the send fails | Only that job is queued for retry | Every job in the run is queued |
| Retry behaviour | Retried individually | Each job retried as its own one-job digest |
| Good for | A handful of high-quality matches | Broad searches, or a channel you read once a day |

```bash
uv run job-scout config set notification_mode digest --user alex
```

Every digest titles itself daily regardless of how often the scheduler actually
runs, so a weekly slot still produces a message headed `Daily Job Digest`. The
exact wording differs by channel: ntfy sends `Daily Job Digest: N matches
found`, while the email subject, the Slack header block and Discord's summary
embed all stop at `Daily Job Digest: N matches`. The heading inside the email
body keeps the `found`.

---

## What a notification contains

A per-job message is built from the evaluated listing. On ntfy it looks like
this, with the title as the notification header and the job URL attached so
that tapping the notification opens the listing:

```text
Senior Quality Engineer @ ASML
Score: 78/100. Strong overlap with your validation and CSV experience
Salary: €5000–6000/month
Vacation: 27 days/year
Location: Veldhoven
Travel: PT: 52min | Bike: 38min | Car: 31min
Source: indeed
Employer page (still open): https://www.asml.com/careers/...
Company review: 71/100 (medium confidence). Strong engineering culture, ...
```

The channels do not all carry the same fields:

| Field | ntfy | email | Slack | Discord |
|---|---|---|---|---|
| Title and company | Header | Subject and heading | Header block | Embed title |
| Fit score | Yes | Yes | Yes | Yes |
| Fit reasoning | Yes | Yes | Yes | Embed description |
| Salary | Yes | Yes | Yes | Yes |
| Location | Yes | Yes | Yes | Yes |
| Travel summary | Yes | Yes | Yes | Yes |
| Vacation days | Yes | — | — | — |
| Source board | Yes | Yes | — | Yes |
| Link to the listing | Tap the notification | `View Job Listing` link | `View Job` button | Embed title links out |
| Employer's own posting + open/filled status | Yes | — | — | — |
| Company work-quality review | Yes | — | — | — |

Travel is rendered as `PT: 52min | Bike: 38min | Car: 31min` for whichever
modes resolved; a listing whose location could not be geocoded says so
instead. Salary reads `Not specified` when neither the model nor the regex
backstop found a figure.

ntfy carries the richest body because it is the channel designed to be read on
a phone without opening anything else.

---

## ntfy

### Set it up

1. Install the **ntfy** app (Android, iOS) or open the web client.
2. Open the dashboard's **Notifications** tab and select the ntfy channel.
3. Press **New secure topic**. This generates a topic of the form
   `job-scout-<your-name>-<16 random characters>` and saves it to your config.
4. Scan the QR code with your phone. The default **Opens app** code encodes an
   `ntfy://` deep link that takes the app straight to the topic; the **Opens
   web** code encodes the plain `https://` URL, which any scanner will follow
   but which lands in a browser. If you are already reading the dashboard on
   your phone, use the **Open in the ntfy app** button instead of scanning.
5. Press **Test Notification** and confirm it arrives.

The QR image is served with `Cache-Control: no-store`, so regenerating a topic
can never leave a stale code on screen that subscribes a phone to the previous
one.

### The topic is the entire access control

An ntfy topic has no password. **Anyone who knows the topic name receives every
message published to it, and anyone can publish to it.** Your notifications
carry job titles, employers, salary figures, commute times measured from your
home address, and the model's reasoning about your fit. Treat the topic name
as a credential.

The built-in default, `job-scout-alerts`, is not a secret. It is a guessable
phrase on a public server and it will collide with other people's topics.
`job-scout init` prompts for a topic with that default pre-filled, so a fresh
install that accepts every prompt is publishing to an effectively public
channel. Fix it before the first real run.

`job_scout.ntfy_topic.generate_topic()` is what the dashboard's **New secure
topic** button calls: a readable `job-scout-<name>-` prefix plus 16 characters
drawn with `secrets` from a 36-symbol alphabet, roughly 82 bits of entropy —
recognisable in the phone app, hopeless to guess. The dashboard also checks
the configured topic with `is_secure_topic()` and shows a warning banner while
it still looks hand-picked.

If you would rather not use the dashboard, set a topic of your own with enough
entropy in it:

```bash
uv run job-scout config set ntfy_topic job-scout-alex-q4m7zv2rk9tp6xhd --user alex
```

Regenerating a topic does not migrate your phone. Re-subscribe after any
change, or the notifications go to a name nobody is listening to.

### Config keys

| Key | Scope | Default | Purpose |
|---|---|---|---|
| `notification_channel` | user | `ntfy` | Set to `ntfy` |
| `ntfy_topic` | user | `job-scout-alerts` | Topic alerts are published to |
| `ntfy_server` | global | `https://ntfy.sh` | Server base URL |

### Caveats

- **The publish request is anonymous.** No token or basic-auth header is sent,
  so a self-hosted server configured to require authentication for publishing
  will reject job-scout. Self-host with anonymous write on an unguessable
  topic, or stay on `ntfy.sh`.
- Set `ntfy_server` to your own instance if you do not want message bodies
  passing through a third party at all. It is a global key, shared by every
  user on the install.
- A server reached over plain `http://` gets `?secure=false` appended to the
  deep link, which is how the app is told not to upgrade the connection.
- The public `ntfy.sh` applies rate limits. Digest mode is one request per run
  instead of one per match, which is the cheaper shape if you hit them.
- Digest messages carry no click target, because a digest has no single
  listing to open.

---

## Email (SMTP)

### Set it up

1. Point the install at a relay. The relay is global, so every user on the
   install shares it:

   ```bash
   uv run job-scout config set smtp_host smtp.example.com
   uv run job-scout config set smtp_port 587
   uv run job-scout config set smtp_from job-scout@example.com
   ```

2. Set the recipient, which is per user:

   ```bash
   uv run job-scout config set notification_channel email --user alex
   uv run job-scout config set smtp_to alex@example.com --user alex
   ```

3. If the relay needs credentials, put them in `data/secrets.yaml` — never in
   a config file. `config set` refuses secret fields outright:

   ```yaml
   smtp_username: job-scout@example.com
   smtp_password: "an-app-specific-password"
   ```

   Or supply them as `JOB_SCOUT_SMTP_USERNAME` and `JOB_SCOUT_SMTP_PASSWORD`,
   which is the route the container deployment uses. See
   [CONFIGURATION.md](CONFIGURATION.md).

With a consumer mailbox (Gmail, Outlook, Fastmail), generate an app-specific
password rather than using your account password, and make `smtp_from` the
same address you authenticate as — most providers reject a mismatched sender.

### Config keys

| Key | Scope | Default | Purpose |
|---|---|---|---|
| `notification_channel` | user | `ntfy` | Set to `email` |
| `smtp_host` | global | `""` | Relay hostname |
| `smtp_port` | global | `587` | Relay port |
| `smtp_from` | global | `""` | Sender address |
| `smtp_to` | user | `""` | Recipient address |
| `smtp_username` | secret | — | `data/secrets.yaml` or `JOB_SCOUT_SMTP_USERNAME` |
| `smtp_password` | secret | — | `data/secrets.yaml` or `JOB_SCOUT_SMTP_PASSWORD` |

### Caveats

- **STARTTLS is only attempted when both a username and a password are
  configured.** A relay that demands TLS but no credentials will refuse the
  message. This is the usual reason an otherwise correct-looking setup fails.
- **Implicit TLS (port 465) is not supported.** The connection is opened as
  plain SMTP and upgraded, so use the submission port, normally 587.
- The whole exchange has a 10-second timeout. A slow or greylisting relay will
  raise rather than wait.
- One recipient only: `smtp_to` is a single address, not a list. Use a
  distribution alias if you need several.
- Known issue: the per-job HTML body renders a stray template fragment where
  the vacation-days line should be. The rest of the message is unaffected, and
  digest emails do not carry the field at all.

---

## Slack

### Set it up

1. Go to <https://api.slack.com/apps> and create an app in your workspace
   (**From scratch** is fine).
2. Open **Incoming Webhooks**, turn the feature on, and press **Add New
   Webhook to Workspace**.
3. Choose the channel to post into and authorise. Slack returns a URL of the
   form `https://hooks.slack.com/services/T.../B.../...`.
4. Store it against the user:

   ```bash
   uv run job-scout config set notification_channel slack --user alex
   uv run job-scout config set slack_webhook_url https://hooks.slack.com/services/T00/B00/XXXX --user alex
   ```

The webhook URL is a bearer credential: anyone holding it can post into that
channel. It lives in the user's `config.yaml` inside the gitignored `data/`
tree — keep it there.

### Config keys

| Key | Scope | Default | Purpose |
|---|---|---|---|
| `notification_channel` | user | `ntfy` | Set to `slack` |
| `slack_webhook_url` | user | `""` | Incoming-webhook URL |

### Caveats

- A webhook is bound to one channel at creation. Posting somewhere else means
  creating a new webhook.
- Slack's header block caps plain text at 150 characters. A pathologically
  long `title @ company` is rejected as an invalid payload rather than
  truncated.
- Slack messages omit the source board, the employer's own posting URL and the
  company review; ntfy is the channel that carries all three.
- Removing or reinstalling the app invalidates the webhook, and sends then
  fail with `no_service`.

---

## Discord

### Set it up

1. In the target Discord server, open **Server Settings -> Integrations ->
   Webhooks** (or a channel's **Edit Channel -> Integrations**).
2. Press **New Webhook**, pick the channel, and press **Copy Webhook URL**. It
   looks like `https://discord.com/api/webhooks/<id>/<token>`.
3. Store it against the user:

   ```bash
   uv run job-scout config set notification_channel discord --user alex
   uv run job-scout config set discord_webhook_url https://discord.com/api/webhooks/123/abc --user alex
   ```

You need **Manage Webhooks** on the server to create one. For a private feed,
make a server of your own with a single channel — it costs nothing and keeps
your job search out of a shared workspace.

### Config keys

| Key | Scope | Default | Purpose |
|---|---|---|---|
| `notification_channel` | user | `ntfy` | Set to `discord` |
| `discord_webhook_url` | user | `""` | Webhook URL |

### Caveats

- **Digests larger than nine matches fail.** A digest posts one summary embed
  plus one embed per job, and Discord caps a webhook message at ten embeds.
  Use `per_job` for a broad search, or expect busy runs to land in the retry
  queue.
- The webhook URL contains its token. Anyone with it can post to the channel
  as job-scout; regenerate it in Discord if it leaks.
- Discord rate-limits webhooks per channel. A per-job run with many matches
  sends one request each, in sequence.
- The top pick in a digest is both recoloured and labelled: its embed turns
  yellow against the standard blue, and ` TOP PICK` is appended to the embed
  title. Discord is the one channel whose marker carries no star.

---

## Testing a channel

There is no CLI command that sends a test notification. Use the dashboard:

```bash
uv run job-scout web --host 127.0.0.1 --port 8080
```

Open the **Notifications** tab, fill in the channel's settings, and press
**Test Notification**. Two things make this test worth trusting:

- It uses the values currently in the form, saved or not, so you can iterate
  on a webhook URL without committing it to config first.
- It sends a real listing (`Test notification @ job-scout`, fit score 100)
  down the same code path a genuine match uses, rather than a parallel
  "does the configuration look plausible" check. If it arrives, delivery
  works.

A failed test reports the underlying error — the missing key, the SMTP
rejection, the HTTP status from the webhook — rather than a generic failure.

To see what a real run *would* send without sending anything:

```bash
uv run job-scout run --user alex --dry-run
```

Dry runs print `[DRY RUN] Would notify: ...` per match (or a single
`Would send digest notification` line), save nothing, and mark nothing.

---

## Delivery, deduplication and retries

**One notification per vacancy, not per listing.** The same job routinely
appears on Indeed and LinkedIn as two rows with different URLs. Before sending,
matches are collapsed on normalised title plus company and the highest-scoring
row wins, so a cross-posted vacancy notifies once. The dropped rows are logged
as `Skipping duplicate notification`.

**A failed send is not lost.** Every job row carries a `notification_pending`
flag:

1. A send that raises marks that job pending. In digest mode, a failed digest
   marks every job it covered.
2. At the end of the notification stage of the next run, every pending job is
   retried. Success clears the flag and marks the job notified; another
   failure leaves it pending for the run after that.
3. In digest mode each pending job is retried on its own, as a one-job digest.

Three consequences worth knowing:

- **The retry pass only runs when the run produced at least one new match.** A
  run that finds nothing returns before the queue is touched, so a backlog
  built up while a webhook was broken clears on the next run that matches
  something — not simply on the next run.
- There is no attempt cap, no backoff and no expiry. A permanently broken
  channel accumulates pending rows that are retried on every qualifying run.
- Dry runs neither send, nor retry, nor mark anything.

**Re-notifying deliberately.** `run --full` bypasses deduplication against the
database and re-notifies every match, which is the supported way to replay a
search after changing your profile. It is also the usual explanation for a
sudden burst of familiar notifications.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Run finishes with 0 notified and logs `Notification channel not available` | The selected channel's required key is empty | `uv run job-scout config show --user alex`, then fill in the key for that channel |
| The test notification arrives but real runs deliver nothing | The run found no matches, or the matches were notified already | Check `uv run job-scout runs history --user alex`; replay with `run --full` |
| ntfy delivers nothing after pressing **New secure topic** | The phone is still subscribed to the previous topic | Re-scan the QR code, then unsubscribe from the old topic |
| ntfy publish rejected with 401/403 | The server requires authentication to publish; job-scout publishes anonymously | Use a server that allows anonymous write, on an unguessable topic |
| ntfy publish rejected with 429 | Rate limit on the shared `ntfy.sh` server | Switch to `digest` mode, or self-host and set `ntfy_server` |
| Email fails with an authentication error | Wrong credentials, or an account that needs an app-specific password | Put the app password in `data/secrets.yaml` or `JOB_SCOUT_SMTP_PASSWORD` |
| Email connection hangs or times out | Port 465 implicit TLS is unsupported, or the 10-second timeout was hit | Use the STARTTLS submission port, normally 587 |
| Email refused for lack of TLS | STARTTLS is only attempted when both username and password are set | Configure both credentials, even on a relay that barely checks them |
| Email arrives with a stray template fragment where vacation days belong | Known defect in the per-job HTML body | Cosmetic; the rest of the message and all digests are unaffected |
| Slack returns `no_service` | The webhook was revoked, or the app was removed from the workspace | Create a new incoming webhook and update `slack_webhook_url` |
| Slack rejects one specific job as an invalid payload | `title @ company` exceeded the 150-character header limit | Nothing to configure; other matches are unaffected |
| Discord digests fail while per-job works | More than nine matches exceeds Discord's ten-embed message cap | `uv run job-scout config set notification_mode per_job --user alex` |
| The same vacancy arrives twice from two boards | Titles or companies differ enough to defeat the dedupe key | Expected at the edges; the two listing URLs will differ |
| Everything is re-notified after a config change | `run --full` was used, which bypasses deduplication | Drop `--full` for ordinary runs |
| A backlog of failed sends never arrives | The retry pass is skipped on runs with no new matches | Fix the channel, then run again once something matches |
| A notification links to a filled vacancy | `verify_matches_open` is off, or the live check was inconclusive | `uv run job-scout config set verify_matches_open true --user alex` |

---

## Related

- [CONFIGURATION.md](CONFIGURATION.md) — every config key, secret and
  environment variable, including the global/per-user split
- [USAGE.md](USAGE.md) — the CLI reference, `run` flags and daily workflows
- [WEB_DASHBOARD.md](WEB_DASHBOARD.md) — the Notifications tab, the ntfy
  pairing panel and the dashboard's security posture
- [DEPLOY.md](DEPLOY.md) — running the scheduler in a container, where
  notification secrets arrive as environment variables
- [../ARCHITECTURE.md](../ARCHITECTURE.md) — where the notification stage sits
  in the pipeline
