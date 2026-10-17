/**
 * Structured server-side logger.
 *
 * Dev:  coloured single-line text to stdout
 * Prod: JSON to stdout (one object per line — suitable for log aggregators)
 *
 * Level filtering: set LOG_LEVEL env var to debug|info|warn|error.
 * Default: debug in development, info in production.
 */

export type Fields = Record<string, unknown>;
type Level = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_RANK: Record<Level, number> = { debug: 0, info: 1, warn: 2, error: 3 };

const isProd = process.env.NODE_ENV === 'production';
const rawLevel = (process.env.LOG_LEVEL ?? (isProd ? 'info' : 'debug')).toLowerCase() as Level;
const minRank = LEVEL_RANK[rawLevel] ?? (isProd ? 1 : 0);

// ANSI colour codes — only used in dev.
const C = {
	gray: '\x1b[90m',
	cyan: '\x1b[36m',
	yellow: '\x1b[33m',
	red: '\x1b[31m',
	reset: '\x1b[0m'
};
const LEVEL_COLOR: Record<Level, string> = {
	debug: C.gray,
	info: C.cyan,
	warn: C.yellow,
	error: C.red
};

function writeLine(level: Level, line: string): void {
	if (level === 'error') console.error(line);
	else if (level === 'warn') console.warn(line);
	else console.log(line);
}

function emit(level: Level, msg: string, fields?: Fields) {
	if (LEVEL_RANK[level] < minRank) return;

	const ts = new Date().toISOString();

	if (isProd) {
		const line = JSON.stringify({ ts, level, msg, ...fields });
		writeLine(level, line);
		return;
	}

	// Dev: human-readable coloured line
	const col = LEVEL_COLOR[level];
	const tag = `${col}${level.toUpperCase().padEnd(5)}${C.reset}`;
	const fieldStr =
		fields && Object.keys(fields).length
			? '  ' +
				Object.entries(fields)
					.map(([k, v]) => `${C.gray}${k}${C.reset}=${JSON.stringify(v)}`)
					.join(' ')
			: '';
	const line = `${C.gray}${ts}${C.reset} ${tag} ${msg}${fieldStr}`;
	writeLine(level, line);
}

export const logger = {
	debug: (msg: string, fields?: Fields) => emit('debug', msg, fields),
	info: (msg: string, fields?: Fields) => emit('info', msg, fields),
	warn: (msg: string, fields?: Fields) => emit('warn', msg, fields),
	error: (msg: string, fields?: Fields) => emit('error', msg, fields)
};

/** Returns the first `len` chars of an opaque ID — enough for log correlation, not enough to reconstruct it. */
export function traceId(id: string, len = 8): string {
	return id.slice(0, len);
}

/** Returns elapsed milliseconds since `start` (from `performance.now()`), rounded. */
export function elapsed(start: number): number {
	return Math.round(performance.now() - start);
}
