# Operate the VM from Oak

Open the Mini App, choose **Робочий стіл**, and connect. Confirm the takeover:
Oak stops active agent turns before sharing the desktop and keeps new agent
work from using it while you are connected. You can then open a website and
complete its login yourself in the VM's browser. This is the configured X11
desktop, not a browser embedded from the external website.

On a computer, use the screen with your mouse and keyboard. On a phone, use
the relative touchpad to move the cursor, tap to click and use two fingers to
scroll. The keyboard button opens the phone keyboard; extra controls provide
Enter, Tab, Escape, Backspace and the browser address-bar shortcut. End manual
control when finished. Interrupted work is not silently restarted; send a new
task in Telegram when you want Oak to continue.

Text and common keyboard shortcuts share an authenticated, ordered input queue
bound to the current connection. This avoids dropped Unicode characters from
X11 keymap updates and keeps edits such as Backspace followed by text in order.
The keyboard input can also accept pasted text; clipboard contents are never
automatically synchronized between your device and the VM. Physical modifier
holds with mouse clicks and desktop IME composition are not provided by this
text-oriented keyboard path; use the keyboard button for composed text.

## Deployment

The VM bootstrap installs `x11vnc`. Oak includes an unmodified, pinned noVNC
library and its licenses, so clients do not download executable code from a
CDN. Existing installations need `x11vnc` and a working `computer.enabled` /
`computer.display` configuration. Managed desktops inherit their Xauthority;
external displays are used as configured and are never restarted by this
feature. The HTTPS proxy must support WebSocket upgrades on the same origin.

The normal owner authentication applies to both the panel and remote control.
A confirmed authenticated request issues a short-lived, single-use WebSocket
ticket. The ticket travels in a subprotocol header, not a URL. Only one manual
connection can own the desktop. Oak starts an ephemeral x11vnc child for that
connection using a local socket pair; there is no additional listening VNC
port. Disconnect, expiration and service shutdown close the child and release
manual control. The existing computer-use permission is preserved.

Remote frames and typed input are not added to chat, memory, screenshots,
artifacts or request logs. Browser sign-in state remains in that desktop's
browser profile. Browser and website policies still apply: a working remote
desktop does not guarantee that a particular provider will accept a login.
Do not copy another desktop's browser profile or credentials to work around
an account problem.

Check desktop input in an isolated test browser before claiming that a phone
or Telegram client works. Device keyboard behavior and provider sign-in are
separate checks from a successful server connection.
