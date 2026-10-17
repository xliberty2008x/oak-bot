"use strict";

// Text only. Callers assign the result through textContent or existing safe
// renderers; substitution never parses markup or evaluates template expressions.
export function createLocale(catalog = {}) {
  const strings = new Map();
  for (const [key, value] of Object.entries(catalog.strings || {})) {
    if (/^[A-Za-z][A-Za-z0-9_.]{0,99}$/.test(key) && typeof value === "string" && value.length <= 2048) strings.set(key, value);
  }
  function scalar(value) {
    if (value === null) return "";
    if (typeof value === "string" || typeof value === "boolean" || Number.isSafeInteger(value)) return String(value);
    throw new TypeError("Text substitutions must be strings, safe integers, booleans or null.");
  }
  return {
    locale: "uk",
    t(key, values = {}) {
      const template = strings.get(key) ?? strings.get("common.unavailable") ?? "Текст недоступний";
      return template.replace(/\{([a-z][a-z0-9_]*)\}/g, (match, name) => Object.hasOwn(values, name) ? scalar(values[name]) : match);
    },
  };
}
