/**
 * Who is calling, as the backend should see it.
 *
 * Signing in is pacod-id's (lib/server/identity.ts); this module only reads the
 * real client off an incoming request, so that server-to-server calls to the
 * backend (lib/server/api.ts) are logged against the user's address and browser
 * rather than this container's.
 */

/**
 * The real client's IP + browser User-Agent (Caddy sets X-Forwarded-For /
 * X-Real-IP to the real client, overwriting whatever the browser sent).
 */
export interface ClientContext {
	ip?: string;
	userAgent?: string;
}

/** Extract the real client IP + User-Agent from an incoming request's headers. */
export function clientContextFrom(request: Request): ClientContext {
	const xff = request.headers.get('x-forwarded-for');
	const ip = (xff ? xff.split(',')[0].trim() : '') || request.headers.get('x-real-ip') || undefined;
	const userAgent = request.headers.get('user-agent') || undefined;
	return { ip, userAgent };
}
