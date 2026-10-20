// Single source of truth for the legal-entity facts shown across the public
// info & legal pages (/about, /contact, /privacy, /terms, /cookies).
//
// The values below are PLACEHOLDERS. Whoever runs a deployment is its operator
// (the data controller in the Privacy Policy, the contracting party in the
// Terms) and must put their own legal entity here before going live. Only
// controller / IP / liability / contact clauses reference the entity.
//
// The legal texts are written for an operator registered in Bulgaria (the
// ЕИК, Sofia jurisdiction, Bulgarian and EU law); an operator elsewhere must
// adapt the documents themselves, not only these values.
//
// Update these values here and every localized legal document picks them up.
import type { Locale } from '$lib/paraglide/runtime';

export const COMPANY = {
	/** PACOD's product brand — used by the PACOD documents only. */
	brand: 'PACOD',
	/** Bulgarian Unified Identification Code (ЕИК/UIC). */
	eik: '000000000',
	/** VAT number, or null when not VAT-registered. */
	vat: null as string | null,
	/** Single monitored inbox for privacy, legal and general contact. */
	email: 'legal@example.com',
	/** Public contact phone (E.164-ish, human-grouped). */
	phone: '+359 000 000 000',
	/** Canonical product domain. */
	domain: 'example.com'
} as const;

/**
 * Registered entity name per UI locale. For a Bulgarian entity show both the
 * Cyrillic registered name and its Latin rendering, so the entity is
 * unambiguous whichever page you land on.
 */
export const legalName: Record<Locale, string> = {
	en: 'Example Operator Ltd (Примерен Оператор ЕООД)',
	bg: 'Примерен Оператор ЕООД (Example Operator Ltd)',
	de: 'Example Operator Ltd (Примерен Оператор ЕООД)',
	ru: 'Example Operator Ltd (Примерен Оператор ЕООД)'
};

/** Registered address per UI locale. */
export const address: Record<Locale, string> = {
	en: 'Sofia, Bulgaria',
	bg: 'София, България',
	de: 'Bulgarien',
	ru: 'София, Болгария'
};

/** City / country used in governing-law and jurisdiction clauses. */
export const jurisdiction: Record<Locale, { city: string; country: string }> = {
	en: { city: 'Sofia', country: 'Bulgaria' },
	bg: { city: 'София', country: 'България' },
	de: { city: 'Sofia', country: 'Bulgarien' },
	ru: { city: 'София', country: 'Болгария' }
};

/**
 * Canonical, locale-independent version id of the Terms & Conditions. pacod-id records it
 * with each sign-up's consent — the realm's `terms_version` in
 * pacod-api/docker/pacod-id/pacod-id*.toml must be the same value. Bump all three
 * **together with** `lastUpdated.terms` whenever the Terms change, so we can tell which
 * version a given user accepted. Keep it sortable (ISO date).
 */
export const TERMS_VERSION = '2026-10-16';

const OCTOBER_16: Record<Locale, string> = {
	en: '16 October 2026',
	bg: '16 октомври 2026 г.',
	de: '16. Oktober 2026',
	ru: '16 октября 2026 г.'
};

const OCTOBER_20: Record<Locale, string> = {
	en: '20 October 2026',
	bg: '20 октомври 2026 г.',
	de: '20. Oktober 2026',
	ru: '20 октября 2026 г.'
};

/**
 * "Last updated / Effective" date per document, localized. Bump ONLY the
 * document that changed — a Terms page claiming a revision that never happened
 * is itself a misstatement. (Terms: also bump `TERMS_VERSION`.)
 */
export const lastUpdated: Record<'terms' | 'privacy' | 'cookies', Record<Locale, string>> = {
	// 2026-10-16: no Sign in with Google any more (accounts are the platform's own sign-in
	// service, pacod-id).
	terms: OCTOBER_16,
	// 2026-10-16: "Your account and sign-in" (pacod-id) replaces the Google sign-in section.
	privacy: OCTOBER_16,
	// 2026-10-16: the sign-in cookies (app_session, pid_login, the sign-in service's own).
	// 2026-10-20: the separate chat app's single sign-on cookie is gone with that app.
	cookies: OCTOBER_20
};

/** What the PACOD documents cover. */
export const pacodService: Record<Locale, string> = {
	en: 'PACOD (www.example.com)',
	bg: 'PACOD (www.example.com)',
	de: 'PACOD (www.example.com)',
	ru: 'PACOD (www.example.com)'
};

/** Support / phone business hours shown on the contact page. */
export const businessHours: Record<Locale, string> = {
	en: 'Mon–Fri, 9:00–18:00 (EET, UTC+2)',
	bg: 'Пон.–Пет., 9:00–18:00 ч. (EET, UTC+2)',
	de: 'Mo–Fr, 9:00–18:00 Uhr (OEZ, UTC+2)',
	ru: 'Пн–Пт, 9:00–18:00 (EET, UTC+2)'
};

/** Data-retention periods, localized (used in the Privacy Policy). */
export const retention: Record<
	Locale,
	{ account: string; logs: string; analytics: string; activity: string }
> = {
	// `analytics` mirrors ANALYTICS_RETENTION_DAYS (pacod-api, default 180);
	// `activity` mirrors ACTIVITY_LOG_RETENTION_MONTHS (default 6) — the per-account
	// activity log and IP-address history.
	en: { account: '90 days', logs: '12 months', analytics: '180 days', activity: '6 months' },
	bg: { account: '90 дни', logs: '12 месеца', analytics: '180 дни', activity: '6 месеца' },
	de: { account: '90 Tagen', logs: '12 Monaten', analytics: '180 Tagen', activity: '6 Monaten' },
	ru: { account: '90 дней', logs: '12 месяцев', analytics: '180 дней', activity: '6 месяцев' }
};

/** Label for the Bulgarian Unified Identification Code, per locale. */
export const eikLabel: Record<Locale, string> = {
	en: 'ЕИК',
	bg: 'ЕИК',
	de: 'ЕИК (UIC)',
	ru: 'ЕИК (UIC)'
};

/**
 * One-line company identification for site footers (imprint):
 * "Example Operator Ltd (Примерен Оператор ЕООД) · ЕИК 000000000 · Sofia, Bulgaria".
 */
export function companyLine(locale: Locale): string {
	return `${legalName[locale]} · ${eikLabel[locale]} ${COMPANY.eik} · ${address[locale]}`;
}
