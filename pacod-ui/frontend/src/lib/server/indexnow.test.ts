import { describe, expect, it } from 'vitest';
import { indexNowResponse } from './indexnow';

const KEY = '3f2b8c1e-aaaa-4bbb-9ccc-0123456789ab';

describe('indexNowResponse', () => {
	it('serves the key as plain text at /<key>.txt', async () => {
		const res = indexNowResponse(`/${KEY}.txt`, KEY, true);
		expect(res?.status).toBe(200);
		expect(res?.headers.get('content-type')).toMatch(/^text\/plain/);
		expect(await res?.text()).toBe(KEY);
	});

	it('does not exist when the key is blank or indexing is off', () => {
		expect(indexNowResponse(`/${KEY}.txt`, '', true)).toBeNull();
		expect(indexNowResponse(`/${KEY}.txt`, KEY, false)).toBeNull();
		// A blank key must not turn "/.txt" into a key file.
		expect(indexNowResponse('/.txt', '', true)).toBeNull();
	});

	it('ignores every other path, including other .txt files', () => {
		for (const path of ['/robots.txt', '/llms.txt', `/${KEY}`, `/x/${KEY}.txt`, '/other-key.txt']) {
			expect(indexNowResponse(path, KEY, true)).toBeNull();
		}
	});
});
