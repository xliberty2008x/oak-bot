// Feature-detected Telegram controls; browser dialogs remain the fallback.
export function createTelegramBridge({app, root, canBack, back}) {
  let connected = null, pending = null;
  const events = ['safeAreaChanged', 'contentSafeAreaChanged'];
  function update() {
    const telegram = app();
    for (const [kind, data] of [['safe', telegram?.safeAreaInset], ['content', telegram?.contentSafeAreaInset]]) {
      for (const side of ['top', 'bottom', 'left', 'right']) {
        const value = data?.[side];
        root.style.setProperty('--native-' + kind + '-' + side, (Number.isFinite(value) && value >= 0 ? Math.min(value, 300) : 0) + 'px');
      }
    }
    try { if (canBack()) telegram?.BackButton?.show(); else telegram?.BackButton?.hide(); } catch {}
  }
  function connect() {
    const telegram = app();
    if (telegram !== connected) {
      destroy(); connected = telegram;
      try { connected?.BackButton?.onClick?.(back); } catch {}
      for (const event of events) connected?.onEvent?.(event, update);
    }
    update();
  }
  function destroy() {
    try { connected?.BackButton?.offClick?.(back); connected?.BackButton?.hide(); } catch {}
    for (const event of events) connected?.offEvent?.(event, update);
    connected = null;
    pending?.(false); pending = null;
  }
  function confirm(text) {
    const telegram = app();
    if (!telegram?.showConfirm || telegram.isVersionAtLeast?.('6.2') === false || pending) return null;
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    const finish = accepted => { if (pending === finish) { pending = null; resolve(accepted === true); } };
    pending = finish;
    try { telegram.showConfirm(text, finish); return promise; }
    catch { pending = null; return null; }
  }
  function feedback() { try { app()?.HapticFeedback?.selectionChanged?.(); } catch {} }
  return {connect, update, destroy, confirm, feedback};
}
