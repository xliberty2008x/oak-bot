# PR 22: display cards integrated with deployed Oak

Review target: [PR 22](https://github.com/xliberty2008x/oak-bot/pull/22), original
head `aeec89c6be209b491508c053f5f6169f874a5b84`, based on `f997494`.
The actual deployed baseline was `9fb6594`, which already contains the private
Jev/MCP website workflow, origin consent, complete surfaces, compact login and
the official Lit adapter. The integration preserves that baseline.

The PR adds an ordinary A2UI display catalogue and durable Telegram mirroring.
It complements the private website form engine; it does not replace page
observation, Jev planning, human credential transport or authentication proof.
Ordinary card models/actions must not contain passwords, codes or cookies.

## Review findings addressed before release

1. **Integration blocker: obsolete renderer.** The original PR extends the old
   custom DOM renderer, while production uses the official pinned Lit/web-core
   runtime. Keeping its old renderer identifier would reject snapshots; keeping
   only the old Lit bundle would reject the new components. The integration
   extends the restricted Lit catalogue and rebuilds its locally served bundle,
   retaining upstream pins and the separate private credential iframe.
2. **Integration blocker: image file authority.** Original
   `A2UIStore.attach_images` accesses workspace paths with synchronous `stat()`;
   deployed executor file paths are deliberately lexical. Image preparation now
   uses capped immutable worker snapshots. Artifact rows commit only with a
   validated surface; failed or stale batches register no artifacts.
3. **Integration blocker: asynchronous artifact lookup.** Original Telegram
   attachment code passes the coroutine returned by deployed
   `EventBus.artifact_path` into a filesystem guard. Image paths are now awaited
   before synchronous rich-block compilation. A regression uses the real async
   bus and snapshot seam with mocked Telegram transport.
4. **Delivery defect: duplicate cards after ambiguous outcomes.** An unknown edit
   HTTP 400 must not imply that the old message disappeared. Only an explicit
   missing-message response permits a replacement send. An uncertain initial
   send quarantines later revisions across restart; known edit targets persist
   before dispatch. Uncertain outgoing actions are not replayed.
5. **Ordering defect: state/revision and deletion races.** Card state and routing
   metadata are captured together under the conversation lock. Delivery uses
   numeric revisions rather than queue insertion order. Deletions receive
   persisted tombstone revisions so a late old deletion cannot close a newer
   recreated card.
6. **Frontend resource defect: unbounded image cache.** The old panel retains
   object URLs after replacing/deleting surfaces. The Lit integration bounds
   image references to active artifacts and releases pending loads/object URLs
   on replacement, removal and session reset.

Tool descriptions retain all existing website capabilities. The parent CSP
retains both broker frame origins and adds only `blob:` image rendering. The
new Telegram card module is included in the protected product build digest.

The installed local Telegram server reported **Bot API 10.3** through a read-only
container `--version` invocation. The official API documents
[rich-message blocks](https://core.telegram.org/bots/api#inputrichmessage) and
[rich editing](https://core.telegram.org/bots/api#editmessagetext). Version and
schema support are distinct from sending a live owner message; no live
Telegram message was sent by these review checks.

## Validation

- Backend A2UI regression module: 27 passed, including immutable worker image
  snapshots, invalid/stale batch atomicity, captured routing and revision races.
- Telegram regression module: 33 passed, including the real asynchronous
  artifact seam, multipart construction, missing-message recovery, uncertain
  delivery across restart and reordered update/deletion tombstones. Telegram
  network transport was mocked.
- Renderer module in actual local Chromium: 10 passed, including existing form
  submit/drafts, all 14 catalogue components, bound display values, negative
  chart values, authenticated image streaming, size/MIME limits, cancellation,
  resource replacement and late response/session races.
- Offline pinned upstream schema validation: 11 server messages and one client
  action passed; three unsupported fixtures were rejected.
- Reproducible vendor bundle check, JavaScript syntax and `git diff --check`
  passed. Generated upstream literals keep their meaningful whitespace through
  a file-specific Git attribute.
- One desktop/mobile browser capture pair was reviewed. A bounded follow-up
  capped desktop chart width and increased axis text size; browser tests cover
  the final source.
- The integrated run exposed two stale assertions in pre-existing private UI
  tests: oversized layout reports are capped, and surface explanations are
  nested inside action items. Tests now check those current contracts without
  changing private login runtime behavior or removing secret-isolation checks.

The complete integrated suite passed **1058 tests in 391.685 seconds**, with
provider/browser checks enabled and no failures, errors or skips. Deployment
pins are recorded in the [release receipt](deployment.json). Local/mock checks
do not establish live owner
Telegram acceptance or successful provider account authentication. No model
configuration, API credential or website authority changed in this integration.

## Host transport correction discovered during deployment

The first HTTPS acceptance check repeatedly stalled while downloading the
444318-byte Lit bundle, although direct HTTP returned the complete file in
milliseconds. The existing executor firewall used a negative socket-UID guard
before the local/private destination rejects. Packets without a full owning
socket skip that guard and can reach those rejects; this behavior follows the
[Linux 6.8 metadata evaluation](https://github.com/torvalds/linux/blob/v6.8/net/netfilter/nft_meta.c#L120-L151).

The follow-up changes only the scope of that firewall: a positive executor-UID
match jumps into a regular chain containing the same DNS exceptions and address
restrictions. Other UIDs and kernel packets without socket credentials cannot
enter that chain. An isolated network-namespace regression reproduces the old
kernel-generated ICMP failure, verifies the corrected behavior, and checks that
executor loopback denial and the DNS exception remain enforced. The affected
boundary suite passed **55 tests**, including that real nft/kernel regression.
An independent namespace TCP probe reproduced the same failure: root and the
gateway user received only 49152 of 2097152 bytes with the old policy, then the
complete matching body with positive dispatch; the executor UID remained denied.
No nginx or provider authority is changed. This installer correction is validated separately
from the complete PR suite above, including a fresh runtime attestation and
HTTPS asset checks during the final cutover.

The final deployed release passed all 10 current-boot boundary checks and all
145 manifest file comparisons. Nine HTTPS assets returned matching hashes. The
same public Lit bundle completed in 0.182 seconds with default TLS; a clean
Chromium context loaded the public sign-in screen and imported all 14 catalogue
entries without a page error. Oak and the private broker were running with zero
restarts. Existing uncertain intake records remained quarantined. The release
receipt distinguishes these checks from live owner Telegram/card acceptance.
