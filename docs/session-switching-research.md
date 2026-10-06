# Session selection and Telegram conversations

Checked against official Telegram documentation on **2026-10-06**. Research itself does not change BotFather settings. A subsequent read-only `getMe` check confirmed that the deployment owner enabled both private topics and user topic management.

## Recommended and implemented session selection

**Use one native Telegram topic per Oak session.** Telegram chooses the conversation through the incoming `(owner chat ID, message_thread_id)`. The Mini App lists those sessions and lets the owner inspect their state, memory counts, scheduled tasks and runtime integrations, or create/rename a native topic after confirmation. Selecting a panel session changes the panel's scope; conversation and topic selection remain in Telegram.

This distinction is an implementation conclusion: the documented Mini App bridge offers link navigation and app closing, but no method to replace the native chat history or select an arbitrary backend conversation in Telegram. Authenticate server requests with validated `Telegram.WebApp.initData`, check `auth_date`, and authorize each session against the authenticated user. `initDataUnsafe` is not trusted identity. Menu-button Mini Apps follow the inline-button launch behavior; `sendData` is available only for keyboard-button launches. [Mini App methods](https://core.telegram.org/bots/webapps#initializing-mini-apps), [validation](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app), [menu launch](https://core.telegram.org/bots/webapps#launching-mini-apps-from-the-menu-button)

## Separate visible conversations: native private topics

Private bot chats now support multiple named topics with separate conversations. Enable private-chat topics for the bot in **@BotFather**; Telegram's API documentation calls this **Threaded mode**. Users still talk to the same bot in Telegram. [Bot features](https://core.telegram.org/bots/features#topics-in-private-chats), [bot forums](https://core.telegram.org/api/forum#bot-forums)

Private-topic messaging/management arrived in Bot API 9.3 (2025-12-31); bot-created private topics and the user-creation flag arrived in 9.4 (2026-02-09). Older guidance restricting all topics to supergroups is outdated. [Official changelog](https://core.telegram.org/bots/api-changelog#february-9-2026), [9.3 changes](https://core.telegram.org/bots/api-changelog#december-31-2025)

The BotFather option **“Disallow users to create new threads”** controls whether users may create, modify and delete topics. The bot can manage its private-chat topics. When user management is enabled, Telegram clients should create a topic for a new topic-less message; otherwise users can write in General. These client behaviors matter when deciding how new sessions appear. [Bot-forum behavior](https://core.telegram.org/api/forum#bot-forums)

Read `getMe` flags `has_topics_enabled` and `allows_users_to_create_topics` before exposing native-topic actions. Incoming `Message.message_thread_id`/`is_topic_message` identify topic traffic; `sendMessage.message_thread_id` targets it. `createForumTopic`, `editForumTopic`, `deleteForumTopic`, and `unpinAllForumTopicMessages` explicitly support private chats. Current `closeForumTopic`/`reopenForumTopic` and General-topic methods are documented for forum supergroups only. [User flags](https://core.telegram.org/bots/api#user), [Message](https://core.telegram.org/bots/api#message), [sending](https://core.telegram.org/bots/api#sendmessage), [topic methods](https://core.telegram.org/bots/api#createforumtopic)

For forum supergroups, creation requires bot administrator status plus `can_manage_topics`; editing permits the topic creator exception. Deletion requires `can_delete_messages`; unpinning requires `can_pin_messages`. These group administrator prerequisites do not apply to private bot chats. Deleting a topic deletes its Telegram messages: never equate an Oak session archive/delete action with native-topic deletion. [Topic management](https://core.telegram.org/bots/api#editforumtopic), [deletion](https://core.telegram.org/bots/api#deleteforumtopic)

Telegram notes an additional fee for Telegram Star purchases for threaded bots; check the linked terms if the deployment sells digital products. The topic documentation does not require a user Premium subscription merely to use topics. [Bot-forum prerequisite and fee note](https://core.telegram.org/api/forum#bot-forums)

## Navigation and precise limits

`Telegram.WebApp.openTelegramLink(url)` opens a Telegram link inside Telegram; the documented behavior since Bot API 7.0 leaves the Mini App open. An explicit **“Return to Telegram”** action can call `close()`. Neither action selects an Oak session. [Mini App bridge](https://core.telegram.org/bots/webapps#initializing-mini-apps)

Telegram documents topic links using message-link syntax, such as `https://t.me/<supergroup_username>/<topic_id>` or `https://t.me/c/<supergroup_id>/<topic_id>`. The latter ID is a channel/supergroup ID, not a private user chat ID. The documented message-link scope is groups/channels; no private-bot topic deep-link syntax was found. Do not manufacture a private-chat topic URL or promise a Mini App can open that exact topic. A plain bot link can open the bot conversation; `?start=...` requests a Start flow, not history switching. [Message links](https://core.telegram.org/api/links#message-links), [topic links](https://core.telegram.org/api/links#forum-topic-links), [bot links](https://core.telegram.org/api/links#bot-links)

Existing Telegram messages remain where they were sent; the researched APIs do not provide history migration or replacement. Changing a backend model session only changes future context. `/new` resets context in its current topic and does not erase Telegram history.

## Oak routing and limits

The core routing change is isolated in this feature: a durable owner/topic mapping supplies a unique internal conversation key. Existing General state keeps its original key. Input queues, native model threads, memory, jobs, approvals, lifecycle events and the outgoing queue use the internal key; native delivery resolves it back to the owner's Telegram chat and topic. Edits/deletes address the original message in that chat. Computer permission remains an owner setting shared by the owner's sessions; conversation data remains separate.

The list contains topics observed in Bot API updates or created through Oak. The Bot API does not expose a complete private-topic listing or ordinary private-message deletion updates, so pre-existing unseen/deleted topics and missed renames cannot be synchronized automatically. Send a message in an older topic to register it; its label may initially be its numeric topic ID. This does not import its old history or another session's memory. The MTProto topic-list methods are a different protocol and are not used with the bot token. [Bot API updates](https://core.telegram.org/bots/api#update), [MTProto topic listing](https://core.telegram.org/api/forum#fetching-topics)

Native topic creation/rename requests retain an owner-scoped operation receipt. A lost response or restart marks the request uncertain and prevents automatic replay, including creation of the same name after a page reload. Inspect Telegram before retrying; unresolved matching operations require operator reconciliation. Oak does not expose native topic deletion or unsupported private-chat close/reopen actions.
