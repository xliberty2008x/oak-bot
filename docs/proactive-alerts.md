# When Oak starts a conversation

Oak can write first in the main private Telegram conversation. An owner can mention a project in ordinary conversation, leave the chat, and later receive a short message when a publisher reports something timely and useful to that project. The message connects the finding to the project in the owner's language and includes a direct source link. An owner can reply and continue the conversation.

The gateway checks every 15 minutes. It looks for matches in recent owner-authored messages in the main chat, owner-authored saved notes, and any project brief explicitly configured for this owner. It checks the matching publishers, re-reads the primary evidence, and asks a separate tool-free model turn whether the finding merits an interruption. The model can choose silence. Before sending, Oak checks the current context, controls, recent conversation links, and previous delivery attempts. Publishers receive only ordinary feed requests. The owner's conversation stays out of public requests.

New automatic discovery settings allow one message per day, with quiet hours from 22:00 to 08:00 in the deployment's configured time zone. Existing owner preferences take precedence. A finding must still be fresh at delivery. Oak records the Telegram receipt and treats an ambiguous delivery as a possible send across restarts.

## Owner controls

The owner can reply **why this?**, **less like this**, or **mute this topic**. They can pause and resume proactive contact in ordinary chat. The optional `/alerts` command shows settings, permitted topics and recent delivery decisions. It also supports setting a daily cap, quiet hours, and explicit publisher watches. Existing watches continue to work.

## Deployment

`proactive_discovery` defaults to `true` for authorized private Telegram owners. Set it to `false` to disable automatic discovery for the deployment. To pilot with a subset, set `proactive_discovery_owner_ids` to the selected numeric Telegram owner IDs. Oak intersects this list with `allowed_user_ids`. Omitting the selector includes every allowed owner. An empty list disables automatic discovery for everyone. Existing manual watches and owner opt-outs survive changes to the selector and restarts. Configure `timezone` with an IANA time zone. Oak has a built-in catalogue of first-party publishers for topics including space exploration, creative electronics, and digital illustration. An explicit `proactive_catalogue` list replaces the defaults. An empty list gives the scanner no automatic sources.

Each catalogue entry specifies a public HTTPS RSS or Atom feed, phrases to match in private context, and terms to match in publisher headlines or summaries. A trailing asterisk matches a word stem of four or more characters. This is an example for a deployment that follows space missions.

```json
{
  "timezone": "America/New_York",
  "proactive_discovery": true,
  "allowed_user_ids": [101, 202],
  "proactive_discovery_owner_ids": [202],
  "proactive_catalogue": [{
    "topic": "Space missions",
    "source": "https://www.nasa.gov/feed/",
    "context_terms": ["space exploration", "Artemis", "Voyager"],
    "item_terms": ["mission*", "spacecraft", "Artemis", "Voyager"]
  }]
}
```

A deployment with an existing owner project brief can use `proactive_context_files`, an object mapping each allowed Telegram owner ID to their own UTF-8 text file. Relative paths resolve from the deployment configuration directory. The gateway considers up to eight nonempty lines (8 KiB total) and prioritizes more recent conversation. Private topics and other owners' files remain separate.

The built-in reader checks exact approved public HTTPS feeds without cookies or redirects, accepts UTF-8 RSS 2.0 and Atom up to 512 KiB, and requires a dated headline with a same-host item link. A finding expires after 48 hours or an earlier explicit publisher expiry. In isolated Oak deployments the execution worker performs public requests. The model receives bounded publisher evidence and a matching private context excerpt. Oak controls the destination and appends the verified link.

## Other assistant hosts

Import `ProactiveAlerts`, `FeedSource`, `load_catalogue` and `make_writer` from the corresponding `oak.proactive*` modules. Supply a private SQLite path, an authenticated owner destination, a bounded read-only context callback (newest excerpts first), a private tool-free completion, and a single-attempt sender that returns a confirmed message ID.

```python
alerts = ProactiveAlerts(
    state_dir / 'proactive.sqlite3', FeedSource(), send_private,
    catalogue=load_catalogue(), context=owner_context,
    compose=make_writer(complete), recent=recently_discussed,
    timezone=local_timezone,
)
for owner_id in authorized_private_owners:
    alerts.configure_discovery(str(owner_id))

# Run from the host's background task even when there is no inbound message.
await alerts.tick(owners=authorized_private_owners)
```

If the transport explicitly rejects a send, raise `DeliveryRejected` with the provider's retry delay in seconds when available. Oak rechecks freshness and owner preferences before trying again. A lost response leaves the delivery uncertain across restarts.

Pass authenticated owner replies to `command(owner, text, reply_to=message_id)`. Use `conversation_context(owner, reply_to=message_id)` in ordinary conversation to let the model refer back to its own confirmed outgoing message. Each assistant instance owns its state and transport.

## Verification

The tests use temporary SQLite state and intercept outbound Telegram sends. Enable the public-feed tests for real HTTPS requests to independent publishers. The model test requires an authenticated subscription runtime. It starts from an ordinary project conversation, obtains a real model decision, and exercises a follow-up through the ordinary assistant thread.

```sh
OAK_TEST_PUBLIC_FEEDS=1 OAK_TEST_PROACTIVE_MODEL=1 python -m unittest discover -s tests -p 'test_proactive*.py' -v
```
