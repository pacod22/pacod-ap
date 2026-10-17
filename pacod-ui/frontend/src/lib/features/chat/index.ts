/**
 * Chat SSE streaming helpers + feedback submission.
 *
 * POST /api/v1/chat  → Server-Sent Events stream
 *
 * Event types emitted by the backend:
 *   token       — { text: string }                         streaming text chunk
 *   tool_call   — { name: string; args: Record }           tool invocation
 *   citation    — { source_id, source_type, title, url, legal_year, chunk_id, snippet }
 *   thinking    — { status: "start" | "end" }              extended thinking boundary
 *   suggestions — { questions: string[] }                  follow-up suggestions
 *   error       — { code: string; message: string; http_status: number }
 *   done        — { turn_id: string; usage: {...}; quota: {...}; duration_ms: number }  turn complete
 *
 * POST /api/v1/feedback  → 204 No Content
 */

export interface ConversationMessage {
	role: 'user' | 'assistant';
	content: string;
}

export interface ChatRequest {
	message: string;
	session_id: string;
	legal_year?: number | null;
	history?: ConversationMessage[];
	/** Which profile (agent) to chat with. Omitted → the user's sole profile. */
	profile_id?: string | null;
	/** Agent-editor variant: 'dev' runs the agent's admin-only dev deployment. */
	agent_variant?: 'prod' | 'dev';
}

// ---------------------------------------------------------------------------
// Profiles (agents) the user may chat with
// ---------------------------------------------------------------------------

export interface MyProfile {
	id: string;
	slug: string;
	name: string;
	description: string | null;
	legal_year: number;
}

/** Profiles the current user is allowed to chat with (admins see all active). */
export async function fetchMyProfiles(): Promise<MyProfile[]> {
	const res = await fetch('/api/v1/my-profiles', { credentials: 'same-origin' });
	if (!res.ok) throw new Error(`Failed to load profiles: ${res.status}`);
	return res.json() as Promise<MyProfile[]>;
}

export interface TokenEvent {
	type: 'token';
	text: string;
}

export interface ToolCallEvent {
	type: 'tool_call';
	name: string;
	args: Record<string, unknown>;
}

export interface CitationEvent {
	type: 'citation';
	source_id: string;
	source_type: string;
	title: string;
	url: string | null;
	legal_year: number | null;
	/** Present for KB hits; null for web/structured citations. Used for feedback. */
	chunk_id: string | null;
	/** First ~200 chars of the chunk text. */
	snippet: string | null;
}

export interface ThinkingEvent {
	type: 'thinking';
	status: 'start' | 'end';
}

export interface SuggestionsEvent {
	type: 'suggestions';
	questions: string[];
}

export interface ErrorEvent {
	type: 'error';
	code: string;
	message: string;
	http_status: number;
}

/** Per-LLM-call token accounting for a completed turn. */
export interface TokenUsage {
	input_tokens: number;
	output_tokens: number;
	cached_tokens: number;
	cost_usd: number;
}

export interface DoneEvent {
	type: 'done';
	/** Stable turn identifier — pass to POST /api/v1/feedback. */
	turn_id: string;
	usage: TokenUsage;
	quota: Record<string, unknown>;
	/** End-to-end response time in milliseconds. */
	duration_ms: number;
}

export type ChatEvent =
	| TokenEvent
	| ToolCallEvent
	| CitationEvent
	| ThinkingEvent
	| SuggestionsEvent
	| ErrorEvent
	| DoneEvent;

// ---------------------------------------------------------------------------
// Chat history
// ---------------------------------------------------------------------------

export interface SessionSummary {
	session_id: string;
	title: string;
	started_at: string;
	last_turn_at: string;
	turn_count: number;
}

export interface SessionTurn {
	turn_id: string | null;
	user_message: string;
	response: string | null;
	occurred_at: string;
	/** Summary metadata — null/empty for legacy turns recorded before it was tracked. */
	duration_ms: number | null;
	tool_names: string[];
	usage: TokenUsage | null;
	/** The Sources shown under the answer, same shape as the SSE citation payload minus `type`. Empty for legacy turns. */
	citations: Omit<CitationEvent, 'type'>[];
}

export async function fetchSessions(
	days = 30,
	profileId?: string | null
): Promise<SessionSummary[]> {
	const qs = new URLSearchParams({ days: String(days) });
	if (profileId) qs.set('profile_id', profileId);
	const res = await fetch(`/api/v1/chat/sessions?${qs}`, { credentials: 'same-origin' });
	if (!res.ok) throw new Error(`Failed to load sessions: ${res.status}`);
	return res.json() as Promise<SessionSummary[]>;
}

export async function fetchSessionTurns(
	sessionId: string,
	profileId?: string | null
): Promise<SessionTurn[]> {
	const qs = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
	const res = await fetch(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/turns${qs}`, {
		credentials: 'same-origin'
	});
	if (!res.ok) throw new Error(`Failed to load session: ${res.status}`);
	return res.json() as Promise<SessionTurn[]>;
}

/** Hide a chat session from the caller's history (soft-delete). */
export async function deleteSession(sessionId: string): Promise<void> {
	const res = await fetch(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`, {
		method: 'DELETE',
		credentials: 'same-origin'
	});
	if (!res.ok && res.status !== 204) {
		throw new Error(`Failed to delete chat: ${res.status}`);
	}
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

export interface FeedbackRequest {
	session_id: string;
	turn_id: string;
	query: string;
	chunk_id: string;
	source_id: string;
	relevance: 1 | -1;
	profile_id?: string | null;
}

export async function submitFeedback(req: FeedbackRequest): Promise<void> {
	const res = await fetch('/api/v1/feedback', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(req),
		credentials: 'same-origin'
	});
	if (!res.ok && res.status !== 204) {
		const body = await res.json().catch(() => ({}));
		throw new Error(body?.detail ?? `Feedback failed: ${res.status}`);
	}
}

/** Response-level thumbs up/down on the whole answer (one per turn; upserts). */
export interface ResponseFeedbackRequest {
	session_id: string;
	turn_id: string;
	query: string;
	rating: 1 | -1;
	response?: string;
	profile_id?: string | null;
}

export async function submitResponseFeedback(req: ResponseFeedbackRequest): Promise<void> {
	const res = await fetch('/api/v1/feedback/response', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(req),
		credentials: 'same-origin'
	});
	if (!res.ok && res.status !== 204) {
		const body = await res.json().catch(() => ({}));
		throw new Error(body?.detail ?? `Feedback failed: ${res.status}`);
	}
}

// ---------------------------------------------------------------------------
// Structured errors
// ---------------------------------------------------------------------------

/**
 * A chat failure carrying the backend's machine `code` and HTTP status, so the
 * UI can render a friendly, per-type message instead of a raw string.
 */
export class ChatError extends Error {
	code: string;
	httpStatus?: number;
	isNetwork: boolean;
	constructor(message: string, code = 'unknown_error', httpStatus?: number, isNetwork = false) {
		super(message);
		this.name = 'ChatError';
		this.code = code;
		this.httpStatus = httpStatus;
		this.isNetwork = isNetwork;
	}
}

export type ErrorSeverity = 'info' | 'warning' | 'error';

export interface ChatErrorInfo {
	code: string;
	title: string;
	message: string;
	severity: ErrorSeverity;
	canRetry: boolean;
}

// Per-code presentation. The backend already returns a localized, friendly
// `message`; here we add a title, severity, and whether a retry makes sense.
const ERROR_META: Record<
	string,
	{ severity: ErrorSeverity; canRetry: boolean; title: { bg: string; en: string } }
> = {
	authentication_error: {
		severity: 'error',
		canRetry: false,
		title: { bg: 'Необходимо е вписване', en: 'Sign-in required' }
	},
	unsupported_language: {
		severity: 'warning',
		canRetry: false,
		title: { bg: 'Неподдържан език', en: 'Unsupported language' }
	},
	off_topic: { severity: 'info', canRetry: false, title: { bg: 'Извън обхвата', en: 'Off topic' } },
	forbidden_request: {
		severity: 'warning',
		canRetry: false,
		title: { bg: 'Недопустимо запитване', en: 'Request not allowed' }
	},
	profile_forbidden: {
		severity: 'warning',
		canRetry: false,
		title: { bg: 'Няма достъп', en: 'Access denied' }
	},
	quota_exceeded: {
		severity: 'warning',
		canRetry: false,
		title: { bg: 'Достигнат лимит', en: 'Daily limit reached' }
	},
	rate_limit: {
		severity: 'warning',
		canRetry: true,
		title: { bg: 'Твърде много запитвания', en: 'Too many requests' }
	},
	upstream_error: {
		severity: 'error',
		canRetry: true,
		title: { bg: 'Временен проблем', en: 'Temporary problem' }
	},
	empty_response: {
		severity: 'warning',
		canRetry: true,
		title: { bg: 'Празен отговор', en: 'No answer returned' }
	},
	configuration_error: {
		severity: 'error',
		canRetry: false,
		title: { bg: 'Проблем с конфигурацията', en: 'Configuration problem' }
	},
	network_error: {
		severity: 'error',
		canRetry: true,
		title: { bg: 'Няма връзка със сървъра', en: 'Connection problem' }
	}
};

const DEFAULT_TITLE = { bg: 'Грешка', en: 'Error' };
const NETWORK_MSG = {
	bg: 'Връзката със сървъра прекъсна. Проверете интернет връзката си и опитайте отново.',
	en: 'The connection to the server was lost. Check your connection and try again.'
};
const FALLBACK_MSG = {
	bg: 'Възникна неочаквана грешка. Моля, опитайте отново.',
	en: 'An unexpected error occurred. Please try again.'
};

/** True when the error is the user aborting the stream (Stop button) — not a failure. */
export function isAbortError(err: unknown): boolean {
	return err instanceof Error && err.name === 'AbortError';
}

/**
 * Turn any thrown value from `streamChat` into a friendly, localized error to
 * render in the chat. `lang` (inferred from the user's message) localizes the
 * title and any client-side fallback text.
 */
export function resolveChatError(err: unknown, lang: 'bg' | 'en' = 'en'): ChatErrorInfo {
	let code = 'unknown_error';
	let message = '';
	let isNetwork = false;

	if (err instanceof ChatError) {
		code = err.code || 'unknown_error';
		message = err.message || '';
		isNetwork = err.isNetwork;
	} else if (err instanceof Error) {
		message = err.message;
		isNetwork = /fetch|network|load failed|connection/i.test(err.message);
	}
	if (isNetwork) code = 'network_error';

	const meta = ERROR_META[code];
	const title = (meta ? meta.title : DEFAULT_TITLE)[lang];
	const severity: ErrorSeverity = meta ? meta.severity : 'error';
	const canRetry = meta ? meta.canRetry : true;

	// Prefer the backend's localized message; fall back to a localized default
	// when there isn't one (network failure, or a raw non-localized string).
	let body = message;
	if (!body || isNetwork || code === 'unknown_error') {
		body = (code === 'network_error' ? NETWORK_MSG : FALLBACK_MSG)[lang];
	}

	return { code, title, message: body, severity, canRetry };
}

/**
 * Streams a chat turn from the backend.
 * Calls `onEvent` for each parsed SSE event and resolves when done.
 * Throws a `ChatError` on an `error` event or HTTP failure, an AbortError on
 * user abort, or a network ChatError when the request can't be sent.
 */
export async function streamChat(
	request: ChatRequest,
	onEvent: (event: ChatEvent) => void,
	signal?: AbortSignal
): Promise<void> {
	let res: Response;
	try {
		res = await fetch('/api/v1/chat', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
			body: JSON.stringify(request),
			credentials: 'same-origin',
			signal
		});
	} catch (e) {
		if (isAbortError(e)) throw e; // user stopped — propagate as-is
		throw new ChatError((e as Error)?.message ?? 'Network error', 'network_error', undefined, true);
	}

	if (!res.ok) {
		const body = await res.json().catch(() => null);
		const detail = body?.detail;
		if (detail && typeof detail === 'object') {
			throw new ChatError(
				detail.message ?? `Request failed (${res.status})`,
				detail.code,
				res.status
			);
		}
		throw new ChatError(
			typeof detail === 'string' ? detail : `Request failed (${res.status})`,
			'unknown_error',
			res.status
		);
	}

	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buffer = '';

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true });
			buffer = buffer.replace(/\r\n/g, '\n');

			const parts = buffer.split('\n\n');
			buffer = parts.pop() ?? '';

			for (const part of parts) {
				const lines = part.split('\n');
				let eventType = '';
				// SSE allows several `data:` lines per event, joined with "\n".
				const dataParts: string[] = [];

				for (const line of lines) {
					if (line.startsWith('event: ')) eventType = line.slice(7).trim();
					else if (line.startsWith('data: ')) dataParts.push(line.slice(6));
					else if (line === 'data:') dataParts.push('');
				}
				const dataStr = dataParts.join('\n').trim();

				if (!eventType || !dataStr) continue;

				let payload: Record<string, unknown>;
				try {
					payload = JSON.parse(dataStr);
				} catch {
					continue;
				}

				const event = { type: eventType, ...payload } as ChatEvent;
				onEvent(event);

				if (eventType === 'error') {
					const ev = event as ErrorEvent;
					throw new ChatError(ev.message, ev.code, ev.http_status);
				}
				if (eventType === 'done') return;
			}
		}
	} finally {
		// `done` returns before the body is drained; release the socket instead of
		// leaving the reader (and the edge's SSE connection) open until timeout.
		reader.cancel().catch(() => {});
	}
}
