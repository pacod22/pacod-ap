import { describe, expect, it } from 'vitest';
import {
	buildEvent,
	configFromEnv,
	createLogger,
	parseLevel,
	renderText,
	requestLine,
	resolveFormat,
	stripQuery,
	type LogConfig
} from './logger';

// The pacod logging convention (pacod-demo/docs/LOGGING.md).

function capture(overrides: Partial<LogConfig> = {}) {
	const lines: string[] = [];
	const config: LogConfig = {
		service: 'ap-frontend',
		level: 'debug',
		format: 'json',
		colors: false,
		write: (line) => lines.push(line),
		...overrides
	};
	return { lines, config };
}

const TS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const ANSI = /\x1b\[/; // eslint-disable-line no-control-regex

describe('the event', () => {
	it('starts with the five keys in order, then the fields flat', () => {
		const { lines, config } = capture();
		createLogger('hooks', config).info('auth.me.ok', { request_id: 'abc', count: 3 });
		expect(lines).toHaveLength(1);
		const event = JSON.parse(lines[0]);
		expect(Object.keys(event)).toEqual([
			'timestamp',
			'level',
			'service',
			'logger',
			'message',
			'request_id',
			'count'
		]);
		expect(event.timestamp).toMatch(TS);
		expect(event).toMatchObject({
			level: 'info',
			service: 'ap-frontend',
			logger: 'hooks',
			message: 'auth.me.ok',
			count: 3
		});
	});

	it('names the default logger "frontend" and never leaves the message empty', () => {
		const { lines, config } = capture();
		createLogger(undefined, config).warn('');
		const event = JSON.parse(lines[0]);
		expect(event.logger).toBe('frontend');
		expect(event.level).toBe('warn');
		expect(event.message).toBe('frontend');
	});

	it('gives reserved field names a trailing underscore', () => {
		const event = buildEvent('info', 's', 'm', 'real', { message: 'x', level: 'y', service: 'z' });
		expect(event.message).toBe('real');
		expect(event.message_).toBe('x');
		expect(event.level_).toBe('y');
		expect(event.service_).toBe('z');
	});

	it('turns an Error into error + stack_trace', () => {
		const err = new TypeError('bad input\nsecond line');
		const event = buildEvent('error', 's', 'm', 'boom', { error: err, path: '/x' });
		expect(event.error).toBe('TypeError: bad input');
		expect(String(event.stack_trace)).toContain('TypeError: bad input');
		expect(Object.keys(event).slice(-2)).toEqual(['error', 'stack_trace']);
	});

	it('filters by level', () => {
		const { lines, config } = capture({ level: 'warn' });
		const log = createLogger('x', config);
		log.debug('a');
		log.info('b');
		log.warn('c');
		log.error('d');
		expect(lines.map((l) => JSON.parse(l).message)).toEqual(['c', 'd']);
	});

	it('writes the text line', () => {
		const event = buildEvent(
			'info',
			'ap-frontend',
			'hooks',
			'GET /chat 200',
			{ request_id: 'r1', note: 'a b', status: 200 },
			new Date('2026-10-23T06:41:07.695Z')
		);
		expect(renderText(event)).toBe(
			'2026-10-23T06:41:07.695Z INFO  ap-frontend hooks: GET /chat 200 request_id=r1 note="a b" status=200'
		);
		expect(renderText(event)).not.toMatch(ANSI);
		expect(renderText(event, true)).toMatch(ANSI);
	});

	it('puts the stack trace on the next lines in text', () => {
		const event = buildEvent('error', 's', 'm', 'boom', { error: new Error('nope') });
		const [first, second] = renderText(event).split('\n');
		expect(first).toMatch(/ERROR s m: boom error="Error: nope"$/);
		expect(second).toBe('Error: nope');
	});
});

describe('the switches', () => {
	it.each([
		['debug', 'debug'],
		['INFO', 'info'],
		['warn', 'warn'],
		['Warning', 'warn'],
		['error', 'error'],
		['verbose', 'info']
	])('LOG_LEVEL=%s → %s', (value, level) => {
		expect(parseLevel(value)).toBe(level);
	});

	it.each([
		['json', 'development', 'json'],
		['TEXT', 'production', 'text'],
		[undefined, 'production', 'json'],
		[undefined, 'development', 'text'],
		['yaml', 'production', 'json'],
		['yaml', 'development', 'json']
	])('LOG_FORMAT=%s NODE_ENV=%s → %s', (value, nodeEnv, format) => {
		expect(resolveFormat(value, nodeEnv)).toBe(format);
	});

	it('reads the environment', () => {
		expect(configFromEnv({ NODE_ENV: 'production' }, true)).toMatchObject({
			service: 'ap-frontend',
			level: 'info',
			format: 'json',
			colors: false
		});
		expect(
			configFromEnv({ NODE_ENV: 'production', LOG_FORMAT: 'text', SERVICE_NAME: 'ap-ui' }, false)
		).toMatchObject({ service: 'ap-ui', format: 'text', colors: false });
		// Colours only on a terminal.
		expect(configFromEnv({ LOG_FORMAT: 'text' }, true).colors).toBe(true);
		// Unset level: debug while developing; set but unknown: info.
		expect(configFromEnv({}, false).level).toBe('debug');
		expect(configFromEnv({ LOG_LEVEL: 'loud' }, false).level).toBe('info');
		expect(configFromEnv({ LOG_LEVEL: 'WARNING' }, false).level).toBe('warn');
	});
});

describe('request lines', () => {
	it('drops the query string', () => {
		const line = requestLine('GET', '/auth/callback?code=secret&state=x', 302, {
			durationMs: 12,
			clientIp: '203.0.113.9'
		});
		expect(line.message).toBe('GET /auth/callback 302');
		expect(line.fields).toEqual({
			method: 'GET',
			path: '/auth/callback',
			status: 302,
			duration_ms: 12,
			client_ip: '203.0.113.9'
		});
		expect(line.level).toBe('info');
		expect(JSON.stringify(line)).not.toContain('secret');
	});

	it('logs health checks at debug, errors by status', () => {
		expect(requestLine('GET', '/health', 200).level).toBe('debug');
		expect(requestLine('GET', '/x', 404).level).toBe('warn');
		expect(requestLine('GET', '/x', 502).level).toBe('error');
		expect(requestLine('GET', '/x', 200).fields).not.toHaveProperty('client_ip');
	});

	it('strips queries and fragments', () => {
		expect(stripQuery('/a?b=1')).toBe('/a');
		expect(stripQuery('/a#frag')).toBe('/a');
		expect(stripQuery('http://ap-api:8000/x?token=1')).toBe('http://ap-api:8000/x');
	});
});
