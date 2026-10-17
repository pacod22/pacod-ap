/**
 * Browser-side API client.
 *
 * Browser code calls `/api/*` paths (same-origin). The SvelteKit
 * `hooks.server.ts` proxy converts the session cookie into a Bearer
 * header before forwarding to the Python backend. So this client
 * just needs to handle JSON, errors, and abort signals.
 *
 * Logging uses `console` (not the server logger) since this module runs
 * in the browser. In dev, every request and response is logged to the
 * console. In production only warnings and errors are emitted.
 */

const API_BASE_URL = '/api';
const DEV = import.meta.env.DEV;

export class ApiError extends Error {
	status: number;
	body: unknown;
	constructor(status: number, message: string, body?: unknown) {
		super(message);
		this.status = status;
		this.body = body;
	}
}

export interface ApiClientOptions {
	fetch?: typeof fetch;
	baseUrl?: string;
}

export class ApiClient {
	#fetch: typeof fetch;
	#baseUrl: string;

	constructor(opts: ApiClientOptions = {}) {
		this.#fetch = opts.fetch ?? globalThis.fetch;
		this.#baseUrl = opts.baseUrl ?? API_BASE_URL;
	}

	async request<T>(path: string, init: RequestInit = {}): Promise<T> {
		const url = path.startsWith('http') ? path : `${this.#baseUrl}${path}`;
		const method = (init.method ?? 'GET').toUpperCase();
		const headers = new Headers(init.headers);
		if (!headers.has('Accept')) headers.set('Accept', 'application/json');
		if (init.body && !headers.has('Content-Type')) {
			headers.set('Content-Type', 'application/json');
		}

		const start = performance.now();
		if (DEV) {
			console.debug(`[api] → ${method} ${path}`);
		}

		const res = await this.#fetch(url, { ...init, headers, credentials: 'same-origin' });

		const ms = Math.round(performance.now() - start);

		if (res.status === 204) {
			if (DEV) console.debug(`[api] ← ${method} ${path} 204 (${ms}ms)`);
			return undefined as T;
		}

		const isJson = res.headers.get('content-type')?.includes('application/json');
		const body = isJson ? await res.json() : await res.text();

		if (!res.ok) {
			const message =
				(isJson && (body as { detail?: string; message?: string }).detail) ||
				(typeof body === 'string' ? body : res.statusText);

			if (res.status >= 500) {
				console.error(`[api] ← ${method} ${path} ${res.status} (${ms}ms)`, body);
			} else {
				console.warn(`[api] ← ${method} ${path} ${res.status} (${ms}ms)`, message);
			}

			throw new ApiError(res.status, message, body);
		}

		if (DEV) {
			console.debug(`[api] ← ${method} ${path} ${res.status} (${ms}ms)`);
		}

		return body as T;
	}

	get<T>(path: string, init?: RequestInit) {
		return this.request<T>(path, { ...init, method: 'GET' });
	}
	post<T>(path: string, body?: unknown, init?: RequestInit) {
		return this.request<T>(path, {
			...init,
			method: 'POST',
			body: body ? JSON.stringify(body) : undefined
		});
	}
	put<T>(path: string, body?: unknown, init?: RequestInit) {
		return this.request<T>(path, {
			...init,
			method: 'PUT',
			body: body ? JSON.stringify(body) : undefined
		});
	}
	patch<T>(path: string, body?: unknown, init?: RequestInit) {
		return this.request<T>(path, {
			...init,
			method: 'PATCH',
			body: body ? JSON.stringify(body) : undefined
		});
	}
	delete<T>(path: string, init?: RequestInit) {
		return this.request<T>(path, { ...init, method: 'DELETE' });
	}
}

export const api = new ApiClient();
