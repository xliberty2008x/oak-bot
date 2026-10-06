# Session selection and Telegram conversations

Checked against official Telegram documentation on **2026-10-06**. This is documentation research; no live bot, client or provider access was tested or changed.

## Current panel and future session selection

**Current recommendation:** show the current Telegram session in Oak's control panel and keep conversation messages and `/new` in Telegram. No backend session picker is implemented. Only after owner-scoped backend routing exists should a future picker offer **“Select session for future messages”**. That selection would change subsequent model context, without replacing Telegram's visible history.

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

Show current session state only for now. Existing Telegram messages remain where they were sent; the researched APIs do not provide history migration or replacement. Verify client behavior and BotFather prerequisites before exposing future topic actions.

Future topic support requires a parent-owned Oak core handoff: the controller must map owner-scoped Telegram `(chat_id, message_thread_id)` keys to sessions; the gateway must persist incoming and outgoing `message_thread_id` through durable queues and retries; scheduler work, memory, approvals and outgoing delivery must retain that session/topic scope. Until those changes are implemented and verified, a panel selection must not claim to route Telegram conversations.
