// Only public Telegram colour hints enter the panel's existing theme tokens.
const colour = value => typeof value === 'string' && /^#[a-f0-9]{6}$/i.test(value) ? value : null;
function luminance(hex) {
  const rgb = [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
}
function readable(value, backgrounds, fallback) {
  const candidate = colour(value);
  return candidate && backgrounds.every(bg => {
    const a = luminance(candidate), b = luminance(bg);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05) >= 4.5;
  }) ? candidate : fallback;
}
export function createTelegramTheme({app, root}) {
  let connected = null;
  function update() {
    const telegram = app(), light = telegram?.colorScheme === 'light', params = telegram?.themeParams || {};
    const fallback = light ? {bg: '#f4f8f5', secondary: '#ffffff', ink: '#152c20', muted: '#425c4c'} :
      {bg: '#050a08', secondary: '#0a1612', ink: '#ecf5f0', muted: '#8fa59c'};
    const background = (value, otherwise) => {
      const candidate = colour(value);
      return candidate && (light ? luminance(candidate) >= .7 : luminance(candidate) <= .035) ? candidate : otherwise;
    };
    const bg = background(params.bg_color, fallback.bg), secondary = background(params.secondary_bg_color, fallback.secondary);
    root.dataset.theme = light ? 'light' : 'dark';
    for (const [name, value] of Object.entries({'--bg': bg, '--bg-2': secondary,
      '--ink': readable(params.text_color, [bg, secondary], fallback.ink),
      '--muted': readable(params.hint_color, [bg, secondary], fallback.muted)})) root.style.setProperty(name, value);
    try { telegram?.setHeaderColor?.(bg); telegram?.setBackgroundColor?.(bg); } catch {}
  }
  function connect() {
    const telegram = app();
    if (telegram !== connected) {
      connected?.offEvent?.('themeChanged', update);
      connected = telegram; connected?.onEvent?.('themeChanged', update);
    }
    update();
  }
  return {connect, destroy: () => { connected?.offEvent?.('themeChanged', update); connected = null; }};
}
