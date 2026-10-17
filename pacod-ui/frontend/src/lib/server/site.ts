/**
 * The analytics `site` value of this frontend.
 *
 * The backend receives it in the `X-Pacod-Site` header on every call this server
 * makes, so LLM usage and agent turns can be attributed to the surface that
 * caused them. Other backend sites (`openwebui`, `internal`) never originate here.
 */
export type FrontendSite = 'pacod';

export const SITE_HEADER = 'X-Pacod-Site';
/**
 * Marks a server-side (SSR) call. The page request that triggered it is already
 * counted here, so the backend does not record a second, API-kind row for it.
 */
export const INTERNAL_HEADER = 'X-Pacod-Internal';

export function currentSite(): FrontendSite {
	return 'pacod';
}
