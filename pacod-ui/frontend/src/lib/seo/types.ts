/**
 * Shared SEO types + the small amount of URL/locale arithmetic the `<Seo>`
 * component and the sitemap endpoint both need.
 */
import type { Locale } from '$lib/paraglide/runtime';
import { LANG_PARAM } from '$lib/i18n/lang-param';

/** Per-page head input. Everything not given falls back to the site defaults. */
export interface SeoMeta {
	/** Page title WITHOUT the brand suffix — `<Seo>` appends it. */
	title: string;
	/** ~150-160 chars. Required: a page with no description is the single most common SEO defect. */
	description: string;
	/**
	 * Canonical path, e.g. `/about`. Defaults to the current pathname.
	 * Use `canonicalUrl` instead when the canonical lives on another origin.
	 */
	canonicalPath?: string;
	/**
	 * Absolute canonical URL. Overrides `canonicalPath`. This exists for the
	 * legal pages, which are served on BOTH registrable domains from the same
	 * files and must point at one of them.
	 */
	canonicalUrl?: string;
	/** Absolute URL or root-relative path. Defaults to the site's OG image. */
	ogImage?: string;
	ogImageAlt?: string;
	ogType?: 'website' | 'article' | 'product';
	/** Emit `noindex,nofollow`. Use for app/auth surfaces and cross-domain duplicates. */
	noindex?: boolean;
	/**
	 * Emit the `?lang=` hreflang set. True only for pages that genuinely exist in
	 * all four locales — i.e. the public marketing surface.
	 */
	localized?: boolean;
	/** Don't append the brand suffix (the landing page title already contains it). */
	rawTitle?: boolean;
}

/** BCP-47-ish locale codes for `og:locale`, which wants the underscore form. */
export const OG_LOCALE: Record<Locale, string> = {
	en: 'en_US',
	bg: 'bg_BG',
	de: 'de_DE',
	ru: 'ru_RU'
};

/** Strip a trailing slash so `/about/` and `/about` never produce two canonicals. */
export function normalizePath(path: string): string {
	if (!path || path === '/') return '/';
	return path.replace(/\/+$/, '') || '/';
}

/**
 * The URL of a page in a specific locale.
 *
 * The bare URL is locale-negotiated (cookie → Accept-Language → en), so it is
 * NOT the English URL — it is `x-default`. Each locale gets `?lang=xx`,
 * including English. See `$lib/i18n/lang-param.ts`.
 */
export function localizedUrl(origin: string, path: string, locale: Locale): string {
	return `${origin}${normalizePath(path)}?${LANG_PARAM}=${locale}`;
}

/** The negotiated, language-neutral URL — the `x-default` target. */
export function defaultUrl(origin: string, path: string): string {
	return `${origin}${normalizePath(path)}`;
}
