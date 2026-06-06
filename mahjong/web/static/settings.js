// Client settings registry (profile-and-settings.md § Part A).
//
// The settings menu is driven by this descriptor list: one row per entry.
// Adding a future toggle = appending a descriptor here, with no layout edit.
//
// Each descriptor:
//   key     — stable id; the <settings-menu> reads the current value and
//             dispatches `setting-cycle {key}` when the row is activated.
//   label   — display label.
//   values  — ordered cycle of allowed values (the control advances through it).
//   hotkey  — display string for the existing keyboard chord (kept working).
//   scope   — "global": persisted app-level preference (theme, tiles).
//             "table":  live pane visibility; only meaningful at a table, so
//                       the row is disabled (with a hint) in the lobby/profile.
export const SETTINGS = [
  { key: "theme", label: "Theme", values: ["dark", "light"], hotkey: "Alt+T", scope: "global" },
  { key: "tile-style", label: "Tiles", values: ["ascii", "unicode"], hotkey: "Alt+U", scope: "global" },
  { key: "sound", label: "Sound", values: ["on", "off"], hotkey: "", scope: "global" },
  { key: "pane-chat", label: "Chat pane", values: ["off", "on"], hotkey: "Alt+C", scope: "table" },
  { key: "pane-stats", label: "Stats pane", values: ["off", "on"], hotkey: "Alt+S", scope: "table" },
  { key: "pane-spectator", label: "Spectator pane", values: ["off", "on"], hotkey: "Alt+W", scope: "table" },
];
