import { json, type RequestHandler } from '@sveltejs/kit';

export const GET: RequestHandler = () => json({ status: 'ok', service: 'frontend' });
