// Placement of the floating Copilot window (04 §6.1), as pure functions over display work areas so they are
// testable without Electron. A rectangle is {x, y, width, height}.

export const MIN_WIDTH = 340;
export const MIN_HEIGHT = 420;
export const DEFAULT_WIDTH = 400;
export const DEFAULT_HEIGHT = 640;
export const EDGE_MARGIN = 16;
/** A restored window keeps at least this much of its top edge on some display so it can still be dragged. */
const GRAB_WIDTH = 120;
const GRAB_HEIGHT = 32;

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

/** Bottom-right of the primary display's work area. */
export function defaultBounds(workArea, width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT) {
  const w = Math.min(width, Math.max(MIN_WIDTH, workArea.width - 2 * EDGE_MARGIN));
  const h = Math.min(height, Math.max(MIN_HEIGHT, workArea.height - 2 * EDGE_MARGIN));
  return {
    x: workArea.x + workArea.width - w - EDGE_MARGIN,
    y: workArea.y + workArea.height - h - EDGE_MARGIN,
    width: w,
    height: h,
  };
}

function overlap(a, b) {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? { w, h } : null;
}

/** True when enough of the title bar is on some display to grab it. */
export function reachable(rect, workAreas) {
  const bar = { x: rect.x, y: rect.y, width: rect.width, height: GRAB_HEIGHT };
  return workAreas.some((wa) => {
    const o = overlap(bar, wa);
    return o !== null && o.w >= Math.min(GRAB_WIDTH, rect.width) && o.h >= GRAB_HEIGHT / 2;
  });
}

/**
 * Saved bounds checked against the displays now connected: the size is kept (within limits), and a position that is
 * no longer reachable moves the window back to the default place on the primary display.
 */
export function fitBounds(saved, workAreas, primary) {
  const valid =
    saved && ["x", "y", "width", "height"].every((k) => Number.isFinite(saved[k])) && saved.width > 0 && saved.height > 0;
  if (!valid) return defaultBounds(primary);
  const width = clamp(Math.round(saved.width), MIN_WIDTH, 4000);
  const height = clamp(Math.round(saved.height), MIN_HEIGHT, 4000);
  const rect = { x: Math.round(saved.x), y: Math.round(saved.y), width, height };
  return reachable(rect, workAreas) ? rect : defaultBounds(primary, width, height);
}
