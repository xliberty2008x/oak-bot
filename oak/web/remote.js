import RFB from "/vendor/novnc/core/rfb.js";
import {releaseCapture} from "/vendor/novnc/core/util/events.js";
import {initLogging} from "/vendor/novnc/core/util/logging.js";

// The protocol library can log keysyms at verbose levels. Never log desktop input.
initLogging("none");

export function createRemotePanel({api, confirmAction, available, busy}) {
  const $ = id => document.getElementById(id);
  const screen = $("remote-screen"), input = $("remote-text"), pad = $("remote-touchpad");
  let rfb = null, connected = false, starting = false, stopping = false, generation = 0, timeout = null;
  let status = null, dragging = false, composing = false, keyboardOpen = false, swipesChanged = false;
  let pointer = {x: .5, y: .5}, gesture = null;
  const fingers = new Map();
  const inputQueue = [];
  let inputRunning = false, inputFailed = false;
  let lease = "";
  const marker = " "; // Lets an empty mobile field generate a Backspace input event.
  function pointerBusy() { return inputRunning || inputQueue.length > 0; }

  function message(text, error = false) {
    $("remote-message").textContent = text;
    $("remote-message").classList.toggle("error-message", error);
  }
  function render() {
    document.body.classList.toggle("remote-active", connected);
    $("remote-console").dataset.keyboard = String(keyboardOpen);
    $("remote-start").hidden = !!rfb;
    $("remote-start").disabled = !available() || busy() || starting || stopping || status?.configured !== true || status?.manual === true;
    $("remote-stop").hidden = !rfb && !starting && !status?.can_stop;
    $("remote-stop").disabled = stopping;
    $("remote-start").textContent = starting ? "Підключаємося…" : "Підключитися";
    $("remote-state").textContent = inputFailed ? "Введення призупинено" : connected && pointerBusy() ? "Вводимо · зачекай із кліком" : connected ? "Ти керуєш" : rfb || starting ? "Підключаємося…" : status?.manual ? "Ручне керування активне" : "Не підключено";
    $("remote-state").dataset.state = inputFailed ? "partial" : connected && pointerBusy() ? "busy" : connected ? "ok" : rfb || starting ? "busy" : status?.manual ? "partial" : "off";
    $("remote-inputs").hidden = !connected;
    $("remote-placeholder").hidden = connected;
    $("remote-fullscreen").hidden = !connected || !document.fullscreenEnabled;
    $("remote-drag").setAttribute("aria-pressed", String(dragging));
    $("remote-keyboard").setAttribute("aria-pressed", String(keyboardOpen));
    for (const button of document.querySelectorAll("#remote-inputs button")) button.disabled = inputFailed;
    for (const button of [$("remote-click"), $("remote-right"), $("remote-drag")]) button.disabled = inputFailed || pointerBusy() && !dragging;
    screen.setAttribute("aria-busy", String(connected && pointerBusy()));
    input.disabled = inputFailed;
  }
  function telegramSwipes(active) {
    const app = window.Telegram?.WebApp;
    try {
      if (!app?.isVersionAtLeast?.("7.7")) return;
      if (active && app.isVerticalSwipesEnabled !== false) {
        app.disableVerticalSwipes(); swipesChanged = true;
      } else if (!active && swipesChanged) {
        app.enableVerticalSwipes(); swipesChanged = false;
      }
    } catch {}
  }
  function canvas() { return screen.querySelector("canvas"); }
  function coordinates() {
    const target = canvas(), rect = target?.getBoundingClientRect();
    if (!target || !rect.width || !rect.height) return null;
    return {target, clientX: rect.left + pointer.x * rect.width, clientY: rect.top + pointer.y * rect.height};
  }
  function mouse(type, button = 0, buttons = dragging ? 1 : 0) {
    const point = coordinates();
    if (!connected || inputFailed || !point) return;
    // noVNC translates CSS coordinates to framebuffer coordinates itself.
    point.target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window, clientX: point.clientX, clientY: point.clientY, button, buttons}));
    // Our pad owns the gesture; leave native noVNC capture for physical mouse input.
    if (type === "mousedown" || type === "mouseup") releaseCapture();
  }
  function releaseButtons() {
    if (connected) mouse("mouseup", 0, 0);
    dragging = false;
    fingers.clear(); gesture = null;
    releaseCapture();
  }
  function clearInput() {
    input.value = "";
    composing = false;
  }
  function localDisconnect() {
    clearTimeout(timeout); timeout = null;
    releaseButtons();
    inputQueue.length = 0; inputFailed = false; lease = "";
    clearInput(); input.blur(); keyboardOpen = false;
    const old = rfb;
    rfb = null; connected = false;
    old?.blur(); old?.disconnect();
    screen.replaceChildren(); // Do not retain the last frame after control ends.
    telegramSwipes(false);
    if (document.fullscreenElement === $("remote-console")) void document.exitFullscreen().catch(() => {});
    render();
  }
  function failure(error) {
    if (error?.status === 401) return "Сеанс завершився. Увійди в панель ще раз.";
    if (error?.status === 409) return "Не вдалося передати керування. Oak ще завершує роботу або інше підключення активне. Онови стан і повтори спробу.";
    if (error?.status === 403) return "Доступ до робочого столу не дозволено для цього сеансу.";
    return "Робочий стіл недоступний. Перевір з’єднання та натисни «Оновити».";
  }
  async function refresh() {
    if (!available() || starting || stopping) { render(); return; }
    const current = generation;
    try {
      const result = await api("/api/remote");
      if (current !== generation || !available()) return;
      if (!result || typeof result.configured !== "boolean" || typeof result.manual !== "boolean") throw new Error();
      status = result;
      if (!rfb) message(!result.configured ? "Робочий стіл ще не налаштовано на машині Oak." : result.manual ? "Ручне керування вже активне. Заверши попереднє підключення, щоб підключитися тут." : "Підключися, щоб відкрити потрібний сайт та увійти у свій обліковий запис.");
    } catch (error) {
      if (current === generation && available()) { status = null; message(failure(error), true); }
    }
    render();
  }
  async function stop(showMessage = true) {
    generation++;
    localDisconnect();
    if (stopping) return;
    stopping = true; render();
    try {
      await api("/api/remote/stop", {confirmed: true});
      status = status ? {...status, connected: false, manual: false, can_stop: false} : null;
      if (showMessage) message("Ручне керування завершено. Можеш продовжити розмову з Oak у Telegram.");
    } catch (error) {
      if (showMessage && available()) message("Екран від’єднано. Не вдалося підтвердити завершення на сервері; натисни «Оновити».", true);
    } finally { stopping = false; render(); }
  }
  async function start() {
    if (!available() || busy() || starting || stopping || rfb || !status?.configured || status.manual) return;
    const requested = generation;
    if (!await confirmAction("Керувати робочим столом?", "Поточна робота Oak буде зупинена. Поки ти підключений, Oak не починатиме нові задачі та не керуватиме комп’ютером. Ти зможеш самостійно увійти у потрібний сайт.", "Підключитися") || requested !== generation || !available()) return;
    starting = true; render(); message("Готуємо робочий стіл і передаємо керування…");
    const current = ++generation;
    let issued = false;
    try {
      const result = await api("/api/remote/start", {confirmed: true});
      issued = true;
      if (current !== generation || !available()) {
        await api("/api/remote/stop", {confirmed: true}).catch(() => {});
        return;
      }
      if (typeof result?.protocol !== "string" || !/^oak-remote\.[A-Za-z0-9_-]+$/.test(result.protocol)) throw new Error();
      if (typeof result.lease !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(result.lease)) throw new Error();
      lease = result.lease;
      const url = new URL("/api/remote/socket", location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const client = new RFB(screen, url.href, {wsProtocols: [result.protocol]});
      rfb = client;
      client.resizeSession = false;
      client.scaleViewport = true;
      client.showDotCursor = true;
      client.focusOnClick = true;
      client.addEventListener("connect", () => {
        if (client !== rfb || current !== generation) { client.disconnect(); return; }
        clearTimeout(timeout); timeout = null;
        connected = true;
        status = {...status, connected: true, manual: true, can_stop: true};
        telegramSwipes(true);
        message("Ти керуєш браузером. Коли завершиш вхід, натисни «Завершити».");
        render();
      });
      client.addEventListener("disconnect", event => {
        if (client !== rfb) return;
        generation++; localDisconnect();
        status = status ? {...status, connected: false, manual: false, can_stop: false} : null;
        message(event.detail.clean ? "Ручне керування завершено." : "З’єднання з робочим столом втрачено. Онови стан, щоб підключитися знову.", !event.detail.clean);
        render();
        if (available()) void refresh();
      });
      const connectionFailure = () => {
        if (client !== rfb) return;
        void stop(false);
        message("Не вдалося підключитися до робочого столу. Онови стан і повтори спробу.", true);
      };
      client.addEventListener("securityfailure", connectionFailure);
      client.addEventListener("credentialsrequired", connectionFailure);
      timeout = setTimeout(connectionFailure, 20000);
      render();
    } catch (error) {
      if (current === generation) {
        localDisconnect();
        if (issued) await api("/api/remote/stop", {confirmed: true}).catch(() => {});
        message(failure(error), true);
      }
    } finally { starting = false; render(); }
  }
  function inputFailure() {
    inputQueue.length = 0;
    releaseButtons();
    inputFailed = true;
    if (rfb) rfb.viewOnly = true;
    clearInput(); input.blur();
    message("Не вдалося підтвердити введення. Воно могло виконатися частково. Заверши керування та підключися знову, щоб перевірити поле перед повторною спробою.", true);
    render();
  }
  async function drainInput() {
    if (inputRunning || inputFailed || !connected) return;
    inputRunning = true;
    releaseButtons(); render();
    const current = generation;
    try {
      while (inputQueue.length && connected && !inputFailed && current === generation) {
        const action = inputQueue.shift();
        try {
          if (action.lease !== lease) continue;
          const result = await api("/api/remote/type", action.text !== undefined ? {lease: action.lease, text: action.text} : {lease: action.lease, key: action.key});
          if (result?.ok !== true) throw new Error();
        } finally { if (action.text !== undefined) action.text = ""; }
      }
    } catch {
      if (current === generation && connected) inputFailure();
    } finally {
      inputRunning = false;
      if (inputQueue.length && connected && !inputFailed) void drainInput();
      else render();
    }
  }
  function enqueueInput(action) {
    if (!connected || inputFailed || !lease) return;
    if (action.text === "") return;
    if ((action.text?.length || 0) + inputQueue.reduce((size, item) => size + (item.text?.length || 0), 0) > 1000 || inputQueue.length >= 1000) {
      inputFailure(); return;
    }
    const previous = inputQueue[inputQueue.length - 1];
    if (action.text !== undefined && previous?.text !== undefined && previous.lease === lease) previous.text += action.text;
    else inputQueue.push({...action, lease});
    void drainInput();
  }
  function key(name) {
    const value = {address: "ctrl+l", enter: "Return", tab: "Tab", escape: "Escape", backspace: "BackSpace"}[name];
    if (value) enqueueInput({key: value});
  }
  function prepareInput() {
    input.value = marker;
    input.setSelectionRange(marker.length, marker.length);
  }
  function sendInput() {
    if (composing) return;
    const value = input.value;
    clearInput();
    if (!connected || inputFailed) return;
    // Diff against a non-secret marker; never retain the previously typed text.
    const prefix = value.startsWith(marker) ? marker.length : 0;
    if (!prefix) key("backspace");
    for (const part of value.slice(prefix).split(/(\t)/)) {
      if (part === "\t") key("tab");
      else enqueueInput({text: part});
    }
    if (document.activeElement === input) prepareInput();
  }
  function toggleKeyboard() {
    if (!connected || inputFailed) return;
    if (keyboardOpen) { input.blur(); return; }
    rfb.blur(); prepareInput(); input.focus({preventScroll: true});
  }
  input.addEventListener("focus", () => { keyboardOpen = true; render(); });
  input.addEventListener("blur", () => { keyboardOpen = false; clearInput(); render(); });
  input.addEventListener("compositionstart", () => { composing = true; });
  input.addEventListener("compositionend", () => { composing = false; sendInput(); });
  input.addEventListener("input", event => { if (!event.isComposing) sendInput(); });
  input.addEventListener("keydown", event => {
    if (event.isComposing || composing) return;
    const name = {Enter: "enter", Tab: "tab", Escape: "escape", Backspace: "backspace"}[event.key];
    if (name) { event.preventDefault(); key(name); prepareInput(); }
  });
  $("remote-keyboard").addEventListener("click", toggleKeyboard);
  for (const button of document.querySelectorAll("[data-remote-key]")) {
    button.addEventListener("pointerdown", event => { if (document.activeElement === input) event.preventDefault(); });
    button.addEventListener("click", () => key(button.dataset.remoteKey));
  }
  function click(button = 0) {
    if (!connected) return;
    if (dragging) { releaseButtons(); render(); return; }
    if (pointerBusy()) return;
    const wasKeyboard = keyboardOpen;
    rfb.focusOnClick = !wasKeyboard;
    mouse("mousedown", button, button === 2 ? 2 : 1);
    mouse("mouseup", button, 0);
    rfb.focusOnClick = true;
  }
  $("remote-click").addEventListener("click", () => click());
  $("remote-right").addEventListener("click", () => click(2));
  $("remote-drag").addEventListener("click", () => {
    if (!connected) return;
    if (pointerBusy() && !dragging) return;
    dragging = !dragging;
    mouse(dragging ? "mousedown" : "mouseup", 0, dragging ? 1 : 0);
    render();
  });
  function centroid() {
    const values = [...fingers.values()];
    return {x: values.reduce((sum, point) => sum + point.x, 0) / values.length, y: values.reduce((sum, point) => sum + point.y, 0) / values.length};
  }
  pad.addEventListener("pointerdown", event => {
    if (!connected || inputFailed || pointerBusy() || event.pointerType === "mouse") return;
    event.preventDefault();
    pad.setPointerCapture(event.pointerId);
    fingers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    gesture = {last: centroid(), distance: fingers.size > 1 ? 100 : 0, started: performance.now(), scroll: fingers.size > 1};
  });
  pad.addEventListener("pointermove", event => {
    if (!connected || !fingers.has(event.pointerId) || !gesture) return;
    event.preventDefault();
    fingers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    const next = centroid(), dx = next.x - gesture.last.x, dy = next.y - gesture.last.y;
    gesture.last = next; gesture.distance += Math.hypot(dx, dy);
    if (fingers.size > 1) {
      gesture.scroll = true;
      const point = coordinates();
      if (point) point.target.dispatchEvent(new WheelEvent("wheel", {bubbles: true, cancelable: true, clientX: point.clientX, clientY: point.clientY, deltaX: -dx * 3, deltaY: -dy * 3, deltaMode: 0}));
    } else if (!gesture.scroll) {
      const rect = pad.getBoundingClientRect();
      pointer.x = Math.max(0, Math.min(.999, pointer.x + dx / Math.max(rect.width, 1)));
      pointer.y = Math.max(0, Math.min(.999, pointer.y + dy / Math.max(rect.width, 1)));
      mouse("mousemove");
    }
  });
  function endTouch(event) {
    if (!fingers.has(event.pointerId)) return;
    event.preventDefault();
    fingers.delete(event.pointerId);
    if (event.type === "pointercancel") { releaseButtons(); render(); return; }
    if (!fingers.size) {
      if (gesture && !gesture.scroll && gesture.distance < 8 && performance.now() - gesture.started < 400) click();
      gesture = null;
    } else if (gesture) { gesture.last = centroid(); gesture.scroll = true; }
  }
  pad.addEventListener("pointerup", endTouch);
  pad.addEventListener("pointercancel", endTouch);
  pad.addEventListener("contextmenu", event => event.preventDefault());
  // HTTP typing may still be waiting for the desktop keymap. Do not let a
  // separate RFB click move its destination; blocked clicks are never replayed.
  for (const type of ["pointerdown", "mousedown", "wheel", "touchstart", "gesturestart", "gesturemove"]) {
    screen.addEventListener(type, event => {
      if (!connected || !pointerBusy()) return;
      event.preventDefault(); event.stopImmediatePropagation();
    }, {capture: true, passive: false});
  }
  // Mouse-up remains available during typing, errors and stop, so a prior
  // drag cannot leave a button held. The queue also releases buttons up front.
  screen.addEventListener("pointermove", event => {
    if (event.pointerType !== "mouse") return;
    const rect = canvas()?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return;
    pointer = {x: Math.max(0, Math.min(.999, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(.999, (event.clientY - rect.top) / rect.height))};
  });
  // Capture before noVNC's canvas listener so physical text and shortcuts share
  // the same ordered path as phone input. No modifier stays held on the server.
  function physicalKey(event) {
    if (event.target !== canvas()) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (event.type === "keyup" || !connected || inputFailed) return;
    if (["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock", "NumLock"].includes(event.key)) return;
    if (event.isComposing || ["Dead", "Process", "Unidentified"].includes(event.key)) {
      message("Для складного введення або IME скористайся кнопкою «Клавіатура».");
      return;
    }
    const printable = [...event.key].length === 1 && event.key.codePointAt(0) >= 32 && event.key !== "\u007f";
    const altGraph = event.getModifierState("AltGraph");
    if (printable && (!event.ctrlKey && !event.altKey && !event.metaKey || altGraph)) {
      enqueueInput({text: event.key}); return;
    }
    const special = {Enter: "Return", Tab: "Tab", Escape: "Escape", Backspace: "BackSpace", Delete: "Delete", Insert: "Insert", ArrowLeft: "Left", ArrowRight: "Right", ArrowUp: "Up", ArrowDown: "Down", Home: "Home", End: "End", PageUp: "Page_Up", PageDown: "Page_Down", " ": "space"};
    const punctuation = {Minus: "minus", Equal: "equal", BracketLeft: "bracketleft", BracketRight: "bracketright", Backslash: "backslash", Semicolon: "semicolon", Quote: "apostrophe", Comma: "comma", Period: "period", Slash: "slash", Backquote: "grave"};
    let base = special[event.key] || (/^F(?:[1-9]|1[0-2])$/.test(event.key) ? event.key : null);
    if (!base && /^Key[A-Z]$/.test(event.code)) base = event.code.slice(3).toLowerCase();
    if (!base && /^Digit[0-9]$/.test(event.code)) base = event.code.slice(5);
    if (!base) base = punctuation[event.code];
    if (!base) return;
    const modifiers = [];
    if (event.ctrlKey || event.metaKey) modifiers.push("ctrl");
    if (event.altKey) modifiers.push("alt");
    if (event.shiftKey) modifiers.push("shift");
    enqueueInput({key: [...modifiers, base].join("+")});
  }
  screen.addEventListener("keydown", physicalKey, true);
  screen.addEventListener("keyup", physicalKey, true);
  screen.addEventListener("blur", clearInput, true);
  $("remote-start").addEventListener("click", () => { void start(); });
  $("remote-stop").addEventListener("click", () => { void stop(); });
  $("remote-fullscreen").addEventListener("click", () => {
    if (!connected) return;
    const action = document.fullscreenElement ? document.exitFullscreen() : $("remote-console").requestFullscreen();
    void action.catch(() => message("Повноекранний режим недоступний у цьому клієнті. Керування працює у вікні."));
  });
  function leave() {
    if (rfb || starting) { void stop(false); message("Ручне керування завершено. Підключися знову, коли буде потрібно."); }
    else { releaseButtons(); clearInput(); }
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) leave(); else if (available()) void refresh(); });
  window.addEventListener("pagehide", leave);
  function viewport() {
    const view = window.visualViewport;
    $("remote-console").style.setProperty("--remote-height", (view?.height || window.innerHeight) + "px");
    $("remote-console").style.setProperty("--remote-top", (view?.offsetTop || 0) + "px");
  }
  window.visualViewport?.addEventListener("resize", viewport);
  window.visualViewport?.addEventListener("scroll", viewport);
  window.addEventListener("resize", viewport);
  viewport();
  render();
  return {refresh, leave, update: render, reset() { generation++; status = null; localDisconnect(); }};
}
