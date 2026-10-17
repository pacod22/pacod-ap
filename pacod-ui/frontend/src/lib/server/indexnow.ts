/**
 * IndexNow key file (indexnow.org): `GET /<key>.txt` answers with the key, which
 * proves to Bing, Yandex, Seznam and Naver that whoever submits URLs for this
 * host controls it. Each site has its OWN key (`INDEXNOW_KEY` per container).
 *
 * Served from a hook rather than a route: the path is dynamic, and as a route it
 * would also need a param matcher. No
 * route means no page-view row and no `route` label either, which is what a
 * key file should produce.
 *
 * Submitting URLs is `make indexnow-ping` (pacod-api/Makefile) after a deploy.
 */

/**
 * The key-file response for `path`, or null when this request is something else.
 * Blank key or indexing disabled ⇒ the file does not exist (null → normal 404).
 */
export function indexNowResponse(path: string, key: string, indexing: boolean): Response | null {
	if (!key || !indexing || path !== `/${key}.txt`) return null;
	return new Response(key, {
		status: 200,
		headers: {
			'content-type': 'text/plain; charset=utf-8',
			'cache-control': 'public, max-age=86400'
		}
	});
}
