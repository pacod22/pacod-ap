/**
 * Server-side logger in the pacod logging convention (pacod-demo/docs/LOGGING.md): one event
 * per line on stdout.
 *
 * JSON (LOG_FORMAT=json, the default under NODE_ENV=production):
 *   {"timestamp":"2026-10-23T06:41:07.695Z","level":"info","service":"ap-frontend",
 *    "logger":"hooks","message":"GET /chat 200","request_id":"x1Y2z3A4","method":"GET",...}
 *
 * Text (LOG_FORMAT=text, the default otherwise):
 *   2026-10-23T06:41:07.695Z INFO  ap-frontend hooks: GET /chat 200 request_id=x1Y2z3A4 ...
 *   Colours only when stdout is a terminal; never in a container log.
 *
 * - `timestamp`, `level` (debug|info|warn|error), `service` (SERVICE_NAME, default ap-frontend),
 *   `logger` (the module: `createLogger('hooks')`; `logger` itself is "frontend"), `message`,
 *   then the fields flat. A field named like one of those five gets a trailing underscore.
 * - An `Error` passed as the `error` (or `err`) field becomes `error` ("TypeError: msg", one
 *   line) and `stack_trace`.
 * - LOG_LEVEL: debug | info | warn (or warning) | error, any case; unknown → info; unset →
 *   debug in development, info in production.
 * - LOG_FORMAT: json | text, any case; unset → json when NODE_ENV=production, else text; set to
 *   anything else → json.
 */

export type Fields = Record<string, unknown>;
export type Level = 'debug' | 'info' | 'warn' | 'error';
export type Format = 'json' | 'text';

const LEVEL_RANK: Record<Level, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const RESERVED = ['timestamp', 'level', 'service', 'logger', 'message'] as const;
const DEFAULT_SERVICE = 'ap-frontend';
const DEFAULT_LOGGER = 'frontend';

// Request paths that are health checks: logged at debug (the convention's request lines).
const HEALTH_PATHS = new Set(['/health', '/healthz', '/ready', '/readyz', '/live', '/livez']);

type Env = Record<string, string | undefined>;

export interface LogConfig {
	service: string;
	level: Level;
	format: Format;
	colors: boolean;
	write: (line: string) => void;
}

/** debug | info | warn (warning) | error, any case; anything else → `fallback`. */
export function parseLevel(value: string | undefined, fallback: Level = 'info'): Level {
	const v = (value ?? '').trim().toLowerCase();
	if (v === 'warning') return 'warn';
	return v in LEVEL_RANK ? (v as Level) : fallback;
}

/** json | text, any case; unset → json in production, else text; set to anything else → json. */
export function resolveFormat(value: string | undefined, nodeEnv: string | undefined): Format {
	const v = (value ?? '').trim().toLowerCase();
	if (v === 'json' || v === 'text') return v;
	return v === '' && nodeEnv !== 'production' ? 'text' : 'json';
}

function writeStdout(line: string): void {
	process.stdout.write(line + '\n');
}

export function configFromEnv(
	env: Env = process.env,
	isTTY: boolean = Boolean(process.stdout?.isTTY)
): LogConfig {
	const prod = env.NODE_ENV === 'production';
	const rawLevel = env.LOG_LEVEL;
	const format = resolveFormat(env.LOG_FORMAT, env.NODE_ENV);
	return {
		service: env.SERVICE_NAME?.trim() || DEFAULT_SERVICE,
		// Unset keeps the old default (debug while developing); set but unknown is info.
		level:
			rawLevel === undefined || rawLevel.trim() === ''
				? prod
					? 'info'
					: 'debug'
				: parseLevel(rawLevel),
		format,
		colors: format === 'text' && isTTY,
		write: writeStdout
	};
}

function describeError(e: Error): string {
	const first =
		(e.message ?? '')
			.split('\n')
			.find((l) => l.trim() !== '')
			?.trim() ?? '';
	const name = e.name || 'Error';
	return first ? `${name}: ${first}` : name;
}

/** The event: the five keys in order, the fields, then error / stack_trace. */
export function buildEvent(
	level: Level,
	service: string,
	loggerName: string,
	message: string,
	fields: Fields | undefined,
	now: Date = new Date()
): Record<string, unknown> {
	const event: Record<string, unknown> = {
		timestamp: now.toISOString(), // RFC 3339, UTC, Z, exactly three fraction digits
		level,
		service,
		logger: loggerName || DEFAULT_LOGGER,
		// Never empty: an event with only fields is named by its logger.
		message: message || loggerName || DEFAULT_LOGGER
	};
	let error: Error | undefined;
	for (const [key, value] of Object.entries(fields ?? {})) {
		if (value === undefined) continue;
		if ((key === 'error' || key === 'err') && value instanceof Error && !error) {
			error = value;
			continue;
		}
		const name = (RESERVED as readonly string[]).includes(key) ? `${key}_` : key;
		event[name] = value instanceof Error ? describeError(value) : value;
	}
	if (error) {
		if ('error' in event) event.error_ = event.error;
		event.error = describeError(error);
		if (error.stack) event.stack_trace = error.stack;
	}
	return event;
}

function jsonReplacer(_key: string, value: unknown): unknown {
	return typeof value === 'bigint' ? value.toString() : value;
}

export function renderJson(event: Record<string, unknown>): string {
	try {
		return JSON.stringify(event, jsonReplacer);
	} catch {
		// A circular structure: its string form rather than losing the event.
		const safe: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(event)) {
			safe[k] = v !== null && typeof v === 'object' ? String(v) : v;
		}
		return JSON.stringify(safe, jsonReplacer);
	}
}

function textValue(value: unknown): string {
	if (typeof value === 'string') {
		return value === '' || /[\s"]/.test(value) ? JSON.stringify(value) : value;
	}
	let text: string;
	try {
		text = JSON.stringify(value, jsonReplacer) ?? String(value);
	} catch {
		text = String(value);
	}
	return /\s/.test(text) ? JSON.stringify(text) : text;
}

const ANSI: Record<Level, string> = {
	debug: '\x1b[90m',
	info: '\x1b[36m',
	warn: '\x1b[33m',
	error: '\x1b[31m'
};
const RESET = '\x1b[0m';

/** `<timestamp> <LEVEL> <service> <logger>: <message> key=value ...`, the stack trace below. */
export function renderText(event: Record<string, unknown>, colors = false): string {
	const level = String(event.level) as Level;
	let tag = level.toUpperCase().padEnd(5);
	if (colors) tag = `${ANSI[level] ?? ''}${tag}${RESET}`;
	let line = `${event.timestamp} ${tag} ${event.service} ${event.logger}: ${event.message}`;
	const pairs = Object.entries(event)
		.filter(([k]) => !(RESERVED as readonly string[]).includes(k) && k !== 'stack_trace')
		.map(([k, v]) => `${k}=${textValue(v)}`);
	if (pairs.length) line += ' ' + pairs.join(' ');
	if (event.stack_trace) line += '\n' + String(event.stack_trace);
	return line;
}

export interface Logger {
	debug: (msg: string, fields?: Fields) => void;
	info: (msg: string, fields?: Fields) => void;
	warn: (msg: string, fields?: Fields) => void;
	error: (msg: string, fields?: Fields) => void;
}

/** A logger for one module (`logger` in the events). `config` is for tests. */
export function createLogger(name: string = DEFAULT_LOGGER, config?: LogConfig): Logger {
	// Read lazily, once: tests and the SvelteKit dev server set the environment first.
	let cfg = config;
	const emit = (level: Level, msg: string, fields?: Fields) => {
		cfg ??= configFromEnv();
		if (LEVEL_RANK[level] < LEVEL_RANK[cfg.level]) return;
		const event = buildEvent(level, cfg.service, name, msg, fields);
		cfg.write(cfg.format === 'json' ? renderJson(event) : renderText(event, cfg.colors));
	};
	return {
		debug: (msg, fields) => emit('debug', msg, fields),
		info: (msg, fields) => emit('info', msg, fields),
		warn: (msg, fields) => emit('warn', msg, fields),
		error: (msg, fields) => emit('error', msg, fields)
	};
}

/** The module-less logger (`"logger":"frontend"`). Prefer `createLogger('<module>')`. */
export const logger = createLogger(DEFAULT_LOGGER);

/** A path without its query string (it can carry tokens and codes). */
export function stripQuery(path: string): string {
	const i = path.search(/[?#]/);
	return i === -1 ? path : path.slice(0, i);
}

export function isHealthPath(path: string): boolean {
	return HEALTH_PATHS.has(path) || path.startsWith('/health/');
}

/**
 * The convention's request line: message `<METHOD> <path> <status>`, fields method, path (no
 * query string), status, duration_ms and client_ip when known. Health checks at debug; other
 * requests at error (5xx), warn (4xx) or info.
 */
export function requestLine(
	method: string,
	path: string,
	status: number,
	opts: { durationMs?: number; clientIp?: string } = {}
): { level: Level; message: string; fields: Fields } {
	const p = stripQuery(path) || '/';
	const fields: Fields = { method, path: p, status };
	if (opts.durationMs !== undefined) fields.duration_ms = opts.durationMs;
	if (opts.clientIp) fields.client_ip = opts.clientIp;
	const level: Level = isHealthPath(p)
		? 'debug'
		: status >= 500
			? 'error'
			: status >= 400
				? 'warn'
				: 'info';
	return { level, message: `${method} ${p} ${status}`, fields };
}

/** Returns the first `len` chars of an opaque ID — enough for log correlation, not enough to reconstruct it. */
export function traceId(id: string, len = 8): string {
	return id.slice(0, len);
}

/** Returns elapsed milliseconds since `start` (from `performance.now()`), rounded. */
export function elapsed(start: number): number {
	return Math.round(performance.now() - start);
}
