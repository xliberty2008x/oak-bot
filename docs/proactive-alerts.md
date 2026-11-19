# Proactive alerts

In the main conversation of your private Telegram chat, send `/alerts` to see your settings. Add a watch with a headline topic, the exact RSS or Atom feed from the publisher, and the reason that topic matters to you. A watch continues until you mute, pause, or remove it.

```text
/alerts watch Alaska | https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.atom | I follow seismic activity in Alaska
/alerts quiet 22:00-08:00 America/Anchorage
/alerts cap 1
```

The topic matches a word or phrase in the headline, ignoring case. Use `*` only if you want updates about any item in that feed. Each owner can register up to eight watches. Quiet hours use the named time zone, including clock changes. The default limit is two alerts per local calendar day.

Reply to a delivered alert with **why this?**, **less like this**, **mute this topic**, or **resolved**. “Less like this” limits the topic to one alert every three days. Use `/alerts resolve ITEM_URL` for a finding you already handled. Send `/alerts history` for recent delivery decisions, `/alerts pause` to stop scanning, and `/alerts resume` to resume with your saved choices. Use `/alerts unmute TOPIC` or `/alerts remove TOPIC` to manage individual topics.

## What gets delivered

Every 15 minutes the gateway scans active watches. The built-in reader accepts public HTTPS RSS 2.0 and Atom feeds in UTF-8, up to 512 KiB. It reads the approved URL without cookies, credentials, or redirects. Each finding needs a dated headline and a link on the publisher's host. In an isolated Oak deployment, the execution worker performs these reads.

Oak checks recent conversation links, previous delivery attempts, the owner's settings, and publication times. Before an eligible send, it reads the publisher again and checks that the item still exists with the same evidence. A changed item can qualify on a later tick if its evidence then agrees. The built-in reader stops considering a finding after 48 hours or an earlier explicit publisher expiry. Oak only reports an expiry supplied by the publisher. Quiet-hour and capped findings can qualify on a later scan while the publisher still lists them and they remain timely.

The Oak gateway sends alerts to the main conversation of the owner's private chat and checks recent links in that conversation. Private topics retain their own history. The message includes the publisher's headline, publication date, the saved reason for the watch and the direct link. Each assistant uses its own SQLite state. A transaction reserves the owner and finding before sending, then records the confirmed private message ID. A lost receipt leaves an uncertain attempt in history. Replays skip that attempt because the message might already have arrived.

## Use the engine in another assistant

Import `ProactiveAlerts` from `oak.proactive` and `FeedSource` from `oak.proactive_sources` in a checkout of this release. The engine and built-in reader use the Python standard library. Supply a path in that assistant's private state directory and its existing private sender. The sender must make one bounded delivery attempt and return its confirmed message ID. An optional synchronous `recent(owner, candidate)` callback checks for the item's URL in the owner's recent conversation.

```python
alerts = ProactiveAlerts(state_dir / 'alerts.sqlite3', FeedSource(), send_private, recent=recent)

# After the host authenticates a private, owner-authored message:
response = alerts.command(owner_id, message.text, reply_to=quoted_message_id)
if response is not None:
    await send_private(owner_id, response)

# From a bounded periodic task in the same service:
await alerts.tick(owners=authorized_owner_ids)
```

A custom read-only source implements `scan(watch, now)` and `verify(watch, candidate, now)` as async methods. The scanner returns candidates. The verifier independently confirms the observed facts and timestamps before delivery. The host supplies authorized owner IDs and scopes storage and transport to its own deployment. Both the Oak gateway and external hosts use the same policy engine.

## Verification

Run the policy and gateway tests with a temporary state directory and intercepted delivery. Set the environment variable below to include real, read-only HTTP requests to PyPI and the USGS. The public-feed checks need internet access and fresh items from both publishers.

```sh
OAK_TEST_PUBLIC_FEEDS=1 python -m unittest discover -s tests -p 'test_proactive*.py' -v
```
