/**
 * Which UI locales a deployment offers in its language switchers.
 *
 * Every locale stays compiled in (Paraglide, en/bg/de/ru) and `en` stays the
 * base locale; `ENABLED_LOCALES` (CSV, read in the root `+layout.server.ts`)
 * only narrows what the switchers offer. Unknown codes are ignored, and a value
 * that names no known locale falls back to all of them, so a typo can never
 * leave a site without a language.
 */
import { locales, type Locale } from '$lib/paraglide/runtime';

export function parseEnabledLocales(raw: string | undefined | null): Locale[] {
	const known = locales as readonly string[];
	const picked = (raw ?? '')
		.split(',')
		.map((s) => s.trim().toLowerCase())
		.filter((s, i, all) => known.includes(s) && all.indexOf(s) === i) as Locale[];
	return picked.length > 0 ? picked : [...(locales as readonly Locale[])];
}

/** Keep only the entries whose locale is enabled (all of them when unknown). */
export function onlyEnabled<T extends { code: Locale }>(
	items: readonly T[],
	enabled: readonly Locale[] | undefined
): T[] {
	if (!enabled || enabled.length === 0) return [...items];
	return items.filter((i) => enabled.includes(i.code));
}
