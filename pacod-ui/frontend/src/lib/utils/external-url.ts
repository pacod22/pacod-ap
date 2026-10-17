/**
 * An http(s) URL from a backend response, or null. Svelte does not sanitize
 * `href`, so a `javascript:` (or otherwise non-web) value is dropped instead of
 * rendered as a link.
 */
export function safeExternalUrl(url: string | null | undefined): string | null {
	if (!url) return null;
	try {
		const parsed = new URL(url);
		return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? url : null;
	} catch {
		return null;
	}
}
