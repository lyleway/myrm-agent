# Web Push (VAPID) Module Architecture

## Purpose

Offline push notifications via W3C Push API + VAPID. Enables notification
delivery when the browser/PWA is closed. Complements existing IM notifications
(NotificationDispatcher) and in-browser OS notifications (SystemNotificationService).

## Data Flow

```
 ServerEventBus (fan-out)
  ↓               ↓               ↓
SSE (Web)    NotificationDispatcher (IM)    WebPushDispatcher (Web Push)
                                              ↓ _handle_event()
                                          WebPushService.broadcast()
                                              ↓ pywebpush + VAPID
                                          Push Service (FCM/APNS/Mozilla)
                                              ↓
                                          Browser Service Worker → OS notification
```

`broadcast()` returns a `BroadcastResult`; `WebPushDispatcher` hands it to `DeliveryAlert`, which reports a push that
reaches none of the registered devices (see Delivery Failure Alert).

## Supported Push Events

| AppEventType | Push Title | When |
|---|---|---|
| `APPROVAL_REQUIRED` | Approval Required | Agent needs human approval |
| `HEALTH_ALERT` | Health Alert | System health issue |
| `BUDGET_ALERT` | Budget Alert | Daily cost threshold reached |
| `GOAL_TERMINAL` | Goal {status} | Long-running goal completed |
| `BACKGROUND_TASK_DONE` | Task {status} | Kanban task outcome (completed/failed/blocked/pending_review/rejected) — title resolved from the `status` field |
| `CHANNEL_DISCONNECTED` | Channel Disconnected | IM channel went offline |
| `SYSTEM_NOTIFICATION` | {title} | Generic system notification |
| `OAUTH_REAUTH_REQUIRED` | Authorization Expired | OAuth token expired |

An event whose data carries `suppress_web_push: true` is never pushed.

## Files

| File | Role | Description |
|------|------|-------------|
| `__init__.py` | Package | Re-exports WebPushService |
| `vapid_keys.py` | Crypto | ECDSA P-256 key pair generation/persistence |
| `service.py` | Core | Subscription CRUD + push sending via pywebpush; `BroadcastResult` (what a broadcast reached, a reason per missed subscription) |
| `dispatcher.py` | EventBus | ServerEventBus subscriber for Web Push fan-out; feeds every broadcast result to `DeliveryAlert` |
| `delivery_alert.py` | Alert | `DeliveryAlert`: one `HEALTH_ALERT` per streak of broadcasts that reach no registered device |
| `push_deep_links.py` | Routing | Map AppEvent → same-origin click-through path (mirrors WebUI SSE routes) |

## Push Click-Through URLs

`WebPushDispatcher` passes `resolve_push_url(event)` into `WebPushService.broadcast()`.
Paths align with in-app SSE handlers (`useGlobalEvents`):

| AppEventType | Click URL |
|---|---|
| `APPROVAL_REQUIRED` | `/{chat_id}?approval={approval_id}` |
| `GOAL_TERMINAL` | `/{session_id}` |
| `BACKGROUND_TASK_DONE` | `pending_review` + `board_id` → `/settings/kanban?board_id={board_id}&status=in_review`；否则 `/{chat_id}` |
| `SYSTEM_NOTIFICATION` | `/{meta_data.chat_id}` or `/` |
| `HEALTH_ALERT` (`component=instance_backup`, automatic backup failing) | `/settings/workbench` |
| `HEALTH_ALERT` (`component=remote_memory_backup`, scheduled cloud sync failing) | `/settings/memory?sub=cloud-backup` |
| other `HEALTH_ALERT`, `BUDGET_ALERT` | `/settings/system` |
| `CHANNEL_DISCONNECTED` | `/settings/channels` |
| `OAUTH_REAUTH_REQUIRED` | `/settings/credentials`（所有 OAuth 账号，含 xAI） |

The browser SW (`src/app/sw.ts`) imports `src/lib/web-push/pushTargetUrl.ts` (bundled into `public/sw.js` by the frontend's `bun run build:sw`). It sanitizes payload URLs before `openWindow`. When a window client is already on the target pathname but the query differs (e.g. approval deep link), the SW calls `client.navigate()` instead of focus-only.

## Delivery Failure Alert

`WebPushService.broadcast()` never raises for a failed push: it returns a `BroadcastResult` (`subscriptions` addressed,
one reason per missed subscription in `failures`). `_send_one` words a reason as the push service's HTTP reply
(`HTTP 403 Forbidden`; a 404/410 adds `(subscription removed)`) or the type of the error (`ConnectTimeout`). The error
message is never part of it: requests puts the whole endpoint URL, which identifies the device, into it.

`WebPushDispatcher` hands every result to `DeliveryAlert`. When there are registered subscriptions and none was
reached it publishes one `HEALTH_ALERT` (`component=web_push`, `status=fail`, `detail` = the distinct reasons,
`suppress_web_push=true` because Web Push is the channel that is failing). The WebUI shows its own localized toast
(`hooks/globalEvents/failureAlertToast.ts`); IM channels get the standard health-alert card. Further failing broadcasts
stay silent until one reaches a device or finds no registered subscription, which ends the streak; the next failure
alerts again. The streak state lives in memory, so a fault that outlives a restart alerts once more. The test
endpoint does not feed the alert: its response already carries the reasons.

## VAPID Key Storage

Keys are stored in `{state_dir}/web_push/`:
- `vapid_private_key.pem` — PKCS8 PEM private key (0600 permissions)
- `vapid_public_key.txt` — URL-safe base64 public key, no padding (0644 permissions)

Generated lazily on first access. Protected by `filelock` for thread safety.
File permissions follow `app/remote_access/e2ee/keystore.py` security conventions.

`WebPushService._send_one` builds a `Vapid` object from the PEM for each push: pywebpush accepts a `Vapid` object, a key
file path or a raw base64 key, but not PEM text. Each push request carries a 10 s timeout (`requests` waits forever by default).
`tests/core/web_push/test_web_push_delivery.py` runs this path through the real pywebpush with only the HTTP POST stubbed.

## Subscription Storage

SQLite table `web_push_subscriptions`:
- `endpoint_hash` (PK) — SHA-256 of endpoint URL, first 32 chars
- `endpoint` — Push service URL
- `p256dh` / `auth` — Encryption keys from PushSubscription
- Auto-cleanup of expired subscriptions (HTTP 410/404)

## Lifecycle

- Started in `app/core/channel_bridge/setup/` after NotificationDispatcher and BtwTaskNotifier
- Stopped before BtwTaskNotifier during shutdown

## API Endpoints (app/api/web_push/)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/web-push/vapid-key` | Return VAPID public key |
| POST | `/web-push/subscribe` | Register subscription |
| POST | `/web-push/unsubscribe` | Remove subscription |
| POST | `/web-push/test` | Send test notification; the response carries `delivered`, `subscriptions` and the distinct `failures` (with no registered device the failure says so) |
