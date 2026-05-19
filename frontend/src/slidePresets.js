// Slide preset configuration per template/mode.
// Each entry declares the available preset values, the default selection,
// and an optional hint function that annotates each button.
//
// Add a new key here to support additional templates or modes without
// touching any shared UI components.

export const SLIDE_PRESET_CONFIGS = {
  default: {
    presets: [4, 5, 7, 10],
    default: 7,
    hint: null,
  },
  dark_manual: {
    presets: [7, 10, 12, 15],
    default: 10,
    // Shows how many content slots the selection creates (hook + CTA = 2 structural)
    hint: (n) => `${n - 2} content slides`,
  },
}

export function resolvePresetConfig(templateType, contentMode) {
  if (templateType === 'dark' && contentMode === 'manual') {
    return SLIDE_PRESET_CONFIGS.dark_manual
  }
  return SLIDE_PRESET_CONFIGS.default
}
