---
version: alpha
name: AttendAI
description: A facility attendance desk with bright work surfaces and a deep-blue camera rail.
colors:
  primary: "#006779"
  primary-hover: "#005566"
  text: "#153140"
  muted: "#526a79"
  surface: "#ffffff"
  background: "#f0f5f7"
  border: "#d5e2e9"
  nav: "#123241"
  nav-text: "#f3f9fb"
  nav-muted: "#b8d1dc"
  nav-accent: "#8de3e7"
  success: "#19664f"
  warning: "#805009"
  danger: "#a32b37"
  scrollbar: "#778e9a"
  scrollbar-hover: "#526a79"
  scrollbar-track: "#eaf1f5"
typography:
  body:
    fontFamily: "IBM Plex Sans, Segoe UI, sans-serif"
  display:
    fontFamily: "IBM Plex Sans, Segoe UI, sans-serif"
  mono:
    fontFamily: "IBM Plex Mono, Consolas, monospace"
rounded:
  control: "8px"
  card: "14px"
spacing:
  page: "28px"
  panel: "24px"
components:
  button:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "8px"
  button-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "14px"
  dialog:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "16px"
  supporting-text:
    textColor: "{colors.muted}"
  navigation:
    backgroundColor: "{colors.nav}"
    textColor: "{colors.nav-text}"
  navigation-caption:
    backgroundColor: "{colors.nav}"
    textColor: "{colors.nav-muted}"
  navigation-mark:
    backgroundColor: "{colors.nav-accent}"
    textColor: "{colors.nav}"
  divider:
    backgroundColor: "{colors.border}"
  success-label:
    textColor: "{colors.success}"
  warning-label:
    textColor: "{colors.warning}"
  error-label:
    textColor: "{colors.danger}"
  scrollbar-thumb:
    backgroundColor: "{colors.scrollbar}"
  scrollbar-thumb-hover:
    backgroundColor: "{colors.scrollbar-hover}"
  scrollbar-track:
    backgroundColor: "{colors.scrollbar-track}"
---

# AttendAI design system

## Overview
The interface serves a facility front desk. A shift operator can glance at the latest arrival and faces waiting for a name, then act from the same strip. The camera wall uses compact previews on a dark backdrop. Face review places camera samples next to the naming form.

## Colors
The pale blue canvas frames white content panels. Cyan accents mark the current task and primary actions. The arrival strip uses a cyan surface for the live view and a warm amber surface for face review. Green indicates a successful or live state. Red identifies errors and destructive actions. Controls use visible focus rings and text labels for state.

## Typography
IBM Plex Sans supplies semibold page titles and comfortable body copy. IBM Plex Mono supplies clock times, diagnostic values and identifiers. The app serves both fonts locally. Segoe UI and Consolas provide fallbacks.

## Layout
The desktop monitor has a 230px rail, a camera grid with up to three columns and an unnamed-face queue. The latest arrival and review controls stay visible while the camera wall scrolls. Camera feeds reserve their frame and contain the full image. Name People puts face samples next to the naming form. On phones, the grid and evidence stack in reading order and navigation opens in the shared modal.

## Elevation & Depth
Separators and surface tones establish hierarchy. Camera metadata sits over a dark scrim. Dialogs use a dimmed backdrop, and saved/failed operations use the shared notification region.

## Shapes
Controls use 8px radii, and cards and camera tiles use 14px radii. Face crops retain a reserved frame size. The brand mark and the two live strip icons use an offset corner inspired by a viewfinder.

## Components
`src/styles.css` owns runtime tokens, and this file mirrors their accepted values. `scripts/check_tokens.py` compares the `colors` entries with their `--color-*` variables. Shared page headings, buttons, dialogs, fields, empty states and pagination live in `src/components.tsx`. `src/monitoring.tsx` owns the camera wall and sidebar, and `src/naming.tsx` owns sample review and naming. Timeline values follow the workspace time zone. Native date and select controls follow platform keyboard behavior.

The camera wall shows the active operating mode outside the video. The Manage camera dialog keeps test measurements next to the operating mode selector. Applying a mode is a separate action so navigating a select does not change attendance behavior.

## Do's and Don'ts
- Keep camera connection and automatic attendance controls explicit.
- Show the saved source and timestamp with each face group.
- Keep the current group pinned while an administrator edits identity information.
- Label uncertain liveness. Keep video availability, face analysis, and roster readiness separate. Use the live camera badge when both the preview and analysis are current.
