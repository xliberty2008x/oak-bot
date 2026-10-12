// Shared native widgets only. Callers own schema validation, values and transport.
// This module never reads values or installs event, network or storage handlers.
export function updateFormControl({label, control}, spec) {
  const type = spec.type ?? 'text';
  if (!['text', 'email', 'password', 'otp', 'checkbox', 'select'].includes(type)) throw new Error('Unsupported control');
  control.id = spec.id;
  label.htmlFor = spec.id;
  label.textContent = spec.label;
  control.required = spec.required === true;
  control.autocomplete = 'off';
  if (spec.name !== undefined) control.name = spec.name;
  if (spec.describedBy !== undefined) control.setAttribute('aria-describedby', spec.describedBy);
  if (spec.privateInput === true) {
    control.setAttribute('autocapitalize', 'off');
    control.spellcheck = false;
  }
  if (type === 'select') {
    const empty = document.createElement('option');
    empty.value = ''; empty.textContent = 'Обери варіант';
    empty.disabled = spec.placeholderDisabled === true;
    empty.selected = true;
    const options = [empty];
    for (const item of spec.options) {
      const option = document.createElement('option');
      option.value = item.value; option.textContent = item.label; options.push(option);
    }
    control.replaceChildren(...options);
  } else {
    control.type = type === 'otp' ? 'text' : type;
    control.maxLength = spec.maxLength ?? 2000;
    if (type === 'otp' || spec.numeric === true) control.inputMode = 'numeric';
  }
}

export function createFormControl(spec) {
  const widgets = {
    label: document.createElement('label'),
    control: document.createElement(spec.type === 'select' ? 'select' : 'input'),
  };
  updateFormControl(widgets, spec);
  return widgets;
}
