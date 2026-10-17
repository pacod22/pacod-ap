/**
 * The closed set of conversions this app reports.
 *
 * Its own module — not `track.ts` — because the server needs it too:
 * `lib/server/site-config.ts` parses the per-platform event-id maps
 * (`X_EVENT_IDS`, `LINKEDIN_CONVERSION_IDS`) against it, and `track.ts` imports
 * browser-only stores.
 */
export const CONVERSION_EVENTS = ['newsletter_subscribe', 'sign_up'] as const;

/** Conversions we report. Keep this a closed set — one name per real event. */
export type ConversionEvent = (typeof CONVERSION_EVENTS)[number];

/** Per-event platform ids; an event absent from the map is not sent to that platform. */
export type ConversionIdMap = Partial<Record<ConversionEvent, string>>;
