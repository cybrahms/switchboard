# An OTLP sink to telemetry relay

This Python 3.9+ example turns ERROR/FATAL logs and error spans in an OTLP JSONL
sink into Switchboard todos. It uses the standard library and runs on macOS or
Linux. The receiver must persist exports as one JSON object per line with an
OTLP JSON `body`, using `logs-*.jsonl` / `traces-*.jsonl` names. Rotated files
may end in `.jsonl.gz`.

See [the deployment guide](../../docs/guides/18-telemetry-relay.md) for grants,
worker configuration, scheduling, and end to end validation.

```sh
python3 telemetry-relay.py --dry-run
python3 telemetry-relay.py --send
python3 -m unittest discover -s . -p 'test_*.py' -v
```

| Environment | Meaning |
| --- | --- |
| `OTLP_SINK_DIR` | Export directory; default `~/.local/state/otlp-sink`. |
| `TELEMETRY_WEBHOOK_URL` | Token-bearing ingress URL from `create_webhook`; required for `--send`. Keep private. |
| `TELEMETRY_STATE` | Sender deduplication checkpoint; default `~/.local/state/telemetry-relay/seen.json`. |
| `TELEMETRY_MIN_SEVERITY` | `ERROR` (default) or `FATAL`. Numeric log severity takes precedence. Error spans are excluded in FATAL mode. |

The relay includes timestamp and content in a stable `X-Delivery-Id` and
checkpoints each acknowledged delivery. Export retries deduplicate; the same
message at another timestamp is a new occurrence. Compression preserves IDs
for records without timestamps. IDs are retained for 30 days; keep sink
retention shorter than that. A lock prevents overlapping sends.

The default is a dry run: it redacts and prints candidate payloads, sends
nothing, and does not change state. Dry-run output can still contain operational
details; common credential patterns are redacted, which is not a guarantee that
arbitrary secrets in messages will be recognized. The sender includes only
bounded service, severity, summary, and log/trace identifiers, not the entire
export or its resource attributes.

Missing configuration or input, corrupt state, and failed HTTP delivery return
nonzero. Exception output omits capability URLs. HTTP 429 and transient failures
stop the current scan; the next scheduled scan retries unacknowledged IDs.
State already checkpointed remains intact. The summary reports matching,
delivered, and previously seen signals separately. The initial scan backfills
retained errors; choose the queue owner and review the dry run before enabling.

## Deployment validator

Save the endpoint's one-time vend response to a mode-0600 JSON file containing
`mcp_url` and `token`. Once the receiver, scheduled relay, and worker are running:

```sh
python3 validate.py --vend-record /private/path/telemetry-endpoint.json
```

The validator injects an OTLP error with service `telemetry-relay.validation` and
summary `telemetry relay deployment probe <UUID>`. It does **not** run the relay.
It waits for the scheduled job to produce exactly one todo and for the worker
to complete it with `{"validation":"acknowledged"}`. Give the worker that probe
rule before running it. This is a validation event, not an incident to fix.
`list_todos` omits payloads, so the validator uses `get_todo` on candidate IDs.

Default wait is 300 seconds. A busy worker may need more time (`--timeout`);
resume an already injected probe with `--probe-id <UUID>` instead of injecting
another. `--mcp-url` can select the same endpoint's local MCP URL; `--otlp-url`
selects the sink's OTLP/HTTP log receiver. Credentials are read from the private
file and never printed. A missing acknowledgement, failed todo, or duplicate
todo returns nonzero.
The replacement at `~/agents/telemetry-relay.py` and PR example now:
- excludes spans and logs carrying `agent.tool.name` (runtime errors remain eligible);
- accepts only exports received in the last five minutes, rejecting missing,
  nonfinite, or future receive timestamps;
- groups service/signal/severity and normalized summary into fixed 15-minute
  windows; numeric and long hexadecimal identifiers are normalized;
- delivers at most eight tasks in any rolling hour using persisted delivery times.

Settings: `TELEMETRY_MAX_AGE_SECONDS=300`, `TELEMETRY_GROUP_SECONDS=900`,
`TELEMETRY_MAX_PER_HOUR=8`. Zero age/window explicitly disables those respective
controls for fixtures or intentional replay; production should keep the defaults.
The hourly budget applies to all deliveries, including validation probes. Budget
exhaustion defers fresh candidates; candidates aging past five minutes are dropped.
First delivery includes the number of distinct occurrences found in that scan.
Later occurrences in the same window do not update the queued task. Fixed-window
boundaries can yield two tasks close together; the rolling hourly budget still holds.

Four offline regression tests pass against real scan/state paths with the HTTP
transport mocked. All four fail against the old policy. HTTP integration tests
were attempted but cannot bind loopback sockets under this session's sandbox.
Live dry-run reports the filtering counts without sending or advancing state.

On upgrade from the occurrence relay, old delivery IDs remain for deduplication.
With grouping enabled, the rolling budget counts only new group deliveries;
legacy raw-span/log IDs do not prevent runtime alerts after rollout.
